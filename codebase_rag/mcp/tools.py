import itertools
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from loguru import logger

from codebase_rag.analysis.analysis_runner import AnalysisRunner
from codebase_rag.architecture.registry import ToolRegistry
from codebase_rag.core import constants as cs
from codebase_rag.core import logs as lg
from codebase_rag.data_models.models import ToolMetadata
from codebase_rag.data_models.types_defs import (
    CodeSnippetResultDict,
    DeleteProjectErrorResult,
    DeleteProjectResult,
    DeleteProjectSuccessResult,
    ListProjectsErrorResult,
    ListProjectsResult,
    ListProjectsSuccessResult,
    MCPHandlerType,
    MCPInputSchema,
    MCPInputSchemaProperty,
    MCPToolSchema,
    QueryResultDict,
)
from codebase_rag.exporters.mermaid_exporter import MermaidExporter
from codebase_rag.graph_db.cypher_queries import (
    CYPHER_GET_LATEST_ANALYSIS_REPORT,
    CYPHER_GET_LATEST_METRIC,
)
from codebase_rag.graph_db.graph_updater import GraphUpdater
from codebase_rag.infrastructure import tool_errors as te
from codebase_rag.infrastructure.parser_loader import load_parsers
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.services.llm import CypherGenerator
from codebase_rag.tools import tool_descriptions as td
from codebase_rag.tools.code_retrieval import CodeRetriever, create_code_retrieval_tool
from codebase_rag.tools.codebase_query import create_query_tool
from codebase_rag.tools.directory_lister import (
    DirectoryLister,
    create_directory_lister_tool,
)
from codebase_rag.tools.file_editor import FileEditor, create_file_editor_tool
from codebase_rag.tools.file_reader import FileReader, create_file_reader_tool
from codebase_rag.tools.file_writer import FileWriter, create_file_writer_tool


class MCPToolsRegistry:
    def __init__(
        self,
        project_root: str,
        ingestor: MemgraphIngestor,
        cypher_gen: CypherGenerator,
    ) -> None:
        self.project_root = project_root
        self.ingestor = ingestor
        self.cypher_gen = cypher_gen

        self.parsers, self.queries = load_parsers()

        self.code_retriever = CodeRetriever(project_root, ingestor)
        self.file_editor = FileEditor(project_root=project_root)
        self.file_reader = FileReader(project_root=project_root)
        self.file_writer = FileWriter(project_root=project_root)
        self.directory_lister = DirectoryLister(project_root=project_root)

        self._query_tool = create_query_tool(
            ingestor=ingestor,
            cypher_gen=cypher_gen,
            console=None,
            render_output=False,
        )
        self._code_tool = create_code_retrieval_tool(code_retriever=self.code_retriever)
        self._file_editor_tool = create_file_editor_tool(file_editor=self.file_editor)
        self._file_reader_tool = create_file_reader_tool(file_reader=self.file_reader)
        self._file_writer_tool = create_file_writer_tool(file_writer=self.file_writer)
        self._directory_lister_tool = create_directory_lister_tool(
            directory_lister=self.directory_lister
        )

        async def _default_plan(goal: str, context: str | None = None) -> object:
            _ = goal
            _ = context
            return SimpleNamespace(
                status="ok",
                content={"summary": "", "steps": [], "risks": [], "tests": []},
            )

        async def _default_run(_task: str) -> object:
            return SimpleNamespace(status="ok", content="")

        self._planner_agent = SimpleNamespace(plan=_default_plan)
        self._test_agent = SimpleNamespace(run=_default_run)
        self._memory_entries: list[dict[str, object]] = []

        self._tools: dict[str, ToolMetadata] = {
            cs.MCPToolName.LIST_PROJECTS: ToolMetadata(
                name=cs.MCPToolName.LIST_PROJECTS,
                description=td.MCP_TOOLS[cs.MCPToolName.LIST_PROJECTS],
                input_schema=MCPInputSchema(
                    type=cs.MCPSchemaType.OBJECT,
                    properties={},
                    required=[],
                ),
                handler=self.list_projects,
                returns_json=True,
            ),
            cs.MCPToolName.DELETE_PROJECT: ToolMetadata(
                name=cs.MCPToolName.DELETE_PROJECT,
                description=td.MCP_TOOLS[cs.MCPToolName.DELETE_PROJECT],
                input_schema=MCPInputSchema(
                    type=cs.MCPSchemaType.OBJECT,
                    properties={
                        cs.MCPParamName.PROJECT_NAME: MCPInputSchemaProperty(
                            type=cs.MCPSchemaType.STRING,
                            description=td.MCP_PARAM_PROJECT_NAME,
                        )
                    },
                    required=[cs.MCPParamName.PROJECT_NAME],
                ),
                handler=self.delete_project,
                returns_json=True,
            ),
            cs.MCPToolName.WIPE_DATABASE: ToolMetadata(
                name=cs.MCPToolName.WIPE_DATABASE,
                description=td.MCP_TOOLS[cs.MCPToolName.WIPE_DATABASE],
                input_schema=MCPInputSchema(
                    type=cs.MCPSchemaType.OBJECT,
                    properties={
                        cs.MCPParamName.CONFIRM: MCPInputSchemaProperty(
                            type=cs.MCPSchemaType.BOOLEAN,
                            description=td.MCP_PARAM_CONFIRM,
                        )
                    },
                    required=[cs.MCPParamName.CONFIRM],
                ),
                handler=self.wipe_database,
                returns_json=False,
            ),
            cs.MCPToolName.INDEX_REPOSITORY: ToolMetadata(
                name=cs.MCPToolName.INDEX_REPOSITORY,
                description=td.MCP_TOOLS[cs.MCPToolName.INDEX_REPOSITORY],
                input_schema=MCPInputSchema(
                    type=cs.MCPSchemaType.OBJECT,
                    properties={},
                    required=[],
                ),
                handler=self.index_repository,
                returns_json=False,
            ),
            cs.MCPToolName.QUERY_CODE_GRAPH: ToolMetadata(
                name=cs.MCPToolName.QUERY_CODE_GRAPH,
                description=td.MCP_TOOLS[cs.MCPToolName.QUERY_CODE_GRAPH],
                input_schema=MCPInputSchema(
                    type=cs.MCPSchemaType.OBJECT,
                    properties={
                        cs.MCPParamName.NATURAL_LANGUAGE_QUERY: MCPInputSchemaProperty(
                            type=cs.MCPSchemaType.STRING,
                            description=td.MCP_PARAM_NATURAL_LANGUAGE_QUERY,
                        ),
                        "output_format": MCPInputSchemaProperty(
                            type=cs.MCPSchemaType.STRING,
                            description="Output format: 'json' (default), 'text', or 'cypher'.",
                            default="json",
                        ),
                    },
                    required=[cs.MCPParamName.NATURAL_LANGUAGE_QUERY],
                ),
                handler=self.query_code_graph,
                returns_json=True,
            ),
            cs.MCPToolName.GET_CODE_SNIPPET: ToolMetadata(
                name=cs.MCPToolName.GET_CODE_SNIPPET,
                description=td.MCP_TOOLS[cs.MCPToolName.GET_CODE_SNIPPET],
                input_schema=MCPInputSchema(
                    type=cs.MCPSchemaType.OBJECT,
                    properties={
                        cs.MCPParamName.QUALIFIED_NAME: MCPInputSchemaProperty(
                            type=cs.MCPSchemaType.STRING,
                            description=td.MCP_PARAM_QUALIFIED_NAME,
                        )
                    },
                    required=[cs.MCPParamName.QUALIFIED_NAME],
                ),
                handler=self.get_code_snippet,
                returns_json=True,
            ),
            cs.MCPToolName.SURGICAL_REPLACE_CODE: ToolMetadata(
                name=cs.MCPToolName.SURGICAL_REPLACE_CODE,
                description=td.MCP_TOOLS[cs.MCPToolName.SURGICAL_REPLACE_CODE],
                input_schema=MCPInputSchema(
                    type=cs.MCPSchemaType.OBJECT,
                    properties={
                        cs.MCPParamName.FILE_PATH: MCPInputSchemaProperty(
                            type=cs.MCPSchemaType.STRING,
                            description=td.MCP_PARAM_FILE_PATH,
                        ),
                        cs.MCPParamName.TARGET_CODE: MCPInputSchemaProperty(
                            type=cs.MCPSchemaType.STRING,
                            description=td.MCP_PARAM_TARGET_CODE,
                        ),
                        cs.MCPParamName.REPLACEMENT_CODE: MCPInputSchemaProperty(
                            type=cs.MCPSchemaType.STRING,
                            description=td.MCP_PARAM_REPLACEMENT_CODE,
                        ),
                    },
                    required=[
                        cs.MCPParamName.FILE_PATH,
                        cs.MCPParamName.TARGET_CODE,
                        cs.MCPParamName.REPLACEMENT_CODE,
                    ],
                ),
                handler=self.surgical_replace_code,
                returns_json=False,
            ),
            cs.MCPToolName.READ_FILE: ToolMetadata(
                name=cs.MCPToolName.READ_FILE,
                description=td.MCP_TOOLS[cs.MCPToolName.READ_FILE],
                input_schema=MCPInputSchema(
                    type=cs.MCPSchemaType.OBJECT,
                    properties={
                        cs.MCPParamName.FILE_PATH: MCPInputSchemaProperty(
                            type=cs.MCPSchemaType.STRING,
                            description=td.MCP_PARAM_FILE_PATH,
                        ),
                        cs.MCPParamName.OFFSET: MCPInputSchemaProperty(
                            type=cs.MCPSchemaType.INTEGER,
                            description=td.MCP_PARAM_OFFSET,
                        ),
                        cs.MCPParamName.LIMIT: MCPInputSchemaProperty(
                            type=cs.MCPSchemaType.INTEGER,
                            description=td.MCP_PARAM_LIMIT,
                        ),
                    },
                    required=[cs.MCPParamName.FILE_PATH],
                ),
                handler=self.read_file,
                returns_json=False,
            ),
            cs.MCPToolName.WRITE_FILE: ToolMetadata(
                name=cs.MCPToolName.WRITE_FILE,
                description=td.MCP_TOOLS[cs.MCPToolName.WRITE_FILE],
                input_schema=MCPInputSchema(
                    type=cs.MCPSchemaType.OBJECT,
                    properties={
                        cs.MCPParamName.FILE_PATH: MCPInputSchemaProperty(
                            type=cs.MCPSchemaType.STRING,
                            description=td.MCP_PARAM_FILE_PATH,
                        ),
                        cs.MCPParamName.CONTENT: MCPInputSchemaProperty(
                            type=cs.MCPSchemaType.STRING,
                            description=td.MCP_PARAM_CONTENT,
                        ),
                    },
                    required=[
                        cs.MCPParamName.FILE_PATH,
                        cs.MCPParamName.CONTENT,
                    ],
                ),
                handler=self.write_file,
                returns_json=False,
            ),
            cs.MCPToolName.LIST_DIRECTORY: ToolMetadata(
                name=cs.MCPToolName.LIST_DIRECTORY,
                description=td.MCP_TOOLS[cs.MCPToolName.LIST_DIRECTORY],
                input_schema=MCPInputSchema(
                    type=cs.MCPSchemaType.OBJECT,
                    properties={
                        cs.MCPParamName.DIRECTORY_PATH: MCPInputSchemaProperty(
                            type=cs.MCPSchemaType.STRING,
                            description=td.MCP_PARAM_DIRECTORY_PATH,
                            default=cs.MCP_DEFAULT_DIRECTORY,
                        )
                    },
                    required=[],
                ),
                handler=self.list_directory,
                returns_json=False,
            ),
        }

    async def list_projects(self) -> ListProjectsResult:
        logger.info(lg.MCP_LISTING_PROJECTS)
        try:
            projects = self.ingestor.list_projects()
            return ListProjectsSuccessResult(projects=projects, count=len(projects))
        except Exception as e:
            logger.error(lg.MCP_ERROR_LIST_PROJECTS.format(error=e))
            return ListProjectsErrorResult(error=str(e), projects=[], count=0)

    async def delete_project(self, project_name: str) -> DeleteProjectResult:
        logger.info(lg.MCP_DELETING_PROJECT.format(project_name=project_name))
        try:
            projects = self.ingestor.list_projects()
            if project_name not in projects:
                return DeleteProjectErrorResult(
                    success=False,
                    error=te.MCP_PROJECT_NOT_FOUND.format(
                        project_name=project_name, projects=projects
                    ),
                )
            self.ingestor.delete_project(project_name)
            return DeleteProjectSuccessResult(
                success=True,
                project=project_name,
                message=cs.MCP_PROJECT_DELETED.format(project_name=project_name),
            )
        except Exception as e:
            logger.error(lg.MCP_ERROR_DELETE_PROJECT.format(error=e))
            return DeleteProjectErrorResult(success=False, error=str(e))

    async def wipe_database(self, confirm: bool) -> str:
        if not confirm:
            return cs.MCP_WIPE_CANCELLED
        logger.warning(lg.MCP_WIPING_DATABASE)
        try:
            self.ingestor.clean_database()
            return cs.MCP_WIPE_SUCCESS
        except Exception as e:
            logger.error(lg.MCP_ERROR_WIPE.format(error=e))
            return cs.MCP_WIPE_ERROR.format(error=e)

    async def index_repository(self) -> str:
        logger.info(lg.MCP_INDEXING_REPO.format(path=self.project_root))
        project_name = Path(self.project_root).resolve().name
        try:
            logger.info(lg.MCP_CLEARING_PROJECT.format(project_name=project_name))
            self.ingestor.delete_project(project_name)

            updater = GraphUpdater(
                ingestor=self.ingestor,
                repo_path=Path(self.project_root),
                parsers=self.parsers,
                queries=self.queries,
            )
            updater.run()

            return cs.MCP_INDEX_SUCCESS_PROJECT.format(
                path=self.project_root, project_name=project_name
            )
        except Exception as e:
            logger.error(lg.MCP_ERROR_INDEXING.format(error=e))
            return cs.MCP_INDEX_ERROR.format(error=e)

    async def query_code_graph(
        self, natural_language_query: str, output_format: str = "json"
    ) -> QueryResultDict | str:
        logger.info(lg.MCP_QUERY_CODE_GRAPH.format(query=natural_language_query))
        try:
            graph_data = await self._query_tool.function(natural_language_query)
            result_dict: QueryResultDict = graph_data.model_dump()
            logger.info(
                lg.MCP_QUERY_RESULTS.format(
                    count=len(result_dict.get(cs.DICT_KEY_RESULTS, []))
                )
            )

            normalized_format = output_format.strip().lower()
            if normalized_format == "cypher":
                return str(result_dict.get("query_used", ""))

            if normalized_format == "text":
                query_used = str(result_dict.get("query_used", ""))
                summary = str(result_dict.get("summary", ""))
                results = result_dict.get("results", [])
                results_text = json.dumps(
                    results,
                    indent=2,
                    ensure_ascii=False,
                )
                return (
                    "CYPHER QUERY:\n"
                    f"{query_used}\n\n"
                    "RESULTS:\n"
                    f"{results_text}\n\n"
                    "SUMMARY:\n"
                    f"{summary}"
                )

            return result_dict
        except Exception as e:
            logger.exception(lg.MCP_ERROR_QUERY.format(error=e))
            return QueryResultDict(
                error=str(e),
                query_used=cs.QUERY_NOT_AVAILABLE,
                results=[],
                summary=cs.MCP_TOOL_EXEC_ERROR.format(
                    name=cs.MCPToolName.QUERY_CODE_GRAPH, error=e
                ),
            )

    async def get_code_snippet(self, qualified_name: str) -> CodeSnippetResultDict:
        logger.info(lg.MCP_GET_CODE_SNIPPET.format(name=qualified_name))
        try:
            snippet = await self._code_tool.function(qualified_name=qualified_name)
            result: CodeSnippetResultDict | None = snippet.model_dump()
            if result is None:
                return CodeSnippetResultDict(
                    error=te.MCP_TOOL_RETURNED_NONE,
                    found=False,
                    error_message=te.MCP_INVALID_RESPONSE,
                )
            return result
        except Exception as e:
            logger.error(lg.MCP_ERROR_CODE_SNIPPET.format(error=e))
            return CodeSnippetResultDict(
                error=str(e),
                found=False,
                error_message=str(e),
            )

    async def surgical_replace_code(
        self, file_path: str, target_code: str, replacement_code: str
    ) -> str:
        logger.info(lg.MCP_SURGICAL_REPLACE.format(path=file_path))
        try:
            result = await self._file_editor_tool.function(
                file_path=file_path,
                target_code=target_code,
                replacement_code=replacement_code,
            )
            return str(result)
        except Exception as e:
            logger.error(lg.MCP_ERROR_REPLACE.format(error=e))
            return te.ERROR_WRAPPER.format(message=e)

    async def read_file(
        self, file_path: str, offset: int | None = None, limit: int | None = None
    ) -> str:
        logger.info(lg.MCP_READ_FILE.format(path=file_path, offset=offset, limit=limit))
        try:
            if offset is not None or limit is not None:
                full_path = Path(self.project_root) / file_path
                start = offset if offset is not None else 0

                with open(full_path, encoding=cs.ENCODING_UTF8) as f:
                    skipped_count = sum(1 for _ in itertools.islice(f, start))

                    if limit is not None:
                        sliced_lines = [line for _, line in zip(range(limit), f)]
                    else:
                        sliced_lines = list(f)

                    paginated_content = "".join(sliced_lines)

                    remaining_lines_count = sum(1 for _ in f)
                    total_lines = (
                        skipped_count + len(sliced_lines) + remaining_lines_count
                    )

                    header = cs.MCP_PAGINATION_HEADER.format(
                        start=start + 1,
                        end=start + len(sliced_lines),
                        total=total_lines,
                    )
                    return header + paginated_content
            else:
                result = await self._file_reader_tool.function(file_path=file_path)
                return str(result)

        except Exception as e:
            logger.error(lg.MCP_ERROR_READ.format(error=e))
            return te.ERROR_WRAPPER.format(message=e)

    async def write_file(self, file_path: str, content: str) -> str:
        logger.info(lg.MCP_WRITE_FILE.format(path=file_path))
        try:
            result = await self._file_writer_tool.function(
                file_path=file_path, content=content
            )
            if result.success:
                return cs.MCP_WRITE_SUCCESS.format(path=file_path)
            return te.ERROR_WRAPPER.format(message=result.error_message)
        except Exception as e:
            logger.error(lg.MCP_ERROR_WRITE.format(error=e))
            return te.ERROR_WRAPPER.format(message=e)

    async def list_directory(
        self, directory_path: str = cs.MCP_DEFAULT_DIRECTORY
    ) -> str:
        logger.info(lg.MCP_LIST_DIR.format(path=directory_path))
        try:
            result = self._directory_lister_tool.function(directory_path=directory_path)
            return str(result)
        except Exception as e:
            logger.error(lg.MCP_ERROR_LIST_DIR.format(error=e))
            return te.ERROR_WRAPPER.format(message=e)

    async def run_cypher(
        self, cypher: str, params: str | None = None, write: bool = False
    ) -> dict[str, object]:
        if not cypher:
            return {"error": te.MCP_INVALID_RESPONSE, "results": []}
        parsed_params: dict[str, object] = {}
        if params:
            try:
                payload = json.loads(params)
                if isinstance(payload, dict):
                    parsed_params = payload
            except json.JSONDecodeError:
                parsed_params = {}

        try:
            if write:
                self.ingestor.execute_write(cypher, cast(dict[str, Any], parsed_params))
                return {"status": "ok", "results": []}
            results = self.ingestor.fetch_all(
                cypher, cast(dict[str, Any], parsed_params)
            )
            return {"status": "ok", "results": results}
        except Exception as exc:
            return {"error": str(exc), "results": []}

    async def get_graph_stats(self) -> dict[str, object]:
        try:
            node_count = self.ingestor.fetch_all("MATCH (n) RETURN count(n) AS count")
            rel_count = self.ingestor.fetch_all(
                "MATCH ()-[r]->() RETURN count(r) AS count"
            )
            label_stats = self.ingestor.fetch_all(
                "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS count ORDER BY count DESC"
            )
            rel_stats = self.ingestor.fetch_all(
                "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count ORDER BY count DESC"
            )
            return {
                "nodes": node_count[0]["count"] if node_count else 0,
                "relationships": rel_count[0]["count"] if rel_count else 0,
                "labels": label_stats,
                "relationship_types": rel_stats,
            }
        except Exception as exc:
            return {"error": str(exc)}

    async def get_dependency_stats(self) -> dict[str, object]:
        try:
            total = self.ingestor.fetch_all(
                "MATCH (m:Module)-[:DEFINES]->(i:Import) RETURN count(i) AS count"
            )
            top_importers = self.ingestor.fetch_all(
                "MATCH (m:Module)-[:DEFINES]->(i:Import) "
                "RETURN m.qualified_name AS module, count(i) AS count "
                "ORDER BY count DESC LIMIT 10"
            )
            top_dependents = self.ingestor.fetch_all(
                "MATCH (m:Module)-[:DEFINES]->(i:Import) "
                "RETURN i.import_source AS target, count(*) AS count "
                "ORDER BY count DESC LIMIT 10"
            )
            return {
                "total_imports": total[0]["count"] if total else 0,
                "top_importers": top_importers,
                "top_dependents": top_dependents,
            }
        except Exception as exc:
            return {"error": str(exc)}

    async def export_mermaid(
        self, diagram: str, output_path: str | None = None
    ) -> dict[str, object]:
        try:
            graph_data = self.ingestor.export_graph_to_dict()
            output_dir = Path(self.project_root) / "output" / "mermaid"
            output_dir.mkdir(parents=True, exist_ok=True)
            graph_path = output_dir / "graph.json"
            graph_path.write_text(
                json.dumps(graph_data, ensure_ascii=False, indent=2),
                encoding=cs.ENCODING_UTF8,
            )
            mermaid = MermaidExporter(str(graph_path))
            target = output_path or str(output_dir / f"{diagram}.mmd")
            mermaid.export(diagram=diagram, output_path=target)
            content = Path(target).read_text(encoding=cs.ENCODING_UTF8)
            return {"status": "ok", "output_path": target, "content": content}
        except Exception as exc:
            return {"error": str(exc)}

    async def run_analysis(self) -> dict[str, object]:
        try:
            runner = AnalysisRunner(self.ingestor, Path(self.project_root))
            runner.run_all()
            return {"status": "ok"}
        except Exception as exc:
            return {"error": str(exc)}

    async def run_analysis_subset(self, modules: str) -> dict[str, object]:
        try:
            parsed = json.loads(modules)
        except json.JSONDecodeError:
            parsed = [item.strip() for item in modules.split(",") if item.strip()]
        if not isinstance(parsed, list) or not parsed:
            return {"error": "modules_required"}
        module_set = {str(item).strip() for item in parsed if str(item).strip()}
        if not module_set:
            return {"error": "modules_required"}
        try:
            runner = AnalysisRunner(self.ingestor, Path(self.project_root))
            runner.run_modules(module_set)
            return {"status": "ok", "modules": sorted(module_set)}
        except Exception as exc:
            return {"error": str(exc)}

    async def security_scan(self) -> dict[str, object]:
        modules = {"security", "secret_scan", "sast_taint_tracking"}
        try:
            runner = AnalysisRunner(self.ingestor, Path(self.project_root))
            runner.run_modules(modules)
        except Exception as exc:
            return {"error": str(exc)}
        try:
            results = self.ingestor.fetch_all(
                CYPHER_GET_LATEST_ANALYSIS_REPORT,
                {cs.KEY_PROJECT_NAME: Path(self.project_root).resolve().name},
            )
            if not results:
                return {"error": "analysis_report_not_found"}
            row = results[0]
            summary_raw = row.get("analysis_summary")
            summary = summary_raw
            if isinstance(summary_raw, str):
                try:
                    summary = json.loads(summary_raw)
                except json.JSONDecodeError:
                    summary = summary_raw
            if isinstance(summary, dict):
                return {
                    "analysis_timestamp": row.get("analysis_timestamp"),
                    **summary,
                }
            return {
                "analysis_timestamp": row.get("analysis_timestamp"),
                "summary": summary,
            }
        except Exception as exc:
            return {"error": str(exc)}

    async def performance_hotspots(self) -> dict[str, object]:
        try:
            runner = AnalysisRunner(self.ingestor, Path(self.project_root))
            runner.run_modules({"performance_hotspots"})
            return {"status": "ok"}
        except Exception as exc:
            return {"error": str(exc)}

    async def get_analysis_report(self) -> dict[str, object]:
        try:
            results = self.ingestor.fetch_all(
                CYPHER_GET_LATEST_ANALYSIS_REPORT,
                {cs.KEY_PROJECT_NAME: Path(self.project_root).resolve().name},
            )
            if not results:
                return {"error": "analysis_report_not_found"}
            row = results[0]
            summary_raw = row.get("analysis_summary")
            summary = summary_raw
            if isinstance(summary_raw, str):
                try:
                    summary = json.loads(summary_raw)
                except json.JSONDecodeError:
                    summary = summary_raw
            return {
                "run_id": row.get("run_id"),
                "analysis_timestamp": row.get("analysis_timestamp"),
                "summary": summary,
            }
        except Exception as exc:
            return {"error": str(exc)}

    async def get_analysis_metric(self, metric_name: str) -> dict[str, object]:
        if not metric_name:
            return {"error": te.MCP_INVALID_RESPONSE}
        try:
            results = self.ingestor.fetch_all(
                CYPHER_GET_LATEST_METRIC,
                {
                    cs.KEY_PROJECT_NAME: Path(self.project_root).resolve().name,
                    "metric_name": metric_name,
                },
            )
            if not results:
                return {"error": "metric_not_found"}
            row = results[0]
            metric_raw = row.get("metric_value")
            metric_value = metric_raw
            if isinstance(metric_raw, str):
                try:
                    metric_value = json.loads(metric_raw)
                except json.JSONDecodeError:
                    metric_value = metric_raw
            return {
                "metric_name": metric_name,
                "analysis_timestamp": row.get("analysis_timestamp"),
                "metric_value": metric_value,
            }
        except Exception as exc:
            return {"error": str(exc)}

    async def get_analysis_artifact(self, artifact_name: str) -> dict[str, object]:
        allowed = {
            "dead_code_report",
            "unused_imports_report",
            "unused_variables_report",
            "unreachable_code_report",
            "refactoring_candidates_report",
            "taint_report",
            "license_report",
            "arch_drift_report",
            "secret_scan_report",
        }
        if artifact_name not in allowed:
            return {"error": "artifact_not_allowed"}
        report_path = (
            Path(self.project_root) / "output" / "analysis" / f"{artifact_name}.json"
        )
        if not report_path.exists():
            return {"error": "artifact_not_found"}
        try:
            content = report_path.read_text(encoding=cs.ENCODING_UTF8)
        except Exception as exc:
            return {"error": str(exc)}
        return {"artifact": artifact_name, "content": content}

    async def apply_diff_safe(self, file_path: str, chunks: str) -> dict[str, object]:
        if file_path.startswith(".env"):
            return {"error": "sensitive_path"}
        try:
            payload = json.loads(chunks)
        except json.JSONDecodeError:
            return {"error": "invalid_chunks_json"}
        if not isinstance(payload, list) or not payload:
            return {"error": "chunks_must_be_list"}
        return await self._apply_diff_chunks(file_path, payload)

    async def refactor_batch(self, chunks: str) -> dict[str, object]:
        try:
            payload = json.loads(chunks)
        except json.JSONDecodeError:
            return {"error": "invalid_chunks_json"}
        if not isinstance(payload, list) or not payload:
            return {"error": "chunks_must_be_list"}
        results: list[dict[str, object]] = []
        for entry in payload:
            if not isinstance(entry, dict):
                return {"error": "batch_entry_not_object"}
            file_path = entry.get("file_path")
            file_chunks = entry.get("chunks")
            if not isinstance(file_path, str) or not isinstance(file_chunks, list):
                return {"error": "batch_entry_invalid"}
            result = await self._apply_diff_chunks(file_path, file_chunks)
            results.append({"file_path": file_path, "result": result})
        return {"status": "ok", "results": results}

    async def test_generate(
        self, goal: str, context: str | None = None
    ) -> dict[str, object]:
        prompt = goal if context is None else f"{goal}\nContext: {context}"
        result = await self._test_agent.run(prompt)
        return {"status": result.status, "content": result.content}

    async def memory_add(
        self, entry: str, tags: str | None = None
    ) -> dict[str, object]:
        parsed_tags: list[str] = []
        if tags:
            parsed_tags = [item.strip() for item in tags.split(",") if item.strip()]
        record = {
            "text": entry,
            "tags": parsed_tags,
            "timestamp": int(time.time()),
        }
        self._memory_entries.insert(0, record)
        return {"status": "ok", "entry": record["text"], "tags": record["tags"]}

    async def memory_list(self, limit: int = 50) -> dict[str, object]:
        entries = self._memory_entries[: max(0, limit)]
        return {"count": len(entries), "entries": entries}

    async def plan_task(
        self, goal: str, context: str | None = None
    ) -> dict[str, object]:
        try:
            result = await self._planner_agent.plan(goal, context=context)
            if hasattr(result, "content") and isinstance(result.content, dict):
                return {"status": result.status, **result.content}
            return {"status": result.status, "content": result.content}
        except Exception as exc:
            return {"error": str(exc)}

    async def impact_graph(
        self,
        qualified_name: str | None = None,
        file_path: str | None = None,
        depth: int = 3,
        limit: int = 200,
    ) -> dict[str, object]:
        _ = depth
        _ = limit
        if not qualified_name and not file_path:
            return {"error": "missing_target"}
        query = "MATCH (n) RETURN n LIMIT 50"
        params = {"qualified_name": qualified_name, "file_path": file_path}
        try:
            results = self.ingestor.fetch_all(query, params)
            return {"count": len(results), "results": results}
        except Exception as exc:
            return {"error": str(exc), "results": []}

    async def _apply_diff_chunks(
        self, file_path: str, payload: list[dict[str, object]]
    ) -> dict[str, object]:
        total_lines = 0
        results: list[str] = []
        for idx, chunk in enumerate(payload, start=1):
            if not isinstance(chunk, dict):
                return {"error": f"chunk_not_object_{idx}"}
            target_code = chunk.get("target_code")
            replacement_code = chunk.get("replacement_code")
            if not isinstance(target_code, str) or not isinstance(
                replacement_code, str
            ):
                return {"error": f"chunk_missing_fields_{idx}"}
            total_lines += len(target_code.splitlines()) + len(
                replacement_code.splitlines()
            )
            if total_lines > 200:
                return {"error": "diff_limit_exceeded"}
            result = await self._file_editor_tool.function(
                file_path=file_path,
                target_code=target_code,
                replacement_code=replacement_code,
            )
            results.append(str(result))
        return {"status": "ok", "results": results}

    def get_tool_schemas(self) -> list[MCPToolSchema]:
        return [
            MCPToolSchema(
                name=metadata.name,
                description=metadata.description,
                inputSchema=metadata.input_schema,
            )
            for metadata in self._tools.values()
        ]

    def get_tool_handler(self, name: str) -> tuple[MCPHandlerType, bool] | None:
        metadata = self._tools.get(name)
        return None if metadata is None else (metadata.handler, metadata.returns_json)


def create_mcp_tools_registry(
    project_root: str,
    ingestor: MemgraphIngestor,
    cypher_gen: CypherGenerator,
) -> MCPToolsRegistry:
    return MCPToolsRegistry(
        project_root=project_root,
        ingestor=ingestor,
        cypher_gen=cypher_gen,
    )


@ToolRegistry.register(cs.MCPToolName.LIST_PROJECTS, category="core")
def _register_list_projects(registry: MCPToolsRegistry) -> ToolMetadata:
    return ToolMetadata(
        name=cs.MCPToolName.LIST_PROJECTS,
        description=td.MCP_TOOLS[cs.MCPToolName.LIST_PROJECTS],
        input_schema=MCPInputSchema(
            type=cs.MCPSchemaType.OBJECT,
            properties={},
            required=[],
        ),
        handler=registry.list_projects,
        returns_json=True,
    )


@ToolRegistry.register(cs.MCPToolName.DELETE_PROJECT, category="core")
def _register_delete_project(registry: MCPToolsRegistry) -> ToolMetadata:
    return ToolMetadata(
        name=cs.MCPToolName.DELETE_PROJECT,
        description=td.MCP_TOOLS[cs.MCPToolName.DELETE_PROJECT],
        input_schema=MCPInputSchema(
            type=cs.MCPSchemaType.OBJECT,
            properties={
                cs.MCPParamName.PROJECT_NAME: MCPInputSchemaProperty(
                    type=cs.MCPSchemaType.STRING,
                    description=td.MCP_PARAM_PROJECT_NAME,
                )
            },
            required=[cs.MCPParamName.PROJECT_NAME],
        ),
        handler=registry.delete_project,
        returns_json=True,
    )


@ToolRegistry.register(cs.MCPToolName.WIPE_DATABASE, category="core")
def _register_wipe_database(registry: MCPToolsRegistry) -> ToolMetadata:
    return ToolMetadata(
        name=cs.MCPToolName.WIPE_DATABASE,
        description=td.MCP_TOOLS[cs.MCPToolName.WIPE_DATABASE],
        input_schema=MCPInputSchema(
            type=cs.MCPSchemaType.OBJECT,
            properties={
                cs.MCPParamName.CONFIRM: MCPInputSchemaProperty(
                    type=cs.MCPSchemaType.BOOLEAN,
                    description=td.MCP_PARAM_CONFIRM,
                )
            },
            required=[cs.MCPParamName.CONFIRM],
        ),
        handler=registry.wipe_database,
        returns_json=False,
    )
