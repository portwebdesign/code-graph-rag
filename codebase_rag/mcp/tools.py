import asyncio
import itertools
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from loguru import logger

from codebase_rag.agents import (
    MCP_SYSTEM_PROMPT,
    PlannerAgent,
    TestAgent,
    ValidatorAgent,
    normalize_orchestrator_prompt,
)
from codebase_rag.analysis.analysis_runner import AnalysisRunner
from codebase_rag.core import constants as cs
from codebase_rag.core import logs as lg
from codebase_rag.core.config import settings
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
from codebase_rag.policy.engine import MCPPolicyEngine
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
from codebase_rag.tools.semantic_search import (
    create_get_function_source_tool,
    create_semantic_search_tool,
    get_function_source_code,
    semantic_code_search,
)


class MCPMemoryStore:
    def __init__(self, project_root: str, max_entries: int = 1000) -> None:
        self._max_entries = max_entries
        self._storage_path = (
            Path(project_root) / ".codebase_rag" / "mcp_memory" / "entries.json"
        )
        self._entries = self._load_entries()

    def add_entry(self, text: str, tags: list[str]) -> dict[str, object]:
        record = {
            "text": text,
            "tags": tags,
            "timestamp": int(time.time()),
        }
        self._entries.insert(0, record)
        self._entries = self._entries[: self._max_entries]
        self._persist_entries()
        return record

    def list_entries(self, limit: int = 50) -> list[dict[str, object]]:
        return self._entries[: max(0, limit)]

    def query_patterns(
        self,
        query: str,
        filter_tags: list[str] | None = None,
        success_only: bool = False,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        normalized_terms = [
            token.strip().lower()
            for token in query.replace("_", " ").split()
            if token.strip()
        ]
        normalized_tags = {
            tag.strip().lower() for tag in (filter_tags or []) if tag.strip()
        }
        ranked: list[tuple[int, int, dict[str, object]]] = []

        for idx, entry in enumerate(self._entries):
            tags = entry.get("tags", [])
            text = str(entry.get("text", ""))
            if not isinstance(tags, list):
                tags = []
            entry_tags = {str(tag).lower() for tag in tags}

            if normalized_tags and not normalized_tags.issubset(entry_tags):
                continue
            if success_only and not self._is_success_record(entry):
                continue

            score = 0
            lowered_text = text.lower()
            for term in normalized_terms:
                if term in lowered_text:
                    score += 3
                if term in entry_tags:
                    score += 2
            if normalized_terms and score == 0:
                continue

            recency_bonus = max(0, len(self._entries) - idx)
            ranked.append((score, recency_bonus, entry))

        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in ranked[: max(0, limit)]]

    @staticmethod
    def _is_success_record(entry: dict[str, object]) -> bool:
        tags = entry.get("tags", [])
        if isinstance(tags, list) and any(
            str(tag).lower() in {"allow", "success", "ok"} for tag in tags
        ):
            return True

        text = entry.get("text")
        if not isinstance(text, str):
            return False
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return False
        if not isinstance(payload, dict):
            return False
        decision = str(payload.get("decision", "")).lower()
        status = str(payload.get("status", "")).lower()
        result = str(payload.get("result", "")).lower()
        return (
            decision in {"allow", "success"}
            or status
            in {
                "ok",
                "success",
            }
            or result in {"success", "ok"}
        )

    def _load_entries(self) -> list[dict[str, object]]:
        if not self._storage_path.exists():
            return []
        try:
            raw = self._storage_path.read_text(encoding=cs.ENCODING_UTF8)
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
            return []
        except Exception as exc:
            logger.warning(
                lg.MCP_SERVER_TOOL_ERROR.format(name="memory_load", error=exc)
            )
            return []

    def _persist_entries(self) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._storage_path.write_text(
            json.dumps(self._entries, ensure_ascii=False, indent=2),
            encoding=cs.ENCODING_UTF8,
        )


class MCPImpactGraphService:
    _IMPACT_REL_TYPES = "CALLS|IMPORTS|INHERITS|USES"

    def __init__(self, ingestor: MemgraphIngestor) -> None:
        self._ingestor = ingestor

    def query(
        self,
        qualified_name: str | None = None,
        file_path: str | None = None,
        depth: int = 3,
        limit: int = 200,
    ) -> dict[str, object]:
        bounded_depth = min(max(1, int(depth)), 6)
        bounded_limit = min(max(1, int(limit)), 1000)
        query = f"""
MATCH (start)
WHERE (
    $qualified_name IS NOT NULL
    AND start.qualified_name = $qualified_name
) OR (
    $file_path IS NOT NULL
    AND (
        start.path = $file_path
        OR start.file_path = $file_path
        OR start.path ENDS WITH $file_path
        OR start.file_path ENDS WITH $file_path
    )
)
WITH collect(DISTINCT start) AS seeds
UNWIND seeds AS seed
MATCH p=(seed)-[:{self._IMPACT_REL_TYPES}*1..{bounded_depth}]->(target)
WITH seed, target, relationships(p) AS rels, length(p) AS hop_count
RETURN DISTINCT
    coalesce(seed.qualified_name, seed.path, seed.file_path, seed.name, toString(id(seed))) AS source,
    labels(seed) AS source_labels,
    coalesce(target.qualified_name, target.path, target.file_path, target.name, toString(id(target))) AS target,
    labels(target) AS target_labels,
    type(last(rels)) AS relation,
    hop_count
LIMIT $limit
"""
        params = {
            "qualified_name": qualified_name,
            "file_path": file_path,
            "limit": bounded_limit,
        }
        results = self._ingestor.fetch_all(query, params)
        return {
            "count": len(results),
            "depth": bounded_depth,
            "limit": bounded_limit,
            "results": results,
        }


def _build_tool_metadata(registry: "MCPToolsRegistry") -> dict[str, ToolMetadata]:
    return {
        cs.MCPToolName.LIST_PROJECTS: ToolMetadata(
            name=cs.MCPToolName.LIST_PROJECTS,
            description=td.MCP_TOOLS[cs.MCPToolName.LIST_PROJECTS],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={},
                required=[],
            ),
            handler=registry.list_projects,
            returns_json=True,
        ),
        cs.MCPToolName.SELECT_ACTIVE_PROJECT: ToolMetadata(
            name=cs.MCPToolName.SELECT_ACTIVE_PROJECT,
            description=td.MCP_TOOLS[cs.MCPToolName.SELECT_ACTIVE_PROJECT],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.REPO_PATH: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_REPO_PATH,
                    )
                },
                required=[],
            ),
            handler=registry.select_active_project,
            returns_json=True,
        ),
        cs.MCPToolName.DETECT_PROJECT_DRIFT: ToolMetadata(
            name=cs.MCPToolName.DETECT_PROJECT_DRIFT,
            description=td.MCP_TOOLS[cs.MCPToolName.DETECT_PROJECT_DRIFT],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.REPO_PATH: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_REPO_PATH,
                    )
                },
                required=[],
            ),
            handler=registry.detect_project_drift,
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
            handler=registry.delete_project,
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
            handler=registry.wipe_database,
            returns_json=False,
        ),
        cs.MCPToolName.INDEX_REPOSITORY: ToolMetadata(
            name=cs.MCPToolName.INDEX_REPOSITORY,
            description=td.MCP_TOOLS[cs.MCPToolName.INDEX_REPOSITORY],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.REPO_PATH: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_REPO_PATH,
                    ),
                    cs.MCPParamName.USER_REQUESTED: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.BOOLEAN,
                        description=td.MCP_PARAM_USER_REQUESTED,
                    ),
                    cs.MCPParamName.DRIFT_CONFIRMED: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.BOOLEAN,
                        description=td.MCP_PARAM_DRIFT_CONFIRMED,
                        default=False,
                    ),
                    cs.MCPParamName.REASON: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_REASON,
                    ),
                },
                required=[
                    cs.MCPParamName.REPO_PATH,
                    cs.MCPParamName.USER_REQUESTED,
                    cs.MCPParamName.REASON,
                ],
            ),
            handler=registry.index_repository,
            returns_json=False,
        ),
        cs.MCPToolName.SYNC_GRAPH_UPDATES: ToolMetadata(
            name=cs.MCPToolName.SYNC_GRAPH_UPDATES,
            description=td.MCP_TOOLS[cs.MCPToolName.SYNC_GRAPH_UPDATES],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.USER_REQUESTED: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.BOOLEAN,
                        description=td.MCP_PARAM_USER_REQUESTED,
                    ),
                    cs.MCPParamName.REASON: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_REASON,
                    ),
                },
                required=[cs.MCPParamName.USER_REQUESTED, cs.MCPParamName.REASON],
            ),
            handler=registry.sync_graph_updates,
            returns_json=True,
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
            handler=registry.query_code_graph,
            returns_json=True,
        ),
        cs.MCPToolName.SEMANTIC_SEARCH: ToolMetadata(
            name=cs.MCPToolName.SEMANTIC_SEARCH,
            description=td.MCP_TOOLS[cs.MCPToolName.SEMANTIC_SEARCH],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.QUERY: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_QUERY,
                    ),
                    cs.MCPParamName.TOP_K: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.INTEGER,
                        description=td.MCP_PARAM_TOP_K,
                        default=5,
                    ),
                },
                required=[cs.MCPParamName.QUERY],
            ),
            handler=registry.semantic_search,
            returns_json=True,
        ),
        cs.MCPToolName.GET_FUNCTION_SOURCE: ToolMetadata(
            name=cs.MCPToolName.GET_FUNCTION_SOURCE,
            description=td.MCP_TOOLS[cs.MCPToolName.GET_FUNCTION_SOURCE],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.NODE_ID: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.INTEGER,
                        description=td.MCP_PARAM_NODE_ID,
                    )
                },
                required=[cs.MCPParamName.NODE_ID],
            ),
            handler=registry.get_function_source,
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
            handler=registry.get_code_snippet,
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
            handler=registry.surgical_replace_code,
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
            handler=registry.read_file,
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
            handler=registry.write_file,
            returns_json=False,
        ),
        cs.MCPToolName.LIST_DIRECTORY: ToolMetadata(
            name=cs.MCPToolName.LIST_DIRECTORY,
            description=td.MCP_TOOLS[cs.MCPToolName.LIST_DIRECTORY],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.REPO_PATH: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_REPO_PATH,
                    ),
                    cs.MCPParamName.DIRECTORY_PATH: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_DIRECTORY_PATH,
                        default=cs.MCP_DEFAULT_DIRECTORY,
                    ),
                },
                required=[cs.MCPParamName.REPO_PATH],
            ),
            handler=registry.list_directory,
            returns_json=False,
        ),
        cs.MCPToolName.GET_GRAPH_STATS: ToolMetadata(
            name=cs.MCPToolName.GET_GRAPH_STATS,
            description=td.MCP_TOOLS[cs.MCPToolName.GET_GRAPH_STATS],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={},
                required=[],
            ),
            handler=registry.get_graph_stats,
            returns_json=True,
        ),
        cs.MCPToolName.GET_DEPENDENCY_STATS: ToolMetadata(
            name=cs.MCPToolName.GET_DEPENDENCY_STATS,
            description=td.MCP_TOOLS[cs.MCPToolName.GET_DEPENDENCY_STATS],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={},
                required=[],
            ),
            handler=registry.get_dependency_stats,
            returns_json=True,
        ),
        cs.MCPToolName.GET_ANALYSIS_REPORT: ToolMetadata(
            name=cs.MCPToolName.GET_ANALYSIS_REPORT,
            description=td.MCP_TOOLS[cs.MCPToolName.GET_ANALYSIS_REPORT],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={},
                required=[],
            ),
            handler=registry.get_analysis_report,
            returns_json=True,
        ),
        cs.MCPToolName.GET_ANALYSIS_METRIC: ToolMetadata(
            name=cs.MCPToolName.GET_ANALYSIS_METRIC,
            description=td.MCP_TOOLS[cs.MCPToolName.GET_ANALYSIS_METRIC],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.METRIC_NAME: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_METRIC_NAME,
                    )
                },
                required=[cs.MCPParamName.METRIC_NAME],
            ),
            handler=registry.get_analysis_metric,
            returns_json=True,
        ),
        cs.MCPToolName.IMPACT_GRAPH: ToolMetadata(
            name=cs.MCPToolName.IMPACT_GRAPH,
            description=td.MCP_TOOLS[cs.MCPToolName.IMPACT_GRAPH],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.QUALIFIED_NAME: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_QUALIFIED_NAME,
                    ),
                    cs.MCPParamName.FILE_PATH: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_FILE_PATH,
                    ),
                    cs.MCPParamName.DEPTH: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.INTEGER,
                        description=td.MCP_PARAM_DEPTH,
                        default=3,
                    ),
                    cs.MCPParamName.LIMIT: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.INTEGER,
                        description=td.MCP_PARAM_LIMIT,
                        default=200,
                    ),
                },
                required=[],
            ),
            handler=registry.impact_graph,
            returns_json=True,
        ),
        cs.MCPToolName.RUN_ANALYSIS: ToolMetadata(
            name=cs.MCPToolName.RUN_ANALYSIS,
            description=td.MCP_TOOLS[cs.MCPToolName.RUN_ANALYSIS],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={},
                required=[],
            ),
            handler=registry.run_analysis,
            returns_json=True,
        ),
        cs.MCPToolName.RUN_ANALYSIS_SUBSET: ToolMetadata(
            name=cs.MCPToolName.RUN_ANALYSIS_SUBSET,
            description=td.MCP_TOOLS[cs.MCPToolName.RUN_ANALYSIS_SUBSET],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.MODULES: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_MODULES,
                    )
                },
                required=[cs.MCPParamName.MODULES],
            ),
            handler=registry.run_analysis_subset,
            returns_json=True,
        ),
        cs.MCPToolName.SECURITY_SCAN: ToolMetadata(
            name=cs.MCPToolName.SECURITY_SCAN,
            description=td.MCP_TOOLS[cs.MCPToolName.SECURITY_SCAN],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={},
                required=[],
            ),
            handler=registry.security_scan,
            returns_json=True,
        ),
        cs.MCPToolName.PERFORMANCE_HOTSPOTS: ToolMetadata(
            name=cs.MCPToolName.PERFORMANCE_HOTSPOTS,
            description=td.MCP_TOOLS[cs.MCPToolName.PERFORMANCE_HOTSPOTS],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={},
                required=[],
            ),
            handler=registry.performance_hotspots,
            returns_json=True,
        ),
        cs.MCPToolName.GET_ANALYSIS_ARTIFACT: ToolMetadata(
            name=cs.MCPToolName.GET_ANALYSIS_ARTIFACT,
            description=td.MCP_TOOLS[cs.MCPToolName.GET_ANALYSIS_ARTIFACT],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.ARTIFACT_NAME: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_ARTIFACT_NAME,
                    )
                },
                required=[cs.MCPParamName.ARTIFACT_NAME],
            ),
            handler=registry.get_analysis_artifact,
            returns_json=True,
        ),
        cs.MCPToolName.LIST_ANALYSIS_ARTIFACTS: ToolMetadata(
            name=cs.MCPToolName.LIST_ANALYSIS_ARTIFACTS,
            description=td.MCP_TOOLS[cs.MCPToolName.LIST_ANALYSIS_ARTIFACTS],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={},
                required=[],
            ),
            handler=registry.list_analysis_artifacts,
            returns_json=True,
        ),
        cs.MCPToolName.EXPORT_MERMAID: ToolMetadata(
            name=cs.MCPToolName.EXPORT_MERMAID,
            description=td.MCP_TOOLS[cs.MCPToolName.EXPORT_MERMAID],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.DIAGRAM: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_DIAGRAM,
                    ),
                    cs.MCPParamName.OUTPUT_PATH: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_OUTPUT_PATH,
                    ),
                },
                required=[cs.MCPParamName.DIAGRAM],
            ),
            handler=registry.export_mermaid,
            returns_json=True,
        ),
        cs.MCPToolName.RUN_CYPHER: ToolMetadata(
            name=cs.MCPToolName.RUN_CYPHER,
            description=td.MCP_TOOLS[cs.MCPToolName.RUN_CYPHER],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.CYPHER: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_CYPHER,
                    ),
                    cs.MCPParamName.PARAMS: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_PARAMS,
                    ),
                    cs.MCPParamName.WRITE: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.BOOLEAN,
                        description=td.MCP_PARAM_WRITE,
                        default=False,
                    ),
                    cs.MCPParamName.USER_REQUESTED: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.BOOLEAN,
                        description=td.MCP_PARAM_USER_REQUESTED,
                        default=False,
                    ),
                    cs.MCPParamName.REASON: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_REASON,
                    ),
                },
                required=[cs.MCPParamName.CYPHER],
            ),
            handler=registry.run_cypher,
            returns_json=True,
        ),
        cs.MCPToolName.APPLY_DIFF_SAFE: ToolMetadata(
            name=cs.MCPToolName.APPLY_DIFF_SAFE,
            description=td.MCP_TOOLS[cs.MCPToolName.APPLY_DIFF_SAFE],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.FILE_PATH: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_FILE_PATH,
                    ),
                    cs.MCPParamName.CHUNKS: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_CHUNKS,
                    ),
                },
                required=[cs.MCPParamName.FILE_PATH, cs.MCPParamName.CHUNKS],
            ),
            handler=registry.apply_diff_safe,
            returns_json=True,
        ),
        cs.MCPToolName.REFACTOR_BATCH: ToolMetadata(
            name=cs.MCPToolName.REFACTOR_BATCH,
            description=td.MCP_TOOLS[cs.MCPToolName.REFACTOR_BATCH],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.CHUNKS: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_CHUNKS,
                    ),
                },
                required=[cs.MCPParamName.CHUNKS],
            ),
            handler=registry.refactor_batch,
            returns_json=True,
        ),
        cs.MCPToolName.PLAN_TASK: ToolMetadata(
            name=cs.MCPToolName.PLAN_TASK,
            description=td.MCP_TOOLS[cs.MCPToolName.PLAN_TASK],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.GOAL: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_GOAL,
                    ),
                    cs.MCPParamName.CONTEXT: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_CONTEXT,
                    ),
                },
                required=[cs.MCPParamName.GOAL],
            ),
            handler=registry.plan_task,
            returns_json=True,
        ),
        cs.MCPToolName.TEST_GENERATE: ToolMetadata(
            name=cs.MCPToolName.TEST_GENERATE,
            description=td.MCP_TOOLS[cs.MCPToolName.TEST_GENERATE],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.GOAL: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_GOAL,
                    ),
                    cs.MCPParamName.CONTEXT: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_CONTEXT,
                    ),
                },
                required=[cs.MCPParamName.GOAL],
            ),
            handler=registry.test_generate,
            returns_json=True,
        ),
        cs.MCPToolName.MEMORY_ADD: ToolMetadata(
            name=cs.MCPToolName.MEMORY_ADD,
            description=td.MCP_TOOLS[cs.MCPToolName.MEMORY_ADD],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.ENTRY: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_ENTRY,
                    ),
                    cs.MCPParamName.TAGS: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_TAGS,
                    ),
                },
                required=[cs.MCPParamName.ENTRY],
            ),
            handler=registry.memory_add,
            returns_json=True,
        ),
        cs.MCPToolName.MEMORY_LIST: ToolMetadata(
            name=cs.MCPToolName.MEMORY_LIST,
            description=td.MCP_TOOLS[cs.MCPToolName.MEMORY_LIST],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.LIMIT: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.INTEGER,
                        description=td.MCP_PARAM_LIMIT,
                        default=50,
                    ),
                },
                required=[],
            ),
            handler=registry.memory_list,
            returns_json=True,
        ),
        cs.MCPToolName.MEMORY_QUERY_PATTERNS: ToolMetadata(
            name=cs.MCPToolName.MEMORY_QUERY_PATTERNS,
            description=td.MCP_TOOLS[cs.MCPToolName.MEMORY_QUERY_PATTERNS],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.QUERY: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_QUERY,
                    ),
                    cs.MCPParamName.FILTER_TAGS: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_FILTER_TAGS,
                    ),
                    cs.MCPParamName.SUCCESS_ONLY: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.BOOLEAN,
                        description=td.MCP_PARAM_SUCCESS_ONLY,
                        default=False,
                    ),
                    cs.MCPParamName.LIMIT: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.INTEGER,
                        description=td.MCP_PARAM_LIMIT,
                        default=20,
                    ),
                },
                required=[cs.MCPParamName.QUERY],
            ),
            handler=registry.memory_query_patterns,
            returns_json=True,
        ),
        cs.MCPToolName.EXECUTION_FEEDBACK: ToolMetadata(
            name=cs.MCPToolName.EXECUTION_FEEDBACK,
            description=td.MCP_TOOLS[cs.MCPToolName.EXECUTION_FEEDBACK],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.ACTION: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_ACTION,
                    ),
                    cs.MCPParamName.RESULT: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_RESULT,
                    ),
                    cs.MCPParamName.ISSUES: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_ISSUES,
                    ),
                },
                required=[cs.MCPParamName.ACTION, cs.MCPParamName.RESULT],
            ),
            handler=registry.execution_feedback,
            returns_json=True,
        ),
        cs.MCPToolName.TEST_QUALITY_GATE: ToolMetadata(
            name=cs.MCPToolName.TEST_QUALITY_GATE,
            description=td.MCP_TOOLS[cs.MCPToolName.TEST_QUALITY_GATE],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.COVERAGE: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_COVERAGE,
                    ),
                    cs.MCPParamName.EDGE_CASES: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_EDGE_CASES,
                    ),
                    cs.MCPParamName.NEGATIVE_TESTS: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_NEGATIVE_TESTS,
                    ),
                },
                required=[
                    cs.MCPParamName.COVERAGE,
                    cs.MCPParamName.EDGE_CASES,
                    cs.MCPParamName.NEGATIVE_TESTS,
                ],
            ),
            handler=registry.test_quality_gate,
            returns_json=True,
        ),
        cs.MCPToolName.GET_TOOL_USEFULNESS_RANKING: ToolMetadata(
            name=cs.MCPToolName.GET_TOOL_USEFULNESS_RANKING,
            description=td.MCP_TOOLS[cs.MCPToolName.GET_TOOL_USEFULNESS_RANKING],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.LIMIT: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.INTEGER,
                        description=td.MCP_PARAM_LIMIT,
                        default=10,
                    ),
                },
                required=[],
            ),
            handler=registry.get_tool_usefulness_ranking,
            returns_json=True,
        ),
        cs.MCPToolName.VALIDATE_DONE_DECISION: ToolMetadata(
            name=cs.MCPToolName.VALIDATE_DONE_DECISION,
            description=td.MCP_TOOLS[cs.MCPToolName.VALIDATE_DONE_DECISION],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.GOAL: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_GOAL,
                    ),
                    cs.MCPParamName.CONTEXT: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_CONTEXT,
                    ),
                },
                required=[],
            ),
            handler=registry.validate_done_decision,
            returns_json=True,
        ),
        cs.MCPToolName.ORCHESTRATE_REALTIME_FLOW: ToolMetadata(
            name=cs.MCPToolName.ORCHESTRATE_REALTIME_FLOW,
            description=td.MCP_TOOLS[cs.MCPToolName.ORCHESTRATE_REALTIME_FLOW],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.ACTION: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_ACTION,
                    ),
                    cs.MCPParamName.RESULT: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_RESULT,
                    ),
                    cs.MCPParamName.ISSUES: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_ISSUES,
                    ),
                    cs.MCPParamName.USER_REQUESTED: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.BOOLEAN,
                        description=td.MCP_PARAM_USER_REQUESTED,
                    ),
                    cs.MCPParamName.SYNC_REASON: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_SYNC_REASON,
                    ),
                    cs.MCPParamName.GOAL: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_GOAL,
                    ),
                    cs.MCPParamName.CONTEXT: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_CONTEXT,
                    ),
                    cs.MCPParamName.AUTO_EXECUTE_NEXT: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.BOOLEAN,
                        description=td.MCP_PARAM_AUTO_EXECUTE_NEXT,
                        default=settings.MCP_ORCHESTRATE_AUTO_EXECUTE_NEXT_DEFAULT,
                    ),
                    cs.MCPParamName.VERIFY_DRIFT: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.BOOLEAN,
                        description=td.MCP_PARAM_VERIFY_DRIFT,
                        default=settings.MCP_ORCHESTRATE_VERIFY_DRIFT_DEFAULT,
                    ),
                    cs.MCPParamName.DEBOUNCE_SECONDS: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.INTEGER,
                        description=td.MCP_PARAM_DEBOUNCE_SECONDS,
                        default=settings.MCP_ORCHESTRATE_DEBOUNCE_DEFAULT_SECONDS,
                    ),
                },
                required=[
                    cs.MCPParamName.ACTION,
                    cs.MCPParamName.RESULT,
                    cs.MCPParamName.USER_REQUESTED,
                    cs.MCPParamName.SYNC_REASON,
                ],
            ),
            handler=registry.orchestrate_realtime_flow,
            returns_json=True,
        ),
        cs.MCPToolName.GET_EXECUTION_READINESS: ToolMetadata(
            name=cs.MCPToolName.GET_EXECUTION_READINESS,
            description=td.MCP_TOOLS[cs.MCPToolName.GET_EXECUTION_READINESS],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={},
                required=[],
            ),
            handler=registry.get_execution_readiness,
            returns_json=True,
        ),
    }


class MCPToolsRegistry:
    _RETRYABLE_ERROR_MARKERS = (
        "conflicting transaction",
        "conflicting transactions",
        "deadlock",
        "lock timeout",
        "temporarily unavailable",
        "timeout",
        "timed out",
    )

    def __init__(
        self,
        project_root: str,
        ingestor: MemgraphIngestor,
        cypher_gen: CypherGenerator,
        orchestrator_prompt: str | None = None,
    ) -> None:
        self.project_root = project_root
        self.ingestor = ingestor
        self.cypher_gen = cypher_gen
        self._orchestrator_prompt = normalize_orchestrator_prompt(orchestrator_prompt)

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
        self._semantic_search_tool = create_semantic_search_tool()
        self._get_function_source_tool = create_get_function_source_tool()

        async def _default_plan(goal: str, context: str | None = None) -> object:
            _ = goal
            _ = context
            return SimpleNamespace(
                status="ok",
                content={"summary": "", "steps": [], "risks": [], "tests": []},
            )

        async def _default_run(_task: str) -> object:
            return SimpleNamespace(status="ok", content="")

        async def _default_validate(_payload: dict[str, object]) -> object:
            return SimpleNamespace(
                status="ok",
                content={
                    "decision": "not_done",
                    "rationale": "validator_unavailable",
                    "required_actions": ["retry_validation"],
                },
            )

        self._planner_agent = SimpleNamespace(plan=_default_plan)
        self._test_agent = SimpleNamespace(run=_default_run)
        self._validator_agent = SimpleNamespace(validate=_default_validate)
        self._refresh_internal_agents()

        self._memory_store = MCPMemoryStore(project_root=project_root)
        self._impact_service = MCPImpactGraphService(ingestor=ingestor)
        self._policy_engine = MCPPolicyEngine(
            active_project_name_getter=self._active_project_name,
            max_write_impact=50,
        )
        self._session_state: dict[str, object] = {
            "orchestrator_prompt": self._orchestrator_prompt,
            "plan_task_completed": False,
            "test_generate_completed": False,
            "test_quality_total": 0.0,
            "test_quality_pass": False,
            "evidence_reads": 0,
            "code_evidence_count": 0,
            "graph_evidence_count": 0,
            "impact_graph_called": False,
            "impact_graph_count": 0,
            "manual_memory_add_count": 0,
            "query_success_count": 0,
            "semantic_success_count": 0,
            "semantic_similarity_mean": 0.0,
            "edit_success_count": 0,
            "replan_required": False,
            "replan_reasons": [],
            "execution_feedback_count": 0,
            "memory_pattern_query_count": 0,
            "done_decision_count": 0,
            "policy_allow_count": 0,
            "policy_deny_count": 0,
            "pattern_reuse_score": 0.0,
            "orchestrate_circuit": {
                "state": "closed",
                "failure_count": 0,
                "failure_threshold": max(
                    1, int(settings.MCP_ORCHESTRATE_CB_FAILURE_THRESHOLD)
                ),
                "opened_at": 0.0,
                "cooldown_seconds": max(
                    0.5, float(settings.MCP_ORCHESTRATE_CB_COOLDOWN_SECONDS)
                ),
            },
            "tool_telemetry": {},
        }
        self._tools = _build_tool_metadata(self)
        for tool_name in self._tools.keys():
            self._ensure_tool_telemetry_bucket(tool_name)

    def _ensure_tool_telemetry_bucket(self, tool_name: str) -> dict[str, object]:
        telemetry = self._session_state.get("tool_telemetry")
        if not isinstance(telemetry, dict):
            telemetry = {}
            self._session_state["tool_telemetry"] = telemetry
        telemetry_dict = cast(dict[str, object], telemetry)
        bucket = telemetry_dict.get(tool_name)
        if isinstance(bucket, dict):
            return cast(dict[str, object], bucket)
        bucket = {
            "calls": 0,
            "success": 0,
            "failure": 0,
            "usefulness_total": 0.0,
        }
        telemetry_dict[tool_name] = bucket
        return bucket

    def _record_tool_usefulness(
        self,
        tool_name: str,
        *,
        success: bool,
        usefulness_score: float,
    ) -> None:
        bounded_score = max(0.0, min(1.0, float(usefulness_score)))
        bucket = self._ensure_tool_telemetry_bucket(tool_name)
        bucket["calls"] = self._coerce_int(bucket.get("calls", 0)) + 1
        if success:
            bucket["success"] = self._coerce_int(bucket.get("success", 0)) + 1
        else:
            bucket["failure"] = self._coerce_int(bucket.get("failure", 0)) + 1
        bucket["usefulness_total"] = (
            self._coerce_float(bucket.get("usefulness_total", 0.0)) + bounded_score
        )

    def _compute_tool_usefulness_ranking(
        self, limit: int = 10
    ) -> list[dict[str, object]]:
        telemetry = self._session_state.get("tool_telemetry")
        if not isinstance(telemetry, dict):
            return []

        ranking: list[dict[str, object]] = []
        for tool_name, raw_bucket in telemetry.items():
            if not isinstance(raw_bucket, dict):
                continue
            bucket = cast(dict[str, object], raw_bucket)
            calls = self._coerce_int(bucket.get("calls", 0))
            if calls <= 0:
                continue
            success = self._coerce_int(bucket.get("success", 0))
            usefulness_total = self._coerce_float(bucket.get("usefulness_total", 0.0))
            avg_usefulness = usefulness_total / calls
            success_rate = success / calls
            ranking.append(
                {
                    "tool": tool_name,
                    "calls": calls,
                    "success": success,
                    "success_rate": round(success_rate, 3),
                    "avg_usefulness": round(avg_usefulness, 3),
                    "score": round((avg_usefulness * 0.7) + (success_rate * 0.3), 3),
                }
            )

        ranking.sort(
            key=lambda row: (
                self._coerce_float(row.get("score", 0.0)),
                self._coerce_int(row.get("calls", 0)),
            ),
            reverse=True,
        )
        return ranking[: max(1, min(int(limit), 50))]

    async def get_tool_usefulness_ranking(self, limit: int = 10) -> dict[str, object]:
        ranking = self._compute_tool_usefulness_ranking(limit=limit)
        self._record_tool_usefulness(
            cs.MCPToolName.GET_TOOL_USEFULNESS_RANKING,
            success=True,
            usefulness_score=1.0 if ranking else 0.5,
        )
        return {"count": len(ranking), "ranking": ranking}

    @classmethod
    def _is_retryable_error(cls, exc: Exception) -> bool:
        if isinstance(exc, asyncio.TimeoutError):
            return True
        message = str(exc).lower()
        return any(marker in message for marker in cls._RETRYABLE_ERROR_MARKERS)

    async def _run_with_retries(
        self,
        operation: Any,
        *,
        attempts: int = 3,
        base_delay_seconds: float = 0.5,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await operation()
            except Exception as exc:
                last_error = exc
                if attempt >= attempts or not self._is_retryable_error(exc):
                    raise
                await asyncio.sleep(base_delay_seconds * attempt)
        if last_error is not None:
            raise last_error
        return None

    def _resolve_repo_root(self, repo_path: str) -> Path:
        candidate = Path(repo_path).resolve()
        if not candidate.exists() or not candidate.is_dir():
            raise ValueError(te.MCP_PATH_NOT_EXISTS.format(path=candidate))
        return candidate

    def _set_project_root(self, repo_path: str) -> Path:
        resolved_repo = self._resolve_repo_root(repo_path)
        resolved_repo_str = str(resolved_repo)
        self.project_root = resolved_repo_str
        self.code_retriever = CodeRetriever(resolved_repo_str, self.ingestor)
        self.file_editor = FileEditor(project_root=resolved_repo_str)
        self.file_reader = FileReader(project_root=resolved_repo_str)
        self.file_writer = FileWriter(project_root=resolved_repo_str)
        self.directory_lister = DirectoryLister(project_root=resolved_repo_str)
        self._code_tool = create_code_retrieval_tool(code_retriever=self.code_retriever)
        self._file_editor_tool = create_file_editor_tool(file_editor=self.file_editor)
        self._file_reader_tool = create_file_reader_tool(file_reader=self.file_reader)
        self._file_writer_tool = create_file_writer_tool(file_writer=self.file_writer)
        self._directory_lister_tool = create_directory_lister_tool(
            directory_lister=self.directory_lister
        )
        self._memory_store = MCPMemoryStore(project_root=resolved_repo_str)
        self._refresh_internal_agents()
        return resolved_repo

    def _refresh_internal_agents(self) -> None:
        agent_tools = [
            self._query_tool,
            self._code_tool,
            self._file_reader_tool,
            self._directory_lister_tool,
            self._semantic_search_tool,
            self._get_function_source_tool,
        ]
        try:
            self._planner_agent = PlannerAgent(
                agent_tools,
                system_prompt=self._orchestrator_prompt,
            )
        except Exception as exc:
            logger.warning(lg.MCP_SERVER_TOOL_ERROR.format(name="planner", error=exc))
        try:
            self._test_agent = TestAgent(
                agent_tools,
                system_prompt=self._orchestrator_prompt,
            )
        except Exception as exc:
            logger.warning(lg.MCP_SERVER_TOOL_ERROR.format(name="test", error=exc))
        try:
            self._validator_agent = ValidatorAgent(
                agent_tools,
                system_prompt=self._orchestrator_prompt,
            )
        except Exception as exc:
            logger.warning(lg.MCP_SERVER_TOOL_ERROR.format(name="validator", error=exc))

    async def list_projects(self) -> ListProjectsResult:
        logger.info(lg.MCP_LISTING_PROJECTS)
        try:
            projects = self.ingestor.list_projects()
            return ListProjectsSuccessResult(projects=projects, count=len(projects))
        except Exception as e:
            logger.error(lg.MCP_ERROR_LIST_PROJECTS.format(error=e))
            return ListProjectsErrorResult(error=str(e), projects=[], count=0)

    async def select_active_project(
        self, repo_path: str | None = None
    ) -> dict[str, object]:
        try:
            if repo_path and repo_path.strip():
                self._set_project_root(repo_path)

            project_name = self._active_project_name()
            project_root = str(Path(self.project_root).resolve())

            try:
                indexed_projects = self.ingestor.list_projects()
            except Exception:
                indexed_projects = []

            active_indexed = project_name in indexed_projects

            module_count_result = self.ingestor.fetch_all(
                "MATCH (m:Module {project_name: $project_name}) RETURN count(m) AS count",
                {cs.KEY_PROJECT_NAME: project_name},
            )
            class_count_result = self.ingestor.fetch_all(
                "MATCH (m:Module {project_name: $project_name})-[:DEFINES]->(c:Class) RETURN count(c) AS count",
                {cs.KEY_PROJECT_NAME: project_name},
            )
            function_count_result = self.ingestor.fetch_all(
                "MATCH (m:Module {project_name: $project_name})-[:DEFINES|DEFINES_METHOD*0..1]->(f) "
                "WHERE f:Function OR f:Method RETURN count(DISTINCT f) AS count",
                {cs.KEY_PROJECT_NAME: project_name},
            )

            latest_report = self.ingestor.fetch_all(
                CYPHER_GET_LATEST_ANALYSIS_REPORT,
                {cs.KEY_PROJECT_NAME: project_name},
            )
            latest_analysis_timestamp = (
                latest_report[0].get("analysis_timestamp") if latest_report else None
            )

            return {
                "status": "ok",
                "active_project": {
                    "name": project_name,
                    "root": project_root,
                    "indexed": active_indexed,
                },
                "indexed_projects": {
                    "count": len(indexed_projects),
                    "names": indexed_projects,
                },
                "project_graph_stats": {
                    "modules": (
                        module_count_result[0]["count"] if module_count_result else 0
                    ),
                    "classes": (
                        class_count_result[0]["count"] if class_count_result else 0
                    ),
                    "functions_and_methods": (
                        function_count_result[0]["count"]
                        if function_count_result
                        else 0
                    ),
                },
                "latest_analysis_timestamp": latest_analysis_timestamp,
                "policy": {
                    "query_code_graph_scope_enforced": True,
                    "run_cypher_scope_enforced": True,
                    "run_cypher_write_requires_user_requested": True,
                    "run_cypher_write_requires_reason": True,
                    "run_cypher_write_allowlist_enforced": True,
                    "index_repository_requires_user_requested": True,
                    "index_repository_requires_reason": True,
                    "index_repository_drift_proof_enforced": True,
                    "completion_gate_refactor_batch_requires_plan": True,
                    "confidence_gate_enabled": True,
                    "pattern_reuse_gate_enabled": True,
                },
            }
        except Exception as exc:
            return {"error": str(exc)}

    async def detect_project_drift(
        self, repo_path: str | None = None
    ) -> dict[str, object]:
        try:
            resolved_root = (
                self._set_project_root(repo_path)
                if repo_path and repo_path.strip()
                else Path(self.project_root).resolve()
            )
            project_name = self._active_project_name()
            filesystem_files = sum(
                1
                for candidate in resolved_root.rglob("*")
                if candidate.is_file() and ".git" not in candidate.parts
            )

            module_count_result = self.ingestor.fetch_all(
                "MATCH (m:Module {project_name: $project_name}) RETURN count(m) AS count",
                {cs.KEY_PROJECT_NAME: project_name},
            )
            file_count_result = self.ingestor.fetch_all(
                "MATCH (f:File)-[:BELONGS_TO]->(:Project {name: $project_name}) RETURN count(f) AS count",
                {cs.KEY_PROJECT_NAME: project_name},
            )

            graph_modules = (
                int(module_count_result[0].get("count", 0))
                if module_count_result
                else 0
            )
            graph_files = (
                int(file_count_result[0].get("count", 0)) if file_count_result else 0
            )

            index_gap = abs(filesystem_files - max(graph_modules, graph_files))
            drift_detected = (
                filesystem_files > 0 and graph_modules == 0
            ) or index_gap > 0

            result = {
                "status": "ok",
                "project": project_name,
                "project_root": str(resolved_root),
                "filesystem": {"file_count": filesystem_files},
                "graph": {
                    "module_count": graph_modules,
                    "file_count": graph_files,
                },
                "drift": {
                    "drift_detected": drift_detected,
                    "delta_count": index_gap,
                    "reason": (
                        "filesystem_graph_mismatch"
                        if drift_detected
                        else "graph_consistent_with_filesystem"
                    ),
                },
            }
            self._record_policy_event(
                action="detect_project_drift",
                allowed=True,
                reason="drift_scan_completed",
                details={
                    "project": project_name,
                    "drift_detected": drift_detected,
                    "delta_count": index_gap,
                },
            )
            return result
        except Exception as exc:
            self._record_policy_event(
                action="detect_project_drift",
                allowed=False,
                reason=str(exc),
                details={"project_root": self.project_root},
            )
            return {"error": str(exc)}

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

    async def index_repository(
        self,
        repo_path: str,
        user_requested: bool,
        drift_confirmed: bool = False,
        reason: str | None = None,
    ) -> str:
        try:
            resolved_repo = self._set_project_root(repo_path)
            logger.info(lg.MCP_INDEXING_REPO.format(path=resolved_repo))
            project_name = resolved_repo.name

            existing_projects = self.ingestor.list_projects()
            if not isinstance(existing_projects, list):
                existing_projects = []
            project_already_indexed = project_name in existing_projects
            policy_result = self._policy_engine.validate_operation(
                tool_name=cs.MCPToolName.INDEX_REPOSITORY,
                params={
                    "user_requested": user_requested,
                    "drift_confirmed": drift_confirmed,
                    "reason": reason,
                },
                context={"project_already_indexed": project_already_indexed},
            )
            if not policy_result.allowed:
                self._record_policy_event(
                    action="index_repository",
                    allowed=False,
                    reason=str(policy_result.error),
                    details={"project": project_name},
                )
                return str(policy_result.error)

            if project_already_indexed and drift_confirmed:
                drift_result = await self.detect_project_drift(str(resolved_repo))
                drift_payload = cast(dict[str, object], drift_result.get("drift", {}))
                if not bool(drift_payload.get("drift_detected", False)):
                    message = cs.MCP_INDEX_DRIFT_NOT_PROVEN.format(
                        project_name=project_name
                    )
                    self._record_policy_event(
                        action="index_repository",
                        allowed=False,
                        reason=message,
                        details={"project": project_name},
                    )
                    return message

            logger.info(lg.MCP_CLEARING_PROJECT.format(project_name=project_name))

            async def _delete_project() -> None:
                await asyncio.to_thread(self.ingestor.delete_project, project_name)

            await self._run_with_retries(
                _delete_project,
                attempts=5,
                base_delay_seconds=0.5,
            )

            updater = GraphUpdater(
                ingestor=self.ingestor,
                repo_path=resolved_repo,
                parsers=self.parsers,
                queries=self.queries,
            )

            async def _run_updater() -> None:
                await asyncio.to_thread(updater.run)

            await self._run_with_retries(
                _run_updater,
                attempts=5,
                base_delay_seconds=0.5,
            )

            self._record_policy_event(
                action="index_repository",
                allowed=True,
                reason="index_completed",
                details={
                    "project": project_name,
                    "reason": reason.strip() if isinstance(reason, str) else "",
                },
            )

            return cs.MCP_INDEX_SUCCESS_PROJECT.format(
                path=resolved_repo, project_name=project_name
            )
        except Exception as e:
            logger.error(lg.MCP_ERROR_INDEXING.format(error=e))
            self._record_policy_event(
                action="index_repository",
                allowed=False,
                reason=str(e),
                details={"repo_path": repo_path},
            )
            return cs.MCP_INDEX_ERROR.format(error=e)

    async def sync_graph_updates(
        self,
        user_requested: bool,
        reason: str,
    ) -> dict[str, object]:
        policy_result = self._policy_engine.validate_operation(
            tool_name=cs.MCPToolName.SYNC_GRAPH_UPDATES,
            params={
                "user_requested": user_requested,
                "reason": reason,
            },
            context={},
        )
        if not policy_result.allowed:
            self._record_policy_event(
                action="sync_graph_updates",
                allowed=False,
                reason=str(policy_result.error),
                details={"project": self._active_project_name()},
            )
            self._record_tool_usefulness(
                cs.MCPToolName.SYNC_GRAPH_UPDATES,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": str(policy_result.error)}

        try:
            updater = GraphUpdater(
                ingestor=self.ingestor,
                repo_path=Path(self.project_root).resolve(),
                parsers=self.parsers,
                queries=self.queries,
            )

            async def _run_sync_once() -> None:
                await asyncio.wait_for(
                    asyncio.to_thread(updater.run),
                    timeout=max(60.0, float(settings.MCP_SYNC_GRAPH_TIMEOUT_SECONDS)),
                )

            await self._run_with_retries(
                _run_sync_once,
                attempts=2,
                base_delay_seconds=1.0,
            )

            config = updater.config
            self._record_policy_event(
                action="sync_graph_updates",
                allowed=True,
                reason="sync_completed",
                details={
                    "project": self._active_project_name(),
                    "git_delta_enabled": config.git_delta_enabled,
                    "selective_update_enabled": config.selective_update_enabled,
                },
            )
            self._record_tool_usefulness(
                cs.MCPToolName.SYNC_GRAPH_UPDATES,
                success=True,
                usefulness_score=1.0,
            )
            return {
                "status": "ok",
                "project": self._active_project_name(),
                "sync_mode": {
                    "git_delta_enabled": config.git_delta_enabled,
                    "selective_update_enabled": config.selective_update_enabled,
                    "incremental_cache_enabled": config.incremental_cache_enabled,
                    "analysis_enabled": config.analysis_enabled,
                },
                "reason": reason.strip(),
            }
        except TimeoutError:
            self._record_tool_usefulness(
                cs.MCPToolName.SYNC_GRAPH_UPDATES,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": "sync_graph_updates_timed_out_after_900s"}
        except Exception as exc:
            self._record_tool_usefulness(
                cs.MCPToolName.SYNC_GRAPH_UPDATES,
                success=False,
                usefulness_score=0.0,
            )
            self._record_policy_event(
                action="sync_graph_updates",
                allowed=False,
                reason=str(exc),
                details={"project": self._active_project_name()},
            )
            return {"error": str(exc)}

    async def _auto_execute_next_best_action(
        self,
        next_best_action: dict[str, object],
    ) -> dict[str, object]:
        tool_name = str(next_best_action.get("tool", "")).strip()
        params_hint = next_best_action.get("params_hint", {})
        if not isinstance(params_hint, dict):
            params_hint = {}
        params_hint_dict = cast(dict[str, object], params_hint)

        if tool_name == cs.MCPToolName.SEMANTIC_SEARCH:
            query = str(params_hint_dict.get("query", "")).strip()
            if not query:
                return {"executed": False, "reason": "missing_query"}
            result = await self.semantic_search(
                query=query,
                top_k=self._coerce_int(params_hint_dict.get("top_k", 5), default=5),
            )
            return {"executed": True, "tool": tool_name, "result": result}

        if tool_name == cs.MCPToolName.QUERY_CODE_GRAPH:
            nl_query = str(params_hint_dict.get("natural_language_query", "")).strip()
            if not nl_query:
                return {"executed": False, "reason": "missing_natural_language_query"}
            result = await self.query_code_graph(natural_language_query=nl_query)
            return {"executed": True, "tool": tool_name, "result": result}

        if tool_name == cs.MCPToolName.IMPACT_GRAPH:
            qualified_name = params_hint_dict.get("qualified_name")
            file_path = params_hint_dict.get("file_path")
            if not isinstance(qualified_name, str):
                qualified_name = None
            if not isinstance(file_path, str):
                file_path = None
            result = await self.impact_graph(
                qualified_name=qualified_name,
                file_path=file_path,
                depth=self._coerce_int(params_hint_dict.get("depth", 3), default=3),
            )
            return {"executed": True, "tool": tool_name, "result": result}

        if tool_name == cs.MCPToolName.PLAN_TASK:
            goal = str(params_hint_dict.get("goal", "")).strip()
            if not goal:
                return {"executed": False, "reason": "missing_goal"}
            context = params_hint_dict.get("context")
            if not isinstance(context, str):
                context = None
            result = await self.plan_task(goal=goal, context=context)
            return {"executed": True, "tool": tool_name, "result": result}

        if tool_name == cs.MCPToolName.TEST_QUALITY_GATE:
            result = await self.test_quality_gate(
                coverage=str(params_hint_dict.get("coverage", "0")),
                edge_cases=str(params_hint_dict.get("edge_cases", "0")),
                negative_tests=str(params_hint_dict.get("negative_tests", "0")),
            )
            return {"executed": True, "tool": tool_name, "result": result}

        if tool_name == cs.MCPToolName.MEMORY_ADD:
            entry = str(params_hint_dict.get("entry", "")).strip()
            if not entry:
                return {"executed": False, "reason": "missing_entry"}
            tags = params_hint_dict.get("tags")
            if not isinstance(tags, str):
                tags = None
            result = await self.memory_add(entry=entry, tags=tags)
            return {"executed": True, "tool": tool_name, "result": result}

        return {
            "executed": False,
            "reason": "tool_not_supported_for_auto_execution",
            "tool": tool_name,
        }

    def _orchestrate_circuit_state(self) -> dict[str, object]:
        raw_state = self._session_state.get("orchestrate_circuit")
        if not isinstance(raw_state, dict):
            raw_state = {
                "state": "closed",
                "failure_count": 0,
                "failure_threshold": 3,
                "opened_at": 0.0,
                "cooldown_seconds": 10.0,
            }
            self._session_state["orchestrate_circuit"] = raw_state
        return cast(dict[str, object], raw_state)

    def _orchestrate_circuit_open(self, now: float) -> tuple[bool, float]:
        state = self._orchestrate_circuit_state()
        if str(state.get("state", "closed")) != "open":
            return False, 0.0

        opened_at = self._coerce_float(state.get("opened_at", 0.0))
        cooldown_seconds = self._coerce_float(
            state.get("cooldown_seconds", 10.0),
            default=10.0,
        )
        elapsed = now - opened_at
        if elapsed >= cooldown_seconds:
            state["state"] = "half_open"
            state["failure_count"] = 0
            return False, 0.0
        return True, max(0.0, cooldown_seconds - elapsed)

    def _record_orchestrate_failure(self, stage: str, error: str) -> dict[str, object]:
        state = self._orchestrate_circuit_state()
        failure_count = self._coerce_int(state.get("failure_count", 0)) + 1
        failure_threshold = max(
            1,
            self._coerce_int(state.get("failure_threshold", 3), default=3),
        )
        state["failure_count"] = failure_count
        state["last_failure_stage"] = stage
        state["last_failure_error"] = error

        current_state = str(state.get("state", "closed"))
        if current_state == "half_open" or failure_count >= failure_threshold:
            state["state"] = "open"
            state["opened_at"] = time.time()

        return {
            "state": str(state.get("state", "closed")),
            "failure_count": self._coerce_int(state.get("failure_count", 0)),
            "failure_threshold": failure_threshold,
            "last_failure_stage": str(state.get("last_failure_stage", "")),
            "last_failure_error": str(state.get("last_failure_error", "")),
            "cooldown_seconds": self._coerce_float(
                state.get("cooldown_seconds", 10.0),
                default=10.0,
            ),
        }

    def _record_orchestrate_success(self) -> dict[str, object]:
        state = self._orchestrate_circuit_state()
        state["state"] = "closed"
        state["failure_count"] = 0
        state["opened_at"] = 0.0
        state["last_failure_stage"] = ""
        state["last_failure_error"] = ""
        return {
            "state": "closed",
            "failure_count": 0,
            "failure_threshold": self._coerce_int(
                state.get("failure_threshold", 3),
                default=3,
            ),
            "last_failure_stage": "",
            "last_failure_error": "",
            "cooldown_seconds": self._coerce_float(
                state.get("cooldown_seconds", 10.0),
                default=10.0,
            ),
        }

    async def _run_orchestrate_stage_with_retry(
        self,
        stage: str,
        operation: Any,
        attempts: int = 3,
        base_delay_seconds: float = 0.05,
    ) -> dict[str, object]:
        last_error = "unknown_error"
        for attempt in range(1, max(1, attempts) + 1):
            try:
                result = await operation()
            except Exception as exc:
                last_error = str(exc)
                if attempt >= attempts:
                    return {
                        "ok": False,
                        "attempts": attempt,
                        "error": last_error,
                    }
                await asyncio.sleep(base_delay_seconds * attempt)
                continue

            if isinstance(result, dict) and "error" in result:
                last_error = str(result.get("error", "unknown_error"))
                if attempt >= attempts:
                    return {
                        "ok": False,
                        "attempts": attempt,
                        "error": last_error,
                        "result": result,
                    }
                await asyncio.sleep(base_delay_seconds * attempt)
                continue

            return {
                "ok": True,
                "attempts": attempt,
                "result": result,
            }

        return {
            "ok": False,
            "attempts": attempts,
            "error": f"{stage}_failed",
        }

    async def orchestrate_realtime_flow(
        self,
        action: str,
        result: str,
        user_requested: bool,
        sync_reason: str,
        issues: str | None = None,
        goal: str | None = None,
        context: str | None = None,
        auto_execute_next: bool | None = None,
        verify_drift: bool | None = None,
        debounce_seconds: int | None = None,
    ) -> dict[str, object]:
        now = time.time()
        circuit_open, retry_after = self._orchestrate_circuit_open(now)
        if circuit_open:
            self._record_tool_usefulness(
                cs.MCPToolName.ORCHESTRATE_REALTIME_FLOW,
                success=False,
                usefulness_score=0.0,
            )
            return {
                "status": "error",
                "stage": "circuit_breaker_open",
                "error": "orchestrate_realtime_flow_temporarily_blocked",
                "retry_after_seconds": round(retry_after, 3),
                "circuit_breaker": self._orchestrate_circuit_state(),
            }

        auto_execute_next_effective = (
            bool(auto_execute_next)
            if auto_execute_next is not None
            else bool(settings.MCP_ORCHESTRATE_AUTO_EXECUTE_NEXT_DEFAULT)
        )
        verify_drift_effective = (
            bool(verify_drift)
            if verify_drift is not None
            else bool(settings.MCP_ORCHESTRATE_VERIFY_DRIFT_DEFAULT)
        )

        debounce_input = debounce_seconds
        if debounce_input is None:
            debounce_input = int(settings.MCP_ORCHESTRATE_DEBOUNCE_DEFAULT_SECONDS)
        bounded_debounce = max(0, min(int(debounce_input), 5))
        if bounded_debounce > 0:
            await asyncio.sleep(bounded_debounce)

        feedback_result = await self.execution_feedback(
            action=action,
            result=result,
            issues=issues,
        )

        sync_stage = await self._run_orchestrate_stage_with_retry(
            stage="sync_graph_updates",
            operation=lambda: self.sync_graph_updates(
                user_requested=user_requested,
                reason=sync_reason,
            ),
            attempts=max(1, int(settings.MCP_ORCHESTRATE_SYNC_RETRY_ATTEMPTS)),
            base_delay_seconds=max(
                0.01, float(settings.MCP_ORCHESTRATE_RETRY_BASE_DELAY_SECONDS)
            ),
        )
        if not bool(sync_stage.get("ok", False)):
            error_text = str(sync_stage.get("error", "sync_graph_updates_failed"))
            circuit_snapshot = self._record_orchestrate_failure(
                stage="sync_graph_updates",
                error=error_text,
            )
            self._record_tool_usefulness(
                cs.MCPToolName.ORCHESTRATE_REALTIME_FLOW,
                success=False,
                usefulness_score=0.0,
            )
            sync_result = sync_stage.get("result")
            if not isinstance(sync_result, dict):
                sync_result = {"error": error_text}
            return {
                "status": "error",
                "stage": "sync_graph_updates",
                "error": error_text,
                "attempts": self._coerce_int(sync_stage.get("attempts", 0)),
                "feedback": feedback_result,
                "sync": sync_result,
                "circuit_breaker": circuit_snapshot,
            }
        sync_result = sync_stage.get("result")
        if not isinstance(sync_result, dict):
            sync_result = {"status": "ok"}

        drift_result: dict[str, object] | None = None
        if verify_drift_effective:
            drift_stage = await self._run_orchestrate_stage_with_retry(
                stage="detect_project_drift",
                operation=lambda: self.detect_project_drift(),
                attempts=2,
                base_delay_seconds=max(
                    0.01, float(settings.MCP_ORCHESTRATE_RETRY_BASE_DELAY_SECONDS)
                ),
            )
            if bool(drift_stage.get("ok", False)):
                drift_payload = drift_stage.get("result")
                if isinstance(drift_payload, dict):
                    drift_result = cast(dict[str, object], drift_payload)
            else:
                drift_result = {
                    "error": str(
                        drift_stage.get("error", "detect_project_drift_failed")
                    ),
                    "attempts": self._coerce_int(drift_stage.get("attempts", 0)),
                }

        done_stage = await self._run_orchestrate_stage_with_retry(
            stage="validate_done_decision",
            operation=lambda: self.validate_done_decision(goal=goal, context=context),
            attempts=max(1, int(settings.MCP_ORCHESTRATE_VALIDATE_RETRY_ATTEMPTS)),
            base_delay_seconds=max(
                0.01, float(settings.MCP_ORCHESTRATE_RETRY_BASE_DELAY_SECONDS)
            ),
        )
        if not bool(done_stage.get("ok", False)):
            error_text = str(done_stage.get("error", "validate_done_decision_failed"))
            circuit_snapshot = self._record_orchestrate_failure(
                stage="validate_done_decision",
                error=error_text,
            )
            self._record_tool_usefulness(
                cs.MCPToolName.ORCHESTRATE_REALTIME_FLOW,
                success=False,
                usefulness_score=0.0,
            )
            return {
                "status": "error",
                "stage": "validate_done_decision",
                "error": error_text,
                "attempts": self._coerce_int(done_stage.get("attempts", 0)),
                "feedback": feedback_result,
                "sync": sync_result,
                "drift": drift_result,
                "circuit_breaker": circuit_snapshot,
            }
        done_result = done_stage.get("result")
        if not isinstance(done_result, dict):
            done_result = {"status": "ok", "decision": "not_done"}

        auto_next_result: dict[str, object] | None = None
        if auto_execute_next_effective and isinstance(done_result, dict):
            done_result_dict = cast(dict[str, object], done_result)
            raw_next_best_action = done_result_dict.get("next_best_action", {})
            if isinstance(raw_next_best_action, dict):
                next_best_action = cast(dict[str, object], raw_next_best_action)
                auto_stage = await self._run_orchestrate_stage_with_retry(
                    stage="auto_execute_next_best_action",
                    operation=lambda: self._auto_execute_next_best_action(
                        next_best_action
                    ),
                    attempts=max(
                        1, int(settings.MCP_ORCHESTRATE_AUTO_NEXT_RETRY_ATTEMPTS)
                    ),
                    base_delay_seconds=max(
                        0.01, float(settings.MCP_ORCHESTRATE_RETRY_BASE_DELAY_SECONDS)
                    ),
                )
                if bool(auto_stage.get("ok", False)):
                    auto_payload = auto_stage.get("result")
                    if isinstance(auto_payload, dict):
                        auto_next_result = cast(dict[str, object], auto_payload)
                    else:
                        auto_next_result = {
                            "executed": False,
                            "reason": "auto_execution_invalid_payload",
                        }
                else:
                    auto_next_result = {
                        "executed": False,
                        "reason": str(auto_stage.get("error", "auto_execution_failed")),
                        "attempts": self._coerce_int(auto_stage.get("attempts", 0)),
                    }

        circuit_snapshot = self._record_orchestrate_success()
        self._record_tool_usefulness(
            cs.MCPToolName.ORCHESTRATE_REALTIME_FLOW,
            success=True,
            usefulness_score=1.0,
        )
        done_ui_summary = ""
        if isinstance(done_result, dict):
            done_result_dict = cast(dict[str, object], done_result)
            done_ui_summary = str(done_result_dict.get("ui_summary", "")).strip()
        top_ui_summary = done_ui_summary or "Realtime flow executed"
        return {
            "status": "ok",
            "ui_summary": top_ui_summary,
            "flow": [
                "execution_feedback",
                "sync_graph_updates",
                (
                    "detect_project_drift"
                    if verify_drift_effective
                    else "skip_detect_project_drift"
                ),
                "validate_done_decision",
                (
                    "auto_execute_next_best_action"
                    if auto_execute_next_effective
                    else "skip_auto_execute_next_best_action"
                ),
            ],
            "debounce_seconds": bounded_debounce,
            "feedback": feedback_result,
            "sync": sync_result,
            "drift": drift_result,
            "done": done_result,
            "auto_next": auto_next_result,
            "circuit_breaker": circuit_snapshot,
        }

    def _active_project_name(self) -> str:
        return Path(self.project_root).resolve().name

    def _validate_project_scope_policy(
        self, cypher_query: str, parsed_params: dict[str, object] | None = None
    ) -> str | None:
        return self._policy_engine.validate_project_scope_policy(
            cypher_query, parsed_params
        )

    def _validate_write_allowlist_policy(self, cypher_query: str) -> str | None:
        return self._policy_engine.validate_write_allowlist_policy(cypher_query)

    @staticmethod
    def _build_scoped_query_prompt(
        natural_language_query: str,
        project_name: str,
        previous_cypher: str | None = None,
    ) -> str:
        prompt = (
            f"{natural_language_query}\n\n"
            "STRICT PROJECT SCOPE REQUIREMENT:\n"
            f"- Active project: '{project_name}'.\n"
            "- Generated Cypher MUST explicitly include project scoping.\n"
            "- Use one of these forms:\n"
            f"  1) MATCH (p:Project {{name: '{project_name}'}}) ...\n"
            f"  2) MATCH (m:Module {{project_name: '{project_name}'}}) ...\n"
            "- Never generate a cross-project query.\n"
            "- Return only Cypher query text."
        )
        if previous_cypher:
            prompt += (
                "\n\nPREVIOUS QUERY WAS REJECTED (UNSCOPED):\n"
                f"{previous_cypher}\n"
                "Regenerate with explicit project scope."
            )
        return prompt

    async def _generate_project_scoped_cypher(
        self, natural_language_query: str, project_name: str
    ) -> str:
        last_query = ""
        for _ in range(3):
            scoped_prompt = self._build_scoped_query_prompt(
                natural_language_query=natural_language_query,
                project_name=project_name,
                previous_cypher=last_query if last_query else None,
            )
            generated_query = await self.cypher_gen.generate(scoped_prompt)
            last_query = generated_query
            scope_error = self._validate_project_scope_policy(generated_query, {})
            if scope_error is None:
                return generated_query
        raise ValueError(cs.MCP_QUERY_SCOPE_ERROR.format(project_name=project_name))

    async def query_code_graph(
        self, natural_language_query: str, output_format: str = "json"
    ) -> QueryResultDict | str:
        logger.info(lg.MCP_QUERY_CODE_GRAPH.format(query=natural_language_query))
        try:
            project_name = self._active_project_name()
            cypher_query = await self._generate_project_scoped_cypher(
                natural_language_query=natural_language_query,
                project_name=project_name,
            )

            async def _read_once() -> list[dict[str, Any]]:
                return await asyncio.wait_for(
                    asyncio.to_thread(self.ingestor.fetch_all, cypher_query),
                    timeout=60.0,
                )

            results = await self._run_with_retries(
                _read_once,
                attempts=3,
                base_delay_seconds=0.5,
            )
            self._session_bump("query_success_count")
            self._session_bump("graph_evidence_count")
            usefulness_score = 1.0 if len(results) > 0 else 0.5
            self._record_tool_usefulness(
                cs.MCPToolName.QUERY_CODE_GRAPH,
                success=True,
                usefulness_score=usefulness_score,
            )
            result_dict: QueryResultDict = QueryResultDict(
                query_used=cypher_query,
                results=results,
                summary=f"Query executed successfully. Returned {len(results)} rows.",
            )
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
            self._record_tool_usefulness(
                cs.MCPToolName.QUERY_CODE_GRAPH,
                success=False,
                usefulness_score=0.0,
            )
            return QueryResultDict(
                error=str(e),
                query_used=cs.QUERY_NOT_AVAILABLE,
                results=[],
                summary=cs.MCP_TOOL_EXEC_ERROR.format(
                    name=cs.MCPToolName.QUERY_CODE_GRAPH, error=e
                ),
            )

    async def semantic_search(self, query: str, top_k: int = 5) -> dict[str, object]:
        if not query.strip():
            self._record_tool_usefulness(
                cs.MCPToolName.SEMANTIC_SEARCH,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": te.MCP_INVALID_RESPONSE, "results": []}
        bounded_top_k = min(max(1, int(top_k)), 50)
        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(semantic_code_search, query, bounded_top_k),
                timeout=60.0,
            )
            if results:
                self._session_bump("semantic_success_count")
                score_values = [
                    float(item.get("score", 0.0))
                    for item in results
                    if isinstance(item, dict)
                ]
                if score_values:
                    self._session_state["semantic_similarity_mean"] = sum(
                        score_values
                    ) / len(score_values)
            score = 1.0 if results else 0.4
            self._record_tool_usefulness(
                cs.MCPToolName.SEMANTIC_SEARCH,
                success=True,
                usefulness_score=score,
            )
            return {"count": len(results), "results": results}
        except TimeoutError:
            self._record_tool_usefulness(
                cs.MCPToolName.SEMANTIC_SEARCH,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": "semantic_search_timed_out_after_60s", "results": []}
        except Exception as exc:
            self._record_tool_usefulness(
                cs.MCPToolName.SEMANTIC_SEARCH,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": str(exc), "results": []}

    async def get_function_source(self, node_id: int) -> dict[str, object]:
        try:
            source = await asyncio.wait_for(
                asyncio.to_thread(get_function_source_code, int(node_id)),
                timeout=60.0,
            )
            if source is None:
                self._record_tool_usefulness(
                    cs.MCPToolName.GET_FUNCTION_SOURCE,
                    success=False,
                    usefulness_score=0.0,
                )
                return {"error": "source_not_found", "node_id": int(node_id)}
            self._session_bump("code_evidence_count")
            self._record_tool_usefulness(
                cs.MCPToolName.GET_FUNCTION_SOURCE,
                success=True,
                usefulness_score=1.0,
            )
            return {
                "status": "ok",
                "node_id": int(node_id),
                "source_code": source,
            }
        except TimeoutError:
            self._record_tool_usefulness(
                cs.MCPToolName.GET_FUNCTION_SOURCE,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": "get_function_source_timed_out_after_60s"}
        except Exception as exc:
            self._record_tool_usefulness(
                cs.MCPToolName.GET_FUNCTION_SOURCE,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": str(exc)}

    async def get_code_snippet(self, qualified_name: str) -> CodeSnippetResultDict:
        logger.info(lg.MCP_GET_CODE_SNIPPET.format(name=qualified_name))
        try:
            snippet = await self._code_tool.function(qualified_name=qualified_name)
            result: CodeSnippetResultDict | None = snippet.model_dump()
            if result is None:
                self._record_tool_usefulness(
                    cs.MCPToolName.GET_CODE_SNIPPET,
                    success=False,
                    usefulness_score=0.0,
                )
                return CodeSnippetResultDict(
                    error=te.MCP_TOOL_RETURNED_NONE,
                    found=False,
                    error_message=te.MCP_INVALID_RESPONSE,
                )
            if result.get("found"):
                self._session_bump("evidence_reads")
                self._session_bump("code_evidence_count")
            self._record_tool_usefulness(
                cs.MCPToolName.GET_CODE_SNIPPET,
                success=bool(result.get("found", False)),
                usefulness_score=1.0 if bool(result.get("found", False)) else 0.3,
            )
            return result
        except Exception as e:
            logger.error(lg.MCP_ERROR_CODE_SNIPPET.format(error=e))
            self._record_tool_usefulness(
                cs.MCPToolName.GET_CODE_SNIPPET,
                success=False,
                usefulness_score=0.0,
            )
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
            self._session_bump("edit_success_count")
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
                    self._session_bump("evidence_reads")
                    self._session_bump("code_evidence_count")
                    self._record_tool_usefulness(
                        cs.MCPToolName.READ_FILE,
                        success=True,
                        usefulness_score=1.0,
                    )
                    return header + paginated_content
            else:
                result = await self._file_reader_tool.function(file_path=file_path)
                self._session_bump("evidence_reads")
                self._session_bump("code_evidence_count")
                self._record_tool_usefulness(
                    cs.MCPToolName.READ_FILE,
                    success=True,
                    usefulness_score=1.0,
                )
                return str(result)

        except Exception as e:
            logger.error(lg.MCP_ERROR_READ.format(error=e))
            self._record_tool_usefulness(
                cs.MCPToolName.READ_FILE,
                success=False,
                usefulness_score=0.0,
            )
            return te.ERROR_WRAPPER.format(message=e)

    async def write_file(self, file_path: str, content: str) -> str:
        logger.info(lg.MCP_WRITE_FILE.format(path=file_path))
        try:
            result = await self._file_writer_tool.function(
                file_path=file_path, content=content
            )
            if result.success:
                self._session_bump("edit_success_count")
                return cs.MCP_WRITE_SUCCESS.format(path=file_path)
            return te.ERROR_WRAPPER.format(message=result.error_message)
        except Exception as e:
            logger.error(lg.MCP_ERROR_WRITE.format(error=e))
            return te.ERROR_WRAPPER.format(message=e)

    async def list_directory(
        self, repo_path: str, directory_path: str = cs.MCP_DEFAULT_DIRECTORY
    ) -> str:
        try:
            self._set_project_root(repo_path)
            logger.info(lg.MCP_LIST_DIR.format(path=f"{repo_path}:{directory_path}"))
            result = self._directory_lister_tool.function(directory_path=directory_path)
            self._session_bump("evidence_reads")
            self._record_tool_usefulness(
                cs.MCPToolName.LIST_DIRECTORY,
                success=True,
                usefulness_score=0.9,
            )
            return str(result)
        except Exception as e:
            logger.error(lg.MCP_ERROR_LIST_DIR.format(error=e))
            self._record_tool_usefulness(
                cs.MCPToolName.LIST_DIRECTORY,
                success=False,
                usefulness_score=0.0,
            )
            return te.ERROR_WRAPPER.format(message=e)

    def _validate_run_cypher_policy(
        self,
        cypher: str,
        parsed_params: dict[str, object],
        write: bool,
        user_requested: bool,
        reason: str | None,
        write_impact: int | None,
        risk_factor: float,
    ) -> str | None:
        policy_result = self._policy_engine.validate_operation(
            tool_name=cs.MCPToolName.RUN_CYPHER,
            params={
                "cypher": cypher,
                "parsed_params": parsed_params,
                "write": write,
                "user_requested": user_requested,
                "reason": reason,
            },
            context={"write_impact": write_impact, "risk_factor": risk_factor},
        )
        if not policy_result.allowed:
            self._record_policy_event(
                action="run_cypher",
                allowed=False,
                reason=str(policy_result.error),
                details={"write": write},
            )
            return str(policy_result.error)

        self._record_policy_event(
            action="run_cypher",
            allowed=True,
            reason="policy_validated",
            details={"write": write, "write_impact": write_impact},
        )
        return None

    async def run_cypher(
        self,
        cypher: str,
        params: str | None = None,
        write: bool = False,
        user_requested: bool = False,
        reason: str | None = None,
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

        write_impact: int | None = None
        risk_factor = 1.0
        if write:
            write_impact = await self._estimate_write_impact(cypher, parsed_params)
            risk_factor = await self._compute_project_risk_factor()

        policy_error = self._validate_run_cypher_policy(
            cypher=cypher,
            parsed_params=parsed_params,
            write=write,
            user_requested=user_requested,
            reason=reason,
            write_impact=write_impact,
            risk_factor=risk_factor,
        )
        if policy_error is not None:
            self._record_tool_usefulness(
                cs.MCPToolName.RUN_CYPHER,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": policy_error, "results": []}

        try:
            if write:

                async def _write_once() -> None:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            self.ingestor.execute_write,
                            cypher,
                            cast(dict[str, Any], parsed_params),
                        ),
                        timeout=60.0,
                    )

                await self._run_with_retries(
                    _write_once,
                    attempts=3,
                    base_delay_seconds=0.5,
                )
                self._session_bump("edit_success_count")
                self._record_tool_usefulness(
                    cs.MCPToolName.RUN_CYPHER,
                    success=True,
                    usefulness_score=0.9,
                )
                return {"status": "ok", "results": []}

            async def _read_once() -> list[dict[str, Any]]:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        self.ingestor.fetch_all,
                        cypher,
                        cast(dict[str, Any], parsed_params),
                    ),
                    timeout=60.0,
                )

            results = await self._run_with_retries(
                _read_once,
                attempts=3,
                base_delay_seconds=0.5,
            )
            self._session_bump("query_success_count")
            self._session_bump("graph_evidence_count")
            self._record_tool_usefulness(
                cs.MCPToolName.RUN_CYPHER,
                success=True,
                usefulness_score=1.0 if len(results) > 0 else 0.5,
            )
            return {"status": "ok", "results": results}
        except TimeoutError:
            self._record_tool_usefulness(
                cs.MCPToolName.RUN_CYPHER,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": "cypher_query_timed_out_after_60s", "results": []}
        except Exception as exc:
            self._record_tool_usefulness(
                cs.MCPToolName.RUN_CYPHER,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": str(exc), "results": []}

    async def _estimate_write_impact(
        self, cypher_query: str, parsed_params: dict[str, object]
    ) -> int | None:
        dry_run_query = self._policy_engine.estimate_write_impact_query(cypher_query)
        if dry_run_query is None:
            return None

        async def _dry_run_once() -> list[dict[str, Any]]:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self.ingestor.fetch_all,
                    dry_run_query,
                    cast(dict[str, Any], parsed_params),
                ),
                timeout=30.0,
            )

        try:
            rows = await self._run_with_retries(
                _dry_run_once,
                attempts=2,
                base_delay_seconds=0.3,
            )
        except Exception:
            return None

        if not isinstance(rows, list) or not rows:
            return 0

        first_row = rows[0]
        if isinstance(first_row, dict):
            affected = first_row.get("affected")
            if isinstance(affected, int | float):
                return int(affected)
        return len(rows)

    async def _compute_project_risk_factor(self) -> float:
        project_name = self._active_project_name()
        query = (
            "MATCH (m:Module {project_name: $project_name}) "
            "RETURN count(m) AS module_count"
        )
        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(
                    self.ingestor.fetch_all,
                    query,
                    {cs.KEY_PROJECT_NAME: project_name},
                ),
                timeout=10.0,
            )
        except Exception:
            return 0.7
        if not isinstance(rows, list) or not rows:
            return 0.7

        module_count = 0
        first_row = rows[0]
        if isinstance(first_row, dict):
            raw_module_count = first_row.get("module_count")
            if isinstance(raw_module_count, int | float):
                module_count = int(raw_module_count)
        if module_count >= 100:
            return 0.85
        return 1.0

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

            async def _run_all_once() -> None:
                await asyncio.wait_for(asyncio.to_thread(runner.run_all), timeout=300.0)

            await self._run_with_retries(
                _run_all_once,
                attempts=3,
                base_delay_seconds=1.0,
            )
            return {"status": "ok"}
        except TimeoutError:
            return {"error": "analysis_timed_out_after_300s"}
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

            async def _run_subset_once() -> None:
                await asyncio.wait_for(
                    asyncio.to_thread(runner.run_modules, module_set), timeout=180.0
                )

            await self._run_with_retries(
                _run_subset_once,
                attempts=3,
                base_delay_seconds=1.0,
            )
            return {"status": "ok", "modules": sorted(module_set)}
        except TimeoutError:
            return {"error": "analysis_timed_out_after_180s"}
        except Exception as exc:
            return {"error": str(exc)}

    async def security_scan(self) -> dict[str, object]:
        modules = {"security", "secret_scan", "sast_taint_tracking"}
        try:
            runner = AnalysisRunner(self.ingestor, Path(self.project_root))

            async def _run_security_once() -> None:
                await asyncio.wait_for(
                    asyncio.to_thread(runner.run_modules, modules), timeout=180.0
                )

            await self._run_with_retries(
                _run_security_once,
                attempts=3,
                base_delay_seconds=1.0,
            )
        except TimeoutError:
            return {"error": "security_scan_timed_out_after_180s"}
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

            async def _run_hotspots_once() -> None:
                await asyncio.wait_for(
                    asyncio.to_thread(runner.run_modules, {"performance_hotspots"}),
                    timeout=120.0,
                )

            await self._run_with_retries(
                _run_hotspots_once,
                attempts=3,
                base_delay_seconds=1.0,
            )
            return {"status": "ok"}
        except TimeoutError:
            return {"error": "performance_hotspots_timed_out_after_120s"}
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
        normalized_name = artifact_name.strip()
        if not normalized_name:
            return {"error": te.MCP_INVALID_RESPONSE}

        analysis_dir = (Path(self.project_root) / "output" / "analysis").resolve()
        if not analysis_dir.exists() or not analysis_dir.is_dir():
            return {"error": "analysis_output_not_found"}

        request_path = Path(normalized_name)
        if request_path.is_absolute() or any(
            part in {"..", "."} for part in request_path.parts
        ):
            return {"error": "artifact_not_allowed"}

        candidate_paths: list[Path] = []
        if request_path.suffix:
            candidate_paths.append((analysis_dir / request_path).resolve())
        else:
            for suffix in (".json", ".md", ".log"):
                candidate_paths.append(
                    (analysis_dir / f"{normalized_name}{suffix}").resolve()
                )

        report_path = next(
            (
                candidate
                for candidate in candidate_paths
                if candidate.parent == analysis_dir
                and candidate.exists()
                and candidate.is_file()
            ),
            None,
        )
        if report_path is None:
            available = sorted(
                file.name for file in analysis_dir.glob("*") if file.is_file()
            )
            return {"error": "artifact_not_found", "available_artifacts": available}
        try:
            content = report_path.read_text(encoding=cs.ENCODING_UTF8)
        except Exception as exc:
            return {"error": str(exc)}
        return {
            "artifact": report_path.stem,
            "filename": report_path.name,
            "content": content,
        }

    async def list_analysis_artifacts(self) -> dict[str, object]:
        analysis_dir = (Path(self.project_root) / "output" / "analysis").resolve()
        if not analysis_dir.exists() or not analysis_dir.is_dir():
            return {"count": 0, "artifacts": []}

        artifacts: list[dict[str, object]] = []
        for file_path in sorted(
            (entry for entry in analysis_dir.glob("*") if entry.is_file()),
            key=lambda item: item.name,
        ):
            stat = file_path.stat()
            artifacts.append(
                {
                    "name": file_path.name,
                    "stem": file_path.stem,
                    "extension": file_path.suffix,
                    "size_bytes": stat.st_size,
                    "modified_at": int(stat.st_mtime),
                }
            )

        return {"count": len(artifacts), "artifacts": artifacts}

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
            self._record_tool_usefulness(
                cs.MCPToolName.REFACTOR_BATCH,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": "invalid_chunks_json"}
        if not isinstance(payload, list) or not payload:
            self._record_tool_usefulness(
                cs.MCPToolName.REFACTOR_BATCH,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": "chunks_must_be_list"}

        memory_query = "refactor pattern " + " ".join(
            str(item.get("file_path", "")) for item in payload if isinstance(item, dict)
        )
        await self.memory_query_patterns(
            query=memory_query.strip(),
            filter_tags="refactor",
            success_only=True,
            limit=5,
        )

        pattern_score = await self._compute_pattern_reuse_score(payload)
        self._session_state["pattern_reuse_score"] = pattern_score
        readiness = self._compute_execution_readiness()
        policy_result = self._policy_engine.validate_operation(
            tool_name=cs.MCPToolName.REFACTOR_BATCH,
            params={},
            context={"readiness": readiness},
        )
        if not policy_result.allowed:
            self._record_policy_event(
                action="refactor_batch",
                allowed=False,
                reason=str(policy_result.error),
                details={"readiness": readiness},
            )
            self._record_tool_usefulness(
                cs.MCPToolName.REFACTOR_BATCH,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": str(policy_result.error)}

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
        self._record_policy_event(
            action="refactor_batch",
            allowed=True,
            reason="refactor_batch_completed",
            details={"files": len(results)},
        )
        self._record_tool_usefulness(
            cs.MCPToolName.REFACTOR_BATCH,
            success=True,
            usefulness_score=1.0,
        )
        return {"status": "ok", "results": results}

    async def test_generate(
        self, goal: str, context: str | None = None
    ) -> dict[str, object]:
        prompt = goal if context is None else f"{goal}\nContext: {context}"
        try:
            result = await asyncio.wait_for(
                self._test_agent.run(prompt),
                timeout=max(30.0, float(settings.MCP_AGENT_TIMEOUT_SECONDS)),
            )
            self._session_state["test_generate_completed"] = True
            self._record_tool_usefulness(
                cs.MCPToolName.TEST_GENERATE,
                success=True,
                usefulness_score=1.0 if str(result.content).strip() else 0.4,
            )
            return {"status": result.status, "content": result.content}
        except TimeoutError:
            self._record_tool_usefulness(
                cs.MCPToolName.TEST_GENERATE,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": "test_generate_timed_out_after_300s"}
        except Exception as exc:
            self._record_tool_usefulness(
                cs.MCPToolName.TEST_GENERATE,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": str(exc)}

    async def memory_add(
        self, entry: str, tags: str | None = None
    ) -> dict[str, object]:
        parsed_tags: list[str] = []
        if tags:
            parsed_tags = [item.strip() for item in tags.split(",") if item.strip()]
        record = self._memory_store.add_entry(entry, parsed_tags)
        self._session_bump("manual_memory_add_count")
        return {"status": "ok", "entry": record["text"], "tags": record["tags"]}

    async def memory_list(self, limit: int = 50) -> dict[str, object]:
        entries = self._memory_store.list_entries(limit=limit)
        return {"count": len(entries), "entries": entries}

    async def memory_query_patterns(
        self,
        query: str,
        filter_tags: str | None = None,
        success_only: bool = False,
        limit: int = 20,
    ) -> dict[str, object]:
        tag_filters = (
            [item.strip() for item in filter_tags.split(",") if item.strip()]
            if isinstance(filter_tags, str)
            else []
        )
        entries = self._memory_store.query_patterns(
            query=query,
            filter_tags=tag_filters,
            success_only=bool(success_only),
            limit=max(1, min(int(limit), 100)),
        )
        self._session_bump("memory_pattern_query_count")
        self._record_tool_usefulness(
            cs.MCPToolName.MEMORY_QUERY_PATTERNS,
            success=True,
            usefulness_score=1.0 if entries else 0.4,
        )
        return {
            "count": len(entries),
            "query": query,
            "filter_tags": tag_filters,
            "success_only": bool(success_only),
            "entries": entries,
        }

    async def execution_feedback(
        self,
        action: str,
        result: str,
        issues: str | None = None,
    ) -> dict[str, object]:
        parsed_issues = (
            [item.strip() for item in issues.split(",") if item.strip()]
            if isinstance(issues, str)
            else []
        )
        normalized_result = result.strip().lower()
        normalized_issues = [item.lower() for item in parsed_issues]
        replan_reasons: list[str] = []

        if normalized_result in {"partial_success", "failed", "error"}:
            replan_reasons.append(f"result={normalized_result}")
        if any(
            marker in issue
            for issue in normalized_issues
            for marker in ("test failure", "low coverage", "failing test")
        ):
            replan_reasons.append("test_feedback")

        quality_total = self._coerce_float(
            self._session_state.get("test_quality_total", 0.0)
        )
        quality_pass = bool(self._session_state.get("test_quality_pass", False))
        if quality_total > 0 and not quality_pass:
            replan_reasons.append("test_quality_gate")

        replan_required = len(replan_reasons) > 0
        if replan_required:
            self._session_state["replan_required"] = True
            self._session_state["replan_reasons"] = replan_reasons

        self._session_bump("execution_feedback_count")
        payload = {
            "action": action,
            "result": normalized_result,
            "issues": parsed_issues,
            "replan_required": replan_required,
            "reasons": replan_reasons,
            "timestamp": int(time.time()),
        }
        self._memory_store.add_entry(
            text=json.dumps(payload, ensure_ascii=False),
            tags=["feedback", action, normalized_result],
        )
        self._record_tool_usefulness(
            cs.MCPToolName.EXECUTION_FEEDBACK,
            success=True,
            usefulness_score=1.0 if replan_required else 0.8,
        )
        return {
            "status": "ok",
            "replan_required": replan_required,
            "reasons": replan_reasons,
            "feedback": payload,
        }

    async def test_quality_gate(
        self,
        coverage: str,
        edge_cases: str,
        negative_tests: str,
    ) -> dict[str, object]:
        coverage_score = self._normalize_quality_score(coverage)
        edge_cases_score = self._normalize_quality_score(edge_cases)
        negative_tests_score = self._normalize_quality_score(negative_tests)
        total_score = coverage_score + edge_cases_score + negative_tests_score
        required = 2.0
        gate_pass = total_score >= required

        self._session_state["test_quality_total"] = round(total_score, 3)
        self._session_state["test_quality_pass"] = gate_pass
        self._record_tool_usefulness(
            cs.MCPToolName.TEST_QUALITY_GATE,
            success=True,
            usefulness_score=1.0 if gate_pass else 0.5,
        )

        return {
            "status": "ok",
            "scores": {
                "coverage": coverage_score,
                "edge_cases": edge_cases_score,
                "negative_tests": negative_tests_score,
                "total": round(total_score, 3),
                "required": required,
            },
            "pass": gate_pass,
        }

    async def plan_task(
        self, goal: str, context: str | None = None
    ) -> dict[str, object]:
        try:
            memory_patterns = await self.memory_query_patterns(
                query=goal,
                filter_tags="plan,refactor,success",
                success_only=True,
                limit=5,
            )
            pattern_entries = memory_patterns.get("entries", [])
            pattern_texts: list[str] = []
            if isinstance(pattern_entries, list):
                for item in pattern_entries:
                    if isinstance(item, dict):
                        item_dict = cast(dict[str, object], item)
                        text = item_dict.get("text")
                        if isinstance(text, str) and text.strip():
                            pattern_texts.append(text.strip()[:300])
            augmented_context = context or ""
            if pattern_texts:
                augmented_context = (
                    ((augmented_context + "\n") if augmented_context else "")
                    + "Memory patterns (must consider):\n"
                    + "\n".join(f"- {line}" for line in pattern_texts[:5])
                )

            result = await asyncio.wait_for(
                self._planner_agent.plan(goal, context=augmented_context),
                timeout=max(30.0, float(settings.MCP_AGENT_TIMEOUT_SECONDS)),
            )
            self._session_state["plan_task_completed"] = True
            self._record_tool_usefulness(
                cs.MCPToolName.PLAN_TASK,
                success=True,
                usefulness_score=1.0,
            )
            if hasattr(result, "content") and isinstance(result.content, dict):
                return {"status": result.status, **result.content}
            return {"status": result.status, "content": result.content}
        except TimeoutError:
            self._record_tool_usefulness(
                cs.MCPToolName.PLAN_TASK,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": "plan_task_timed_out_after_300s"}
        except Exception as exc:
            self._record_tool_usefulness(
                cs.MCPToolName.PLAN_TASK,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": str(exc)}

    async def impact_graph(
        self,
        qualified_name: str | None = None,
        file_path: str | None = None,
        depth: int = 3,
        limit: int = 200,
    ) -> dict[str, object]:
        if not qualified_name and not file_path:
            return {"error": "missing_target"}
        try:
            result = self._impact_service.query(
                qualified_name=qualified_name,
                file_path=file_path,
                depth=depth,
                limit=limit,
            )
            result_count = self._coerce_int(result.get("count", 0))
            self._session_state["impact_graph_called"] = True
            self._session_state["impact_graph_count"] = result_count
            self._record_tool_usefulness(
                cs.MCPToolName.IMPACT_GRAPH,
                success=True,
                usefulness_score=1.0 if result_count > 0 else 0.6,
            )
            return result
        except Exception as exc:
            self._record_tool_usefulness(
                cs.MCPToolName.IMPACT_GRAPH,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": str(exc), "results": []}

    @staticmethod
    def _normalize_quality_score(raw_value: str) -> float:
        try:
            parsed = float(raw_value)
        except (TypeError, ValueError):
            parsed = 0.0
        return round(max(0.0, min(1.0, parsed)), 3)

    @staticmethod
    def _coerce_int(value: object, default: int = 0) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return default
            try:
                return int(float(candidate))
            except ValueError:
                return default
        return default

    @staticmethod
    def _coerce_float(value: object, default: float = 0.0) -> float:
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return default
            try:
                return float(candidate)
            except ValueError:
                return default
        return default

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
        self._session_bump("edit_success_count")
        return {"status": "ok", "results": results}

    async def _compute_pattern_reuse_score(
        self, payload: list[dict[str, object]]
    ) -> float:
        query_inputs: list[str] = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            file_chunks = entry.get("chunks")
            if not isinstance(file_chunks, list):
                continue
            for chunk in file_chunks:
                if not isinstance(chunk, dict):
                    continue
                chunk_dict = cast(dict[str, object], chunk)
                replacement_code = chunk_dict.get("replacement_code")
                if isinstance(replacement_code, str) and replacement_code.strip():
                    query_inputs.append(replacement_code.strip()[:300])

        if not query_inputs:
            return 0.0

        semantic_scores: list[float] = []
        for query_text in query_inputs[:3]:
            try:
                matches = await asyncio.wait_for(
                    asyncio.to_thread(semantic_code_search, query_text, 3),
                    timeout=30.0,
                )
            except Exception:
                continue
            for item in matches:
                if isinstance(item, dict):
                    score = item.get("score")
                    if isinstance(score, int | float):
                        semantic_scores.append(float(score))

        if not semantic_scores:
            return 0.0
        return (sum(semantic_scores) / len(semantic_scores)) * 100.0

    def _session_bump(self, key: str, amount: int = 1) -> None:
        current = self._coerce_int(self._session_state.get(key, 0))
        self._session_state[key] = current + amount

    def _record_policy_event(
        self,
        action: str,
        allowed: bool,
        reason: str,
        details: dict[str, object] | None = None,
    ) -> None:
        status_tag = "allow" if allowed else "deny"
        if allowed:
            self._session_bump("policy_allow_count")
        else:
            self._session_bump("policy_deny_count")
        payload = {
            "action": action,
            "decision": status_tag,
            "reason": reason,
            "details": details or {},
            "timestamp": int(time.time()),
        }
        self._memory_store.add_entry(
            text=json.dumps(payload, ensure_ascii=False),
            tags=["policy", action, status_tag],
        )

    def _compute_execution_readiness(self) -> dict[str, object]:
        plan_done = bool(self._session_state.get("plan_task_completed", False))
        test_done = bool(self._session_state.get("test_generate_completed", False))
        test_quality_total = self._coerce_float(
            self._session_state.get("test_quality_total", 0.0)
        )
        test_quality_pass = bool(self._session_state.get("test_quality_pass", False))
        code_evidence_count = self._coerce_int(
            self._session_state.get("code_evidence_count", 0)
        )
        graph_evidence_count = self._coerce_int(
            self._session_state.get("graph_evidence_count", 0)
        )
        semantic_success = self._coerce_int(
            self._session_state.get("semantic_success_count", 0)
        )
        impact_graph_called = bool(
            self._session_state.get("impact_graph_called", False)
        )
        impact_graph_count = self._coerce_int(
            self._session_state.get("impact_graph_count", 0)
        )
        manual_memory_add_count = self._coerce_int(
            self._session_state.get("manual_memory_add_count", 0)
        )
        semantic_similarity_mean = self._coerce_float(
            self._session_state.get("semantic_similarity_mean", 0.0)
        )
        pattern_reuse_score = self._coerce_float(
            self._session_state.get("pattern_reuse_score", 0.0)
        )

        completion_requirements = {
            "semantic": semantic_success > 0,
            "code_source": code_evidence_count > 0,
            "graph_read": graph_evidence_count > 0,
            "test_generate": test_done,
            "test_quality": test_quality_pass,
            "memory_add": manual_memory_add_count > 0,
            "impact_graph": impact_graph_called,
        }
        completion_missing = [
            name for name, satisfied in completion_requirements.items() if not satisfied
        ]

        confidence_components = {
            "graph": 1.0 if graph_evidence_count > 0 else 0.0,
            "code": 1.0 if code_evidence_count > 0 else 0.0,
            "semantic": max(0.0, min(1.0, semantic_similarity_mean)),
        }
        confidence_total = sum(confidence_components.values())

        confidence_required = 2.0
        pattern_required = 70.0
        impact_threshold = 25
        replan_required = bool(self._session_state.get("replan_required", False))
        replan_reasons = self._session_state.get("replan_reasons", [])
        if not isinstance(replan_reasons, list):
            replan_reasons = []

        return {
            "confidence_gate": {
                "score": round(confidence_total, 3),
                "required": confidence_required,
                "components": confidence_components,
                "pass": confidence_total >= confidence_required,
            },
            "pattern_reuse_gate": {
                "score": round(pattern_reuse_score, 3),
                "required": pattern_required,
                "pass": pattern_reuse_score >= pattern_required,
            },
            "completion_gate": {
                "required": [
                    "semantic",
                    "code_source",
                    "graph_read",
                    "test_generate",
                    "test_quality",
                    "memory_add",
                    "impact_graph",
                ],
                "missing": completion_missing,
                "pass": not completion_missing,
            },
            "test_quality_gate": {
                "score": round(test_quality_total, 3),
                "required": 2.0,
                "pass": test_quality_pass,
            },
            "impact_graph_gate": {
                "called": impact_graph_called,
                "affected": impact_graph_count,
                "threshold": impact_threshold,
                "require_plan": impact_graph_count > impact_threshold,
                "pass": impact_graph_called,
            },
            "replan_gate": {
                "required": replan_required,
                "reasons": replan_reasons,
                "pass": not replan_required,
            },
            "signals": {
                "plan_task_completed": plan_done,
                "test_generate_completed": test_done,
                "test_quality_total": round(test_quality_total, 3),
                "test_quality_pass": test_quality_pass,
                "code_evidence_count": code_evidence_count,
                "graph_evidence_count": graph_evidence_count,
                "impact_graph_called": impact_graph_called,
                "impact_graph_count": impact_graph_count,
                "semantic_success_count": semantic_success,
                "manual_memory_add_count": manual_memory_add_count,
                "semantic_similarity_mean": round(semantic_similarity_mean, 3),
                "pattern_reuse_score": round(pattern_reuse_score, 3),
                "execution_feedback_count": self._coerce_int(
                    self._session_state.get("execution_feedback_count", 0)
                ),
                "memory_pattern_query_count": self._coerce_int(
                    self._session_state.get("memory_pattern_query_count", 0)
                ),
                "tool_usefulness_ranking": self._compute_tool_usefulness_ranking(
                    limit=5
                ),
                "edit_success_count": self._coerce_int(
                    self._session_state.get("edit_success_count", 0)
                ),
                "policy_allow_count": self._coerce_int(
                    self._session_state.get("policy_allow_count", 0)
                ),
                "policy_deny_count": self._coerce_int(
                    self._session_state.get("policy_deny_count", 0)
                ),
            },
        }

    async def get_execution_readiness(self) -> dict[str, object]:
        return self._compute_execution_readiness()

    @staticmethod
    def _done_protocol_checks(readiness: dict[str, object]) -> list[dict[str, object]]:
        check_specs = [
            ("confidence_gate", "confidence gate is below required threshold"),
            ("pattern_reuse_gate", "pattern reuse score is below required threshold"),
            ("completion_gate", "required completion evidence is missing"),
            ("test_quality_gate", "test quality gate did not pass"),
            ("impact_graph_gate", "impact graph gate did not pass"),
            ("replan_gate", "replan is required before completion"),
        ]
        checks: list[dict[str, object]] = []
        for gate_name, failure_message in check_specs:
            payload = readiness.get(gate_name, {})
            gate_pass = False
            if isinstance(payload, dict):
                payload_dict = cast(dict[str, object], payload)
                gate_pass = bool(payload_dict.get("pass", False))
            checks.append(
                {
                    "name": gate_name,
                    "pass": gate_pass,
                    "reason": "ok" if gate_pass else failure_message,
                }
            )
        return checks

    @staticmethod
    def _normalize_required_actions(
        raw_actions: object,
        fallback_actions: list[str],
    ) -> list[str]:
        if isinstance(raw_actions, list):
            normalized = [
                str(item).strip() for item in raw_actions if str(item).strip()
            ]
            if normalized:
                return normalized
        elif isinstance(raw_actions, str):
            candidate = raw_actions.strip()
            if candidate:
                return [candidate]
        return fallback_actions

    @staticmethod
    def _build_confidence_summary(readiness: dict[str, object]) -> dict[str, object]:
        confidence_gate = readiness.get("confidence_gate", {})
        components: dict[str, object] = {}
        score = 0.0
        required = 2.0
        gate_pass = False

        if isinstance(confidence_gate, dict):
            confidence_gate_dict = cast(dict[str, object], confidence_gate)
            score = MCPToolsRegistry._coerce_float(
                confidence_gate_dict.get("score", 0.0)
            )
            required = MCPToolsRegistry._coerce_float(
                confidence_gate_dict.get("required", 2.0),
                default=2.0,
            )
            gate_pass = bool(confidence_gate_dict.get("pass", False))
            raw_components = confidence_gate_dict.get("components", {})
            if isinstance(raw_components, dict):
                components = cast(dict[str, object], raw_components)

        status = "high" if score >= 2.7 else "medium" if score >= required else "low"
        pass_label = "pass" if gate_pass else "block"
        summary_text = (
            "confidence="
            + str(round(score, 3))
            + "/"
            + str(round(required, 3))
            + " ("
            + pass_label
            + ")"
        )

        return {
            "score": round(score, 3),
            "required": round(required, 3),
            "pass": gate_pass,
            "status": status,
            "components": components,
            "text": summary_text,
        }

    @staticmethod
    def _build_next_best_action(
        blockers: list[str],
        readiness: dict[str, object],
    ) -> dict[str, object]:
        if not blockers:
            return {
                "action": "proceed_to_apply_or_finalize",
                "tool": "refactor_batch",
                "why": "All completion gates pass.",
                "params_hint": {},
            }

        completion_gate = readiness.get("completion_gate", {})
        missing: list[str] = []
        if isinstance(completion_gate, dict):
            completion_gate_dict = cast(dict[str, object], completion_gate)
            raw_missing = completion_gate_dict.get("missing", [])
            if isinstance(raw_missing, list):
                missing = [str(item) for item in raw_missing]

        if "semantic" in missing:
            return {
                "action": "collect_semantic_evidence",
                "tool": "semantic_search",
                "why": "Completion gate is missing semantic evidence.",
                "params_hint": {"query": "target function behavior", "top_k": 5},
            }
        if "code_source" in missing:
            return {
                "action": "collect_code_evidence",
                "tool": "read_file",
                "why": "Completion gate is missing code source evidence.",
                "params_hint": {"file_path": "path/to/file.py"},
            }
        if "graph_read" in missing:
            return {
                "action": "collect_graph_evidence",
                "tool": "query_code_graph",
                "why": "Completion gate is missing graph-read evidence.",
                "params_hint": {
                    "natural_language_query": "dependencies of target module"
                },
            }
        if "impact_graph" in missing:
            return {
                "action": "run_impact_analysis",
                "tool": "impact_graph",
                "why": "Impact graph gate requires dependency impact data.",
                "params_hint": {"qualified_name": "module.Class.method", "depth": 3},
            }
        if "test_generate" in missing or "test_quality" in missing:
            return {
                "action": "improve_test_readiness",
                "tool": "test_quality_gate",
                "why": "Test quality/completion gate is not satisfied.",
                "params_hint": {
                    "coverage": "0.8",
                    "edge_cases": "0.7",
                    "negative_tests": "0.7",
                },
            }
        if "memory_add" in missing:
            return {
                "action": "persist_decision_memory",
                "tool": "memory_add",
                "why": "Completion gate requires at least one memory evidence entry.",
                "params_hint": {
                    "entry": "decision summary",
                    "tags": "decision,success",
                },
            }

        if any("replan" in blocker.lower() for blocker in blockers):
            return {
                "action": "replan_before_completion",
                "tool": "plan_task",
                "why": "Execution feedback requires replanning before done decision.",
                "params_hint": {"goal": "replan task with failure feedback"},
            }

        return {
            "action": "resolve_blockers_iteratively",
            "tool": "execution_feedback",
            "why": "At least one completion blocker is active.",
            "params_hint": {
                "action": "current_step",
                "result": "partial_success",
                "issues": "list blockers",
            },
        }

    async def validate_done_decision(
        self,
        goal: str | None = None,
        context: str | None = None,
    ) -> dict[str, object]:
        readiness = self._compute_execution_readiness()
        checks = self._done_protocol_checks(readiness)
        blockers = [
            str(item.get("reason", ""))
            for item in checks
            if isinstance(item, dict) and item.get("pass") is False
        ]
        decision = "done" if not blockers else "not_done"

        validator_payload = {
            "goal": goal or "",
            "context": context or "",
            "decision": decision,
            "blockers": blockers,
            "readiness": readiness,
            "checks": checks,
        }

        validator_output: dict[str, object] = {
            "decision": decision,
            "rationale": "deterministic_gate_protocol",
            "required_actions": blockers,
        }
        try:
            result = await asyncio.wait_for(
                self._validator_agent.validate(validator_payload),
                timeout=max(30.0, float(settings.MCP_AGENT_TIMEOUT_SECONDS)),
            )
            if hasattr(result, "content") and isinstance(result.content, dict):
                validator_output = result.content
        except Exception:
            validator_output = {
                "decision": decision,
                "rationale": "validator_fallback_used",
                "required_actions": blockers,
            }

        validator_decision = str(validator_output.get("decision", decision)).lower()
        if blockers:
            final_decision = "not_done"
        else:
            final_decision = "done" if validator_decision == "done" else "not_done"

        fallback_actions = blockers or [
            "resolve_validation_findings_before_marking_done"
        ]
        normalized_required_actions = self._normalize_required_actions(
            validator_output.get("required_actions", []),
            fallback_actions=fallback_actions,
        )
        if final_decision == "done":
            normalized_required_actions = []

        self._session_bump("done_decision_count")
        self._record_tool_usefulness(
            cs.MCPToolName.VALIDATE_DONE_DECISION,
            success=True,
            usefulness_score=1.0 if final_decision == "done" else 0.8,
        )
        confidence_summary = self._build_confidence_summary(readiness)
        next_best_action = self._build_next_best_action(blockers, readiness)
        ui_summary = (
            f"Decision: {final_decision}\n"
            f"Confidence: {confidence_summary.get('text', '')}\n"
            f"Blockers: {len(blockers)}\n"
            f"Next Best Action: {next_best_action.get('action', '')}"
        )
        return {
            "status": "ok",
            "decision": final_decision,
            "protocol": {
                "checks": checks,
                "pass": len(blockers) == 0,
            },
            "blockers": blockers,
            "confidence_summary": confidence_summary,
            "next_best_action": next_best_action,
            "ui_summary": ui_summary,
            "validator": {
                "decision": validator_decision,
                "rationale": str(validator_output.get("rationale", "")).strip(),
                "required_actions": normalized_required_actions,
            },
            "deterministic_decision": decision,
            "readiness": readiness,
        }

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
    orchestrator_prompt: str | None = None,
) -> MCPToolsRegistry:
    return MCPToolsRegistry(
        project_root=project_root,
        ingestor=ingestor,
        cypher_gen=cypher_gen,
        orchestrator_prompt=orchestrator_prompt or MCP_SYSTEM_PROMPT,
    )
