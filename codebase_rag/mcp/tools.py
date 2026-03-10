import asyncio
import itertools
import json
import os
import random
import re
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
from codebase_rag.agents.output_parser import (
    JSONOutputParser,
    decode_escaped_text,
    extract_code_block,
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
    ResultRow,
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
from codebase_rag.services.analysis_evidence import AnalysisEvidenceService
from codebase_rag.services.cleanup_service import CleanupService
from codebase_rag.services.context7_client import Context7Client
from codebase_rag.services.context7_persistence import (
    Context7KnowledgeStore,
    Context7MemoryStore,
    Context7Persistence,
)
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.services.llm import CypherGenerator
from codebase_rag.services.repo_semantics import RepoSemanticEnricher
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
        chain_signature = self._extract_chain_signature(text)
        record = {
            "text": text,
            "tags": tags,
            "timestamp": int(time.time()),
            "vector": self._build_sparse_vector(text),
            "chain_signature": chain_signature,
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
        chain_rates = self.get_chain_success_rates(query=query, limit=50)
        chain_rate_map: dict[str, float] = {
            str(item.get("chain_signature", "")): self._to_float(
                item.get("success_rate", 0.0)
            )
            for item in chain_rates
            if isinstance(item, dict)
        }
        query_vector = self._build_sparse_vector(query)
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
            if normalized_terms and score == 0 and not query_vector:
                continue

            entry_vector_raw = entry.get("vector", {})
            entry_vector = (
                cast(dict[str, float], entry_vector_raw)
                if isinstance(entry_vector_raw, dict)
                else {}
            )
            vector_similarity = self._cosine_similarity(query_vector, entry_vector)
            score += int(round(vector_similarity * 10))

            chain_signature = str(entry.get("chain_signature", "")).strip().lower()
            if chain_signature and any(
                term in chain_signature for term in normalized_terms
            ):
                score += 2

            if normalized_terms and score == 0:
                continue

            recency_bonus = max(0, len(self._entries) - idx)
            entry_with_score = dict(entry)
            entry_with_score["score"] = score
            entry_with_score["vector_similarity"] = round(vector_similarity, 4)
            if chain_signature:
                entry_with_score["chain_success_rate"] = round(
                    chain_rate_map.get(chain_signature, 0.0),
                    4,
                )
            ranked.append((score, recency_bonus, entry_with_score))

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

    def get_chain_success_rates(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, object]]:
        query_vector = self._build_sparse_vector(query)
        normalized_terms = {
            token.strip().lower()
            for token in query.replace("_", " ").split()
            if token.strip()
        }
        aggregates: dict[str, dict[str, object]] = {}

        for entry in self._entries:
            chain_signature = str(entry.get("chain_signature", "")).strip().lower()
            if not chain_signature:
                continue

            text = str(entry.get("text", ""))
            entry_vector_raw = entry.get("vector", {})
            entry_vector = (
                cast(dict[str, float], entry_vector_raw)
                if isinstance(entry_vector_raw, dict)
                else {}
            )
            vector_similarity = self._cosine_similarity(query_vector, entry_vector)
            term_match = any(
                term in chain_signature or term in text.lower()
                for term in normalized_terms
            )
            if normalized_terms and not term_match and vector_similarity <= 0.0:
                continue

            bucket = aggregates.get(chain_signature)
            if bucket is None:
                bucket = {
                    "chain_signature": chain_signature,
                    "success_count": 0,
                    "total_count": 0,
                    "last_seen": 0,
                    "query_relevance": 0.0,
                }
                aggregates[chain_signature] = bucket

            bucket["total_count"] = self._to_int(bucket.get("total_count", 0)) + 1
            if self._is_success_record(entry):
                bucket["success_count"] = (
                    self._to_int(bucket.get("success_count", 0)) + 1
                )
            bucket["last_seen"] = max(
                self._to_int(bucket.get("last_seen", 0)),
                self._to_int(entry.get("timestamp", 0)),
            )
            bucket["query_relevance"] = max(
                self._to_float(bucket.get("query_relevance", 0.0)),
                self._to_float(vector_similarity),
            )

        rows: list[dict[str, object]] = []
        for chain_signature, bucket in aggregates.items():
            total_count = self._to_int(bucket.get("total_count", 0))
            success_count = self._to_int(bucket.get("success_count", 0))
            success_rate = (success_count / total_count) if total_count > 0 else 0.0
            rows.append(
                {
                    "chain_signature": chain_signature,
                    "success_count": success_count,
                    "total_count": total_count,
                    "success_rate": round(success_rate, 4),
                    "last_seen": self._to_int(bucket.get("last_seen", 0)),
                    "query_relevance": round(
                        self._to_float(bucket.get("query_relevance", 0.0)), 4
                    ),
                }
            )

        rows.sort(
            key=lambda item: (
                self._to_float(item.get("success_rate", 0.0)),
                self._to_int(item.get("total_count", 0)),
                self._to_float(item.get("query_relevance", 0.0)),
                self._to_int(item.get("last_seen", 0)),
            ),
            reverse=True,
        )
        bounded_limit = max(1, min(self._to_int(limit, 10), 100))
        return rows[:bounded_limit]

    @staticmethod
    def _to_int(value: object, default: int = 0) -> int:
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
    def _to_float(value: object, default: float = 0.0) -> float:
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

    @staticmethod
    def _tokenize_text(value: str) -> list[str]:
        return [
            token
            for token in re.split(r"[^a-zA-Z0-9_]+", value.lower())
            if token and len(token) >= 2
        ]

    @classmethod
    def _build_sparse_vector(cls, value: str) -> dict[str, float]:
        if not value.strip():
            return {}
        tokens = cls._tokenize_text(value)
        if not tokens:
            return {}
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        max_count = max(counts.values()) if counts else 1
        return {token: round(count / max_count, 6) for token, count in counts.items()}

    @staticmethod
    def _cosine_similarity(
        left: dict[str, float],
        right: dict[str, float],
    ) -> float:
        if not left or not right:
            return 0.0
        overlap = set(left.keys()) & set(right.keys())
        if not overlap:
            return 0.0
        dot = sum(left[token] * right[token] for token in overlap)
        left_norm = sum(value * value for value in left.values()) ** 0.5
        right_norm = sum(value * value for value in right.values()) ** 0.5
        if left_norm <= 0 or right_norm <= 0:
            return 0.0
        return max(0.0, min(1.0, dot / (left_norm * right_norm)))

    @staticmethod
    def _extract_chain_signature(text: str) -> str:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return ""
        if not isinstance(payload, dict):
            return ""

        candidate_lists = [
            payload.get("tool_history"),
            payload.get("chain"),
            payload.get("flow"),
            payload.get("tools"),
        ]
        for candidate in candidate_lists:
            if isinstance(candidate, list):
                normalized = [
                    str(item).strip().lower() for item in candidate if str(item).strip()
                ]
                if normalized:
                    return " -> ".join(normalized)
        return ""


class MCPImpactGraphService:
    _IMPACT_REL_TYPES = "CALLS|IMPORTS|INHERITS|USES"

    def __init__(self, ingestor: MemgraphIngestor) -> None:
        self._ingestor = ingestor

    def query(
        self,
        qualified_name: str | None = None,
        file_path: str | None = None,
        project_name: str | None = None,
        depth: int = 3,
        limit: int = 200,
    ) -> dict[str, object]:
        bounded_depth = min(max(1, int(depth)), 6)
        bounded_limit = min(max(1, int(limit)), 1000)
        query = f"""
MATCH (start)
WHERE (
    (
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
)
AND (
    $project_name IS NULL
    OR coalesce(start.project_name, $project_name) = $project_name
)
WITH collect(DISTINCT start) AS seeds
UNWIND seeds AS seed
MATCH p=(seed)-[:{self._IMPACT_REL_TYPES}*1..{bounded_depth}]->(target)
WHERE (
    $project_name IS NULL
    OR all(node IN nodes(p) WHERE coalesce(node.project_name, $project_name) = $project_name)
)
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
            "project_name": project_name,
            "limit": bounded_limit,
        }
        results = self._ingestor.fetch_all(query, params)
        return {
            "count": len(results),
            "depth": bounded_depth,
            "limit": bounded_limit,
            "results": results,
        }


def _build_tool_metadata_catalog(
    registry: "MCPToolsRegistry",
) -> dict[str, ToolMetadata]:
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
                    ),
                    cs.MCPParamName.CLIENT_PROFILE: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_CLIENT_PROFILE,
                    ),
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
                    cs.MCPParamName.SYNC_MODE: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_SYNC_MODE,
                        default="fast",
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
        cs.MCPToolName.MULTI_HOP_ANALYSIS: ToolMetadata(
            name=cs.MCPToolName.MULTI_HOP_ANALYSIS,
            description=td.MCP_TOOLS[cs.MCPToolName.MULTI_HOP_ANALYSIS],
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
                        default=80,
                    ),
                    cs.MCPParamName.INCLUDE_CONTEXT7: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.BOOLEAN,
                        description=td.MCP_PARAM_INCLUDE_CONTEXT7,
                        default=False,
                    ),
                    cs.MCPParamName.CONTEXT7_QUERY: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_CONTEXT7_QUERY,
                    ),
                },
                required=[],
            ),
            handler=registry.multi_hop_analysis,
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
        cs.MCPToolName.CONTEXT7_DOCS: ToolMetadata(
            name=cs.MCPToolName.CONTEXT7_DOCS,
            description=td.MCP_TOOLS[cs.MCPToolName.CONTEXT7_DOCS],
            input_schema=MCPInputSchema(
                type=cs.MCPSchemaType.OBJECT,
                properties={
                    cs.MCPParamName.LIBRARY: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_LIBRARY,
                    ),
                    cs.MCPParamName.QUERY: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_QUERY,
                    ),
                    cs.MCPParamName.VERSION: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_VERSION,
                    ),
                },
                required=[cs.MCPParamName.LIBRARY, cs.MCPParamName.QUERY],
            ),
            handler=registry.context7_docs,
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
        cs.MCPToolName.ANALYSIS_BUNDLE_FOR_GOAL: ToolMetadata(
            name=cs.MCPToolName.ANALYSIS_BUNDLE_FOR_GOAL,
            description=td.MCP_TOOLS[cs.MCPToolName.ANALYSIS_BUNDLE_FOR_GOAL],
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
            handler=registry.analysis_bundle_for_goal,
            returns_json=True,
        ),
        cs.MCPToolName.ARCHITECTURE_BUNDLE: ToolMetadata(
            name=cs.MCPToolName.ARCHITECTURE_BUNDLE,
            description=td.MCP_TOOLS[cs.MCPToolName.ARCHITECTURE_BUNDLE],
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
            handler=registry.architecture_bundle,
            returns_json=True,
        ),
        cs.MCPToolName.CHANGE_BUNDLE: ToolMetadata(
            name=cs.MCPToolName.CHANGE_BUNDLE,
            description=td.MCP_TOOLS[cs.MCPToolName.CHANGE_BUNDLE],
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
                    cs.MCPParamName.QUALIFIED_NAME: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_QUALIFIED_NAME,
                    ),
                    cs.MCPParamName.FILE_PATH: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_FILE_PATH,
                    ),
                },
                required=[cs.MCPParamName.GOAL],
            ),
            handler=registry.change_bundle,
            returns_json=True,
        ),
        cs.MCPToolName.RISK_BUNDLE: ToolMetadata(
            name=cs.MCPToolName.RISK_BUNDLE,
            description=td.MCP_TOOLS[cs.MCPToolName.RISK_BUNDLE],
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
                    cs.MCPParamName.QUALIFIED_NAME: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_QUALIFIED_NAME,
                    ),
                    cs.MCPParamName.FILE_PATH: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_FILE_PATH,
                    ),
                },
                required=[cs.MCPParamName.GOAL],
            ),
            handler=registry.risk_bundle,
            returns_json=True,
        ),
        cs.MCPToolName.TEST_BUNDLE: ToolMetadata(
            name=cs.MCPToolName.TEST_BUNDLE,
            description=td.MCP_TOOLS[cs.MCPToolName.TEST_BUNDLE],
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
                    cs.MCPParamName.QUALIFIED_NAME: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_QUALIFIED_NAME,
                    ),
                    cs.MCPParamName.FILE_PATH: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_FILE_PATH,
                    ),
                },
                required=[cs.MCPParamName.GOAL],
            ),
            handler=registry.test_bundle,
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
                    cs.MCPParamName.ADVANCED_MODE: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.BOOLEAN,
                        description=td.MCP_PARAM_ADVANCED_MODE,
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
                    cs.MCPParamName.OUTPUT_MODE: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_OUTPUT_MODE,
                        default="code",
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
                    cs.MCPParamName.FAILURE_REASONS: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_FAILURE_REASONS,
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
                    cs.MCPParamName.REPO_EVIDENCE: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_REPO_EVIDENCE,
                    ),
                    cs.MCPParamName.LAYER_CORRECTNESS: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_LAYER_CORRECTNESS,
                    ),
                    cs.MCPParamName.CLEANUP_SAFETY: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_CLEANUP_SAFETY,
                    ),
                    cs.MCPParamName.ANTI_HALLUCINATION: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_ANTI_HALLUCINATION,
                    ),
                    cs.MCPParamName.IMPLEMENTATION_COUPLING_PENALTY: MCPInputSchemaProperty(
                        type=cs.MCPSchemaType.STRING,
                        description=td.MCP_PARAM_IMPLEMENTATION_COUPLING_PENALTY,
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


_TOOL_DOMAIN_GROUPS: dict[str, tuple[str, ...]] = {
    "project": (
        cs.MCPToolName.LIST_PROJECTS,
        cs.MCPToolName.SELECT_ACTIVE_PROJECT,
        cs.MCPToolName.DETECT_PROJECT_DRIFT,
        cs.MCPToolName.DELETE_PROJECT,
        cs.MCPToolName.WIPE_DATABASE,
        cs.MCPToolName.INDEX_REPOSITORY,
        cs.MCPToolName.SYNC_GRAPH_UPDATES,
    ),
    "graph": (
        cs.MCPToolName.QUERY_CODE_GRAPH,
        cs.MCPToolName.MULTI_HOP_ANALYSIS,
        cs.MCPToolName.RUN_CYPHER,
        cs.MCPToolName.IMPACT_GRAPH,
        cs.MCPToolName.GET_GRAPH_STATS,
        cs.MCPToolName.GET_DEPENDENCY_STATS,
    ),
    "retrieval": (
        cs.MCPToolName.SEMANTIC_SEARCH,
        cs.MCPToolName.CONTEXT7_DOCS,
        cs.MCPToolName.GET_FUNCTION_SOURCE,
        cs.MCPToolName.GET_CODE_SNIPPET,
        cs.MCPToolName.READ_FILE,
        cs.MCPToolName.LIST_DIRECTORY,
    ),
    "mutation": (
        cs.MCPToolName.WRITE_FILE,
        cs.MCPToolName.SURGICAL_REPLACE_CODE,
        cs.MCPToolName.APPLY_DIFF_SAFE,
        cs.MCPToolName.REFACTOR_BATCH,
    ),
    "analysis": (
        cs.MCPToolName.RUN_ANALYSIS,
        cs.MCPToolName.RUN_ANALYSIS_SUBSET,
        cs.MCPToolName.SECURITY_SCAN,
        cs.MCPToolName.PERFORMANCE_HOTSPOTS,
        cs.MCPToolName.GET_ANALYSIS_REPORT,
        cs.MCPToolName.GET_ANALYSIS_METRIC,
        cs.MCPToolName.GET_ANALYSIS_ARTIFACT,
        cs.MCPToolName.LIST_ANALYSIS_ARTIFACTS,
        cs.MCPToolName.ANALYSIS_BUNDLE_FOR_GOAL,
        cs.MCPToolName.ARCHITECTURE_BUNDLE,
        cs.MCPToolName.CHANGE_BUNDLE,
        cs.MCPToolName.RISK_BUNDLE,
        cs.MCPToolName.TEST_BUNDLE,
        cs.MCPToolName.EXPORT_MERMAID,
    ),
    "workflow": (
        cs.MCPToolName.PLAN_TASK,
        cs.MCPToolName.TEST_GENERATE,
        cs.MCPToolName.TEST_QUALITY_GATE,
        cs.MCPToolName.EXECUTION_FEEDBACK,
        cs.MCPToolName.MEMORY_ADD,
        cs.MCPToolName.MEMORY_LIST,
        cs.MCPToolName.MEMORY_QUERY_PATTERNS,
        cs.MCPToolName.GET_TOOL_USEFULNESS_RANKING,
        cs.MCPToolName.VALIDATE_DONE_DECISION,
        cs.MCPToolName.ORCHESTRATE_REALTIME_FLOW,
        cs.MCPToolName.GET_EXECUTION_READINESS,
    ),
}


def _build_tool_metadata(registry: "MCPToolsRegistry") -> dict[str, ToolMetadata]:
    catalog = _build_tool_metadata_catalog(registry)
    domain_order = [
        "project",
        "graph",
        "retrieval",
        "mutation",
        "analysis",
        "workflow",
    ]
    composed: dict[str, ToolMetadata] = {}
    for domain_name in domain_order:
        for tool_name in _TOOL_DOMAIN_GROUPS.get(domain_name, ()):  # pragma: no branch
            metadata = catalog.get(tool_name)
            if metadata is not None:
                composed[tool_name] = metadata

    for tool_name, metadata in catalog.items():
        if tool_name not in composed:
            composed[tool_name] = metadata
    return composed


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
    _ORCHESTRATOR_MAX_TOOL_CHAIN_STEPS = 8
    _ORCHESTRATOR_VISIBLE_TIERS = {"tier1", "meta"}
    _DEFAULT_CLIENT_PROFILE = cs.MCPClientProfile.BALANCED
    _CLIENT_PROFILE_CONFIGS: dict[str, dict[str, object]] = {
        cs.MCPClientProfile.BALANCED: {
            "tool_chain_max_steps": 8,
            "response_mode": "balanced",
            "summary_style": "readable_compact",
            "planner_contract": "standard",
            "preferred_auto_next": False,
        },
        cs.MCPClientProfile.VSCODE: {
            "tool_chain_max_steps": 8,
            "response_mode": "ide_readable",
            "summary_style": "copy_paste_guided",
            "planner_contract": "standard",
            "preferred_auto_next": False,
        },
        cs.MCPClientProfile.CLINE: {
            "tool_chain_max_steps": 9,
            "response_mode": "tool_forward",
            "summary_style": "dense_with_exact_calls",
            "planner_contract": "standard",
            "preferred_auto_next": True,
        },
        cs.MCPClientProfile.COPILOT: {
            "tool_chain_max_steps": 7,
            "response_mode": "short_guided",
            "summary_style": "ide_readable",
            "planner_contract": "compact",
            "preferred_auto_next": False,
        },
        cs.MCPClientProfile.OLLAMA: {
            "tool_chain_max_steps": 5,
            "response_mode": "minimal_deterministic",
            "summary_style": "shortest_safe",
            "planner_contract": "ultra_compact",
            "preferred_auto_next": False,
        },
        cs.MCPClientProfile.HTTP: {
            "tool_chain_max_steps": 8,
            "response_mode": "api_copy_paste",
            "summary_style": "copy_paste_guided",
            "planner_contract": "compact",
            "preferred_auto_next": False,
        },
    }
    _EXPLORATION_BASE_EPSILON = 0.15
    _EXPLORATION_MIN_EPSILON = 0.05
    _EXPLORATION_MAX_EPSILON = 0.35
    _EXPLORATION_ALLOWED_FAILURE_TYPES = {"no_data", "low_confidence", "unknown"}
    _EXPLORATION_POLICY_UCB_BONUS = 0.2
    _TOOL_TIER_MAP = {
        cs.MCPToolName.LIST_PROJECTS: "tier1",
        cs.MCPToolName.QUERY_CODE_GRAPH: "tier1",
        cs.MCPToolName.MULTI_HOP_ANALYSIS: "tier1",
        cs.MCPToolName.ANALYSIS_BUNDLE_FOR_GOAL: "tier1",
        cs.MCPToolName.ARCHITECTURE_BUNDLE: "tier1",
        cs.MCPToolName.SEMANTIC_SEARCH: "tier1",
        cs.MCPToolName.RUN_CYPHER: "tier1",
        cs.MCPToolName.SELECT_ACTIVE_PROJECT: "tier1",
        cs.MCPToolName.CHANGE_BUNDLE: "tier2",
        cs.MCPToolName.RISK_BUNDLE: "tier2",
        cs.MCPToolName.TEST_BUNDLE: "tier2",
        cs.MCPToolName.CONTEXT7_DOCS: "tier2",
        cs.MCPToolName.GET_FUNCTION_SOURCE: "tier2",
        cs.MCPToolName.READ_FILE: "tier2",
        cs.MCPToolName.WRITE_FILE: "tier3",
        cs.MCPToolName.REFACTOR_BATCH: "tier3",
        cs.MCPToolName.APPLY_DIFF_SAFE: "tier3",
        cs.MCPToolName.SURGICAL_REPLACE_CODE: "tier3",
        cs.MCPToolName.PLAN_TASK: "meta",
        cs.MCPToolName.TEST_GENERATE: "meta",
        cs.MCPToolName.MEMORY_LIST: "meta",
        cs.MCPToolName.MEMORY_QUERY_PATTERNS: "meta",
        cs.MCPToolName.EXECUTION_FEEDBACK: "meta",
        cs.MCPToolName.GET_EXECUTION_READINESS: "meta",
        cs.MCPToolName.TEST_QUALITY_GATE: "meta",
        cs.MCPToolName.GET_TOOL_USEFULNESS_RANKING: "meta",
        cs.MCPToolName.VALIDATE_DONE_DECISION: "meta",
        cs.MCPToolName.ORCHESTRATE_REALTIME_FLOW: "meta",
        cs.MCPToolName.MEMORY_ADD: "meta",
        cs.MCPToolName.IMPACT_GRAPH: "meta",
    }
    _EXECUTION_PHASES = (
        "preflight",
        "retrieval",
        "validation",
        "execution",
        "post_validation",
    )
    _EXECUTION_TRANSITIONS = {
        "preflight": {"preflight", "retrieval"},
        "retrieval": {"retrieval", "validation", "execution", "post_validation"},
        "validation": {"retrieval", "validation", "execution", "post_validation"},
        "execution": {"retrieval", "validation", "execution", "post_validation"},
        "post_validation": {
            "preflight",
            "retrieval",
            "validation",
            "execution",
            "post_validation",
        },
    }
    _PHASE_EXEMPT_TOOLS = {
        cs.MCPToolName.LIST_PROJECTS,
        cs.MCPToolName.SELECT_ACTIVE_PROJECT,
        cs.MCPToolName.GET_EXECUTION_READINESS,
        cs.MCPToolName.GET_TOOL_USEFULNESS_RANKING,
    }
    _PHASE_ALLOWED_TOOLS = {
        "preflight": {
            cs.MCPToolName.LIST_PROJECTS,
            cs.MCPToolName.SELECT_ACTIVE_PROJECT,
            cs.MCPToolName.GET_EXECUTION_READINESS,
        },
        "retrieval": {
            cs.MCPToolName.QUERY_CODE_GRAPH,
            cs.MCPToolName.MULTI_HOP_ANALYSIS,
            cs.MCPToolName.SEMANTIC_SEARCH,
            cs.MCPToolName.CONTEXT7_DOCS,
            cs.MCPToolName.GET_FUNCTION_SOURCE,
            cs.MCPToolName.GET_CODE_SNIPPET,
            cs.MCPToolName.READ_FILE,
            cs.MCPToolName.RUN_CYPHER,
            cs.MCPToolName.LIST_DIRECTORY,
            cs.MCPToolName.PLAN_TASK,
            cs.MCPToolName.MEMORY_QUERY_PATTERNS,
            cs.MCPToolName.MEMORY_LIST,
            cs.MCPToolName.IMPACT_GRAPH,
            cs.MCPToolName.GET_GRAPH_STATS,
            cs.MCPToolName.GET_DEPENDENCY_STATS,
            cs.MCPToolName.GET_ANALYSIS_REPORT,
            cs.MCPToolName.GET_ANALYSIS_METRIC,
            cs.MCPToolName.GET_ANALYSIS_ARTIFACT,
            cs.MCPToolName.LIST_ANALYSIS_ARTIFACTS,
            cs.MCPToolName.ANALYSIS_BUNDLE_FOR_GOAL,
            cs.MCPToolName.ARCHITECTURE_BUNDLE,
            cs.MCPToolName.CHANGE_BUNDLE,
            cs.MCPToolName.RISK_BUNDLE,
            cs.MCPToolName.TEST_BUNDLE,
            cs.MCPToolName.RUN_ANALYSIS,
            cs.MCPToolName.RUN_ANALYSIS_SUBSET,
            cs.MCPToolName.SECURITY_SCAN,
            cs.MCPToolName.PERFORMANCE_HOTSPOTS,
            cs.MCPToolName.EXPORT_MERMAID,
            cs.MCPToolName.VALIDATE_DONE_DECISION,
            cs.MCPToolName.GET_EXECUTION_READINESS,
            cs.MCPToolName.ORCHESTRATE_REALTIME_FLOW,
        },
        "validation": {
            cs.MCPToolName.QUERY_CODE_GRAPH,
            cs.MCPToolName.MULTI_HOP_ANALYSIS,
            cs.MCPToolName.SEMANTIC_SEARCH,
            cs.MCPToolName.CONTEXT7_DOCS,
            cs.MCPToolName.GET_FUNCTION_SOURCE,
            cs.MCPToolName.GET_CODE_SNIPPET,
            cs.MCPToolName.READ_FILE,
            cs.MCPToolName.RUN_CYPHER,
            cs.MCPToolName.LIST_DIRECTORY,
            cs.MCPToolName.VALIDATE_DONE_DECISION,
            cs.MCPToolName.TEST_QUALITY_GATE,
            cs.MCPToolName.TEST_GENERATE,
            cs.MCPToolName.PLAN_TASK,
            cs.MCPToolName.MEMORY_QUERY_PATTERNS,
            cs.MCPToolName.IMPACT_GRAPH,
            cs.MCPToolName.GET_GRAPH_STATS,
            cs.MCPToolName.GET_DEPENDENCY_STATS,
            cs.MCPToolName.GET_ANALYSIS_REPORT,
            cs.MCPToolName.GET_ANALYSIS_METRIC,
            cs.MCPToolName.GET_ANALYSIS_ARTIFACT,
            cs.MCPToolName.LIST_ANALYSIS_ARTIFACTS,
            cs.MCPToolName.ANALYSIS_BUNDLE_FOR_GOAL,
            cs.MCPToolName.ARCHITECTURE_BUNDLE,
            cs.MCPToolName.CHANGE_BUNDLE,
            cs.MCPToolName.RISK_BUNDLE,
            cs.MCPToolName.TEST_BUNDLE,
            cs.MCPToolName.RUN_ANALYSIS,
            cs.MCPToolName.RUN_ANALYSIS_SUBSET,
            cs.MCPToolName.SECURITY_SCAN,
            cs.MCPToolName.PERFORMANCE_HOTSPOTS,
            cs.MCPToolName.EXPORT_MERMAID,
            cs.MCPToolName.GET_EXECUTION_READINESS,
            cs.MCPToolName.ORCHESTRATE_REALTIME_FLOW,
        },
        "execution": {
            cs.MCPToolName.APPLY_DIFF_SAFE,
            cs.MCPToolName.SURGICAL_REPLACE_CODE,
            cs.MCPToolName.WRITE_FILE,
            cs.MCPToolName.REFACTOR_BATCH,
            cs.MCPToolName.RUN_CYPHER,
            cs.MCPToolName.SYNC_GRAPH_UPDATES,
            cs.MCPToolName.EXECUTION_FEEDBACK,
            cs.MCPToolName.SECURITY_SCAN,
            cs.MCPToolName.PERFORMANCE_HOTSPOTS,
            cs.MCPToolName.TEST_QUALITY_GATE,
            cs.MCPToolName.VALIDATE_DONE_DECISION,
            cs.MCPToolName.GET_EXECUTION_READINESS,
            cs.MCPToolName.ORCHESTRATE_REALTIME_FLOW,
        },
        "post_validation": {
            cs.MCPToolName.VALIDATE_DONE_DECISION,
            cs.MCPToolName.TEST_QUALITY_GATE,
            cs.MCPToolName.TEST_GENERATE,
            cs.MCPToolName.EXECUTION_FEEDBACK,
            cs.MCPToolName.MEMORY_ADD,
            cs.MCPToolName.PLAN_TASK,
            cs.MCPToolName.GET_EXECUTION_READINESS,
            cs.MCPToolName.ORCHESTRATE_REALTIME_FLOW,
        },
    }

    @classmethod
    def _tool_tier(cls, tool_name: str) -> str:
        return str(cls._TOOL_TIER_MAP.get(tool_name, "unknown"))

    @classmethod
    def _normalize_client_profile_value(cls, client_profile: str | None) -> str:
        raw = str(client_profile or "").strip().lower()
        aliases = {
            "vs code": cs.MCPClientProfile.VSCODE,
            "vs_code": cs.MCPClientProfile.VSCODE,
            "cursor": cs.MCPClientProfile.VSCODE,
            "copilot-chat": cs.MCPClientProfile.COPILOT,
            "copilot_chat": cs.MCPClientProfile.COPILOT,
            "http_api": cs.MCPClientProfile.HTTP,
            "local": cs.MCPClientProfile.OLLAMA,
        }
        normalized = aliases.get(raw, raw)
        if normalized in cls._CLIENT_PROFILE_CONFIGS:
            return normalized
        return str(cls._DEFAULT_CLIENT_PROFILE)

    def set_client_profile(self, client_profile: str | None) -> str:
        normalized = self._normalize_client_profile_value(client_profile)
        session_state = getattr(self, "_session_state", None)
        if isinstance(session_state, dict):
            session_state["client_profile"] = normalized
        return normalized

    def _client_profile(self) -> str:
        session_state = getattr(self, "_session_state", None)
        if not isinstance(session_state, dict):
            return str(self._DEFAULT_CLIENT_PROFILE)
        return self._normalize_client_profile_value(
            str(session_state.get("client_profile", self._DEFAULT_CLIENT_PROFILE))
        )

    def _client_profile_config(self) -> dict[str, object]:
        return dict(
            self._CLIENT_PROFILE_CONFIGS.get(
                self._client_profile(),
                self._CLIENT_PROFILE_CONFIGS[str(self._DEFAULT_CLIENT_PROFILE)],
            )
        )

    def _orchestrator_max_tool_chain_steps(self) -> int:
        profile_config = self._client_profile_config()
        return max(
            3,
            self._coerce_int(
                profile_config.get(
                    "tool_chain_max_steps", self._ORCHESTRATOR_MAX_TOOL_CHAIN_STEPS
                ),
                self._ORCHESTRATOR_MAX_TOOL_CHAIN_STEPS,
            ),
        )

    def _state_machine_contract(self) -> dict[str, object]:
        return {
            "enabled": True,
            "deterministic": True,
            "current_phase": self._current_execution_phase(),
            "allowed_transitions": {
                phase: sorted(next_phases)
                for phase, next_phases in self._EXECUTION_TRANSITIONS.items()
            },
            "last_transition_allowed": bool(
                self._session_state.get("last_phase_transition_allowed", True)
            ),
            "last_transition_error": str(
                self._session_state.get("last_phase_transition_error", "")
            ).strip(),
        }

    @classmethod
    def _is_tool_visible_in_orchestrator(cls, tool_name: str) -> tuple[bool, str]:
        tier = cls._tool_tier(tool_name)
        return tier in cls._ORCHESTRATOR_VISIBLE_TIERS, tier

    def _current_execution_phase(self) -> str:
        phase = str(self._session_state.get("execution_phase", "preflight")).strip()
        if phase not in self._EXECUTION_PHASES:
            return "preflight"
        return phase

    def _set_execution_phase(self, phase: str, reason: str) -> None:
        normalized_phase = str(phase).strip()
        if normalized_phase not in self._EXECUTION_PHASES:
            normalized_phase = "preflight"
        previous = self._current_execution_phase()
        allowed_transitions = self._EXECUTION_TRANSITIONS.get(previous, {previous})
        if normalized_phase not in allowed_transitions:
            self._session_state["last_phase_transition_allowed"] = False
            self._session_state["last_phase_transition_error"] = (
                f"invalid_transition:{previous}->{normalized_phase}"
            )
            normalized_phase = previous
        else:
            self._session_state["last_phase_transition_allowed"] = True
            self._session_state["last_phase_transition_error"] = ""
        if previous == normalized_phase:
            return
        self._session_state["execution_phase"] = normalized_phase
        history_raw = self._session_state.get("execution_phase_history", [])
        history: list[dict[str, object]] = []
        if isinstance(history_raw, list):
            for row in history_raw:
                if isinstance(row, dict):
                    history.append(cast(dict[str, object], row))
        history.insert(
            0,
            {
                "from": previous,
                "to": normalized_phase,
                "reason": reason,
                "timestamp": int(time.time()),
            },
        )
        self._session_state["execution_phase_history"] = history[:50]

    def _build_execution_state_contract(self) -> dict[str, object]:
        phase = self._current_execution_phase()
        allowed_tools = sorted(self._PHASE_ALLOWED_TOOLS.get(phase, set()))
        forbidden_tools = sorted(
            [
                tool_name
                for tool_name in self._tools.keys()
                if tool_name not in allowed_tools
            ]
        )
        return {
            "phase": phase,
            "allowed_tools": allowed_tools,
            "forbidden_tools": forbidden_tools,
            "phase_history": self._session_state.get("execution_phase_history", []),
            "state_machine": self._state_machine_contract(),
        }

    def _visible_tool_names(self, *, allow_phase_bypass: bool = False) -> set[str]:
        phase = self._current_execution_phase()
        visible: set[str] = {
            cs.MCPToolName.LIST_PROJECTS,
            cs.MCPToolName.SELECT_ACTIVE_PROJECT,
        }
        if allow_phase_bypass and phase != "preflight":
            visible.update(self._PHASE_ALLOWED_TOOLS.get(phase, set()))
        if not bool(self._session_state.get("preflight_project_selected", False)):
            return visible
        if not bool(self._session_state.get("preflight_schema_summary_loaded", False)):
            visible.add(cs.MCPToolName.GET_EXECUTION_READINESS)
            return visible

        visible.update(
            {
                cs.MCPToolName.GET_EXECUTION_READINESS,
                cs.MCPToolName.GET_TOOL_USEFULNESS_RANKING,
                cs.MCPToolName.MEMORY_QUERY_PATTERNS,
                cs.MCPToolName.MEMORY_LIST,
                cs.MCPToolName.PLAN_TASK,
                cs.MCPToolName.QUERY_CODE_GRAPH,
                cs.MCPToolName.MULTI_HOP_ANALYSIS,
                cs.MCPToolName.ANALYSIS_BUNDLE_FOR_GOAL,
                cs.MCPToolName.ARCHITECTURE_BUNDLE,
                cs.MCPToolName.GET_GRAPH_STATS,
                cs.MCPToolName.GET_DEPENDENCY_STATS,
            }
        )

        graph_evidence = self._coerce_int(
            self._session_state.get("graph_evidence_count", 0)
        )
        code_evidence = self._coerce_int(
            self._session_state.get("code_evidence_count", 0)
        )
        semantic_hits = self._coerce_int(
            self._session_state.get("semantic_success_count", 0)
        )
        impact_called = bool(self._session_state.get("impact_graph_called", False))
        plan_done = bool(self._session_state.get("plan_task_completed", False))
        graph_dirty = bool(self._session_state.get("graph_dirty", False))
        edit_success = self._coerce_int(
            self._session_state.get("edit_success_count", 0)
        )

        if graph_evidence > 0 or semantic_hits > 0 or impact_called:
            visible.update(
                {
                    cs.MCPToolName.SEMANTIC_SEARCH,
                    cs.MCPToolName.IMPACT_GRAPH,
                    cs.MCPToolName.RUN_CYPHER,
                    cs.MCPToolName.GET_CODE_SNIPPET,
                    cs.MCPToolName.GET_FUNCTION_SOURCE,
                    cs.MCPToolName.LIST_DIRECTORY,
                    cs.MCPToolName.GET_ANALYSIS_REPORT,
                    cs.MCPToolName.GET_ANALYSIS_METRIC,
                    cs.MCPToolName.GET_ANALYSIS_ARTIFACT,
                    cs.MCPToolName.LIST_ANALYSIS_ARTIFACTS,
                    cs.MCPToolName.CHANGE_BUNDLE,
                    cs.MCPToolName.RISK_BUNDLE,
                    cs.MCPToolName.TEST_BUNDLE,
                    cs.MCPToolName.RUN_ANALYSIS,
                    cs.MCPToolName.RUN_ANALYSIS_SUBSET,
                    cs.MCPToolName.SECURITY_SCAN,
                    cs.MCPToolName.PERFORMANCE_HOTSPOTS,
                    cs.MCPToolName.EXPORT_MERMAID,
                }
            )

        if graph_evidence > 0 or semantic_hits > 0 or code_evidence > 0:
            visible.add(cs.MCPToolName.READ_FILE)
            visible.add(cs.MCPToolName.CONTEXT7_DOCS)

        if graph_evidence > 0 or impact_called or code_evidence > 0 or plan_done:
            visible.update(
                {
                    cs.MCPToolName.TEST_GENERATE,
                    cs.MCPToolName.TEST_QUALITY_GATE,
                    cs.MCPToolName.EXECUTION_FEEDBACK,
                    cs.MCPToolName.VALIDATE_DONE_DECISION,
                    cs.MCPToolName.ORCHESTRATE_REALTIME_FLOW,
                }
            )

        if code_evidence > 0 or edit_success > 0 or graph_dirty:
            visible.update(
                {
                    cs.MCPToolName.WRITE_FILE,
                    cs.MCPToolName.SURGICAL_REPLACE_CODE,
                    cs.MCPToolName.APPLY_DIFF_SAFE,
                    cs.MCPToolName.REFACTOR_BATCH,
                    cs.MCPToolName.SYNC_GRAPH_UPDATES,
                }
            )

        if plan_done or graph_evidence > 0 or code_evidence > 0:
            visible.add(cs.MCPToolName.MEMORY_ADD)

        return visible

    def _staged_tool_visibility_contract(self) -> dict[str, object]:
        visible = sorted(self._visible_tool_names())
        hidden = sorted(
            tool_name for tool_name in self._tools if tool_name not in visible
        )
        return {
            "active_stage": self._current_execution_phase(),
            "visible_tools": visible,
            "hidden_tools": hidden,
            "stages": [
                {
                    "name": "startup",
                    "tools": [
                        cs.MCPToolName.LIST_PROJECTS,
                        cs.MCPToolName.SELECT_ACTIVE_PROJECT,
                    ],
                },
                {
                    "name": "graph_bootstrap",
                    "tools": [
                        cs.MCPToolName.QUERY_CODE_GRAPH,
                        cs.MCPToolName.MULTI_HOP_ANALYSIS,
                        cs.MCPToolName.ANALYSIS_BUNDLE_FOR_GOAL,
                        cs.MCPToolName.ARCHITECTURE_BUNDLE,
                        cs.MCPToolName.PLAN_TASK,
                        cs.MCPToolName.GET_EXECUTION_READINESS,
                    ],
                },
                {
                    "name": "evidence_enrichment",
                    "tools": [
                        cs.MCPToolName.RUN_CYPHER,
                        cs.MCPToolName.IMPACT_GRAPH,
                        cs.MCPToolName.SEMANTIC_SEARCH,
                        cs.MCPToolName.GET_CODE_SNIPPET,
                        cs.MCPToolName.GET_FUNCTION_SOURCE,
                        cs.MCPToolName.CONTEXT7_DOCS,
                        cs.MCPToolName.CHANGE_BUNDLE,
                        cs.MCPToolName.RISK_BUNDLE,
                        cs.MCPToolName.TEST_BUNDLE,
                    ],
                },
                {
                    "name": "implementation_confirmation",
                    "tools": [cs.MCPToolName.READ_FILE],
                },
                {
                    "name": "mutation_and_sync",
                    "tools": [
                        cs.MCPToolName.APPLY_DIFF_SAFE,
                        cs.MCPToolName.SURGICAL_REPLACE_CODE,
                        cs.MCPToolName.WRITE_FILE,
                        cs.MCPToolName.REFACTOR_BATCH,
                        cs.MCPToolName.SYNC_GRAPH_UPDATES,
                    ],
                },
                {
                    "name": "validation_and_tests",
                    "tools": [
                        cs.MCPToolName.TEST_GENERATE,
                        cs.MCPToolName.TEST_QUALITY_GATE,
                        cs.MCPToolName.EXECUTION_FEEDBACK,
                        cs.MCPToolName.VALIDATE_DONE_DECISION,
                    ],
                },
            ],
        }

    def _is_tool_visible_for_session(self, tool_name: str) -> tuple[bool, str]:
        visible_names = self._visible_tool_names(allow_phase_bypass=True)
        return tool_name in visible_names, self._tool_tier(tool_name)

    def _tool_stage_name(self, tool_name: str) -> str:
        stages = self._staged_tool_visibility_contract().get("stages")
        if not isinstance(stages, list):
            return "always_available"
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            stage_payload = cast(dict[str, object], stage)
            tools = stage_payload.get("tools", [])
            if isinstance(tools, list) and tool_name in tools:
                return str(stage_payload.get("name", "unknown"))
        return "always_available"

    def _has_graph_read_prerequisite(self) -> bool:
        graph_evidence_count = self._coerce_int(
            self._session_state.get("graph_evidence_count", 0)
        )
        return graph_evidence_count > 0 and self._has_graph_query_digest()

    def _has_repo_evidence_for_external_docs(self) -> bool:
        graph_evidence_count = self._coerce_int(
            self._session_state.get("graph_evidence_count", 0)
        )
        code_evidence_count = self._coerce_int(
            self._session_state.get("code_evidence_count", 0)
        )
        semantic_success_count = self._coerce_int(
            self._session_state.get("semantic_success_count", 0)
        )
        return (
            graph_evidence_count > 0
            or code_evidence_count > 0
            or semantic_success_count > 0
        )

    def get_visibility_gate_payload(
        self,
        tool_name: str,
        arguments: dict[str, object] | None,
    ) -> dict[str, object] | None:
        visible_names = self._visible_tool_names()
        if tool_name in visible_names or self._is_preflight_exempt_tool(tool_name):
            return None

        args = arguments if isinstance(arguments, dict) else {}
        query_text = self._extract_gate_query_text(tool_name, args)
        exact_next_calls: list[dict[str, object]] = []

        if tool_name in {
            cs.MCPToolName.RUN_CYPHER,
            cs.MCPToolName.CONTEXT7_DOCS,
            cs.MCPToolName.READ_FILE,
            cs.MCPToolName.GET_CODE_SNIPPET,
            cs.MCPToolName.GET_FUNCTION_SOURCE,
        }:
            bootstrap_query = query_text or (
                "Locate the relevant symbol, file, and dependencies for this task"
            )
            if tool_name == cs.MCPToolName.CONTEXT7_DOCS:
                library_name = str(args.get(cs.MCPParamName.LIBRARY, "")).strip()
                docs_query = str(args.get(cs.MCPParamName.QUERY, "")).strip()
                if library_name:
                    bootstrap_query = f"Find files, imports, modules, and symbols that use {library_name}."
                    if docs_query:
                        bootstrap_query += f" Focus on: {docs_query}"
            escaped_query = bootstrap_query.replace("\\", "\\\\").replace('"', '\\"')
            exact_next_calls.append(
                {
                    "tool": cs.MCPToolName.QUERY_CODE_GRAPH,
                    "args": {
                        "natural_language_query": bootstrap_query,
                        "output_format": "json",
                    },
                    "priority": 1,
                    "when": "graph-first evidence is missing for this tool",
                    "copy_paste": (
                        "query_code_graph("
                        f'natural_language_query="{escaped_query}", output_format="json")'
                    ),
                    "why": "unlock_graph_evidence_stage",
                }
            )
        elif tool_name in {
            cs.MCPToolName.WRITE_FILE,
            cs.MCPToolName.SURGICAL_REPLACE_CODE,
            cs.MCPToolName.APPLY_DIFF_SAFE,
            cs.MCPToolName.REFACTOR_BATCH,
        }:
            exact_next_calls.extend(
                [
                    {
                        "tool": cs.MCPToolName.IMPACT_GRAPH,
                        "args": {"depth": 3},
                        "priority": 1,
                        "when": "mutation requested before impact evidence",
                        "copy_paste": "impact_graph(depth=3)",
                        "why": "collect_blast_radius_before_edit",
                    },
                    {
                        "tool": cs.MCPToolName.READ_FILE,
                        "args": {},
                        "priority": 2,
                        "when": "after graph evidence narrows implementation target",
                        "copy_paste": "read_file(...)",
                        "why": "confirm_implementation_before_edit",
                    },
                ]
            )
        else:
            exact_next_calls.append(
                {
                    "tool": cs.MCPToolName.PLAN_TASK,
                    "args": {
                        "goal": query_text
                        or f"Prepare deterministic sequence for {tool_name}",
                        "context": "Stage visibility blocked the requested tool. Build the next safe GraphRAG-first sequence.",
                    },
                    "priority": 1,
                    "when": "session stage has not unlocked the requested tool",
                    "copy_paste": (
                        "plan_task("
                        f'goal="{(query_text or f"Prepare deterministic sequence for {tool_name}").replace(chr(34), '\\"')}", '
                        'context="Stage visibility blocked the requested tool. Build the next safe GraphRAG-first sequence.")'
                    ),
                    "why": "re-enter_visible_stage_safely",
                }
            )

        exact_next_calls = self._normalize_exact_next_calls(exact_next_calls)
        return {
            "status": "blocked",
            "gate": "visibility",
            "error": (
                f"session_visibility_blocked: tool '{tool_name}' is not unlocked in the current session stage."
            ),
            "blocked_tool": tool_name,
            "active_project": self._active_project_name(),
            "tool_stage": self._tool_stage_name(tool_name),
            "visible_tools": sorted(visible_names),
            "staged_tool_visibility": self._staged_tool_visibility_contract(),
            "exact_next_calls": exact_next_calls,
            "next_best_action": self._project_next_best_action_from_exact_calls(
                exact_next_calls
            ),
            "session_contract": self._session_state.get("session_contract", {}),
            "ui_summary": (
                f"Tool '{tool_name}' is published for client compatibility, but not yet unlocked. "
                "Follow the recommended graph-first next action."
            ),
        }

    def get_phase_gate_error(self, tool_name: str) -> str | None:
        if tool_name in self._PHASE_EXEMPT_TOOLS:
            return None
        phase = self._current_execution_phase()
        allowed = self._PHASE_ALLOWED_TOOLS.get(phase, set())
        if tool_name in allowed:
            return None
        allowed_text = ", ".join(sorted(allowed))
        return (
            f"phase_guard_blocked: tool '{tool_name}' is not allowed in phase '{phase}'. "
            f"Allowed tools: {allowed_text}."
        )

    _WORKFLOW_GATE_EXEMPT_TOOLS: set[str] = {
        cs.MCPToolName.LIST_PROJECTS,
        cs.MCPToolName.SELECT_ACTIVE_PROJECT,
        cs.MCPToolName.GET_EXECUTION_READINESS,
        cs.MCPToolName.MEMORY_QUERY_PATTERNS,
        cs.MCPToolName.MEMORY_LIST,
        cs.MCPToolName.PLAN_TASK,
    }
    _PLAN_GATE_TOOLS: set[str] = {
        cs.MCPToolName.QUERY_CODE_GRAPH,
        cs.MCPToolName.MULTI_HOP_ANALYSIS,
        cs.MCPToolName.RUN_CYPHER,
        cs.MCPToolName.SEMANTIC_SEARCH,
        cs.MCPToolName.READ_FILE,
        cs.MCPToolName.IMPACT_GRAPH,
        cs.MCPToolName.REFACTOR_BATCH,
        cs.MCPToolName.APPLY_DIFF_SAFE,
    }
    _COMPLEX_TASK_KEYWORDS: tuple[str, ...] = (
        "refactor",
        "multi",
        "dependency",
        "architecture",
        "impact",
        "change",
        "modify",
        "migration",
        "caller",
        "callee",
        "hop",
        "chain",
    )

    @staticmethod
    def _extract_gate_query_text(
        tool_name: str,
        arguments: dict[str, object],
    ) -> str:
        if tool_name == cs.MCPToolName.QUERY_CODE_GRAPH:
            return str(arguments.get("natural_language_query", "")).strip()
        if tool_name == cs.MCPToolName.SEMANTIC_SEARCH:
            return str(arguments.get("query", "")).strip()
        if tool_name == cs.MCPToolName.RUN_CYPHER:
            return str(arguments.get("cypher", "")).strip()
        if tool_name == cs.MCPToolName.READ_FILE:
            return str(arguments.get("file_path", "")).strip()
        return ""

    @classmethod
    def _is_complex_task(cls, query_text: str) -> bool:
        normalized = query_text.strip().lower()
        if len(normalized) >= 140:
            return True
        return any(keyword in normalized for keyword in cls._COMPLEX_TASK_KEYWORDS)

    def get_workflow_gate_payload(
        self,
        tool_name: str,
        arguments: dict[str, object] | None,
    ) -> dict[str, object] | None:
        if tool_name in self._WORKFLOW_GATE_EXEMPT_TOOLS:
            return None
        args = arguments if isinstance(arguments, dict) else {}
        query_text = self._extract_gate_query_text(tool_name, args)
        escaped_query = query_text.replace("\\", "\\\\").replace('"', '\\"')

        if (
            bool(settings.MCP_READ_FILE_REQUIRES_QUERY_GRAPH)
            and tool_name == cs.MCPToolName.READ_FILE
            and not self._has_graph_read_prerequisite()
        ):
            exact_next_calls: list[dict[str, object]] = [
                {
                    "tool": cs.MCPToolName.QUERY_CODE_GRAPH,
                    "args": {
                        "natural_language_query": (
                            f"Locate implementation context for file: {args.get('file_path', '')}"
                        ),
                        "output_format": "json",
                    },
                    "priority": 1,
                    "when": "read_file requested without graph-first evidence and digest",
                    "copy_paste": (
                        "query_code_graph("
                        f'natural_language_query="Locate implementation context for file: {str(args.get("file_path", "")).replace("\\", "\\\\").replace('"', '\\"')}", '
                        'output_format="json")'
                    ),
                    "why": "graph_first_for_read_file",
                },
                {
                    "tool": cs.MCPToolName.READ_FILE,
                    "args": dict(args),
                    "priority": 2,
                    "when": "after query_code_graph returns evidence",
                    "copy_paste": "read_file(...)",
                    "why": "implementation_verification_after_graph_evidence",
                },
            ]
            return {
                "status": "blocked",
                "gate": "workflow",
                "error": (
                    "graph_read_prerequisite_required: run query_code_graph, multi_hop_analysis, "
                    "or run_cypher after graph-first flow before read_file."
                ),
                "blocked_tool": tool_name,
                "active_project": self._active_project_name(),
                "exact_next_calls": exact_next_calls,
                "next_best_action": self._project_next_best_action_from_exact_calls(
                    exact_next_calls
                ),
                "session_contract": self._session_state.get("session_contract", {}),
                "ui_summary": "workflow_gate_blocked: run query_code_graph before read_file.",
            }

        if (
            tool_name == cs.MCPToolName.CONTEXT7_DOCS
            and not self._has_repo_evidence_for_external_docs()
        ):
            library = str(args.get("library", "")).strip()
            query = str(args.get("query", "")).strip()
            repo_query = (
                f"How is {library} used in this repository? {query}".strip()
                if library
                else (
                    query
                    or "Find the relevant repo evidence before external documentation lookup"
                )
            )
            escaped_repo_query = repo_query.replace("\\", "\\\\").replace('"', '\\"')
            exact_next_calls = [
                {
                    "tool": cs.MCPToolName.QUERY_CODE_GRAPH,
                    "args": {
                        "natural_language_query": repo_query,
                        "output_format": "json",
                    },
                    "priority": 1,
                    "when": "Context7 requested before repository evidence exists",
                    "copy_paste": (
                        "query_code_graph("
                        f'natural_language_query="{escaped_repo_query}", output_format="json")'
                    ),
                    "why": "repo_evidence_first_for_external_docs",
                },
                {
                    "tool": cs.MCPToolName.CONTEXT7_DOCS,
                    "args": dict(args),
                    "priority": 2,
                    "when": "after repository evidence confirms the external library gap",
                    "copy_paste": "context7_docs(...)",
                    "why": "resume_external_doc_enrichment",
                },
            ]
            return {
                "status": "blocked",
                "gate": "workflow",
                "error": (
                    "context7_repo_evidence_required: collect repository evidence before "
                    "querying external documentation."
                ),
                "blocked_tool": tool_name,
                "active_project": self._active_project_name(),
                "exact_next_calls": exact_next_calls,
                "next_best_action": self._project_next_best_action_from_exact_calls(
                    exact_next_calls
                ),
                "session_contract": self._session_state.get("session_contract", {}),
                "ui_summary": "workflow_gate_blocked: collect repo evidence before context7_docs.",
            }

        if bool(settings.MCP_ENFORCE_MEMORY_PRIMING_GATE) and not bool(
            self._session_state.get("memory_primed", False)
        ):
            memory_query = query_text or "session bootstrap patterns"
            exact_next_calls = [
                {
                    "tool": cs.MCPToolName.MEMORY_QUERY_PATTERNS,
                    "args": {
                        "query": memory_query,
                        "success_only": True,
                        "limit": 8,
                    },
                    "priority": 1,
                    "when": "memory priming required before non-exempt tools",
                    "copy_paste": (
                        "memory_query_patterns("
                        f'query="{memory_query.replace("\\", "\\\\").replace('"', '\\"')}", '
                        "success_only=true, limit=8)"
                    ),
                    "why": "memory_first_policy",
                },
                {
                    "tool": tool_name,
                    "args": dict(args),
                    "priority": 2,
                    "when": "after memory_query_patterns succeeds",
                    "copy_paste": f"{tool_name}(...)",
                    "why": "resume_original_intent",
                },
            ]
            return {
                "status": "blocked",
                "gate": "workflow",
                "error": cs.MCP_MEMORY_PRIMING_REQUIRED.format(tool_name=tool_name),
                "blocked_tool": tool_name,
                "active_project": self._active_project_name(),
                "exact_next_calls": exact_next_calls,
                "next_best_action": self._project_next_best_action_from_exact_calls(
                    exact_next_calls
                ),
                "session_contract": self._session_state.get("session_contract", {}),
                "ui_summary": "workflow_gate_blocked: run memory_query_patterns first.",
            }

        if (
            bool(settings.MCP_ENFORCE_COMPLEX_PLAN_GATE)
            and tool_name in self._PLAN_GATE_TOOLS
            and not bool(self._session_state.get("plan_task_completed", False))
            and self._is_complex_task(query_text)
        ):
            goal_text = query_text or f"Create plan for tool {tool_name}"
            exact_next_calls = [
                {
                    "tool": cs.MCPToolName.PLAN_TASK,
                    "args": {
                        "goal": goal_text,
                        "context": (
                            "Mandatory complex-task plan gate. "
                            "Prepare graph-first deterministic sequence before execution."
                        ),
                    },
                    "priority": 1,
                    "when": "complex task detected before execution",
                    "copy_paste": (
                        "plan_task("
                        f'goal="{escaped_query}", '
                        'context="Mandatory complex-task plan gate. Prepare graph-first deterministic sequence before execution.")'
                    ),
                    "why": "complex_task_plan_gate",
                },
                {
                    "tool": tool_name,
                    "args": dict(args),
                    "priority": 2,
                    "when": "after plan_task returns deterministic steps",
                    "copy_paste": f"{tool_name}(...)",
                    "why": "resume_original_intent",
                },
            ]
            return {
                "status": "blocked",
                "gate": "workflow",
                "error": cs.MCP_PLAN_TASK_REQUIRED_FOR_COMPLEX_QUERY.format(
                    tool_name=tool_name
                ),
                "blocked_tool": tool_name,
                "active_project": self._active_project_name(),
                "exact_next_calls": exact_next_calls,
                "next_best_action": self._project_next_best_action_from_exact_calls(
                    exact_next_calls
                ),
                "session_contract": self._session_state.get("session_contract", {}),
                "ui_summary": "workflow_gate_blocked: run plan_task for complex intent.",
            }

        return None

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
        self._context7_client = Context7Client()
        self._context7_knowledge_store = Context7KnowledgeStore(ingestor)
        self._context7_memory_store = Context7MemoryStore(project_root)
        self._context7_persistence = Context7Persistence(ingestor, project_root)
        self._analysis_evidence = AnalysisEvidenceService(project_root)
        self._repo_semantic_enricher = RepoSemanticEnricher()

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
            require_project_name_param=bool(settings.MCP_REQUIRE_PROJECT_NAME_PARAM),
        )
        self._session_state: dict[str, object] = {
            "orchestrator_prompt": self._orchestrator_prompt,
            "client_profile": str(self._DEFAULT_CLIENT_PROFILE),
            "preflight_project_selected": False,
            "preflight_schema_summary_loaded": False,
            "preflight_schema_summary_rows": 0,
            "preflight_schema_context": "",
            "plan_task_completed": False,
            "auto_plan_attempted": False,
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
            "query_result_chunks": [],
            "file_depth_sum": 0.0,
            "file_depth_count": 0,
            "fallback_exploration": self._default_fallback_exploration_state(),
            "last_graph_result_digest": "",
            "last_graph_query_digest_id": "",
            "query_code_graph_success_count": 0,
            "memory_primed": False,
            "plan_task_count": 0,
            "graph_query_attempt_count": 0,
            "session_contract": {},
            "execution_phase": "preflight",
            "execution_phase_history": [
                {
                    "from": "none",
                    "to": "preflight",
                    "reason": "session_initialized",
                    "timestamp": int(time.time()),
                }
            ],
            "last_phase_transition_allowed": True,
            "last_phase_transition_error": "",
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
            "graph_dirty": False,
            "last_graph_sync_status": "not_needed",
            "last_graph_sync_timestamp": 0,
            "last_graph_sync_error": "",
            "last_graph_sync_paths": [],
            "last_mutation_paths": [],
            "last_analysis_bundle": {},
            "last_architecture_bundle": {},
            "last_change_bundle": {},
            "last_impact_bundle": {},
            "last_multi_hop_bundle": {},
            "last_risk_bundle": {},
            "last_test_bundle": {},
            "last_test_selection": {},
            "repo_semantics": {},
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
        self._json_output_parser = JSONOutputParser()

    @staticmethod
    def _is_preflight_exempt_tool(tool_name: str) -> bool:
        exempt_tools = {
            cs.MCPToolName.LIST_PROJECTS,
            cs.MCPToolName.SELECT_ACTIVE_PROJECT,
        }
        return tool_name in exempt_tools

    def get_preflight_gate_error(self, tool_name: str) -> str | None:
        if not bool(settings.MCP_REQUIRE_SESSION_PREFLIGHT):
            return None
        if self._is_preflight_exempt_tool(tool_name):
            return None

        project_selected = bool(
            self._session_state.get("preflight_project_selected", False)
        )
        if not project_selected:
            return (
                "session_preflight_required: call select_active_project first. "
                "Project scope and schema preflight must be initialized before analysis tools."
            )

        schema_loaded = bool(
            self._session_state.get("preflight_schema_summary_loaded", False)
        )
        if not schema_loaded:
            return (
                "session_preflight_required: schema summary preflight is missing. "
                "Re-run select_active_project to initialize project-scoped schema context."
            )
        return None

    def build_gate_guidance_payload(
        self,
        *,
        tool_name: str,
        gate_error: str,
        gate_type: str,
    ) -> dict[str, object]:
        repo_root = str(Path(self.project_root).resolve())
        project_name = self._active_project_name()

        def _esc(value: str) -> str:
            return value.replace("\\", "\\\\").replace('"', '\\"')

        exact_next_calls: list[dict[str, object]] = []
        if gate_type == "preflight":
            project_selected = bool(
                self._session_state.get("preflight_project_selected", False)
            )
            schema_loaded = bool(
                self._session_state.get("preflight_schema_summary_loaded", False)
            )

            if not project_selected:
                exact_next_calls = [
                    {
                        "tool": cs.MCPToolName.LIST_PROJECTS,
                        "args": {},
                        "priority": 1,
                        "when": "session is new or active project unknown",
                        "copy_paste": "list_projects()",
                        "why": "discover_available_projects",
                    },
                    {
                        "tool": cs.MCPToolName.SELECT_ACTIVE_PROJECT,
                        "args": {"repo_path": repo_root},
                        "priority": 2,
                        "when": "after list_projects confirms target",
                        "copy_paste": (
                            f'select_active_project(repo_path="{_esc(repo_root)}")'
                        ),
                        "why": "initialize_project_scope_and_schema_preflight",
                    },
                ]
            elif not schema_loaded:
                exact_next_calls = [
                    {
                        "tool": cs.MCPToolName.SELECT_ACTIVE_PROJECT,
                        "args": {"repo_path": repo_root},
                        "priority": 1,
                        "when": "project selected but schema preflight missing",
                        "copy_paste": (
                            f'select_active_project(repo_path="{_esc(repo_root)}")'
                        ),
                        "why": "rebuild_schema_preflight_context",
                    }
                ]
            else:
                exact_next_calls = [
                    {
                        "tool": cs.MCPToolName.SELECT_ACTIVE_PROJECT,
                        "args": {"repo_path": repo_root},
                        "priority": 1,
                        "when": "preflight guard blocked unexpectedly",
                        "copy_paste": (
                            f'select_active_project(repo_path="{_esc(repo_root)}")'
                        ),
                        "why": "refresh_preflight_state",
                    }
                ]
        else:
            phase = self._current_execution_phase()
            allowed = sorted(self._PHASE_ALLOWED_TOOLS.get(phase, set()))
            fallback_tool = (
                cs.MCPToolName.GET_EXECUTION_READINESS
                if cs.MCPToolName.GET_EXECUTION_READINESS in allowed
                else (allowed[0] if allowed else cs.MCPToolName.SELECT_ACTIVE_PROJECT)
            )
            fallback_args: dict[str, object] = {}
            fallback_copy = f"{fallback_tool}()"
            if fallback_tool == cs.MCPToolName.SELECT_ACTIVE_PROJECT:
                fallback_args = {"repo_path": repo_root}
                fallback_copy = f'select_active_project(repo_path="{_esc(repo_root)}")'
            exact_next_calls = [
                {
                    "tool": fallback_tool,
                    "args": fallback_args,
                    "priority": 1,
                    "when": f"tool {tool_name} is blocked in phase {phase}",
                    "copy_paste": fallback_copy,
                    "why": "phase_guard_recovery",
                }
            ]

        next_best_action = self._project_next_best_action_from_exact_calls(
            exact_next_calls
        )
        startup_sequence = [
            cs.MCPToolName.LIST_PROJECTS,
            cs.MCPToolName.SELECT_ACTIVE_PROJECT,
        ]
        return {
            "status": "blocked",
            "gate": gate_type,
            "error": str(gate_error),
            "blocked_tool": tool_name,
            "active_project": project_name,
            "repo_root": repo_root,
            "mandatory_startup_sequence": startup_sequence,
            "exact_next_calls": exact_next_calls,
            "next_best_action": next_best_action,
            "session_contract": self._session_state.get("session_contract", {}),
            "ui_summary": (
                f"{gate_type}_gate_blocked: run mandatory startup sequence "
                "list_projects -> select_active_project before non-exempt tools."
            ),
        }

    @staticmethod
    def _project_scoped_schema_summary_query(project_name: str, limit: int) -> str:
        _ = project_name
        bounded_limit = max(20, min(int(limit), 2000))
        return (
            "MATCH (m:Module {project_name: $project_name}) "
            "WITH collect(DISTINCT m) AS modules "
            "UNWIND modules AS module "
            "OPTIONAL MATCH (module)-[:DEFINES]->(def) "
            "OPTIONAL MATCH (def)-[:DEFINES_METHOD]->(meth) "
            "WITH modules + collect(DISTINCT def) + collect(DISTINCT meth) AS seed_nodes "
            "UNWIND seed_nodes AS n "
            "WITH DISTINCT n WHERE n IS NOT NULL "
            "OPTIONAL MATCH (n)-[out_r]->(out_b) "
            "WITH DISTINCT "
            "  n, "
            "  head(labels(n)) AS n_type, "
            "  type(out_r) AS out_rel, "
            "  head(labels(out_b)) AS out_to "
            "OPTIONAL MATCH (in_a)-[in_r]->(n) "
            "WITH DISTINCT "
            "  [n_type, out_rel, out_to] AS out_triplet, "
            "  [head(labels(in_a)), type(in_r), head(labels(n))] AS in_triplet "
            "UNWIND [out_triplet, in_triplet] AS triplet "
            "WITH DISTINCT triplet WHERE triplet[1] IS NOT NULL "
            "RETURN "
            "  triplet[0] AS from_node_type, "
            "  triplet[1] AS relationship_type, "
            "  triplet[2] AS to_node_type "
            "ORDER BY from_node_type, relationship_type, to_node_type "
            "LIMIT " + str(bounded_limit)
        )

    @staticmethod
    def _schema_summary_markdown(rows: list[dict[str, object]]) -> str:
        header = [
            "| from_node_type | relationship_type | to_node_type |",
            "|----------------|-------------------|--------------|",
        ]
        if not rows:
            return "\n".join(header)

        lines = header.copy()
        for row in rows:
            if not isinstance(row, dict):
                continue
            from_node_type = str(row.get("from_node_type", ""))
            relationship_type = str(row.get("relationship_type", ""))
            to_node_type = str(row.get("to_node_type", ""))
            lines.append(f"| {from_node_type} | {relationship_type} | {to_node_type} |")
        return "\n".join(lines)

    @staticmethod
    def _schema_summary_preview_text(
        rows: list[dict[str, object]],
        max_items: int = 5,
    ) -> str:
        if not rows:
            return "schema preview empty"
        bounded_max = max(1, min(int(max_items), 10))
        snippets: list[str] = []
        for row in rows[:bounded_max]:
            from_node_type = str(row.get("from_node_type", "?"))
            relationship_type = str(row.get("relationship_type", "?"))
            to_node_type = str(row.get("to_node_type", "?"))
            snippets.append(f"{from_node_type}-[{relationship_type}]->{to_node_type}")
        return "; ".join(snippets)

    def _build_schema_context(self, rows: list[dict[str, object]]) -> str:
        if not rows:
            return ""
        max_relations = max(1, int(settings.MCP_SCHEMA_CONTEXT_MAX_RELATIONS))
        bounded_rows = rows[:max_relations]
        snippets = self._schema_summary_preview_text(
            bounded_rows, max_items=max_relations
        )
        project_name = self._active_project_name()
        return (
            f"Active project: {project_name}. Observed schema relationships: {snippets}"
        )

    def _persist_preflight_context(self, preflight: dict[str, object]) -> None:
        summary_rows_raw = preflight.get("results", [])
        summary_rows: list[dict[str, object]] = []
        if isinstance(summary_rows_raw, list):
            for row in summary_rows_raw:
                if isinstance(row, dict):
                    summary_rows.append(cast(dict[str, object], row))
        schema_context = self._build_schema_context(summary_rows)
        self._session_state["preflight_schema_context"] = schema_context
        if schema_context:
            self._memory_store.add_entry(
                text=json.dumps(
                    {
                        "kind": "preflight_schema_context",
                        "project": self._active_project_name(),
                        "rows": self._coerce_int(preflight.get("rows", 0)),
                        "context": schema_context,
                    },
                    ensure_ascii=False,
                ),
                tags=["preflight", "schema", "context", "success"],
            )

    async def _auto_plan_if_needed(self, user_query: str) -> None:
        if not bool(settings.MCP_AUTO_PLAN_ON_FIRST_QUERY):
            return
        if not self._is_complex_task(user_query):
            return
        if bool(self._session_state.get("plan_task_completed", False)):
            return
        if bool(self._session_state.get("auto_plan_attempted", False)):
            return

        self._session_state["auto_plan_attempted"] = True
        schema_context = str(
            self._session_state.get("preflight_schema_context", "")
        ).strip()
        plan_context_parts = [
            "Mandatory flow: query_code_graph -> run_cypher(read-only scoped) -> read_file(last resort)",
            "Use list_directory for atomic discovery before broad semantic fallback.",
        ]
        if schema_context:
            plan_context_parts.append(f"Schema context: {schema_context}")
        await self.plan_task(
            goal=f"Create graph-first retrieval plan for: {user_query[:240]}",
            context="\n".join(plan_context_parts),
        )

    @staticmethod
    def _split_rows_into_chunks(
        rows: list[dict[str, object]],
        chunk_size: int = 25,
    ) -> list[list[dict[str, object]]]:
        bounded_chunk_size = max(1, int(chunk_size))
        return [
            rows[idx : idx + bounded_chunk_size]
            for idx in range(0, len(rows), bounded_chunk_size)
        ]

    def _cap_query_results(
        self,
        rows: list[dict[str, object]],
    ) -> tuple[list[dict[str, object]], bool, int]:
        max_rows = max(1, int(settings.MCP_QUERY_RESULT_MAX_ROWS))
        max_chars = max(2000, int(settings.MCP_QUERY_RESULT_MAX_CHARS))
        capped_by_rows = rows[:max_rows]
        capped_serialized = json.dumps(capped_by_rows, ensure_ascii=False)
        if len(capped_serialized) <= max_chars:
            return capped_by_rows, len(rows) > len(capped_by_rows), len(rows)

        trimmed_rows: list[dict[str, object]] = []
        current_chars = 2
        for row in capped_by_rows:
            row_chars = len(json.dumps(row, ensure_ascii=False))
            projected = current_chars + row_chars + (1 if trimmed_rows else 0)
            if projected > max_chars:
                break
            trimmed_rows.append(row)
            current_chars = projected
        return trimmed_rows, len(rows) > len(trimmed_rows), len(rows)

    @staticmethod
    def _build_graph_result_digest(
        rows: list[dict[str, object]], max_items: int = 10
    ) -> str:
        if not rows:
            return ""
        snippets: list[str] = []
        for row in rows[: max(1, min(int(max_items), 20))]:
            name = str(row.get("qualified_name") or row.get("name") or "unknown")
            label_values = row.get("type", [])
            if isinstance(label_values, list):
                label_text = "/".join(
                    str(item) for item in label_values if str(item).strip()
                )
            else:
                label_text = str(label_values)
            snippets.append(f"{label_text}:{name}")
        return "; ".join(snippets)

    async def _run_session_schema_preflight(
        self, project_name: str
    ) -> dict[str, object]:
        schema_query = self._project_scoped_schema_summary_query(
            project_name=project_name,
            limit=int(settings.MCP_PREFLIGHT_SCHEMA_SUMMARY_LIMIT),
        )
        # ── Block _auto_plan_if_needed during preflight ─────────────────────
        # _auto_plan_if_needed triggers plan_task → LLM API call (up to
        # MCP_AGENT_TIMEOUT_SECONDS = 300 s by default).  That inner timeout
        # cannot be reliably interrupted by the outer wait_for(35 s) wrapper
        # because pydantic-ai's agent.run() may not propagate CancelledError.
        # Temporarily mark the flag so the guard in _auto_plan_if_needed fires
        # and the LLM call is skipped entirely during preflight execution.
        # The flag is restored to False afterwards so the first real user query
        # still triggers auto-planning as intended.
        _auto_plan_was_attempted = bool(
            self._session_state.get("auto_plan_attempted", False)
        )
        self._session_state["auto_plan_attempted"] = True
        preserved_graph_evidence_count = self._coerce_int(
            self._session_state.get("graph_evidence_count", 0)
        )
        preserved_query_success_count = self._coerce_int(
            self._session_state.get("query_success_count", 0)
        )
        preserved_graph_digest = str(
            self._session_state.get("last_graph_result_digest", "")
        )
        preserved_graph_query_digest_id = str(
            self._session_state.get("last_graph_query_digest_id", "")
        )
        try:
            cypher_result = await self.run_cypher(
                cypher=schema_query,
                params="{}",
                write=False,
                user_requested=False,
                reason="session_preflight_schema_summary",
            )
        finally:
            # Restore the original value — if it was already True (e.g. from a
            # prior plan call) keep it; otherwise reset so auto-plan still fires
            # on the first genuine query after select_active_project returns.
            if not _auto_plan_was_attempted:
                self._session_state["auto_plan_attempted"] = False
            self._session_state["graph_evidence_count"] = preserved_graph_evidence_count
            self._session_state["query_success_count"] = preserved_query_success_count
            self._session_state["last_graph_result_digest"] = preserved_graph_digest
            self._session_state["last_graph_query_digest_id"] = (
                preserved_graph_query_digest_id
            )
        # ── End auto-plan guard ──────────────────────────────────────────────
        if not isinstance(cypher_result, dict):
            self._session_state["preflight_schema_summary_loaded"] = False
            self._session_state["preflight_schema_summary_rows"] = 0
            return {
                "status": "error",
                "error": "schema_preflight_failed",
                "rows": 0,
                "preview_row_count": 0,
                "schema_summary_preview": [],
                "schema_summary_json": {"schema_summary": []},
                "schema_summary_markdown": self._schema_summary_markdown([]),
            }

        if "error" in cypher_result:
            self._session_state["preflight_schema_summary_loaded"] = False
            self._session_state["preflight_schema_summary_rows"] = 0
            return {
                "status": "error",
                "error": str(cypher_result.get("error", "schema_preflight_failed")),
                "rows": 0,
                "preview_row_count": 0,
                "schema_summary_preview": [],
                "schema_summary_json": {"schema_summary": []},
                "schema_summary_markdown": self._schema_summary_markdown([]),
            }

        raw_results = cypher_result.get("results", [])
        summary_rows: list[dict[str, object]] = []
        if isinstance(raw_results, list):
            for row in raw_results:
                if isinstance(row, dict):
                    summary_rows.append(cast(dict[str, object], row))
        rows = len(summary_rows)
        if rows <= 0:
            self._session_state["preflight_schema_summary_loaded"] = False
            self._session_state["preflight_schema_summary_rows"] = 0
            return {
                "status": "error",
                "error": "schema_preflight_empty",
                "rows": 0,
                "preview_row_count": 0,
                "schema_summary_preview": [],
                "query": schema_query,
                "results": [],
                "schema_summary_json": {"schema_summary": []},
                "schema_summary_markdown": self._schema_summary_markdown([]),
            }

        self._session_state["preflight_schema_summary_loaded"] = True
        self._session_state["preflight_schema_summary_rows"] = rows
        preview_limit = max(
            1, min(int(settings.MCP_PREFLIGHT_SCHEMA_PREVIEW_ROWS), 200)
        )
        preview_rows = summary_rows[:preview_limit]
        schema_summary_json: dict[str, object] = {
            "schema_summary": preview_rows,
            "total_rows": rows,
            "truncated": rows > len(preview_rows),
        }
        schema_summary_markdown = self._schema_summary_markdown(preview_rows)
        return {
            "status": "ok",
            "rows": rows,
            "preview_row_count": len(preview_rows),
            "schema_summary_preview": preview_rows,
            "query": schema_query,
            "results": summary_rows,
            "schema_summary_json": schema_summary_json,
            "schema_summary_markdown": schema_summary_markdown,
        }

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
        self._context7_memory_store = Context7MemoryStore(resolved_repo_str)
        self._context7_persistence = Context7Persistence(
            self.ingestor, resolved_repo_str
        )
        self._session_state["preflight_project_selected"] = False
        self._session_state["preflight_schema_summary_loaded"] = False
        self._session_state["preflight_schema_summary_rows"] = 0
        self._session_state["preflight_schema_context"] = ""
        self._session_state["plan_task_completed"] = False
        self._session_state["auto_plan_attempted"] = False
        self._session_state["query_result_chunks"] = []
        self._session_state["file_depth_sum"] = 0.0
        self._session_state["file_depth_count"] = 0
        self._session_state["fallback_exploration"] = (
            self._default_fallback_exploration_state()
        )
        self._session_state["last_graph_result_digest"] = ""
        self._session_state["last_graph_query_digest_id"] = ""
        self._session_state["query_code_graph_success_count"] = 0
        self._session_state["memory_primed"] = False
        self._session_state["session_contract"] = {}
        self._session_state["execution_phase"] = "preflight"
        self._session_state["execution_phase_history"] = [
            {
                "from": "none",
                "to": "preflight",
                "reason": "project_root_reset",
                "timestamp": int(time.time()),
            }
        ]
        self._session_state["last_phase_transition_allowed"] = True
        self._session_state["last_phase_transition_error"] = ""
        self._session_state["repo_semantics"] = {}
        self._session_state["last_analysis_bundle"] = {}
        self._session_state["last_architecture_bundle"] = {}
        self._session_state["last_change_bundle"] = {}
        self._session_state["last_impact_bundle"] = {}
        self._session_state["last_multi_hop_bundle"] = {}
        self._session_state["last_risk_bundle"] = {}
        self._session_state["last_test_bundle"] = {}
        self._session_state["last_test_selection"] = {}
        self._refresh_internal_agents()
        return resolved_repo

    def _build_session_contract(
        self, project_name: str, client_profile: str | None = None
    ) -> dict[str, object]:
        resolved_client_profile = self.set_client_profile(client_profile)
        profile_config = self._client_profile_config()
        max_tool_chain_steps = self._orchestrator_max_tool_chain_steps()
        repo_semantics = self._session_state.get("repo_semantics", {})
        if not isinstance(repo_semantics, dict):
            repo_semantics = {}
        orchestrator_policy = {
            "version": "2026-03-10",
            "published_on_first_call": "select_active_project",
            "client_profile": resolved_client_profile,
            "tool_tiering": {
                "visible_tiers": sorted(self._ORCHESTRATOR_VISIBLE_TIERS),
                "tier_map": dict(self._TOOL_TIER_MAP),
                "enforcement": "auto_execution_strict_visibility",
            },
            "staged_tool_visibility": self._staged_tool_visibility_contract(),
            "registry_domains": {
                domain: list(tool_names)
                for domain, tool_names in _TOOL_DOMAIN_GROUPS.items()
            },
            "tool_chain_guard": {
                "max_steps": max_tool_chain_steps,
                "base_flow_steps": [
                    "execution_feedback",
                    "sync_graph_updates",
                    "detect_project_drift(optional)",
                    "validate_done_decision",
                ],
                "auto_next_budget": "max_steps - base_flow_steps",
                "overflow_behavior": "truncate_exact_next_calls_and_report",
            },
            "client_profile_policy": {
                "response_mode": str(profile_config.get("response_mode", "balanced")),
                "summary_style": str(profile_config.get("summary_style", "balanced")),
                "planner_contract": str(
                    profile_config.get("planner_contract", "standard")
                ),
                "preferred_auto_next": bool(
                    profile_config.get("preferred_auto_next", False)
                ),
            },
            "exploration_policy": {
                "strategy": "epsilon_greedy",
                "adaptation": "reward_latency_failure_history",
                "policy_level_optimization": "ucb_style_chain_scoring",
                "base_epsilon": self._EXPLORATION_BASE_EPSILON,
                "epsilon_bounds": [
                    self._EXPLORATION_MIN_EPSILON,
                    self._EXPLORATION_MAX_EPSILON,
                ],
                "allowed_failure_types": sorted(
                    self._EXPLORATION_ALLOWED_FAILURE_TYPES
                ),
                "safety_constraints": {
                    "disable_on_policy_block": True,
                    "disable_on_bad_query": True,
                    "allowed_tools": [
                        cs.MCPToolName.RUN_CYPHER,
                        cs.MCPToolName.SEMANTIC_SEARCH,
                    ],
                },
            },
            "guard_model": {
                "hard_guards": [
                    "preflight_gate",
                    "phase_gate",
                    "scope_gate",
                    "write_safety_gate",
                    "tool_chain_guard",
                    "state_machine_gate",
                ],
                "soft_guards": [
                    "confidence_gate",
                    "context_confidence_gate",
                    "pattern_reuse_gate",
                    "completion_gate",
                    "test_quality_gate",
                    "impact_graph_gate",
                    "replan_gate",
                ],
                "hard_guard_behavior": "must_pass_for_execution_safety",
                "soft_guard_behavior": "advisory_or_done_decision_blocking",
                "context_confidence_model": {
                    "name": "context_confidence_v1",
                    "required_score": 0.6,
                    "signals": {
                        "graph_density": 0.35,
                        "semantic_overlap": 0.30,
                        "file_depth": 0.20,
                        "memory_match": 0.15,
                        "exploration_calibration": "dynamic",
                    },
                },
            },
            "execution_state": self._build_execution_state_contract(),
            "state_machine": self._state_machine_contract(),
        }
        return {
            "active_project": project_name,
            "client_profile": resolved_client_profile,
            "default_flow": [
                "list_projects",
                "select_active_project",
                "query_code_graph",
                "plan_task(for multi-step or backlog-driven work)",
                "run_cypher(advanced_mode=false, only after graph evidence)",
                "read_file(only with graph query digest id)",
            ],
            "mandatory_startup_sequence": [
                "list_projects",
                "select_active_project",
            ],
            "startup_playbook": [
                {
                    "priority": 1,
                    "tool": cs.MCPToolName.LIST_PROJECTS,
                    "why": "discover indexed projects before any scoped retrieval",
                },
                {
                    "priority": 2,
                    "tool": cs.MCPToolName.SELECT_ACTIVE_PROJECT,
                    "why": "lock active project and publish session/tool policy",
                },
            ],
            "tool_preference_policy": {
                "graph_rag_first": True,
                "prefer_tools": [
                    cs.MCPToolName.QUERY_CODE_GRAPH,
                    cs.MCPToolName.MULTI_HOP_ANALYSIS,
                    cs.MCPToolName.SEMANTIC_SEARCH,
                    cs.MCPToolName.PLAN_TASK,
                    cs.MCPToolName.GET_EXECUTION_READINESS,
                    cs.MCPToolName.TEST_GENERATE,
                ],
                "defer_tools": [
                    cs.MCPToolName.READ_FILE,
                    cs.MCPToolName.GET_FUNCTION_SOURCE,
                ],
                "guidance": [
                    "Use GraphRAG discovery tools before direct file reads whenever possible.",
                    "For architecture, dependency-chain, or blast-radius questions, prefer multi_hop_analysis before deep file inspection.",
                    "Use plan_task for multi-step work so downstream tools like test_generate become natural next steps.",
                    "Use read_file only after graph or semantic evidence narrows the target.",
                    "After successful source edits, refresh the graph before trusting GraphRAG answers for changed code.",
                    "Use context7_docs only when repository evidence is insufficient and external library behavior matters.",
                ],
            },
            "tool_choice_heuristics": {
                "relationship_questions": [
                    cs.MCPToolName.QUERY_CODE_GRAPH,
                    cs.MCPToolName.MULTI_HOP_ANALYSIS,
                    cs.MCPToolName.RUN_CYPHER,
                ],
                "implementation_questions": [
                    cs.MCPToolName.QUERY_CODE_GRAPH,
                    cs.MCPToolName.READ_FILE,
                ],
                "external_library_questions": [
                    cs.MCPToolName.QUERY_CODE_GRAPH,
                    cs.MCPToolName.MULTI_HOP_ANALYSIS,
                    cs.MCPToolName.CONTEXT7_DOCS,
                ],
                "edit_flow": [
                    cs.MCPToolName.IMPACT_GRAPH,
                    cs.MCPToolName.APPLY_DIFF_SAFE,
                    cs.MCPToolName.SYNC_GRAPH_UPDATES,
                    cs.MCPToolName.VALIDATE_DONE_DECISION,
                ],
            },
            "response_profiles": {
                "query_code_graph": (
                    "short summary + cypher + compact details"
                    if resolved_client_profile == cs.MCPClientProfile.OLLAMA
                    else "summary + cypher + compact details"
                ),
                "analysis_bundles": "normalized artifacts + trusted_findings + session evidence + exact_next_calls",
                "multi_hop_analysis": (
                    "ultra-compressed evidence bundle + exact next calls"
                    if resolved_client_profile == cs.MCPClientProfile.OLLAMA
                    else "compressed evidence bundle + recommended reads + exact next calls"
                ),
                "test_generate": {
                    "default_output_mode": (
                        "plan_json"
                        if resolved_client_profile == cs.MCPClientProfile.OLLAMA
                        else "code"
                    ),
                    "supported_output_modes": ["code", "plan_json", "both"],
                },
                "run_cypher": (
                    "minimal json with readable cypher and params"
                    if resolved_client_profile == cs.MCPClientProfile.HTTP
                    else "compact json with readable cypher and params"
                ),
                "context7_docs": (
                    "summary + minimal doc excerpts + persistence source"
                    if resolved_client_profile == cs.MCPClientProfile.OLLAMA
                    else "summary + doc excerpts + persistence source"
                ),
            },
            "graph_sync_policy": {
                "after_edits": "sync_graph_updates",
                "default_mode": "fast",
                "consistency_mode": "full",
                "readiness_gate": "graph_sync_gate",
            },
            "client_profile_policy": {
                "response_mode": str(profile_config.get("response_mode", "balanced")),
                "summary_style": str(profile_config.get("summary_style", "balanced")),
                "planner_contract": str(
                    profile_config.get("planner_contract", "standard")
                ),
                "tool_chain_max_steps": max_tool_chain_steps,
            },
            "context7_policy": {
                "usage": "external_library_gap_only",
                "prerequisite": "repo_evidence_first",
                "preferred_after": [
                    cs.MCPToolName.QUERY_CODE_GRAPH,
                    cs.MCPToolName.MULTI_HOP_ANALYSIS,
                ],
            },
            "scope_rules": {
                "preferred": "MATCH (m:Module {project_name: $project_name}) ...",
                "params": {"project_name": project_name},
                "literal_allowed": f"MATCH (m:Module {{project_name: '{project_name}'}}) ...",
            },
            "query_skeletons": {
                "single_hop": (
                    "MATCH (m:Module {project_name: $project_name})-[:CALLS]->(target) "
                    "RETURN m.name AS source, target.name AS target LIMIT 50"
                ),
                "schema_map": (
                    "MATCH (c:Class {project_name: $project_name, path: $file_path}) "
                    "RETURN c.name AS name LIMIT 100"
                ),
            },
            "repo_semantics": repo_semantics,
            "evidence_plane": {
                "artifacts": "output/analysis normalized by analysis evidence service",
                "resources": [
                    resource.get("uri", "")
                    for resource in self._analysis_evidence.list_resources()[:12]
                ],
                "prompts": [
                    prompt.get("name", "")
                    for prompt in self._analysis_evidence.list_prompts()
                ],
                "bundle_entrypoints": [
                    cs.MCPToolName.ANALYSIS_BUNDLE_FOR_GOAL,
                    cs.MCPToolName.ARCHITECTURE_BUNDLE,
                    cs.MCPToolName.CHANGE_BUNDLE,
                    cs.MCPToolName.RISK_BUNDLE,
                    cs.MCPToolName.TEST_BUNDLE,
                ],
                "default_rule": "prefer bundles/resources before raw artifact retrieval",
            },
            "state_machine": self._state_machine_contract(),
            "orchestrator_policy": orchestrator_policy,
            "staged_tool_visibility": self._staged_tool_visibility_contract(),
        }

    @staticmethod
    def _mint_query_digest_id(query_text: str, row_count: int) -> str:
        token = abs(hash((query_text, row_count, int(time.time() * 1000)))) % 1_000_000
        return f"qd_{int(time.time() * 1000)}_{token}"

    def _planner_usage_rate(self) -> float:
        plan_task_count = self._coerce_int(
            self._session_state.get("plan_task_count", 0)
        )
        graph_query_attempt_count = self._coerce_int(
            self._session_state.get("graph_query_attempt_count", 0)
        )
        if graph_query_attempt_count <= 0:
            return 0.0
        return round(plan_task_count / graph_query_attempt_count, 3)

    def _has_graph_query_digest(self) -> bool:
        digest_id = str(
            self._session_state.get("last_graph_query_digest_id", "")
        ).strip()
        return bool(digest_id)

    @staticmethod
    def _build_exact_next_query_graph_call(cypher_query: str) -> dict[str, object]:
        query_excerpt = " ".join(cypher_query.strip().split())[:300]
        suggested_nl_query = (
            "Convert this Cypher intent into graph evidence and return matching rows: "
            f"{query_excerpt}"
        )
        escaped_query = suggested_nl_query.replace('"', '\\"')
        return {
            "tool": "query_code_graph",
            "args": {
                "natural_language_query": suggested_nl_query,
                "output_format": "json",
            },
            "copy_paste": (
                "query_code_graph("
                f'natural_language_query="{escaped_query}", '
                'output_format="json")'
            ),
        }

    def _build_exact_next_call_for_policy_error(
        self,
        *,
        policy_error: str,
        cypher_query: str,
        parsed_params: dict[str, object],
        write: bool,
        advanced_mode: bool,
    ) -> dict[str, object]:
        project_name = self._active_project_name()
        error_text = str(policy_error or "")
        repo_root = str(Path(self.project_root).resolve())

        def _esc(value: str) -> str:
            return value.replace("\\", "\\\\").replace('"', '\\"')

        if "session_preflight_required" in error_text:
            return {
                "tool": "select_active_project",
                "args": {"repo_path": repo_root},
                "copy_paste": (f'select_active_project(repo_path="{_esc(repo_root)}")'),
                "why": "preflight_required",
            }

        if (
            "run_cypher rejected. Query must be explicitly scoped" in error_text
            or "run_cypher rejected. Query must use parameterized project scope"
            in error_text
            or "run_cypher rejected. Provided $project_name parameter" in error_text
        ):
            normalized_query, normalized_params, _ = self._normalize_run_cypher_scope(
                cypher_query,
                parsed_params,
            )
            params_text = json.dumps(normalized_params, ensure_ascii=False)
            escaped_query = _esc(normalized_query)
            escaped_params = params_text.replace("'", "\\'")
            return {
                "tool": "run_cypher",
                "args": {
                    "cypher": normalized_query,
                    "params": params_text,
                    "write": write,
                    "advanced_mode": True,
                },
                "copy_paste": (
                    f'run_cypher(cypher="{escaped_query}", '
                    f"params='{escaped_params}', write={str(write).lower()}, advanced_mode=true)"
                ),
                "why": "scope_or_param_mismatch",
            }

        if "run_cypher write rejected" in error_text:
            return {
                "tool": "plan_task",
                "args": {
                    "goal": "Prepare safe graph write plan for run_cypher",
                    "context": (
                        f"Policy error: {error_text}\n"
                        f"Target project: {project_name}\n"
                        "Generate a safe read-validate-write sequence with scope and impact checks."
                    ),
                },
                "copy_paste": (
                    "plan_task("
                    'goal="Prepare safe graph write plan for run_cypher", '
                    f'context="Policy error: {_esc(error_text)}")'
                ),
                "why": "write_policy_violation",
            }

        if not advanced_mode:
            return self._build_exact_next_query_graph_call(cypher_query)

        return {
            "tool": "query_code_graph",
            "args": {
                "natural_language_query": "Show project-scoped graph evidence for this failed run_cypher intent",
                "output_format": "json",
            },
            "copy_paste": (
                "query_code_graph("
                'natural_language_query="Show project-scoped graph evidence for this failed run_cypher intent", '
                'output_format="json")'
            ),
            "why": "generic_policy_error",
        }

    def _build_exact_next_calls_chain(
        self,
        *,
        policy_error: str,
        cypher_query: str,
        parsed_params: dict[str, object],
        write: bool,
        advanced_mode: bool,
    ) -> list[dict[str, object]]:
        project_name = self._active_project_name()
        error_text = str(policy_error or "")

        def _annotate(
            item: dict[str, object],
            *,
            priority: int,
            when: str,
        ) -> dict[str, object]:
            enriched = dict(item)
            enriched["priority"] = int(priority)
            enriched["when"] = when
            return enriched

        if "session_preflight_required" in error_text:
            primary_when = (
                "if preflight is missing or schema summary is not initialized"
            )
        elif "run_cypher write rejected" in error_text:
            primary_when = "if write policy blocks run_cypher"
        elif "run_cypher rejected" in error_text:
            primary_when = "if project scope or project_name parameter is invalid"
        elif "run_cypher_advanced_mode_required" in error_text:
            primary_when = "if graph-first default flow blocks direct run_cypher"
        else:
            primary_when = "if run_cypher policy/gating error occurs"

        primary = self._build_exact_next_call_for_policy_error(
            policy_error=policy_error,
            cypher_query=cypher_query,
            parsed_params=parsed_params,
            write=write,
            advanced_mode=advanced_mode,
        )
        primary = _annotate(primary, priority=1, when=primary_when)

        fallback_query_graph = self._build_exact_next_query_graph_call(cypher_query)
        fallback_query_graph = _annotate(
            fallback_query_graph,
            priority=2,
            when="if primary action fails or returns insufficient graph evidence",
        )
        fallback_run_cypher_advanced = {
            "tool": "run_cypher",
            "args": {
                "cypher": cypher_query,
                "params": json.dumps(parsed_params, ensure_ascii=False),
                "write": write,
                "advanced_mode": True,
            },
            "copy_paste": (
                "run_cypher("
                f'cypher="{cypher_query.replace("\\", "\\\\").replace('"', '\\"')}", '
                f"params='{json.dumps(parsed_params, ensure_ascii=False).replace("'", "\\'")}', "
                f"write={str(write).lower()}, advanced_mode=true)"
            ),
            "why": "expert_override_fallback",
        }
        fallback_run_cypher_advanced = _annotate(
            fallback_run_cypher_advanced,
            priority=3,
            when="if expert override is explicitly desired after graph evidence",
        )
        fallback_plan = {
            "tool": "plan_task",
            "args": {
                "goal": "Recover from run_cypher policy/gating error",
                "context": (
                    f"Policy/gating error: {error_text}\n"
                    f"Project: {project_name}\n"
                    "Create a safe graph-first sequence with query_code_graph and scoped run_cypher."
                ),
            },
            "copy_paste": (
                "plan_task("
                'goal="Recover from run_cypher policy/gating error", '
                f'context="Policy/gating error: {error_text.replace('"', '\\"')}")'
            ),
            "why": "planner_recovery_fallback",
        }
        fallback_plan = _annotate(
            fallback_plan,
            priority=3,
            when="if repeated failures occur and a guided plan is needed",
        )

        candidates = [primary]
        if "session_preflight_required" in error_text:
            candidates.extend([fallback_query_graph, fallback_plan])
        elif "run_cypher write rejected" in error_text:
            candidates.extend([fallback_query_graph, fallback_run_cypher_advanced])
        elif "run_cypher rejected" in error_text:
            candidates.extend([fallback_query_graph, fallback_plan])
        elif "run_cypher_advanced_mode_required" in error_text:
            candidates.extend([fallback_query_graph, fallback_run_cypher_advanced])
        else:
            candidates.extend([fallback_query_graph, fallback_plan])

        deduped: list[dict[str, object]] = []
        seen: set[str] = set()
        for item in candidates:
            if not isinstance(item, dict):
                continue
            signature = str(item.get("copy_paste", "")).strip()
            if not signature:
                signature = json.dumps(item.get("args", {}), ensure_ascii=False)
            if signature in seen:
                continue
            seen.add(signature)
            deduped.append(item)
        deduped.sort(
            key=lambda row: (
                self._coerce_int(row.get("priority", 99)),
                str(row.get("tool", "")),
            )
        )
        return deduped

    @staticmethod
    def _project_next_best_action_from_exact_calls(
        exact_next_calls: list[dict[str, object]],
    ) -> dict[str, object]:
        if not exact_next_calls:
            return {}
        first = exact_next_calls[0]
        if not isinstance(first, dict):
            return {}
        tool_name = str(first.get("tool", "")).strip()
        if not tool_name:
            return {}
        args = first.get("args", {})
        params_hint = args if isinstance(args, dict) else {}
        return {
            "action": "execute_exact_next_call",
            "tool": tool_name,
            "params_hint": params_hint,
            "priority": MCPToolsRegistry._coerce_int(first.get("priority", 1)),
            "when": str(first.get("when", "")).strip(),
            "copy_paste": str(first.get("copy_paste", "")).strip(),
            "why": str(first.get("why", "")).strip(),
        }

    def _build_policy_guidance_payload(
        self,
        *,
        policy_error: str,
        cypher_query: str,
        parsed_params: dict[str, object],
        write: bool,
        advanced_mode: bool,
    ) -> dict[str, object]:
        schema_context = str(
            self._session_state.get("preflight_schema_context", "")
        ).strip()
        session_contract = self._session_state.get("session_contract", {})
        exact_next_call = self._build_exact_next_call_for_policy_error(
            policy_error=policy_error,
            cypher_query=cypher_query,
            parsed_params=parsed_params,
            write=write,
            advanced_mode=advanced_mode,
        )
        exact_next_calls = self._build_exact_next_calls_chain(
            policy_error=policy_error,
            cypher_query=cypher_query,
            parsed_params=parsed_params,
            write=write,
            advanced_mode=advanced_mode,
        )
        next_best_action = self._project_next_best_action_from_exact_calls(
            exact_next_calls
        )
        return {
            "exact_next_call": exact_next_call,
            "exact_next_calls": exact_next_calls,
            "next_best_action": next_best_action,
            "schema_context": schema_context,
            "session_contract": session_contract,
            "planner_usage_rate": self._planner_usage_rate(),
        }

    @staticmethod
    def _normalize_sync_mode(sync_mode: str | None) -> str:
        normalized = str(sync_mode or "fast").strip().lower()
        if normalized not in {"fast", "full"}:
            return "fast"
        return normalized

    def _build_select_active_project_next_calls(
        self,
        *,
        project_name: str,
        project_root: str,
        active_indexed: bool,
    ) -> list[dict[str, object]]:
        repo_root_escaped = project_root.replace("\\", "\\\\").replace('"', '\\"')
        if not active_indexed:
            return [
                {
                    "tool": cs.MCPToolName.SELECT_ACTIVE_PROJECT,
                    "args": {"repo_path": project_root},
                    "priority": 1,
                    "when": "active project must stay pinned for this session",
                    "copy_paste": (
                        f'select_active_project(repo_path="{repo_root_escaped}")'
                    ),
                    "why": "confirm_active_project_scope",
                }
            ]

        return [
            {
                "tool": cs.MCPToolName.QUERY_CODE_GRAPH,
                "args": {
                    "natural_language_query": (
                        f"Summarize the main modules, entry points, and dependency hotspots in {project_name}"
                    ),
                    "output_format": "json",
                },
                "priority": 1,
                "when": "first scoped GraphRAG exploration after project selection",
                "copy_paste": (
                    "query_code_graph("
                    f'natural_language_query="Summarize the main modules, entry points, and dependency hotspots in {project_name}", '
                    'output_format="json")'
                ),
                "why": "graph_first_bootstrap",
            },
            {
                "tool": cs.MCPToolName.PLAN_TASK,
                "args": {
                    "goal": f"Create a GraphRAG-first exploration plan for {project_name}",
                    "context": (
                        "Use select_active_project policy, prefer query_code_graph before read_file, "
                        "and prepare an edit-safe workflow with sync_graph_updates."
                    ),
                },
                "priority": 2,
                "when": "task is multi-step, refactor-oriented, or architecture-heavy",
                "copy_paste": (
                    "plan_task("
                    f'goal="Create a GraphRAG-first exploration plan for {project_name}", '
                    'context="Use select_active_project policy, prefer query_code_graph before read_file, and prepare an edit-safe workflow with sync_graph_updates.")'
                ),
                "why": "planner_first_for_complex_work",
            },
        ]

    def _refresh_internal_agents(self) -> None:
        provider_name = (
            str(settings.active_orchestrator_config.provider).strip().lower()
        )
        profile_name = self._client_profile()
        agent_system_prompt: str | None = self._orchestrator_prompt
        if provider_name == "ollama" or profile_name == cs.MCPClientProfile.OLLAMA:
            from codebase_rag.agents.mcp_prompt_pack import LOCAL_MCP_SYSTEM_PROMPT

            agent_system_prompt = LOCAL_MCP_SYSTEM_PROMPT
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
                system_prompt=agent_system_prompt,
            )
        except Exception as exc:
            logger.warning(lg.MCP_SERVER_TOOL_ERROR.format(name="planner", error=exc))
        try:
            self._test_agent = TestAgent(
                agent_tools,
                system_prompt=agent_system_prompt,
            )
        except Exception as exc:
            logger.warning(lg.MCP_SERVER_TOOL_ERROR.format(name="test", error=exc))
        try:
            self._validator_agent = ValidatorAgent(
                agent_tools,
                system_prompt=agent_system_prompt,
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
        self,
        repo_path: str | None = None,
        client_profile: str | None = None,
    ) -> dict[str, object]:
        try:
            if repo_path and repo_path.strip():
                self._set_project_root(repo_path)
            resolved_client_profile = self.set_client_profile(client_profile)

            project_name = self._active_project_name()
            project_root = str(Path(self.project_root).resolve())

            try:
                indexed_projects = await asyncio.wait_for(
                    asyncio.to_thread(self.ingestor.list_projects),
                    timeout=10.0,
                )
            except Exception:
                indexed_projects = []

            active_indexed = project_name in indexed_projects

            # Warn early if no repo_path was supplied and the current root is not indexed
            if (
                not (repo_path and repo_path.strip())
                and not active_indexed
                and indexed_projects
            ):
                return {
                    "status": "no_active_project",
                    "ui_summary": (
                        f"No active project set (current root '{project_name}' is not indexed). "
                        f"Call select_active_project(repo_path=<path>) with one of the indexed projects: "
                        f"{indexed_projects}"
                    ),
                    "indexed_projects": {
                        "count": len(indexed_projects),
                        "names": indexed_projects,
                    },
                    "hint": "Pass repo_path to select an indexed project.",
                }

            try:
                module_count_result = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.ingestor.fetch_all,
                        "MATCH (m:Module {project_name: $project_name}) RETURN count(m) AS count",
                        {cs.KEY_PROJECT_NAME: project_name},
                    ),
                    timeout=10.0,
                )
            except Exception:
                module_count_result = []

            try:
                class_count_result = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.ingestor.fetch_all,
                        "MATCH (m:Module {project_name: $project_name})-[:DEFINES]->(c:Class) RETURN count(c) AS count",
                        {cs.KEY_PROJECT_NAME: project_name},
                    ),
                    timeout=10.0,
                )
            except Exception:
                class_count_result = []

            try:
                function_count_result = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.ingestor.fetch_all,
                        "MATCH (m:Module {project_name: $project_name})-[:DEFINES|DEFINES_METHOD*0..1]->(f) "
                        "WHERE f:Function OR f:Method RETURN count(DISTINCT f) AS count",
                        {cs.KEY_PROJECT_NAME: project_name},
                    ),
                    timeout=10.0,
                )
            except Exception:
                function_count_result = []

            try:
                latest_report = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.ingestor.fetch_all,
                        CYPHER_GET_LATEST_ANALYSIS_REPORT,
                        {cs.KEY_PROJECT_NAME: project_name},
                    ),
                    timeout=10.0,
                )
            except Exception:
                latest_report = []

            latest_analysis_timestamp = (
                latest_report[0].get("analysis_timestamp") if latest_report else None
            )

            self._session_state["preflight_project_selected"] = True
            self._set_execution_phase("retrieval", "select_active_project_completed")
            try:
                preflight = await asyncio.wait_for(
                    self._run_session_schema_preflight(project_name),
                    timeout=35.0,
                )
            except TimeoutError:
                preflight = {"status": "timeout", "rows": 0}
            self._persist_preflight_context(preflight)
            preflight_rows = self._coerce_int(preflight.get("rows", 0))
            preflight_status = str(preflight.get("status", "unknown"))
            preview_rows_raw = preflight.get("schema_summary_preview", [])
            preview_rows: list[dict[str, object]] = []
            if isinstance(preview_rows_raw, list):
                for item in preview_rows_raw:
                    if isinstance(item, dict):
                        preview_rows.append(cast(dict[str, object], item))
            preview_text = self._schema_summary_preview_text(preview_rows, max_items=5)
            try:
                repo_semantics = self._repo_semantic_enricher.summarize(project_root)
            except Exception as exc:
                repo_semantics = {
                    "summary": f"repo_semantics_unavailable: {exc}",
                    "frameworks": [],
                    "framework_metadata": {},
                    "infra": {},
                }
            self._session_state["repo_semantics"] = repo_semantics
            analysis_resources = await self.list_mcp_resources()
            analysis_prompts = await self.list_mcp_prompts()
            session_contract = self._build_session_contract(
                project_name,
                client_profile=resolved_client_profile,
            )
            self._session_state["session_contract"] = session_contract
            exact_next_calls = self._build_select_active_project_next_calls(
                project_name=project_name,
                project_root=project_root,
                active_indexed=active_indexed,
            )
            next_best_action = self._project_next_best_action_from_exact_calls(
                exact_next_calls
            )
            ui_summary = (
                f"Active project: {project_name} | indexed={active_indexed} | "
                f"preflight={preflight_status} | schema_rows={preflight_rows}\n"
                f"Schema preview: {preview_text}\n"
                f"Recommended next tool: {next_best_action.get('tool', 'query_code_graph')}"
            )

            return {
                "status": "ok",
                "ui_summary": ui_summary,
                "active_project": {
                    "name": project_name,
                    "root": project_root,
                    "indexed": active_indexed,
                    "client_profile": resolved_client_profile,
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
                "session_preflight": preflight,
                "repo_semantics": repo_semantics,
                "analysis_resources": analysis_resources[:12],
                "analysis_prompts": analysis_prompts,
                "analysis_bundle_entrypoints": [
                    cs.MCPToolName.ANALYSIS_BUNDLE_FOR_GOAL,
                    cs.MCPToolName.ARCHITECTURE_BUNDLE,
                    cs.MCPToolName.CHANGE_BUNDLE,
                    cs.MCPToolName.RISK_BUNDLE,
                    cs.MCPToolName.TEST_BUNDLE,
                ],
                "session_contract": session_contract,
                "initial_llm_policy_broadcast": cast(
                    dict[str, object],
                    session_contract.get("orchestrator_policy", {}),
                ),
                "bootstrap_playbook": {
                    "summary": "Startup sequence complete. Stay graph-first.",
                    "mandatory_sequence": ["list_projects", "select_active_project"],
                    "next_focus": (
                        "Use query_code_graph for first scoped exploration; use plan_task when the request is multi-step."
                    ),
                    "edit_rule": (
                        "After source edits, refresh graph state before relying on GraphRAG answers for changed code."
                    ),
                },
                "recommended_next_queries": [
                    f"Summarize the main modules, entry points, and dependency hotspots in {project_name}",
                    f"Which files or symbols in {project_name} have the highest blast radius?",
                    f"Show me the most central classes and functions in {project_name}",
                    "After identifying a target symbol or file, run multi_hop_analysis for compressed dependency traversal.",
                ],
                "exact_next_calls": exact_next_calls,
                "next_best_action": next_best_action,
                "execution_state": self._build_execution_state_contract(),
                "staged_tool_visibility": self._staged_tool_visibility_contract(),
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
                    "context_confidence_gate_enabled": True,
                    "pattern_reuse_gate_enabled": True,
                    "soft_hard_guard_partition_enabled": True,
                    "epsilon_exploration_enabled": True,
                    "adaptive_epsilon_enabled": True,
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

            embedding_rows = self.ingestor.fetch_all(
                """
                MATCH (n {project_name: $project_name})
                WHERE n:Function OR n:Method
                RETURN id(n) AS node_id
                """,
                {cs.KEY_PROJECT_NAME: project_name},
            )
            embedding_node_ids: list[int] = []
            for row in embedding_rows:
                node_id = row.get("node_id")
                if isinstance(node_id, int):
                    embedding_node_ids.append(node_id)

            project_root_rows = self.ingestor.fetch_all(
                "MATCH (p:Project {name: $project_name}) RETURN p.path AS repo_path LIMIT 1",
                {cs.KEY_PROJECT_NAME: project_name},
            )
            repo_path_value = None
            if project_root_rows:
                candidate = project_root_rows[0].get("repo_path")
                if isinstance(candidate, str) and candidate.strip():
                    repo_path_value = candidate

            self.ingestor.delete_project(project_name)

            cleanup_service = CleanupService()
            cleanup_service.delete_project_embeddings(embedding_node_ids)
            if isinstance(repo_path_value, str) and repo_path_value.strip():
                cleanup_service.clear_repo_parser_state(Path(repo_path_value))
            else:
                try:
                    active_root = Path(self.project_root).resolve()
                    if active_root.name == project_name:
                        cleanup_service.clear_repo_parser_state(active_root)
                except Exception:
                    pass

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
            cleanup_service = CleanupService()
            cleanup_service.clear_all_parser_state()
            cleanup_service.wipe_embeddings()
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

            embedding_rows = self.ingestor.fetch_all(
                """
                MATCH (n {project_name: $project_name})
                WHERE n:Function OR n:Method
                RETURN id(n) AS node_id
                """,
                {cs.KEY_PROJECT_NAME: project_name},
            )
            embedding_node_ids: list[int] = []
            for row in embedding_rows:
                node_id = row.get("node_id")
                if isinstance(node_id, int):
                    embedding_node_ids.append(node_id)

            async def _delete_project() -> None:
                await asyncio.to_thread(self.ingestor.delete_project, project_name)

            await self._run_with_retries(
                _delete_project,
                attempts=5,
                base_delay_seconds=0.5,
            )

            cleanup_service = CleanupService()
            cleanup_service.delete_project_embeddings(embedding_node_ids)
            cleanup_service.clear_repo_parser_state(resolved_repo)

            updater = GraphUpdater(
                ingestor=self.ingestor,
                repo_path=resolved_repo,
                parsers=self.parsers,
                queries=self.queries,
                force_full_reparse=True,
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

    def _mark_graph_dirty(self, action: str, file_paths: list[str]) -> None:
        normalized_paths = sorted({path.strip() for path in file_paths if path.strip()})
        self._session_state["graph_dirty"] = True
        self._session_state["last_graph_sync_status"] = "pending"
        self._session_state["last_graph_sync_error"] = ""
        self._session_state["last_graph_sync_paths"] = normalized_paths
        self._session_state["last_graph_sync_action"] = action
        self._session_state["last_mutation_paths"] = normalized_paths

    def _record_graph_sync_result(
        self,
        *,
        status: str,
        error: str | None = None,
        file_paths: list[str] | None = None,
    ) -> None:
        raw_paths = (
            file_paths
            if file_paths is not None
            else self._session_state.get("last_graph_sync_paths", [])
        )
        normalized_paths: list[str] = []
        if isinstance(raw_paths, list):
            normalized_paths = sorted(
                {
                    path.strip()
                    for path in raw_paths
                    if isinstance(path, str) and path.strip()
                }
            )
        success = status == "ok"
        self._session_state["graph_dirty"] = not success
        self._session_state["last_graph_sync_status"] = status
        self._session_state["last_graph_sync_timestamp"] = int(time.time())
        self._session_state["last_graph_sync_error"] = (error or "").strip()
        self._session_state["last_graph_sync_paths"] = normalized_paths

    async def _maybe_auto_sync_graph_after_edit(
        self,
        *,
        action: str,
        file_paths: list[str],
    ) -> dict[str, object]:
        normalized_paths = sorted({path.strip() for path in file_paths if path.strip()})
        self._mark_graph_dirty(action, normalized_paths)

        if not bool(self._session_state.get("preflight_project_selected", False)):
            return {
                "status": "skipped_no_preflight",
                "graph_dirty": True,
                "reason": "preflight_not_initialized",
                "paths": normalized_paths,
            }

        if not bool(settings.MCP_AUTO_SYNC_GRAPH_AFTER_EDITS):
            return {
                "status": "pending_manual_sync",
                "graph_dirty": True,
                "reason": "auto_sync_disabled",
                "paths": normalized_paths,
            }

        sync_reason = f"Auto sync after {action}"
        if normalized_paths:
            sync_reason += ": " + ", ".join(normalized_paths[:5])

        sync_result = await self.sync_graph_updates(
            user_requested=True,
            reason=sync_reason,
            sync_mode="fast",
        )
        if sync_result.get("status") == "ok":
            payload: dict[str, object] = {
                "status": "ok",
                "graph_dirty": False,
                "sync": sync_result,
            }
            if bool(settings.MCP_AUTO_VERIFY_DRIFT_AFTER_EDITS):
                payload["drift"] = await self.detect_project_drift()
            return payload

        error_text = str(sync_result.get("error", "graph_sync_failed"))
        self._record_graph_sync_result(
            status="error",
            error=error_text,
            file_paths=normalized_paths,
        )
        return {
            "status": "error",
            "graph_dirty": True,
            "error": error_text,
            "sync": sync_result,
        }

    @staticmethod
    def _append_graph_sync_status_to_message(
        message: str,
        graph_sync: dict[str, object],
    ) -> str:
        status = str(graph_sync.get("status", "")).strip()
        if not status:
            return message
        if status == "ok":
            return f"{message}\nGraph sync: ok"
        reason = str(
            graph_sync.get("error")
            or graph_sync.get("reason")
            or graph_sync.get("status")
        ).strip()
        return f"{message}\nGraph sync: {status}" + (f" ({reason})" if reason else "")

    async def sync_graph_updates(
        self,
        user_requested: bool,
        reason: str,
        sync_mode: str = "fast",
    ) -> dict[str, object]:
        normalized_sync_mode = self._normalize_sync_mode(sync_mode)
        policy_result = self._policy_engine.validate_operation(
            tool_name=cs.MCPToolName.SYNC_GRAPH_UPDATES,
            params={
                "user_requested": user_requested,
                "reason": reason,
                "sync_mode": normalized_sync_mode,
            },
            context={},
        )
        if not policy_result.allowed:
            self._record_graph_sync_result(
                status="error",
                error=str(policy_result.error),
            )
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
                force_full_reparse=normalized_sync_mode == "full",
            )

            timeout_seconds = max(60.0, float(settings.MCP_SYNC_GRAPH_TIMEOUT_SECONDS))

            async def _run_sync_once() -> None:
                await asyncio.wait_for(
                    asyncio.to_thread(updater.run),
                    timeout=timeout_seconds,
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
                    "sync_mode": normalized_sync_mode,
                },
            )
            self._record_tool_usefulness(
                cs.MCPToolName.SYNC_GRAPH_UPDATES,
                success=True,
                usefulness_score=1.0,
            )
            self._record_graph_sync_result(status="ok")
            return {
                "status": "ok",
                "project": self._active_project_name(),
                "sync_mode": {
                    "requested": normalized_sync_mode,
                    "force_full_reparse": normalized_sync_mode == "full",
                    "git_delta_enabled": config.git_delta_enabled,
                    "selective_update_enabled": config.selective_update_enabled,
                    "incremental_cache_enabled": config.incremental_cache_enabled,
                    "analysis_enabled": config.analysis_enabled,
                },
                "reason": reason.strip(),
            }
        except TimeoutError:
            timeout_seconds = max(60.0, float(settings.MCP_SYNC_GRAPH_TIMEOUT_SECONDS))
            self._record_graph_sync_result(
                status="error",
                error=f"sync_graph_updates_timed_out_after_{int(timeout_seconds)}s",
            )
            self._record_tool_usefulness(
                cs.MCPToolName.SYNC_GRAPH_UPDATES,
                success=False,
                usefulness_score=0.0,
            )
            return {
                "error": f"sync_graph_updates_timed_out_after_{int(timeout_seconds)}s"
            }
        except Exception as exc:
            self._record_graph_sync_result(status="error", error=str(exc))
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
        if not tool_name:
            return {"executed": False, "reason": "missing_tool_name"}

        visible, tier = self._is_tool_visible_for_session(tool_name)
        if not visible:
            return {
                "executed": False,
                "reason": "tool_not_visible_in_current_session_stage",
                "tool": tool_name,
                "tier": tier,
                "visible_tools": sorted(self._visible_tool_names()),
            }

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

        if tool_name == cs.MCPToolName.MULTI_HOP_ANALYSIS:
            qualified_name = params_hint_dict.get("qualified_name")
            file_path = params_hint_dict.get("file_path")
            if not isinstance(qualified_name, str):
                qualified_name = None
            if not isinstance(file_path, str):
                file_path = None
            result = await self.multi_hop_analysis(
                qualified_name=qualified_name,
                file_path=file_path,
                depth=self._coerce_int(params_hint_dict.get("depth", 3), default=3),
                limit=self._coerce_int(params_hint_dict.get("limit", 80), default=80),
                include_context7=bool(params_hint_dict.get("include_context7", False)),
                context7_query=cast(str | None, params_hint_dict.get("context7_query")),
            )
            return {"executed": True, "tool": tool_name, "result": result}

        if tool_name == cs.MCPToolName.SELECT_ACTIVE_PROJECT:
            repo_path = params_hint_dict.get("repo_path")
            if not isinstance(repo_path, str):
                repo_path = None
            result = await self.select_active_project(repo_path=repo_path)
            return {"executed": True, "tool": tool_name, "result": result}

        if tool_name == cs.MCPToolName.SYNC_GRAPH_UPDATES:
            reason = str(params_hint_dict.get("reason", "")).strip()
            if not reason:
                reason = "refresh graph after code edits"
            result = await self.sync_graph_updates(
                user_requested=bool(params_hint_dict.get("user_requested", True)),
                reason=reason,
                sync_mode=str(params_hint_dict.get("sync_mode", "fast")),
            )
            return {"executed": True, "tool": tool_name, "result": result}

        if tool_name == cs.MCPToolName.RUN_CYPHER:
            cypher = str(params_hint_dict.get("cypher", "")).strip()
            if not cypher:
                return {"executed": False, "reason": "missing_cypher"}

            params_value = params_hint_dict.get("params")
            params_text: str | None = None
            if isinstance(params_value, str):
                params_text = params_value
            elif isinstance(params_value, dict):
                params_text = json.dumps(params_value, ensure_ascii=False)

            reason = params_hint_dict.get("reason")
            if not isinstance(reason, str):
                reason = None

            result = await self.run_cypher(
                cypher=cypher,
                params=params_text,
                write=bool(params_hint_dict.get("write", False)),
                user_requested=bool(params_hint_dict.get("user_requested", False)),
                reason=reason,
                advanced_mode=bool(params_hint_dict.get("advanced_mode", False)),
            )
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

        if tool_name == cs.MCPToolName.TEST_GENERATE:
            goal = str(params_hint_dict.get("goal", "")).strip()
            if not goal:
                return {"executed": False, "reason": "missing_goal"}
            context = params_hint_dict.get("context")
            if not isinstance(context, str):
                context = None
            result = await self.test_generate(
                goal=goal,
                context=context,
                output_mode=str(params_hint_dict.get("output_mode", "code")),
            )
            return {"executed": True, "tool": tool_name, "result": result}

        if tool_name == cs.MCPToolName.CONTEXT7_DOCS:
            library = str(params_hint_dict.get("library", "")).strip()
            query = str(params_hint_dict.get("query", "")).strip()
            if not library:
                return {"executed": False, "reason": "missing_library"}
            if not query:
                return {"executed": False, "reason": "missing_query"}
            result = await self.context7_docs(
                library=library,
                query=query,
                version=cast(str | None, params_hint_dict.get("version")),
            )
            return {"executed": True, "tool": tool_name, "result": result}

        if tool_name == cs.MCPToolName.TEST_QUALITY_GATE:
            result = await self.test_quality_gate(
                coverage=str(params_hint_dict.get("coverage", "0")),
                edge_cases=str(params_hint_dict.get("edge_cases", "0")),
                negative_tests=str(params_hint_dict.get("negative_tests", "0")),
                repo_evidence=cast(str | None, params_hint_dict.get("repo_evidence")),
                layer_correctness=cast(
                    str | None, params_hint_dict.get("layer_correctness")
                ),
                cleanup_safety=cast(str | None, params_hint_dict.get("cleanup_safety")),
                anti_hallucination=cast(
                    str | None, params_hint_dict.get("anti_hallucination")
                ),
                implementation_coupling_penalty=cast(
                    str | None,
                    params_hint_dict.get("implementation_coupling_penalty"),
                ),
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

    def _normalize_exact_next_calls(
        self,
        exact_next_calls: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        normalized: list[dict[str, object]] = []
        for raw_item in exact_next_calls:
            if not isinstance(raw_item, dict):
                continue
            tool_name = str(raw_item.get("tool", "")).strip()
            if not tool_name:
                continue
            args = raw_item.get("args", {})
            if not isinstance(args, dict):
                args = {}
            normalized.append(
                {
                    "tool": tool_name,
                    "args": cast(dict[str, object], args),
                    "priority": self._coerce_int(raw_item.get("priority", 99), 99),
                    "when": str(raw_item.get("when", "")).strip(),
                    "copy_paste": str(raw_item.get("copy_paste", "")).strip(),
                    "why": str(raw_item.get("why", "")).strip(),
                }
            )

        normalized.sort(
            key=lambda row: (
                self._coerce_int(row.get("priority", 99), 99),
                str(row.get("tool", "")),
            )
        )
        return normalized

    async def _auto_execute_exact_next_calls(
        self,
        exact_next_calls: list[dict[str, object]],
        *,
        max_candidates: int | None = None,
    ) -> dict[str, object]:
        ordered_calls = self._normalize_exact_next_calls(exact_next_calls)
        if not ordered_calls:
            return {
                "executed": False,
                "mode": "exact_next_calls",
                "reason": "no_exact_next_calls",
            }

        max_tool_chain_steps = self._orchestrator_max_tool_chain_steps()
        candidate_limit = max_tool_chain_steps
        if max_candidates is not None:
            candidate_limit = max(1, int(max_candidates))
        candidate_limit = max(
            1,
            min(candidate_limit, max_tool_chain_steps),
        )
        truncated = len(ordered_calls) > candidate_limit
        bounded_calls = ordered_calls[:candidate_limit]

        attempts: list[dict[str, object]] = []
        for item in bounded_calls:
            tool_name = str(item.get("tool", "")).strip()
            args = item.get("args", {})
            if not isinstance(args, dict):
                args = {}

            step_result = await self._auto_execute_next_best_action(
                {
                    "tool": tool_name,
                    "params_hint": cast(dict[str, object], args),
                }
            )
            executed = bool(step_result.get("executed", False))
            attempts.append(
                {
                    "tool": tool_name,
                    "priority": self._coerce_int(item.get("priority", 99), 99),
                    "when": str(item.get("when", "")),
                    "executed": executed,
                    "reason": str(step_result.get("reason", "")),
                }
            )
            if executed:
                return {
                    "executed": True,
                    "mode": "exact_next_calls",
                    "selected": item,
                    "result": step_result.get("result", {}),
                    "attempts": attempts,
                    "candidate_limit": candidate_limit,
                    "total_candidates": len(ordered_calls),
                    "truncated": truncated,
                }

        return {
            "executed": False,
            "mode": "exact_next_calls",
            "reason": "no_supported_exact_next_call_executed",
            "attempts": attempts,
            "candidate_limit": candidate_limit,
            "total_candidates": len(ordered_calls),
            "truncated": truncated,
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
        self._set_execution_phase("execution", "orchestrate_realtime_flow_start")
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
                sync_mode="fast",
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

        max_tool_chain_steps = self._orchestrator_max_tool_chain_steps()
        base_tool_chain_steps = 3 + (1 if verify_drift_effective else 0)
        remaining_tool_chain_budget = max(
            0,
            max_tool_chain_steps - base_tool_chain_steps,
        )

        auto_next_result: dict[str, object] | None = None
        auto_stage_name = "skip_auto_execute_next_best_action"
        if auto_execute_next_effective and isinstance(done_result, dict):
            done_result_dict = cast(dict[str, object], done_result)
            auto_stage: dict[str, object] | None = None
            if remaining_tool_chain_budget <= 0:
                auto_next_result = {
                    "executed": False,
                    "reason": "max_tool_chain_guard_reached",
                    "max_tool_chain_steps": max_tool_chain_steps,
                    "base_tool_chain_steps": base_tool_chain_steps,
                }
            else:
                raw_exact_next_calls = done_result_dict.get("exact_next_calls", [])
                if isinstance(raw_exact_next_calls, list) and raw_exact_next_calls:
                    auto_stage_name = "auto_execute_exact_next_calls"
                    auto_stage = await self._run_orchestrate_stage_with_retry(
                        stage=auto_stage_name,
                        operation=lambda: self._auto_execute_exact_next_calls(
                            cast(list[dict[str, object]], raw_exact_next_calls),
                            max_candidates=remaining_tool_chain_budget,
                        ),
                        attempts=max(
                            1, int(settings.MCP_ORCHESTRATE_AUTO_NEXT_RETRY_ATTEMPTS)
                        ),
                        base_delay_seconds=max(
                            0.01,
                            float(settings.MCP_ORCHESTRATE_RETRY_BASE_DELAY_SECONDS),
                        ),
                    )
                else:
                    raw_next_best_action = done_result_dict.get("next_best_action", {})
                    if isinstance(raw_next_best_action, dict):
                        auto_stage_name = "auto_execute_next_best_action"
                        next_best_action = cast(dict[str, object], raw_next_best_action)
                        auto_stage = await self._run_orchestrate_stage_with_retry(
                            stage=auto_stage_name,
                            operation=lambda: self._auto_execute_next_best_action(
                                next_best_action
                            ),
                            attempts=max(
                                1,
                                int(settings.MCP_ORCHESTRATE_AUTO_NEXT_RETRY_ATTEMPTS),
                            ),
                            base_delay_seconds=max(
                                0.01,
                                float(
                                    settings.MCP_ORCHESTRATE_RETRY_BASE_DELAY_SECONDS
                                ),
                            ),
                        )

            if auto_stage is not None:
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
        flow_steps = [
            "execution_feedback",
            "sync_graph_updates",
            (
                "detect_project_drift"
                if verify_drift_effective
                else "skip_detect_project_drift"
            ),
            "validate_done_decision",
            (
                auto_stage_name
                if auto_execute_next_effective
                else "skip_auto_execute_next_best_action"
            ),
        ]
        self._memory_store.add_entry(
            text=json.dumps(
                {
                    "kind": "successful_tool_chain",
                    "action": action,
                    "result": result,
                    "tool_history": flow_steps,
                    "auto_next": auto_next_result or {},
                    "timestamp": int(time.time()),
                },
                ensure_ascii=False,
            ),
            tags=["pattern", "chain", "orchestrate", "success"],
        )
        top_ui_summary = done_ui_summary or "Realtime flow executed"
        return {
            "status": "ok",
            "ui_summary": top_ui_summary,
            "flow": flow_steps,
            "debounce_seconds": bounded_debounce,
            "tool_chain_guard": {
                "max_steps": max_tool_chain_steps,
                "base_steps": base_tool_chain_steps,
                "remaining_for_auto_next": remaining_tool_chain_budget,
            },
            "tool_tiering": {
                "visible_tiers": sorted(self._ORCHESTRATOR_VISIBLE_TIERS),
                "max_tool_chain_steps": max_tool_chain_steps,
            },
            "feedback": feedback_result,
            "sync": sync_result,
            "drift": drift_result,
            "done": done_result,
            "auto_next": auto_next_result,
            "circuit_breaker": circuit_snapshot,
            "execution_state": self._build_execution_state_contract(),
        }

    def _active_project_name(self) -> str:
        return Path(self.project_root).resolve().name

    def _validate_project_scope_policy(
        self, cypher_query: str, parsed_params: dict[str, object] | None = None
    ) -> str | None:
        return self._policy_engine.validate_project_scope_policy(
            cypher_query, parsed_params
        )

    @staticmethod
    def _replace_project_scope_literals(cypher_query: str, project_name: str) -> str:
        updated = cypher_query
        updated = re.sub(
            r"(`?project_name`?\s*(?::|=)\s*['\"])\s*[^'\"]+\s*(['\"])",
            rf"\1{project_name}\2",
            updated,
            flags=re.IGNORECASE,
        )
        updated = re.sub(
            r"(:\s*Project\s*\{[^{}]*`?name`?\s*:\s*['\"])\s*[^'\"]+\s*(['\"][^{}]*\})",
            rf"\1{project_name}\2",
            updated,
            flags=re.IGNORECASE | re.DOTALL,
        )
        return updated

    @staticmethod
    def _parameterize_project_scope_literals(cypher_query: str) -> str:
        updated = cypher_query
        updated = re.sub(
            r"(`?project_name`?\s*(?::|=)\s*)['\"][^'\"]+['\"]",
            r"\1$project_name",
            updated,
            flags=re.IGNORECASE,
        )
        updated = re.sub(
            r"(:\s*Project\s*\{[^{}]*`?name`?\s*:\s*)['\"][^'\"]+['\"]",
            r"\1$project_name",
            updated,
            flags=re.IGNORECASE | re.DOTALL,
        )
        return updated

    def _append_scope_fix_hint(self, project_name: str) -> str:
        if bool(settings.MCP_REQUIRE_PROJECT_NAME_PARAM):
            return (
                "Scope fix hint: use explicit active-project scope with $project_name -> "
                f'MATCH (m:Module {{project_name: $project_name}}) ... params={{"project_name":"{project_name}"}}'
            )
        return (
            "Scope fix hint: use explicit active-project scope with one of these forms -> "
            f'MATCH (m:Module {{project_name: $project_name}}) ... params={{"project_name":"{project_name}"}} '
            f"or MATCH (m:Module {{project_name: '{project_name}'}}) ..."
        )

    def _normalize_run_cypher_scope(
        self,
        cypher_query: str,
        parsed_params: dict[str, object],
    ) -> tuple[str, dict[str, object], list[str]]:
        project_name = self._active_project_name()
        normalized_query = cypher_query
        normalized_params = dict(parsed_params)
        notes: list[str] = []

        replaced_query = self._replace_project_scope_literals(
            normalized_query, project_name
        )
        if replaced_query != normalized_query:
            normalized_query = replaced_query
            notes.append("normalized_project_scope_literal")

        if bool(settings.MCP_REQUIRE_PROJECT_NAME_PARAM):
            parameterized_query = self._parameterize_project_scope_literals(
                normalized_query
            )
            if parameterized_query != normalized_query:
                normalized_query = parameterized_query
                notes.append("parameterized_project_scope_literal")

        if "$project_name" in normalized_query.lower() and (
            not isinstance(normalized_params.get(cs.KEY_PROJECT_NAME), str)
            or str(normalized_params.get(cs.KEY_PROJECT_NAME, "")).strip()
            != project_name
        ):
            normalized_params[cs.KEY_PROJECT_NAME] = project_name
            notes.append("injected_project_name_param")

        return normalized_query, normalized_params, notes

    def _validate_write_allowlist_policy(self, cypher_query: str) -> str | None:
        return self._policy_engine.validate_write_allowlist_policy(cypher_query)

    @staticmethod
    def _build_scoped_query_prompt(
        natural_language_query: str,
        project_name: str,
        previous_cypher: str | None = None,
        previous_error: str | None = None,
        schema_context: str | None = None,
    ) -> str:
        prompt = (
            f"{natural_language_query}\n\n"
            "STRICT PROJECT SCOPE REQUIREMENT:\n"
            f"- Active project: '{project_name}'.\n"
            "- Generated Cypher MUST explicitly include parameterized project scoping with $project_name.\n"
            "- Required form examples:\n"
            "  1) MATCH (p:Project {name: $project_name}) ...\n"
            "  2) MATCH (m:Module {project_name: $project_name}) ...\n"
            "- Never generate a cross-project query.\n"
            "- Generate a SINGLE read-only query block with one final RETURN clause.\n"
            "- Never place MATCH/OPTIONAL MATCH after RETURN.\n"
            "- Prefer path-safe filters with replace(coalesce(x.path, ''), '\\\\', '/').\n"
            "- Return only Cypher query text."
        )
        if previous_cypher:
            prompt += (
                "\n\nPREVIOUS QUERY WAS REJECTED (UNSCOPED):\n"
                f"{previous_cypher}\n"
                "Regenerate with explicit project scope."
            )
        if previous_error:
            prompt += (
                "\n\nPREVIOUS EXECUTION ERROR:\n"
                f"{previous_error}\n"
                "Regenerate a corrected Cypher query that fixes this error while keeping strict project scope."
            )
        if schema_context and schema_context.strip():
            prompt += (
                "\n\nSESSION SCHEMA CONTEXT (use this as guidance):\n"
                f"{schema_context.strip()}"
            )
        return prompt

    async def _generate_project_scoped_cypher(
        self, natural_language_query: str, project_name: str
    ) -> str:
        last_query = ""
        schema_context = str(
            self._session_state.get("preflight_schema_context", "")
        ).strip()
        for _ in range(3):
            scoped_prompt = self._build_scoped_query_prompt(
                natural_language_query=natural_language_query,
                project_name=project_name,
                previous_cypher=last_query if last_query else None,
                schema_context=schema_context if schema_context else None,
            )
            generated_query = await self.cypher_gen.generate(scoped_prompt)
            generated_query, _, _ = self._normalize_run_cypher_scope(
                generated_query,
                {cs.KEY_PROJECT_NAME: project_name},
            )
            last_query = generated_query
            scope_error = self._validate_project_scope_policy(
                generated_query,
                {cs.KEY_PROJECT_NAME: project_name},
            )
            if scope_error is None:
                return generated_query
        raise ValueError(cs.MCP_QUERY_SCOPE_ERROR.format(project_name=project_name))

    async def _regenerate_project_scoped_cypher(
        self,
        natural_language_query: str,
        project_name: str,
        previous_cypher: str,
        previous_error: str,
    ) -> str:
        schema_context = str(
            self._session_state.get("preflight_schema_context", "")
        ).strip()
        scoped_prompt = self._build_scoped_query_prompt(
            natural_language_query=natural_language_query,
            project_name=project_name,
            previous_cypher=previous_cypher,
            previous_error=previous_error,
            schema_context=schema_context if schema_context else None,
        )
        generated_query = await self.cypher_gen.generate(scoped_prompt)
        generated_query, _, _ = self._normalize_run_cypher_scope(
            generated_query,
            {cs.KEY_PROJECT_NAME: project_name},
        )
        scope_error = self._validate_project_scope_policy(
            generated_query,
            {cs.KEY_PROJECT_NAME: project_name},
        )
        if scope_error is not None:
            raise ValueError(cs.MCP_QUERY_SCOPE_ERROR.format(project_name=project_name))
        return generated_query

    @staticmethod
    def _is_parser_focused_query(natural_language_query: str) -> bool:
        lowered = natural_language_query.lower()
        parser_cues = (
            "parser",
            "parsers",
            "codebase_rag.parsers",
            "codebase_rag/parsers",
            "tree-sitter",
        )
        return any(cue in lowered for cue in parser_cues)

    @staticmethod
    def _build_parser_scope_fallback_query(project_name: str) -> str:
        _ = project_name
        return (
            "MATCH (m:Module {project_name: $project_name}) "
            "WHERE replace(coalesce(m.path, ''), '\\\\', '/') CONTAINS '/codebase_rag/parsers' "
            "OPTIONAL MATCH (m)-[:DEFINES]->(d) "
            "OPTIONAL MATCH (d)-[:DEFINES_METHOD]->(meth) "
            "WITH m, d, meth "
            "UNWIND [m, d, meth] AS n "
            "WITH DISTINCT n WHERE n IS NOT NULL "
            "AND (n:Module OR n:Class OR n:Function OR n:Method) "
            "RETURN "
            "  n.name AS name, "
            "  n.qualified_name AS qualified_name, "
            "  labels(n) AS type, "
            "  n.path AS path "
            "ORDER BY coalesce(n.path, ''), coalesce(n.qualified_name, n.name, '') "
            "LIMIT 200"
        )

    @staticmethod
    def _classify_query_failure(
        *,
        failure_hint: str,
        error_text: str,
        result_rows: int,
    ) -> str:
        normalized_hint = failure_hint.strip().lower()
        normalized_error = error_text.strip().lower()

        if result_rows <= 0 and (
            "zero_rows" in normalized_hint
            or "no_data" in normalized_hint
            or "empty" in normalized_hint
        ):
            return "no_data"

        if any(
            marker in normalized_error
            for marker in (
                "scope",
                "policy",
                "advanced_mode_required",
                "phase_guard_blocked",
                "session_preflight_required",
            )
        ):
            return "policy_block"

        if any(
            marker in normalized_error
            for marker in (
                "syntax",
                "invalid",
                "can't be put after return",
                "query execution failed",
            )
        ):
            return "bad_query"

        if "low_confidence" in normalized_hint:
            return "low_confidence"

        if result_rows <= 0:
            return "no_data"
        return "unknown"

    def _adaptive_fallback_order(
        self,
        *,
        natural_language_query: str,
        failure_type: str,
    ) -> list[str]:
        default_order = [cs.MCPToolName.RUN_CYPHER, cs.MCPToolName.SEMANTIC_SEARCH]

        if failure_type in {"bad_query", "policy_block"}:
            return default_order

        lowered_query = natural_language_query.lower()
        semantic_first_cues = (
            "grep",
            "keyword",
            "text",
            "regex",
            "string",
            "search in files",
        )
        if any(cue in lowered_query for cue in semantic_first_cues):
            return [cs.MCPToolName.SEMANTIC_SEARCH, cs.MCPToolName.RUN_CYPHER]

        chain_patterns = self._memory_store.query_patterns(
            query=natural_language_query,
            filter_tags=["pattern", "chain"],
            success_only=True,
            limit=5,
        )
        for entry in chain_patterns:
            chain_signature = str(entry.get("chain_signature", "")).lower()
            if "semantic_search" in chain_signature and "run_cypher" in chain_signature:
                semantic_idx = chain_signature.find("semantic_search")
                run_cypher_idx = chain_signature.find("run_cypher")
                if (
                    semantic_idx >= 0
                    and run_cypher_idx >= 0
                    and semantic_idx < run_cypher_idx
                ):
                    return [cs.MCPToolName.SEMANTIC_SEARCH, cs.MCPToolName.RUN_CYPHER]

        return default_order

    @staticmethod
    def _fallback_chain_signature(tool_order: list[str]) -> str:
        normalized = [
            str(tool).strip().lower() for tool in tool_order if str(tool).strip()
        ]
        return "->".join(normalized)

    @staticmethod
    def _default_fallback_exploration_state() -> dict[str, object]:
        return {
            "calls": 0,
            "explore": 0,
            "exploit": 0,
            "success": 0,
            "failure": 0,
            "consecutive_failures": 0,
            "reward_total": 0.0,
            "latency_ms_total": 0.0,
            "last_mode": "exploit",
            "last_epsilon": 0.0,
            "last_draw": 0.0,
            "chains": {},
            "recent": [],
        }

    @staticmethod
    def _normalize_chain_signature(chain_signature: str) -> str:
        parts = [
            segment.strip().lower()
            for segment in re.split(r"\s*->\s*", str(chain_signature))
            if segment.strip()
        ]
        return "->".join(parts)

    def _ensure_exploration_bucket(self) -> dict[str, object]:
        raw_bucket = self._session_state.get("fallback_exploration")
        if isinstance(raw_bucket, dict):
            return cast(dict[str, object], raw_bucket)
        bucket = self._default_fallback_exploration_state()
        self._session_state["fallback_exploration"] = bucket
        return bucket

    def _build_fallback_chain_candidates(
        self,
        baseline_order: list[str],
    ) -> list[list[str]]:
        candidates = [list(baseline_order)]
        reversed_order = list(reversed(baseline_order))
        if reversed_order != baseline_order:
            candidates.append(reversed_order)
        deduped: list[list[str]] = []
        seen: set[str] = set()
        for order in candidates:
            key = self._fallback_chain_signature(order)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(order)
        return deduped

    def _score_fallback_chain_policy(
        self,
        *,
        tool_order: list[str],
        natural_language_query: str,
    ) -> dict[str, object]:
        chain_key = self._fallback_chain_signature(tool_order)
        bucket = self._ensure_exploration_bucket()
        chains_raw = bucket.get("chains", {})
        chains = (
            cast(dict[str, object], chains_raw) if isinstance(chains_raw, dict) else {}
        )
        chain_bucket_raw = chains.get(chain_key)
        chain_bucket = (
            cast(dict[str, object], chain_bucket_raw)
            if isinstance(chain_bucket_raw, dict)
            else {}
        )

        calls = self._coerce_int(chain_bucket.get("calls", 0))
        success = self._coerce_int(chain_bucket.get("success", 0))
        failure = self._coerce_int(chain_bucket.get("failure", 0))
        reward_total = self._coerce_float(chain_bucket.get("reward_total", 0.0))
        latency_ms_total = self._coerce_float(chain_bucket.get("latency_ms_total", 0.0))

        success_rate = (success / calls) if calls > 0 else 0.5
        avg_reward = (reward_total / calls) if calls > 0 else 0.5
        avg_latency_ms = (latency_ms_total / calls) if calls > 0 else 600.0
        latency_score = max(0.0, min(1.0, 1.0 - (avg_latency_ms / 3000.0)))
        failure_rate = (failure / calls) if calls > 0 else 0.0

        total_calls = max(1, self._coerce_int(bucket.get("calls", 0)))
        exploration_bonus = self._EXPLORATION_POLICY_UCB_BONUS * (
            ((total_calls + 1) ** 0.5) / ((calls + 1) ** 0.5)
        )

        memory_rate = 0.5
        memory_rates = self._memory_store.get_chain_success_rates(
            query=natural_language_query,
            limit=20,
        )
        for item in memory_rates:
            if not isinstance(item, dict):
                continue
            candidate_key = self._normalize_chain_signature(
                str(item.get("chain_signature", ""))
            )
            candidate_trimmed = candidate_key.removeprefix("query_code_graph->")
            if chain_key in (candidate_key, candidate_trimmed):
                memory_rate = self._coerce_float(item.get("success_rate", 0.5), 0.5)
                break

        score = (
            (success_rate * 0.35)
            + (avg_reward * 0.30)
            + (latency_score * 0.15)
            + (memory_rate * 0.20)
            + exploration_bonus
            - (failure_rate * 0.10)
        )
        return {
            "chain": chain_key,
            "calls": calls,
            "success_rate": round(success_rate, 3),
            "avg_reward": round(avg_reward, 3),
            "avg_latency_ms": round(avg_latency_ms, 3),
            "memory_success_rate": round(memory_rate, 3),
            "exploration_bonus": round(exploration_bonus, 3),
            "score": round(score, 3),
        }

    def _compute_exploration_epsilon(self, failure_type: str) -> float:
        override = self._session_state.get("exploration_epsilon_override")
        if override is not None:
            return max(
                self._EXPLORATION_MIN_EPSILON,
                min(self._EXPLORATION_MAX_EPSILON, self._coerce_float(override)),
            )

        epsilon = self._EXPLORATION_BASE_EPSILON
        if failure_type == "no_data":
            epsilon += 0.03
        elif failure_type == "unknown":
            epsilon += 0.02

        summary = self._build_exploration_summary()
        calls = self._coerce_int(summary.get("calls", 0))
        explore_ratio = self._coerce_float(summary.get("explore_ratio", 0.0))
        failure_rate = self._coerce_float(summary.get("failure_rate", 0.0))
        dominant_chain_ratio = self._coerce_float(
            summary.get("dominant_chain_ratio", 0.0)
        )
        consecutive_failures = self._coerce_int(summary.get("consecutive_failures", 0))
        reward_trend = self._coerce_float(summary.get("reward_trend", 0.0))

        epsilon += min(0.12, failure_rate * 0.12)
        if calls >= 8 and explore_ratio < 0.12:
            epsilon += 0.04
        if calls >= 8 and dominant_chain_ratio > 0.75:
            epsilon += 0.05
        if consecutive_failures >= 2:
            epsilon += 0.05
        if reward_trend < -0.05:
            epsilon += 0.04
        if calls >= 30 and failure_rate < 0.2 and reward_trend > 0.02:
            epsilon -= 0.03

        return max(
            self._EXPLORATION_MIN_EPSILON, min(self._EXPLORATION_MAX_EPSILON, epsilon)
        )

    def _is_exploration_safe(
        self,
        *,
        failure_type: str,
        natural_language_query: str,
        baseline_order: list[str],
    ) -> tuple[bool, str]:
        if failure_type not in self._EXPLORATION_ALLOWED_FAILURE_TYPES:
            return False, "failure_type_not_explorable"
        if len(baseline_order) != 2:
            return False, "unsupported_chain_length"
        allowed_tools = {cs.MCPToolName.RUN_CYPHER, cs.MCPToolName.SEMANTIC_SEARCH}
        if not all(tool in allowed_tools for tool in baseline_order):
            return False, "unsupported_tools"
        lowered_query = natural_language_query.lower()
        semantic_first_cues = (
            "grep",
            "keyword",
            "text",
            "regex",
            "string",
            "search in files",
        )
        if any(cue in lowered_query for cue in semantic_first_cues):
            return False, "strong_semantic_cue"
        return True, "eligible"

    def _select_fallback_order_with_exploration(
        self,
        *,
        natural_language_query: str,
        failure_type: str,
        baseline_order: list[str],
    ) -> tuple[list[str], dict[str, object]]:
        override_mode_raw = self._session_state.get("exploration_force_mode")
        override_mode = (
            str(override_mode_raw).strip().lower()
            if override_mode_raw is not None
            else ""
        )
        safety_ok, safety_reason = self._is_exploration_safe(
            failure_type=failure_type,
            natural_language_query=natural_language_query,
            baseline_order=baseline_order,
        )
        candidates = self._build_fallback_chain_candidates(baseline_order)
        policy_scores = [
            self._score_fallback_chain_policy(
                tool_order=order,
                natural_language_query=natural_language_query,
            )
            for order in candidates
        ]
        best_policy = max(
            policy_scores,
            key=lambda item: self._coerce_float(item.get("score", 0.0)),
        )
        best_order_key = str(best_policy.get("chain", ""))
        best_order = next(
            (
                order
                for order in candidates
                if self._fallback_chain_signature(order) == best_order_key
            ),
            baseline_order,
        )
        epsilon = self._compute_exploration_epsilon(failure_type)

        if override_mode in {"off", "exploit"}:
            return best_order, {
                "mode": "exploit",
                "reason": "forced_exploit",
                "epsilon": round(epsilon, 3),
                "draw": 1.0,
                "safety": safety_reason,
                "policy_scores": policy_scores,
                "policy_best_chain": best_order_key,
            }
        if override_mode == "explore" and safety_ok:
            alternative_candidates = [
                order
                for order in candidates
                if self._fallback_chain_signature(order) != best_order_key
            ]
            selected_order = (
                alternative_candidates[0] if alternative_candidates else best_order
            )
            return selected_order, {
                "mode": "explore",
                "reason": "forced_explore",
                "epsilon": round(epsilon, 3),
                "draw": 0.0,
                "safety": safety_reason,
                "policy_scores": policy_scores,
                "policy_best_chain": best_order_key,
            }
        if not safety_ok:
            return baseline_order, {
                "mode": "exploit",
                "reason": "safety_constraint",
                "epsilon": round(epsilon, 3),
                "draw": 1.0,
                "safety": safety_reason,
                "policy_scores": policy_scores,
                "policy_best_chain": best_order_key,
            }

        draw = random.random()
        explore = draw < epsilon
        alternative_candidates = [
            order
            for order in candidates
            if self._fallback_chain_signature(order) != best_order_key
        ]
        selected_order = (
            (alternative_candidates[0] if alternative_candidates else best_order)
            if explore
            else best_order
        )
        return selected_order, {
            "mode": "explore" if explore else "exploit",
            "reason": "epsilon_greedy",
            "epsilon": round(epsilon, 3),
            "draw": round(draw, 6),
            "safety": safety_reason,
            "policy_scores": policy_scores,
            "policy_best_chain": best_order_key,
        }

    def _compute_fallback_reward(
        self,
        *,
        success: bool,
        rows: int,
        latency_ms: float,
        failure_type: str,
        mode: str,
    ) -> float:
        success_component = 1.0 if success else 0.0
        row_component = max(0.0, min(1.0, rows / 10.0))
        latency_component = max(0.0, min(1.0, 1.0 - (latency_ms / 3000.0)))
        failure_penalty = 0.0
        if failure_type in {"bad_query", "policy_block"}:
            failure_penalty = -0.1
        mode_bonus = 0.05 if (mode == "explore" and success) else 0.0
        raw = (
            (success_component * 0.45)
            + (row_component * 0.25)
            + (latency_component * 0.20)
            + mode_bonus
            + failure_penalty
        )
        return max(0.0, min(1.0, raw))

    def _record_fallback_exploration(
        self,
        *,
        mode: str,
        selected_order: list[str],
        baseline_order: list[str],
        epsilon: float,
        draw: float,
        success: bool,
        rows: int,
        latency_ms: float,
        reward: float,
        failure_type: str,
    ) -> None:
        bucket = self._ensure_exploration_bucket()
        bucket["calls"] = self._coerce_int(bucket.get("calls", 0)) + 1
        if mode == "explore":
            bucket["explore"] = self._coerce_int(bucket.get("explore", 0)) + 1
        else:
            bucket["exploit"] = self._coerce_int(bucket.get("exploit", 0)) + 1
        if success:
            bucket["success"] = self._coerce_int(bucket.get("success", 0)) + 1
            bucket["consecutive_failures"] = 0
        else:
            bucket["failure"] = self._coerce_int(bucket.get("failure", 0)) + 1
            bucket["consecutive_failures"] = (
                self._coerce_int(bucket.get("consecutive_failures", 0)) + 1
            )
        bucket["reward_total"] = (
            self._coerce_float(bucket.get("reward_total", 0.0)) + reward
        )
        bucket["latency_ms_total"] = self._coerce_float(
            bucket.get("latency_ms_total", 0.0)
        ) + max(0.0, float(latency_ms))
        bucket["last_mode"] = mode
        bucket["last_epsilon"] = round(epsilon, 3)
        bucket["last_draw"] = round(draw, 6)

        chain_key = self._fallback_chain_signature(selected_order)
        chains_raw = bucket.get("chains", {})
        chains = (
            cast(dict[str, object], chains_raw) if isinstance(chains_raw, dict) else {}
        )
        chain_bucket_raw = chains.get(chain_key)
        chain_bucket = (
            cast(dict[str, object], chain_bucket_raw)
            if isinstance(chain_bucket_raw, dict)
            else {
                "calls": 0,
                "success": 0,
                "failure": 0,
                "rows_total": 0,
                "latency_ms_total": 0.0,
                "reward_total": 0.0,
                "explore": 0,
                "exploit": 0,
            }
        )
        chain_bucket["calls"] = self._coerce_int(chain_bucket.get("calls", 0)) + 1
        if success:
            chain_bucket["success"] = (
                self._coerce_int(chain_bucket.get("success", 0)) + 1
            )
        else:
            chain_bucket["failure"] = (
                self._coerce_int(chain_bucket.get("failure", 0)) + 1
            )
        chain_bucket["rows_total"] = self._coerce_int(
            chain_bucket.get("rows_total", 0)
        ) + max(0, int(rows))
        chain_bucket["latency_ms_total"] = self._coerce_float(
            chain_bucket.get("latency_ms_total", 0.0)
        ) + max(0.0, float(latency_ms))
        chain_bucket["reward_total"] = (
            self._coerce_float(chain_bucket.get("reward_total", 0.0)) + reward
        )
        chain_bucket[mode] = self._coerce_int(chain_bucket.get(mode, 0)) + 1
        chains[chain_key] = chain_bucket
        bucket["chains"] = chains

        recent_raw = bucket.get("recent", [])
        recent = (
            cast(list[dict[str, object]], recent_raw)
            if isinstance(recent_raw, list)
            else []
        )
        recent.insert(
            0,
            {
                "mode": mode,
                "selected_chain": chain_key,
                "baseline_chain": self._fallback_chain_signature(baseline_order),
                "success": bool(success),
                "rows": max(0, int(rows)),
                "failure_type": failure_type,
                "latency_ms": round(max(0.0, float(latency_ms)), 3),
                "reward": round(reward, 3),
                "epsilon": round(epsilon, 3),
                "draw": round(draw, 6),
                "timestamp": int(time.time()),
            },
        )
        bucket["recent"] = recent[:30]
        self._session_state["fallback_exploration"] = bucket

    def _build_exploration_summary(self) -> dict[str, object]:
        bucket = self._ensure_exploration_bucket()
        calls = self._coerce_int(bucket.get("calls", 0))
        explore = self._coerce_int(bucket.get("explore", 0))
        exploit = self._coerce_int(bucket.get("exploit", 0))
        success_total = self._coerce_int(bucket.get("success", 0))
        failure_total = self._coerce_int(bucket.get("failure", 0))
        explore_ratio = (explore / calls) if calls > 0 else 0.0
        failure_rate = (failure_total / calls) if calls > 0 else 0.0
        avg_reward = (
            self._coerce_float(bucket.get("reward_total", 0.0)) / calls
            if calls > 0
            else 0.0
        )
        avg_latency_ms = (
            self._coerce_float(bucket.get("latency_ms_total", 0.0)) / calls
            if calls > 0
            else 0.0
        )
        consecutive_failures = self._coerce_int(bucket.get("consecutive_failures", 0))
        chains_raw = bucket.get("chains", {})
        chains = (
            cast(dict[str, object], chains_raw) if isinstance(chains_raw, dict) else {}
        )
        chain_rows: list[dict[str, object]] = []
        for chain_signature, raw_chain in chains.items():
            if not isinstance(raw_chain, dict):
                continue
            chain_bucket = cast(dict[str, object], raw_chain)
            chain_calls = self._coerce_int(chain_bucket.get("calls", 0))
            chain_success = self._coerce_int(chain_bucket.get("success", 0))
            chain_failure = self._coerce_int(chain_bucket.get("failure", 0))
            chain_success_rate = (
                (chain_success / chain_calls) if chain_calls > 0 else 0.0
            )
            chain_avg_reward = (
                self._coerce_float(chain_bucket.get("reward_total", 0.0)) / chain_calls
                if chain_calls > 0
                else 0.0
            )
            chain_avg_latency = (
                self._coerce_float(chain_bucket.get("latency_ms_total", 0.0))
                / chain_calls
                if chain_calls > 0
                else 0.0
            )
            chain_rows.append(
                {
                    "chain": str(chain_signature),
                    "calls": chain_calls,
                    "success": chain_success,
                    "failure": chain_failure,
                    "success_rate": round(chain_success_rate, 3),
                    "avg_reward": round(chain_avg_reward, 3),
                    "avg_latency_ms": round(chain_avg_latency, 3),
                    "rows_total": self._coerce_int(chain_bucket.get("rows_total", 0)),
                    "explore": self._coerce_int(chain_bucket.get("explore", 0)),
                    "exploit": self._coerce_int(chain_bucket.get("exploit", 0)),
                }
            )
        chain_rows.sort(
            key=lambda row: (
                self._coerce_float(row.get("success_rate", 0.0)),
                self._coerce_int(row.get("calls", 0)),
            ),
            reverse=True,
        )
        dominant_chain_ratio = 0.0
        if calls > 0 and chain_rows:
            dominant_chain_ratio = (
                self._coerce_int(chain_rows[0].get("calls", 0)) / calls
            )

        recent_raw = bucket.get("recent", [])
        recent = (
            cast(list[dict[str, object]], recent_raw)
            if isinstance(recent_raw, list)
            else []
        )
        recent_rewards = [
            self._coerce_float(item.get("reward", 0.0))
            for item in recent
            if isinstance(item, dict)
        ]
        latest_slice = recent_rewards[:10]
        previous_slice = recent_rewards[10:20]
        latest_avg_reward = (
            sum(latest_slice) / len(latest_slice) if latest_slice else avg_reward
        )
        previous_avg_reward = (
            sum(previous_slice) / len(previous_slice)
            if previous_slice
            else latest_avg_reward
        )
        reward_trend = latest_avg_reward - previous_avg_reward
        return {
            "calls": calls,
            "explore": explore,
            "exploit": exploit,
            "success": success_total,
            "failure": failure_total,
            "explore_ratio": round(explore_ratio, 3),
            "failure_rate": round(failure_rate, 3),
            "avg_reward": round(avg_reward, 3),
            "avg_latency_ms": round(avg_latency_ms, 3),
            "latest_avg_reward": round(latest_avg_reward, 3),
            "reward_trend": round(reward_trend, 3),
            "dominant_chain_ratio": round(dominant_chain_ratio, 3),
            "consecutive_failures": consecutive_failures,
            "last_mode": str(bucket.get("last_mode", "exploit")),
            "last_epsilon": round(
                self._coerce_float(bucket.get("last_epsilon", 0.0)), 3
            ),
            "last_draw": round(self._coerce_float(bucket.get("last_draw", 0.0)), 6),
            "chains": chain_rows[:5],
        }

    async def _run_standardized_query_fallback_chain(
        self,
        *,
        natural_language_query: str,
        cypher_query: str,
        failure_hint: str = "query_returned_zero_rows",
        error_text: str = "",
        result_rows: int = 0,
    ) -> dict[str, object]:
        fallback_started_at = time.perf_counter()
        failure_type = self._classify_query_failure(
            failure_hint=failure_hint,
            error_text=error_text,
            result_rows=result_rows,
        )
        baseline_order = self._adaptive_fallback_order(
            natural_language_query=natural_language_query,
            failure_type=failure_type,
        )
        tool_order, exploration_decision = self._select_fallback_order_with_exploration(
            natural_language_query=natural_language_query,
            failure_type=failure_type,
            baseline_order=baseline_order,
        )
        fallback_steps: list[dict[str, object]] = []

        for tool_name in tool_order:
            if tool_name == cs.MCPToolName.RUN_CYPHER:
                step_started_at = time.perf_counter()
                run_cypher_result = await self.run_cypher(
                    cypher=cypher_query,
                    params=json.dumps({}, ensure_ascii=False),
                    write=False,
                    reason="query_code_graph_standardized_fallback",
                    advanced_mode=True,
                )
                step_latency_ms = (time.perf_counter() - step_started_at) * 1000.0
                run_cypher_ok = (
                    isinstance(run_cypher_result, dict)
                    and not run_cypher_result.get("error")
                    and isinstance(run_cypher_result.get("results"), list)
                    and bool(run_cypher_result.get("results"))
                )
                fallback_steps.append(
                    {
                        "tool": cs.MCPToolName.RUN_CYPHER,
                        "executed": True,
                        "success": bool(run_cypher_ok),
                        "latency_ms": round(step_latency_ms, 3),
                        "rows": (
                            len(
                                cast(list[object], run_cypher_result.get("results", []))
                            )
                            if isinstance(run_cypher_result, dict)
                            and isinstance(run_cypher_result.get("results"), list)
                            else 0
                        ),
                    }
                )
                if run_cypher_ok:
                    result_count = (
                        len(cast(list[object], run_cypher_result.get("results", [])))
                        if isinstance(run_cypher_result, dict)
                        and isinstance(run_cypher_result.get("results"), list)
                        else 0
                    )
                    total_latency_ms = (
                        time.perf_counter() - fallback_started_at
                    ) * 1000.0
                    mode = str(exploration_decision.get("mode", "exploit"))
                    reward = self._compute_fallback_reward(
                        success=True,
                        rows=result_count,
                        latency_ms=total_latency_ms,
                        failure_type=failure_type,
                        mode=mode,
                    )
                    self._record_fallback_exploration(
                        mode=mode,
                        selected_order=tool_order,
                        baseline_order=baseline_order,
                        epsilon=self._coerce_float(
                            exploration_decision.get("epsilon", 0.0)
                        ),
                        draw=self._coerce_float(exploration_decision.get("draw", 1.0)),
                        success=True,
                        rows=result_count,
                        latency_ms=total_latency_ms,
                        reward=reward,
                        failure_type=failure_type,
                    )
                    return {
                        "status": "ok",
                        "source": "run_cypher",
                        "results": run_cypher_result.get("results", []),
                        "query_used": cypher_query,
                        "fallback_chain": fallback_steps,
                        "fallback_diagnostics": {
                            "failure_type": failure_type,
                            "baseline_order": baseline_order,
                            "tool_order": tool_order,
                            "selected_chain": self._fallback_chain_signature(
                                tool_order
                            ),
                            "latency_ms": round(total_latency_ms, 3),
                            "reward": round(reward, 3),
                            "exploration": exploration_decision,
                        },
                    }

            if tool_name == cs.MCPToolName.SEMANTIC_SEARCH:
                step_started_at = time.perf_counter()
                semantic_result = await self.semantic_search(
                    query=natural_language_query,
                    top_k=10,
                )
                step_latency_ms = (time.perf_counter() - step_started_at) * 1000.0
                semantic_rows = semantic_result.get("results", [])
                semantic_ok = isinstance(semantic_rows, list) and bool(semantic_rows)
                fallback_steps.append(
                    {
                        "tool": cs.MCPToolName.SEMANTIC_SEARCH,
                        "executed": True,
                        "success": bool(semantic_ok),
                        "latency_ms": round(step_latency_ms, 3),
                        "rows": (
                            len(cast(list[object], semantic_rows))
                            if isinstance(semantic_rows, list)
                            else 0
                        ),
                    }
                )
                if semantic_ok:
                    semantic_normalized: list[dict[str, object]] = []
                    for item in cast(list[object], semantic_rows):
                        if not isinstance(item, dict):
                            continue
                        item_dict = cast(dict[str, object], item)
                        semantic_normalized.append(
                            {
                                "name": item_dict.get("qualified_name")
                                or item_dict.get("symbol")
                                or item_dict.get("name"),
                                "qualified_name": item_dict.get("qualified_name"),
                                "path": item_dict.get("file_path")
                                or item_dict.get("path"),
                                "score": item_dict.get("score", 0.0),
                                "source": "semantic_search",
                            }
                        )
                    total_latency_ms = (
                        time.perf_counter() - fallback_started_at
                    ) * 1000.0
                    mode = str(exploration_decision.get("mode", "exploit"))
                    reward = self._compute_fallback_reward(
                        success=True,
                        rows=len(semantic_normalized),
                        latency_ms=total_latency_ms,
                        failure_type=failure_type,
                        mode=mode,
                    )
                    self._record_fallback_exploration(
                        mode=mode,
                        selected_order=tool_order,
                        baseline_order=baseline_order,
                        epsilon=self._coerce_float(
                            exploration_decision.get("epsilon", 0.0)
                        ),
                        draw=self._coerce_float(exploration_decision.get("draw", 1.0)),
                        success=True,
                        rows=len(semantic_normalized),
                        latency_ms=total_latency_ms,
                        reward=reward,
                        failure_type=failure_type,
                    )
                    return {
                        "status": "ok",
                        "source": "semantic_search",
                        "results": semantic_normalized,
                        "query_used": cypher_query,
                        "fallback_chain": fallback_steps,
                        "fallback_diagnostics": {
                            "failure_type": failure_type,
                            "baseline_order": baseline_order,
                            "tool_order": tool_order,
                            "selected_chain": self._fallback_chain_signature(
                                tool_order
                            ),
                            "latency_ms": round(total_latency_ms, 3),
                            "reward": round(reward, 3),
                            "exploration": exploration_decision,
                        },
                    }

        total_latency_ms = (time.perf_counter() - fallback_started_at) * 1000.0
        mode = str(exploration_decision.get("mode", "exploit"))
        reward = self._compute_fallback_reward(
            success=False,
            rows=0,
            latency_ms=total_latency_ms,
            failure_type=failure_type,
            mode=mode,
        )
        self._record_fallback_exploration(
            mode=mode,
            selected_order=tool_order,
            baseline_order=baseline_order,
            epsilon=self._coerce_float(exploration_decision.get("epsilon", 0.0)),
            draw=self._coerce_float(exploration_decision.get("draw", 1.0)),
            success=False,
            rows=0,
            latency_ms=total_latency_ms,
            reward=reward,
            failure_type=failure_type,
        )
        return {
            "status": "error",
            "source": "none",
            "results": [],
            "query_used": cypher_query,
            "fallback_chain": fallback_steps,
            "fallback_diagnostics": {
                "failure_type": failure_type,
                "baseline_order": baseline_order,
                "tool_order": tool_order,
                "selected_chain": self._fallback_chain_signature(tool_order),
                "latency_ms": round(total_latency_ms, 3),
                "reward": round(reward, 3),
                "exploration": exploration_decision,
            },
        }

    async def query_code_graph(
        self, natural_language_query: str, output_format: str = "json"
    ) -> QueryResultDict | str:
        logger.info(lg.MCP_QUERY_CODE_GRAPH.format(query=natural_language_query))
        try:
            self._set_execution_phase("retrieval", "query_code_graph")
            await self._auto_plan_if_needed(natural_language_query)
            self._session_bump("graph_query_attempt_count")
            project_name = self._active_project_name()
            cypher_query = await self._generate_project_scoped_cypher(
                natural_language_query=natural_language_query,
                project_name=project_name,
            )
            query_params: dict[str, Any] = {cs.KEY_PROJECT_NAME: project_name}

            async def _read_once() -> list[dict[str, Any]]:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        self.ingestor.fetch_all,
                        cypher_query,
                        query_params,
                    ),
                    timeout=60.0,
                )

            results: list[dict[str, Any]] = []
            repaired_attempts = 0
            max_repaired_attempts = 2
            parser_fallback_attempted = False
            deterministic_second_pass_attempted = False
            while True:
                try:
                    results = await self._run_with_retries(
                        _read_once,
                        attempts=3,
                        base_delay_seconds=0.5,
                    )
                except Exception as exec_error:
                    if repaired_attempts >= max_repaired_attempts:
                        raise
                    repaired_attempts += 1
                    cypher_query = await self._regenerate_project_scoped_cypher(
                        natural_language_query=natural_language_query,
                        project_name=project_name,
                        previous_cypher=cypher_query,
                        previous_error=str(exec_error),
                    )
                    continue

                if results:
                    break

                if not deterministic_second_pass_attempted:
                    deterministic_second_pass_attempted = True
                    template_queries = self._build_deterministic_second_pass_queries(
                        natural_language_query=natural_language_query,
                        project_name=project_name,
                    )
                    for template_query in template_queries:
                        try:
                            template_scope_error = self._validate_project_scope_policy(
                                template_query,
                                {cs.KEY_PROJECT_NAME: project_name},
                            )
                            if template_scope_error is not None:
                                continue

                            async def _template_read_once() -> list[dict[str, Any]]:
                                return await asyncio.wait_for(
                                    asyncio.to_thread(
                                        self.ingestor.fetch_all,
                                        template_query,
                                        query_params,
                                    ),
                                    timeout=60.0,
                                )

                            template_results = await self._run_with_retries(
                                _template_read_once,
                                attempts=2,
                                base_delay_seconds=0.3,
                            )
                            if template_results:
                                cypher_query = template_query
                                results = template_results
                                break
                        except Exception:
                            continue

                    if results:
                        break

                if not parser_fallback_attempted and self._is_parser_focused_query(
                    natural_language_query
                ):
                    parser_fallback_attempted = True
                    cypher_query = self._build_parser_scope_fallback_query(project_name)
                    continue

                if repaired_attempts >= max_repaired_attempts:
                    break
                repaired_attempts += 1
                cypher_query = await self._regenerate_project_scoped_cypher(
                    natural_language_query=natural_language_query,
                    project_name=project_name,
                    previous_cypher=cypher_query,
                    previous_error="query_returned_zero_rows",
                )

            fallback_chain: list[dict[str, object]] = []
            fallback_diagnostics: dict[str, object] = {}
            if not results:
                fallback = await self._run_standardized_query_fallback_chain(
                    natural_language_query=natural_language_query,
                    cypher_query=cypher_query,
                    failure_hint="query_returned_zero_rows",
                    error_text="",
                    result_rows=0,
                )
                fallback_chain = cast(
                    list[dict[str, object]], fallback.get("fallback_chain", [])
                )
                fallback_diagnostics = cast(
                    dict[str, object],
                    fallback.get("fallback_diagnostics", {}),
                )
                fallback_results = fallback.get("results", [])
                if isinstance(fallback_results, list):
                    results = cast(list[dict[str, Any]], fallback_results)

                if isinstance(fallback.get("query_used"), str):
                    cypher_query = str(fallback.get("query_used"))

                if isinstance(fallback.get("source"), str) and str(
                    fallback.get("source")
                ) in {"run_cypher", "semantic_search"}:
                    realized_chain = ["query_code_graph"]
                    for step in fallback_chain:
                        if not isinstance(step, dict):
                            continue
                        tool_name = str(step.get("tool", "")).strip()
                        if tool_name:
                            realized_chain.append(tool_name)
                    exploration_details = (
                        cast(
                            dict[str, object],
                            fallback_diagnostics.get("exploration", {}),
                        )
                        if isinstance(fallback_diagnostics.get("exploration", {}), dict)
                        else {}
                    )
                    self._memory_store.add_entry(
                        text=json.dumps(
                            {
                                "kind": "successful_tool_chain",
                                "query": natural_language_query,
                                "tool_history": realized_chain,
                                "selected_source": fallback.get("source"),
                                "exploration_mode": exploration_details.get(
                                    "mode", "exploit"
                                ),
                                "exploration_reason": exploration_details.get(
                                    "reason", ""
                                ),
                                "rows": len(results),
                            },
                            ensure_ascii=False,
                        ),
                        tags=["pattern", "chain", "fallback", "success"],
                    )

            self._session_bump("query_success_count")
            self._session_bump("graph_evidence_count")
            self._session_bump("query_code_graph_success_count")
            capped_results, truncated, total_rows = self._cap_query_results(results)
            chunks = self._split_rows_into_chunks(results)
            self._session_state["query_result_chunks"] = chunks
            graph_digest = self._build_graph_result_digest(results)
            self._session_state["last_graph_result_digest"] = graph_digest
            query_digest_id = ""
            if total_rows > 0:
                query_digest_id = self._mint_query_digest_id(cypher_query, total_rows)
                self._session_state["last_graph_query_digest_id"] = query_digest_id
            if graph_digest:
                self._memory_store.add_entry(
                    text=json.dumps(
                        {
                            "kind": "graph_query_digest",
                            "project": project_name,
                            "query": natural_language_query,
                            "rows": total_rows,
                            "digest": graph_digest,
                            "query_digest_id": query_digest_id,
                        },
                        ensure_ascii=False,
                    ),
                    tags=["graph", "query", "evidence", "success"],
                )

            usefulness_score = 1.0 if total_rows > 0 else 0.5
            self._record_tool_usefulness(
                cs.MCPToolName.QUERY_CODE_GRAPH,
                success=True,
                usefulness_score=usefulness_score,
            )
            summary = f"Query executed successfully. Returned {total_rows} rows."
            if truncated:
                summary += f" Response truncated to {len(capped_results)} rows for context safety."
            result_dict: QueryResultDict = QueryResultDict(
                query_used=cypher_query,
                results=cast(list[ResultRow], capped_results),
                summary=summary,
            )
            result_payload: dict[str, object] = {
                "query_used": result_dict.get("query_used", ""),
                "results": result_dict.get("results", []),
                "summary": result_dict.get("summary", ""),
            }
            result_payload["query_digest_id"] = query_digest_id
            result_payload["planner_usage_rate"] = self._planner_usage_rate()
            result_payload["schema_context"] = str(
                self._session_state.get("preflight_schema_context", "")
            )
            result_payload["session_contract"] = self._session_state.get(
                "session_contract", {}
            )
            if fallback_chain:
                result_payload["fallback_chain"] = fallback_chain
                result_payload["fallback_diagnostics"] = fallback_diagnostics
            logger.info(
                lg.MCP_QUERY_RESULTS.format(
                    count=len(
                        cast(list[object], result_payload.get(cs.DICT_KEY_RESULTS, []))
                    )
                )
            )

            normalized_format = output_format.strip().lower()
            if normalized_format == "cypher":
                return str(result_payload.get("query_used", ""))

            if normalized_format == "text":
                query_used = str(result_payload.get("query_used", ""))
                summary = str(result_payload.get("summary", ""))
                results_payload = cast(list[object], result_payload.get("results", []))
                results_text = json.dumps(
                    results_payload,
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

            return cast(QueryResultDict, result_payload)
        except Exception as e:
            logger.exception(lg.MCP_ERROR_QUERY.format(error=e))
            self._record_tool_usefulness(
                cs.MCPToolName.QUERY_CODE_GRAPH,
                success=False,
                usefulness_score=0.0,
            )
            fallback = await self._run_standardized_query_fallback_chain(
                natural_language_query=natural_language_query,
                cypher_query=(
                    "MATCH (m:Module {project_name: $project_name}) "
                    "RETURN m.name AS name LIMIT 50"
                ),
                failure_hint="query_execution_exception",
                error_text=str(e),
                result_rows=0,
            )
            fallback_results_raw = fallback.get("results", [])
            fallback_results: list[ResultRow] = []
            if isinstance(fallback_results_raw, list):
                fallback_results = cast(list[ResultRow], fallback_results_raw)
            if fallback_results:
                response = QueryResultDict(
                    query_used=str(fallback.get("query_used", "")),
                    results=fallback_results,
                    summary=(
                        "Query execution failed; standardized fallback returned "
                        f"{len(fallback_results)} rows via {fallback.get('source', 'fallback')}."
                    ),
                )
                response_payload: dict[str, object] = {
                    "query_used": response.get("query_used", ""),
                    "results": response.get("results", []),
                    "summary": response.get("summary", ""),
                }
                response_payload["fallback_chain"] = fallback.get("fallback_chain", [])
                response_payload["fallback_diagnostics"] = fallback.get(
                    "fallback_diagnostics", {}
                )
                return cast(QueryResultDict, response_payload)
            return QueryResultDict(
                error=str(e),
                query_used=cs.QUERY_NOT_AVAILABLE,
                results=[],
                summary=cs.MCP_TOOL_EXEC_ERROR.format(
                    name=cs.MCPToolName.QUERY_CODE_GRAPH, error=e
                ),
            )

    async def semantic_search(self, query: str, top_k: int = 5) -> dict[str, object]:
        self._set_execution_phase("retrieval", "semantic_search")
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
        self._set_execution_phase("retrieval", "get_function_source")
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
        self._set_execution_phase("retrieval", "get_code_snippet")
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
        self._set_execution_phase("execution", "surgical_replace_code")
        try:
            result = await self._file_editor_tool.function(
                file_path=file_path,
                target_code=target_code,
                replacement_code=replacement_code,
            )
            if "successfully" not in str(result).lower():
                return str(result)
            self._session_bump("edit_success_count")
            graph_sync = await self._maybe_auto_sync_graph_after_edit(
                action=cs.MCPToolName.SURGICAL_REPLACE_CODE,
                file_paths=[file_path],
            )
            return self._append_graph_sync_status_to_message(str(result), graph_sync)
        except Exception as e:
            logger.error(lg.MCP_ERROR_REPLACE.format(error=e))
            return te.ERROR_WRAPPER.format(message=e)

    async def read_file(
        self, file_path: str, offset: int | None = None, limit: int | None = None
    ) -> str:
        logger.info(lg.MCP_READ_FILE.format(path=file_path, offset=offset, limit=limit))
        self._set_execution_phase("retrieval", "read_file")
        try:
            graph_evidence_count = self._coerce_int(
                self._session_state.get("graph_evidence_count", 0)
            )
            if (
                bool(settings.MCP_ENFORCE_GRAPH_FIRST_READS)
                and graph_evidence_count <= 0
            ):
                self._record_tool_usefulness(
                    cs.MCPToolName.READ_FILE,
                    success=False,
                    usefulness_score=0.0,
                )
                return te.ERROR_WRAPPER.format(
                    message=(
                        "graph_first_enforced: call query_code_graph or run_cypher "
                        "before read_file"
                    )
                )

            if (
                bool(settings.MCP_ENFORCE_GRAPH_FIRST_READS)
                and not self._has_graph_read_prerequisite()
            ):
                self._record_tool_usefulness(
                    cs.MCPToolName.READ_FILE,
                    success=False,
                    usefulness_score=0.0,
                )
                return te.ERROR_WRAPPER.format(
                    message=(
                        "graph_digest_required: call query_code_graph (or run_cypher after graph-first flow) "
                        "and obtain a successful query_digest_id before read_file"
                    )
                )

            if (
                bool(settings.MCP_READ_FILE_REQUIRES_QUERY_GRAPH)
                and not self._has_graph_read_prerequisite()
            ):
                self._record_tool_usefulness(
                    cs.MCPToolName.READ_FILE,
                    success=False,
                    usefulness_score=0.0,
                )
                return te.ERROR_WRAPPER.format(
                    message=(
                        "graph_read_prerequisite_required: call query_code_graph, multi_hop_analysis, "
                        "or run_cypher after graph-first flow and obtain a successful query digest before read_file"
                    )
                )

            if offset is not None or limit is not None:
                full_path = Path(self.project_root) / file_path
                path_depth = max(0, len(Path(file_path).parts) - 1)
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
                    self._session_state["file_depth_sum"] = self._coerce_float(
                        self._session_state.get("file_depth_sum", 0.0)
                    ) + float(path_depth)
                    self._session_state["file_depth_count"] = (
                        self._coerce_int(self._session_state.get("file_depth_count", 0))
                        + 1
                    )
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
                path_depth = max(0, len(Path(file_path).parts) - 1)
                self._session_state["file_depth_sum"] = self._coerce_float(
                    self._session_state.get("file_depth_sum", 0.0)
                ) + float(path_depth)
                self._session_state["file_depth_count"] = (
                    self._coerce_int(self._session_state.get("file_depth_count", 0)) + 1
                )
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
        self._set_execution_phase("execution", "write_file")
        try:
            result = await self._file_writer_tool.function(
                file_path=file_path, content=content
            )
            if result.success:
                self._session_bump("edit_success_count")
                graph_sync = await self._maybe_auto_sync_graph_after_edit(
                    action=cs.MCPToolName.WRITE_FILE,
                    file_paths=[file_path],
                )
                return self._append_graph_sync_status_to_message(
                    cs.MCP_WRITE_SUCCESS.format(path=file_path),
                    graph_sync,
                )
            return te.ERROR_WRAPPER.format(message=result.error_message)
        except Exception as e:
            logger.error(lg.MCP_ERROR_WRITE.format(error=e))
            return te.ERROR_WRAPPER.format(message=e)

    async def list_directory(
        self, repo_path: str, directory_path: str = cs.MCP_DEFAULT_DIRECTORY
    ) -> str:
        self._set_execution_phase("retrieval", "list_directory")
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
        advanced_mode: bool = False,
    ) -> dict[str, object]:
        self._set_execution_phase(
            "execution" if bool(write) else "retrieval",
            "run_cypher_write" if bool(write) else "run_cypher_read",
        )
        if not cypher:
            return {"error": te.MCP_INVALID_RESPONSE, "results": []}
        if not write:
            await self._auto_plan_if_needed("run_cypher read-only query")
        parsed_params: dict[str, object] = {}
        if params:
            try:
                payload = json.loads(params)
                if isinstance(payload, dict):
                    parsed_params = payload
            except json.JSONDecodeError:
                parsed_params = {}

        normalized_cypher, normalized_params, normalization_notes = (
            self._normalize_run_cypher_scope(cypher, parsed_params)
        )

        write_impact: int | None = None
        risk_factor = 1.0
        if write:
            write_impact = await self._estimate_write_impact(
                normalized_cypher, normalized_params
            )
            risk_factor = await self._compute_project_risk_factor()

        policy_error = self._validate_run_cypher_policy(
            cypher=normalized_cypher,
            parsed_params=normalized_params,
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
            response: dict[str, object] = {"error": policy_error, "results": []}
            if (
                cs.MCP_RUN_CYPHER_SCOPE_ERROR.format(
                    project_name=self._active_project_name()
                )
                in policy_error
                or cs.MCP_RUN_CYPHER_PROJECT_PARAM_MISMATCH.format(
                    project_name=self._active_project_name()
                )
                in policy_error
            ):
                response["scope_hint"] = self._append_scope_fix_hint(
                    self._active_project_name()
                )
            if normalization_notes:
                response["scope_normalization"] = {
                    "applied": normalization_notes,
                    "query_used": normalized_cypher,
                    "params_used": normalized_params,
                }
            response.update(
                self._build_policy_guidance_payload(
                    policy_error=policy_error,
                    cypher_query=normalized_cypher,
                    parsed_params=normalized_params,
                    write=write,
                    advanced_mode=advanced_mode,
                )
            )
            return response

        bypass_reasons = {"session_preflight_schema_summary"}
        if (
            not write
            and not advanced_mode
            and str(reason or "").strip() not in bypass_reasons
            and not self._has_graph_query_digest()
        ):
            self._record_tool_usefulness(
                cs.MCPToolName.RUN_CYPHER,
                success=False,
                usefulness_score=0.0,
            )
            return {
                "error": (
                    "run_cypher_advanced_mode_required: default flow enforces graph-first retrieval. "
                    "Call query_code_graph first, then use run_cypher; or set advanced_mode=true for expert traversal control."
                ),
                "results": [],
                "flow_hint": [
                    "select_active_project",
                    "query_code_graph",
                    "run_cypher",
                ],
                **self._build_policy_guidance_payload(
                    policy_error="run_cypher_advanced_mode_required",
                    cypher_query=normalized_cypher,
                    parsed_params=normalized_params,
                    write=write,
                    advanced_mode=advanced_mode,
                ),
            }

        try:
            if write:

                async def _write_once() -> None:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            self.ingestor.execute_write,
                            normalized_cypher,
                            cast(dict[str, Any], normalized_params),
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
                result_payload: dict[str, object] = {"status": "ok", "results": []}
                result_payload["schema_context"] = str(
                    self._session_state.get("preflight_schema_context", "")
                )
                result_payload["session_contract"] = self._session_state.get(
                    "session_contract", {}
                )
                if normalization_notes:
                    result_payload["scope_normalization"] = {
                        "applied": normalization_notes,
                        "query_used": normalized_cypher,
                        "params_used": normalized_params,
                    }
                return result_payload

            async def _read_once() -> list[dict[str, Any]]:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        self.ingestor.fetch_all,
                        normalized_cypher,
                        cast(dict[str, Any], normalized_params),
                    ),
                    timeout=60.0,
                )

            results = await self._run_with_retries(
                _read_once,
                attempts=3,
                base_delay_seconds=0.5,
            )
            query_digest_id = ""
            if len(results) > 0:
                query_digest_id = self._mint_query_digest_id(
                    normalized_cypher, len(results)
                )
                self._session_state["last_graph_query_digest_id"] = query_digest_id
                graph_digest = self._build_graph_result_digest(results)
                self._session_state["last_graph_result_digest"] = graph_digest
            self._session_bump("query_success_count")
            self._session_bump("graph_evidence_count")
            self._record_tool_usefulness(
                cs.MCPToolName.RUN_CYPHER,
                success=True,
                usefulness_score=1.0 if len(results) > 0 else 0.5,
            )
            response_payload: dict[str, object] = {"status": "ok", "results": results}
            response_payload["query_digest_id"] = query_digest_id
            response_payload["planner_usage_rate"] = self._planner_usage_rate()
            response_payload["schema_context"] = str(
                self._session_state.get("preflight_schema_context", "")
            )
            response_payload["session_contract"] = self._session_state.get(
                "session_contract", {}
            )
            if normalization_notes:
                response_payload["scope_normalization"] = {
                    "applied": normalization_notes,
                    "query_used": normalized_cypher,
                    "params_used": normalized_params,
                }
            return response_payload
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

    @staticmethod
    def _build_deterministic_second_pass_queries(
        natural_language_query: str,
        project_name: str,
    ) -> list[str]:
        _ = project_name
        lowered = natural_language_query.lower()

        templates: list[str] = []

        if any(
            token in lowered
            for token in ("call", "caller", "callee", "hop", "chain", "dependency")
        ):
            templates.append(
                "MATCH (m:Module {project_name: $project_name})-[:CALLS]->(target) "
                "RETURN m.name AS source, m.path AS source_path, "
                "target.name AS target, target.path AS target_path "
                "LIMIT 80"
            )

        if any(token in lowered for token in ("class", "method", "function")):
            templates.append(
                "MATCH (m:Module {project_name: $project_name})-[:DEFINES]->(c:Class) "
                "OPTIONAL MATCH (c)-[:DEFINES_METHOD]->(meth:Method) "
                "RETURN m.path AS module_path, c.name AS class_name, meth.name AS method_name "
                "LIMIT 120"
            )
            templates.append(
                "MATCH (m:Module {project_name: $project_name})-[:DEFINES]->(f:Function) "
                "RETURN m.path AS module_path, f.name AS function_name, f.qualified_name AS qualified_name "
                "LIMIT 120"
            )

        templates.append(
            "MATCH (m:Module {project_name: $project_name}) "
            "RETURN m.name AS name, m.path AS path, m.qualified_name AS qualified_name "
            "LIMIT 80"
        )

        deduped: list[str] = []
        seen: set[str] = set()
        for query in templates:
            if query in seen:
                continue
            seen.add(query)
            deduped.append(query)
        return deduped

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
                "analysis_overview": self._analysis_evidence.read_resource(
                    "analysis://overview",
                    session_state=self._session_state,
                ),
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

        result = self._analysis_evidence.get_artifact(normalized_name)
        if result.get("error") == "artifact_not_found":
            return result
        if "error" in result:
            return result

        normalized_payload = result.get("normalized", {})
        response = dict(result)
        if isinstance(normalized_payload, dict):
            normalized_dict = cast(dict[str, object], normalized_payload)
            response["ui_summary"] = normalized_dict.get("summary", "")
            response["confidence"] = normalized_dict.get("confidence", 0.0)
            response["freshness"] = normalized_dict.get("freshness", {})
            response["next_actions"] = normalized_dict.get("next_actions", [])
        return response

    async def list_analysis_artifacts(self) -> dict[str, object]:
        result = self._analysis_evidence.list_artifacts()
        result["ui_summary"] = (
            f"Normalized {result.get('count', 0)} analysis artifacts into evidence-ready resources."
        )
        return result

    async def analysis_bundle_for_goal(
        self,
        goal: str,
        context: str | None = None,
    ) -> dict[str, object]:
        self._set_execution_phase("retrieval", "analysis_bundle_for_goal")
        bundle = self._analysis_evidence.build_bundle(
            "analysis_bundle_for_goal",
            goal=goal,
            context=context,
            session_state=self._session_state,
        )
        self._session_state["last_analysis_bundle"] = bundle
        bundle["ui_summary"] = str(bundle.get("summary", "")).strip()
        return bundle

    async def architecture_bundle(
        self,
        goal: str | None = None,
        context: str | None = None,
    ) -> dict[str, object]:
        self._set_execution_phase("retrieval", "architecture_bundle")
        bundle = self._analysis_evidence.build_bundle(
            "architecture_bundle",
            goal=goal,
            context=context,
            session_state=self._session_state,
        )
        self._session_state["last_architecture_bundle"] = bundle
        bundle["ui_summary"] = str(bundle.get("summary", "")).strip()
        return bundle

    async def change_bundle(
        self,
        goal: str,
        context: str | None = None,
        qualified_name: str | None = None,
        file_path: str | None = None,
    ) -> dict[str, object]:
        self._set_execution_phase("retrieval", "change_bundle")
        bundle = self._analysis_evidence.build_bundle(
            "change_bundle",
            goal=goal,
            context=context,
            qualified_name=qualified_name,
            file_path=file_path,
            session_state=self._session_state,
        )
        self._session_state["last_change_bundle"] = bundle
        bundle["ui_summary"] = str(bundle.get("summary", "")).strip()
        return bundle

    async def risk_bundle(
        self,
        goal: str,
        context: str | None = None,
        qualified_name: str | None = None,
        file_path: str | None = None,
    ) -> dict[str, object]:
        self._set_execution_phase("retrieval", "risk_bundle")
        bundle = self._analysis_evidence.build_bundle(
            "risk_bundle",
            goal=goal,
            context=context,
            qualified_name=qualified_name,
            file_path=file_path,
            session_state=self._session_state,
        )
        self._session_state["last_risk_bundle"] = bundle
        bundle["ui_summary"] = str(bundle.get("summary", "")).strip()
        return bundle

    async def test_bundle(
        self,
        goal: str,
        context: str | None = None,
        qualified_name: str | None = None,
        file_path: str | None = None,
    ) -> dict[str, object]:
        self._set_execution_phase("retrieval", "test_bundle")
        bundle = self._analysis_evidence.build_bundle(
            "test_bundle",
            goal=goal,
            context=context,
            qualified_name=qualified_name,
            file_path=file_path,
            session_state=self._session_state,
        )
        self._session_state["last_test_bundle"] = bundle
        bundle["ui_summary"] = str(bundle.get("summary", "")).strip()
        return bundle

    async def list_mcp_resources(self) -> list[dict[str, object]]:
        return self._analysis_evidence.list_resources()

    async def read_mcp_resource(self, uri: str) -> dict[str, object]:
        return self._analysis_evidence.read_resource(
            uri,
            session_state=self._session_state,
        )

    async def list_mcp_prompts(self) -> list[dict[str, object]]:
        return self._analysis_evidence.list_prompts()

    async def get_mcp_prompt(
        self,
        prompt_name: str,
        arguments: dict[str, str] | None = None,
    ) -> dict[str, object]:
        return self._analysis_evidence.get_prompt(
            prompt_name,
            arguments=arguments,
            session_state=self._session_state,
        )

    async def apply_diff_safe(self, file_path: str, chunks: str) -> dict[str, object]:
        self._set_execution_phase("execution", "apply_diff_safe")
        if file_path.startswith(".env"):
            return {"error": "sensitive_path"}
        try:
            payload = json.loads(chunks)
        except json.JSONDecodeError:
            return {"error": "invalid_chunks_json"}
        if not isinstance(payload, list) or not payload:
            return {"error": "chunks_must_be_list"}
        result = await self._apply_diff_chunks(file_path, payload)
        if result.get("status") == "ok":
            result["graph_sync"] = await self._maybe_auto_sync_graph_after_edit(
                action=cs.MCPToolName.APPLY_DIFF_SAFE,
                file_paths=[file_path],
            )
        return result

    async def refactor_batch(self, chunks: str) -> dict[str, object]:
        self._set_execution_phase("execution", "refactor_batch")
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
        response = {"status": "ok", "results": results}
        file_paths = [
            str(entry.get("file_path", "")).strip()
            for entry in payload
            if isinstance(entry, dict)
        ]
        response["graph_sync"] = await self._maybe_auto_sync_graph_after_edit(
            action=cs.MCPToolName.REFACTOR_BATCH,
            file_paths=file_paths,
        )
        return response

    async def test_generate(
        self,
        goal: str,
        context: str | None = None,
        output_mode: str = "code",
    ) -> dict[str, object]:
        self._set_execution_phase("post_validation", "test_generate")
        test_selection = self._build_test_selection_bundle()
        evidence_packet = self._build_evidence_bundle_packet(
            goal=goal,
            context=context,
            include_architecture=True,
            include_change=True,
            include_risk=True,
            include_test=True,
        )
        selection_lines = [
            "Impact-aware test selection:",
            f"- Strategy: {test_selection.get('selection_strategy', 'goal-only')}",
            f"- Impacted files: {test_selection.get('impacted_files', [])}",
            f"- Impacted symbols: {test_selection.get('impacted_symbols', [])}",
            f"- Candidate existing tests: {test_selection.get('candidate_existing_tests', [])}",
            f"- New test file hints: {test_selection.get('new_test_file_hints', [])}",
        ]
        prompt = goal if context is None else f"{goal}\nContext: {context}"
        prompt += "\n" + "\n".join(selection_lines)
        prompt += "\n" + self._format_evidence_packet_for_prompt(
            evidence_packet,
            title="Structured evidence packet:",
        )
        try:
            result = await asyncio.wait_for(
                self._test_agent.run(prompt),
                timeout=max(30.0, float(settings.MCP_AGENT_TIMEOUT_SECONDS)),
            )
            normalized_output = self._normalize_test_generation_output(
                result.content,
                output_mode=output_mode,
            )
            normalized_output["impact_context"] = {
                "impacted_files": test_selection.get("impacted_files", []),
                "impacted_symbols": test_selection.get("impacted_symbols", []),
            }
            normalized_output["test_selection"] = test_selection
            normalized_output["evidence_packet"] = evidence_packet
            self._session_state["test_generate_completed"] = True
            self._session_state["last_test_generation"] = normalized_output
            self._record_tool_usefulness(
                cs.MCPToolName.TEST_GENERATE,
                success=True,
                usefulness_score=(
                    1.0 if str(normalized_output.get("content", "")).strip() else 0.4
                ),
            )
            return {"status": result.status, **normalized_output}
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
        self._set_execution_phase("post_validation", "memory_add")
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
        self._set_execution_phase("retrieval", "memory_query_patterns")
        tag_filters = (
            [item.strip() for item in filter_tags.split(",") if item.strip()]
            if isinstance(filter_tags, str)
            else []
        )
        bounded_limit = max(1, min(int(limit), 100))
        entries = self._memory_store.query_patterns(
            query=query,
            filter_tags=tag_filters,
            success_only=bool(success_only),
            limit=bounded_limit,
        )
        chain_success_rates = self._memory_store.get_chain_success_rates(
            query=query,
            limit=min(10, bounded_limit),
        )
        self._session_state["memory_primed"] = True
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
            "chain_success_rates": chain_success_rates,
        }

    async def execution_feedback(
        self,
        action: str,
        result: str,
        issues: str | None = None,
        failure_reasons: str | None = None,
    ) -> dict[str, object]:
        self._set_execution_phase("post_validation", "execution_feedback")
        parsed_issues = (
            [item.strip() for item in issues.split(",") if item.strip()]
            if isinstance(issues, str)
            else []
        )
        normalized_result = result.strip().lower()
        normalized_issues = [item.lower() for item in parsed_issues]
        structured_reasons = self._parse_failure_reasons(
            failure_reasons,
            normalized_issues,
        )
        replan_reasons: list[str] = list(structured_reasons)

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
            "structured_reasons": structured_reasons,
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
            "structured_reasons": structured_reasons,
            "feedback": payload,
        }

    async def test_quality_gate(
        self,
        coverage: str,
        edge_cases: str,
        negative_tests: str,
        repo_evidence: str | None = None,
        layer_correctness: str | None = None,
        cleanup_safety: str | None = None,
        anti_hallucination: str | None = None,
        implementation_coupling_penalty: str | None = None,
    ) -> dict[str, object]:
        self._set_execution_phase("validation", "test_quality_gate")
        coverage_score = self._normalize_quality_score(coverage)
        edge_cases_score = self._normalize_quality_score(edge_cases)
        negative_tests_score = self._normalize_quality_score(negative_tests)
        repo_evidence_score = self._normalize_optional_quality_score(repo_evidence)
        layer_correctness_score = self._normalize_optional_quality_score(
            layer_correctness
        )
        cleanup_safety_score = self._normalize_optional_quality_score(cleanup_safety)
        anti_hallucination_score = self._normalize_optional_quality_score(
            anti_hallucination
        )
        coupling_penalty = self._normalize_optional_quality_score(
            implementation_coupling_penalty
        )
        optional_scores = {
            "repo_evidence": repo_evidence_score,
            "layer_correctness": layer_correctness_score,
            "cleanup_safety": cleanup_safety_score,
            "anti_hallucination": anti_hallucination_score,
        }
        active_optional_scores = {
            key: value for key, value in optional_scores.items() if value is not None
        }
        total_score = (
            coverage_score
            + edge_cases_score
            + negative_tests_score
            + sum(active_optional_scores.values())
            - (coupling_penalty or 0.0)
        )
        required = (
            4.0 if active_optional_scores or coupling_penalty is not None else 2.0
        )
        hard_failures: list[str] = []
        if coverage_score < 0.5:
            hard_failures.append("coverage_below_threshold")
        if negative_tests_score < 0.5:
            hard_failures.append("negative_tests_below_threshold")
        if repo_evidence_score is not None and repo_evidence_score < 0.6:
            hard_failures.append("repo_evidence_below_threshold")
        if anti_hallucination_score is not None and anti_hallucination_score < 0.6:
            hard_failures.append("anti_hallucination_below_threshold")
        gate_pass = total_score >= required and not hard_failures

        self._session_state["test_quality_total"] = round(total_score, 3)
        self._session_state["test_quality_pass"] = gate_pass
        self._session_state["test_quality_breakdown"] = {
            "coverage": coverage_score,
            "edge_cases": edge_cases_score,
            "negative_tests": negative_tests_score,
            **active_optional_scores,
            "implementation_coupling_penalty": coupling_penalty or 0.0,
            "required": required,
            "hard_failures": hard_failures,
        }
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
                **active_optional_scores,
                "implementation_coupling_penalty": coupling_penalty or 0.0,
                "total": round(total_score, 3),
                "required": required,
            },
            "hard_failures": hard_failures,
            "pass": gate_pass,
            "ui_summary": (
                f"Test quality: {'pass' if gate_pass else 'block'} | "
                f"score={round(total_score, 3)}/{required}"
            ),
        }

    @staticmethod
    def _planner_payload_has_substance(payload: dict[str, object]) -> bool:
        actionable_list_fields = (
            "steps",
            "required_evidence",
            "evidence_priority",
            "multi_hop_plan",
            "recommended_tool_chain",
            "copy_paste_calls",
        )

        for key in actionable_list_fields:
            value = payload.get(key, [])
            if isinstance(value, list) and any(str(item).strip() for item in value):
                return True

        return False

    def _build_plan_task_failure_payload(
        self,
        *,
        reason: str,
        goal: str,
        context: str | None,
        pattern_texts: list[str],
        chain_success_rates: list[dict[str, object]],
    ) -> dict[str, object]:
        normalized_goal = str(goal).strip() or "Create deterministic task plan"
        retry_context = (
            "Planner returned no actionable output. Collect graph evidence first, "
            "then retry with a shorter, deterministic tool chain."
        )
        escaped_goal = normalized_goal.replace("\\", "\\\\").replace('"', '\\"')
        escaped_context = retry_context.replace("\\", "\\\\").replace('"', '\\"')
        exact_next_calls = [
            {
                "tool": cs.MCPToolName.QUERY_CODE_GRAPH,
                "args": {
                    "natural_language_query": normalized_goal,
                    "output_format": "json",
                },
                "priority": 1,
                "when": "planner output is empty or non-actionable",
                "copy_paste": (
                    "query_code_graph("
                    f'natural_language_query="{escaped_goal}", output_format="json")'
                ),
                "why": "collect_graph_evidence_before_retrying_plan",
            },
            {
                "tool": cs.MCPToolName.PLAN_TASK,
                "args": {"goal": normalized_goal, "context": retry_context},
                "priority": 2,
                "when": "after graph evidence is available",
                "copy_paste": (
                    f'plan_task(goal="{escaped_goal}", context="{escaped_context}")'
                ),
                "why": "retry_planner_with_tighter_context",
            },
        ]
        return {
            "status": "error",
            "error": reason,
            "ui_summary": (
                "Planner returned no actionable steps. Plan gate remains closed."
            ),
            "goal": normalized_goal,
            "exact_next_calls": exact_next_calls,
            "next_best_action": self._project_next_best_action_from_exact_calls(
                exact_next_calls
            ),
            "retrieved_patterns": pattern_texts[:5],
            "chain_success_rates": chain_success_rates[:5],
            "memory_injection_mandatory": True,
            "retry_context": retry_context,
            "previous_context": str(context or "").strip(),
        }

    async def plan_task(
        self, goal: str, context: str | None = None
    ) -> dict[str, object]:
        try:
            self._set_execution_phase("validation", "plan_task")
            self._session_bump("plan_task_count")
            memory_patterns = await self.memory_query_patterns(
                query=goal,
                filter_tags="plan,refactor,success",
                success_only=True,
                limit=5,
            )
            self._set_execution_phase(
                "validation",
                "plan_task_resume_after_memory_query",
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
            chain_success_rates_raw = memory_patterns.get("chain_success_rates", [])
            chain_success_rates: list[dict[str, object]] = []
            if isinstance(chain_success_rates_raw, list):
                for item in chain_success_rates_raw:
                    if isinstance(item, dict):
                        chain_success_rates.append(cast(dict[str, object], item))

            pattern_lines = (
                [f"- {line}" for line in pattern_texts[:5]]
                if pattern_texts
                else ["- none"]
            )
            chain_lines: list[str] = []
            for item in chain_success_rates[:5]:
                signature = str(item.get("chain_signature", "")).strip()
                success_rate = self._coerce_float(item.get("success_rate", 0.0))
                total_count = self._coerce_int(item.get("total_count", 0))
                if signature:
                    chain_lines.append(
                        f"- {signature} (success_rate={success_rate:.2f}, total={total_count})"
                    )
            if not chain_lines:
                chain_lines = ["- none"]

            mandatory_memory_block = (
                "Memory pattern injection (mandatory):\n"
                "Matched patterns:\n"
                + "\n".join(pattern_lines)
                + "\n"
                + "Chain success-rate candidates:\n"
                + "\n".join(chain_lines)
            )

            lowered_goal = str(goal).strip().lower()
            evidence_packet = self._build_evidence_bundle_packet(
                goal=goal,
                context=context,
                include_architecture=True,
                include_change=any(
                    token in lowered_goal
                    for token in (
                        "change",
                        "edit",
                        "fix",
                        "impact",
                        "refactor",
                    )
                )
                or bool(self._collect_recent_impact_context().get("has_impact", False)),
                include_risk=any(
                    token in lowered_goal
                    for token in ("dependency", "performance", "risk", "security")
                ),
                include_test=any(
                    token in lowered_goal for token in ("coverage", "test", "tests")
                ),
            )

            augmented_context = (
                (context + "\n") if context else ""
            ) + mandatory_memory_block
            augmented_context += "\n" + self._format_evidence_packet_for_prompt(
                evidence_packet,
                title="Structured evidence packet:",
            )

            result = await asyncio.wait_for(
                self._planner_agent.plan(goal, context=augmented_context),
                timeout=max(30.0, float(settings.MCP_AGENT_TIMEOUT_SECONDS)),
            )
            planner_content = (
                result.content
                if hasattr(result, "content") and isinstance(result.content, dict)
                else {}
            )
            planner_status = str(getattr(result, "status", "")).strip().lower()
            if planner_status != "ok" or not self._planner_payload_has_substance(
                planner_content
            ):
                self._session_state["plan_task_completed"] = False
                self._record_tool_usefulness(
                    cs.MCPToolName.PLAN_TASK,
                    success=False,
                    usefulness_score=0.0,
                )
                return self._build_plan_task_failure_payload(
                    reason="planner_empty_output",
                    goal=goal,
                    context=context,
                    pattern_texts=pattern_texts,
                    chain_success_rates=chain_success_rates,
                )

            self._session_state["plan_task_completed"] = True
            self._record_tool_usefulness(
                cs.MCPToolName.PLAN_TASK,
                success=True,
                usefulness_score=1.0,
            )
            if planner_content:
                return {
                    "status": result.status,
                    **planner_content,
                    "retrieved_patterns": pattern_texts[:5],
                    "chain_success_rates": chain_success_rates[:5],
                    "memory_injection_mandatory": True,
                    "evidence_packet": evidence_packet,
                }
            return {
                "status": result.status,
                "content": result.content,
                "retrieved_patterns": pattern_texts[:5],
                "chain_success_rates": chain_success_rates[:5],
                "memory_injection_mandatory": True,
                "evidence_packet": evidence_packet,
            }
        except TimeoutError:
            self._session_state["plan_task_completed"] = False
            self._record_tool_usefulness(
                cs.MCPToolName.PLAN_TASK,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": "plan_task_timed_out_after_300s"}
        except Exception as exc:
            self._session_state["plan_task_completed"] = False
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
                project_name=self._active_project_name(),
                depth=depth,
                limit=limit,
            )
            result_count = self._coerce_int(result.get("count", 0))
            self._session_state["impact_graph_called"] = True
            self._session_state["impact_graph_count"] = result_count
            result_rows = result.get("results", [])
            affected_symbols: list[str] = []
            if isinstance(result_rows, list):
                for row in result_rows:
                    if not isinstance(row, dict):
                        continue
                    row_dict = cast(dict[str, object], row)
                    target_ref = str(row_dict.get("target", "")).strip()
                    if target_ref and target_ref not in affected_symbols:
                        affected_symbols.append(target_ref)
            self._session_state["last_impact_bundle"] = {
                "qualified_name": qualified_name,
                "file_path": file_path,
                "affected_symbols": affected_symbols[:20],
                "affected_files": [],
                "count": result_count,
            }
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
    def _compact_text(value: object, *, limit: int = 280) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    @staticmethod
    def _normalize_path_value(value: object) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            return ""
        return candidate.replace("\\", "/")

    def _build_multi_hop_exact_next_calls(
        self,
        *,
        qualified_name: str | None,
        affected_files: list[str],
        include_context7: bool,
        context7_query: str | None,
    ) -> list[dict[str, object]]:
        exact_next_calls: list[dict[str, object]] = []
        if qualified_name:
            qualified_name_escaped = qualified_name.replace('"', '\\"')
            exact_next_calls.append(
                {
                    "tool": cs.MCPToolName.GET_CODE_SNIPPET,
                    "args": {"qualified_name": qualified_name},
                    "priority": 1,
                    "when": "implementation confirmation is needed for the analyzed symbol",
                    "copy_paste": (
                        f'get_code_snippet(qualified_name="{qualified_name_escaped}")'
                    ),
                    "why": "inspect_target_symbol_source",
                }
            )
        if affected_files:
            target_path = affected_files[0]
            path_escaped = target_path.replace("\\", "\\\\").replace('"', '\\"')
            exact_next_calls.append(
                {
                    "tool": cs.MCPToolName.READ_FILE,
                    "args": {"file_path": target_path},
                    "priority": 2,
                    "when": "graph evidence narrowed the implementation to a concrete file",
                    "copy_paste": f'read_file(file_path="{path_escaped}")',
                    "why": "inspect_highest_priority_affected_file",
                }
            )
        if include_context7 and context7_query:
            library = self._context7_client.detect_library(context7_query)
            if library:
                exact_next_calls.append(
                    {
                        "tool": cs.MCPToolName.CONTEXT7_DOCS,
                        "args": {"library": library, "query": context7_query},
                        "priority": 3,
                        "when": "external framework behavior must be verified after repo evidence",
                        "copy_paste": (
                            "context7_docs("
                            f'library="{library}", query="{context7_query.replace(chr(34), '\\"')}")'
                        ),
                        "why": "external_library_enrichment",
                    }
                )
        return self._normalize_exact_next_calls(exact_next_calls)

    def _summarize_context7_docs(self, docs: object) -> list[str]:
        lines: list[str] = []
        if isinstance(docs, list):
            normalized_docs = cast(list[object], docs)
            for item in normalized_docs[:3]:
                if not isinstance(item, dict):
                    lines.append(self._compact_text(item, limit=180))
                    continue
                doc_payload = cast(dict[str, object], item)
                title = self._compact_text(
                    doc_payload.get("title") or doc_payload.get("topic") or ""
                )
                content = self._compact_text(
                    doc_payload.get("content") or doc_payload.get("summary") or ""
                )
                if title and content:
                    lines.append(f"{title}: {content}")
                elif title:
                    lines.append(title)
                elif content:
                    lines.append(content)
        elif isinstance(docs, dict):
            doc_payload = cast(dict[str, object], docs)
            content = (
                doc_payload.get("content")
                or doc_payload.get("summary")
                or doc_payload.get("docs")
            )
            if content:
                lines.append(self._compact_text(content, limit=180))
        elif docs:
            lines.append(self._compact_text(docs, limit=180))
        return lines[:3]

    async def context7_docs(
        self,
        library: str,
        query: str,
        version: str | None = None,
    ) -> dict[str, object]:
        normalized_library = str(library or "").strip()
        normalized_query = str(query or "").strip()
        normalized_version = str(version or "").strip() or None
        if not normalized_library:
            return {"error": "library_required"}
        if not normalized_query:
            return {"error": "query_required"}

        self._set_execution_phase("retrieval", "context7_docs")
        cached = self._context7_knowledge_store.lookup(
            normalized_library, normalized_query
        )
        cache_source = "graph"
        if cached is None:
            cached = self._context7_memory_store.lookup(
                normalized_library, normalized_query
            )
            cache_source = "memory"
        if cached is not None:
            docs = cached.get("docs", [])
            highlights = self._summarize_context7_docs(docs)
            self._record_tool_usefulness(
                cs.MCPToolName.CONTEXT7_DOCS,
                success=True,
                usefulness_score=0.9,
            )
            return {
                "status": "ok",
                "library": normalized_library,
                "query": normalized_query,
                "version": normalized_version,
                "source": cache_source,
                "docs": docs,
                "highlights": highlights,
                "ui_summary": (
                    f"Context7 cache hit for {normalized_library}. "
                    f"Returned {len(docs) if isinstance(docs, list) else 1} documentation items."
                ),
            }

        result = await self._context7_client.get_docs(
            normalized_library,
            normalized_query,
            normalized_version,
        )
        if isinstance(result, dict) and result.get("error"):
            self._record_tool_usefulness(
                cs.MCPToolName.CONTEXT7_DOCS,
                success=False,
                usefulness_score=0.0,
            )
            return {
                "error": str(result.get("error")),
                "library": normalized_library,
                "query": normalized_query,
                "version": normalized_version,
            }

        if isinstance(result, dict):
            self._context7_persistence.persist(
                str(result.get("library_id", normalized_library)),
                normalized_library,
                normalized_query,
                result.get("docs"),
            )
        docs = result.get("docs", []) if isinstance(result, dict) else []
        highlights = self._summarize_context7_docs(docs)
        self._record_tool_usefulness(
            cs.MCPToolName.CONTEXT7_DOCS,
            success=True,
            usefulness_score=1.0 if highlights else 0.7,
        )
        return {
            "status": "ok",
            "library": normalized_library,
            "query": normalized_query,
            "version": normalized_version,
            "source": "context7_api",
            "library_id": (
                result.get("library_id") if isinstance(result, dict) else None
            ),
            "docs": docs,
            "highlights": highlights,
            "ui_summary": (
                f"Context7 retrieved external documentation for {normalized_library}."
            ),
        }

    async def multi_hop_analysis(
        self,
        qualified_name: str | None = None,
        file_path: str | None = None,
        depth: int = 3,
        limit: int = 80,
        include_context7: bool = False,
        context7_query: str | None = None,
    ) -> dict[str, object]:
        normalized_qualified_name = str(qualified_name or "").strip() or None
        normalized_file_path = str(file_path or "").strip() or None
        if not normalized_qualified_name and not normalized_file_path:
            return {"error": "missing_target"}

        self._set_execution_phase("retrieval", "multi_hop_analysis")
        bounded_depth = min(max(1, int(depth)), 6)
        bounded_limit = min(max(5, int(limit)), 200)
        project_name = self._active_project_name()
        hop_pattern = MCPImpactGraphService._IMPACT_REL_TYPES
        seed_filter = """
WHERE (
    (
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
)
AND (
    coalesce(start.project_name, $project_name) = $project_name
)
"""
        outbound_query = f"""
MATCH (start)
{seed_filter}
WITH collect(DISTINCT start) AS seeds
UNWIND seeds AS seed
MATCH p=(seed)-[:{hop_pattern}*1..{bounded_depth}]->(target)
WHERE all(node IN nodes(p) WHERE coalesce(node.project_name, $project_name) = $project_name)
RETURN DISTINCT
    'outbound' AS direction,
    coalesce(seed.qualified_name, seed.path, seed.file_path, seed.name, toString(id(seed))) AS seed_ref,
    coalesce(seed.path, seed.file_path, '') AS seed_path,
    coalesce(target.qualified_name, target.path, target.file_path, target.name, toString(id(target))) AS node_ref,
    coalesce(target.path, target.file_path, '') AS node_path,
    labels(target) AS node_labels,
    type(last(relationships(p))) AS relation,
    length(p) AS hop_count
LIMIT $limit
"""
        inbound_query = f"""
MATCH (start)
{seed_filter}
WITH collect(DISTINCT start) AS seeds
UNWIND seeds AS seed
MATCH p=(source)-[:{hop_pattern}*1..{bounded_depth}]->(seed)
WHERE all(node IN nodes(p) WHERE coalesce(node.project_name, $project_name) = $project_name)
RETURN DISTINCT
    'inbound' AS direction,
    coalesce(seed.qualified_name, seed.path, seed.file_path, seed.name, toString(id(seed))) AS seed_ref,
    coalesce(seed.path, seed.file_path, '') AS seed_path,
    coalesce(source.qualified_name, source.path, source.file_path, source.name, toString(id(source))) AS node_ref,
    coalesce(source.path, source.file_path, '') AS node_path,
    labels(source) AS node_labels,
    type(last(relationships(p))) AS relation,
    length(p) AS hop_count
LIMIT $limit
"""
        params = {
            "qualified_name": normalized_qualified_name,
            "file_path": normalized_file_path,
            "project_name": project_name,
            "limit": bounded_limit,
        }
        try:
            outbound_rows = await asyncio.wait_for(
                asyncio.to_thread(self.ingestor.fetch_all, outbound_query, params),
                timeout=45.0,
            )
            inbound_rows = await asyncio.wait_for(
                asyncio.to_thread(self.ingestor.fetch_all, inbound_query, params),
                timeout=45.0,
            )
        except Exception as exc:
            self._record_tool_usefulness(
                cs.MCPToolName.MULTI_HOP_ANALYSIS,
                success=False,
                usefulness_score=0.0,
            )
            return {"error": str(exc), "results": []}

        combined_rows = [
            cast(dict[str, object], row)
            for row in [*outbound_rows, *inbound_rows]
            if isinstance(row, dict)
        ]
        if combined_rows:
            self._session_bump("graph_evidence_count")
            self._session_bump("query_success_count")
            self._session_state["last_graph_query_digest_id"] = (
                self._mint_query_digest_id(
                    normalized_qualified_name
                    or normalized_file_path
                    or "multi_hop_analysis",
                    len(combined_rows),
                )
            )
            self._session_state["last_graph_result_digest"] = (
                self._build_graph_result_digest(combined_rows)
            )
            self._session_state["query_result_chunks"] = self._split_rows_into_chunks(
                combined_rows
            )

        symbol_counts: dict[str, int] = {}
        file_counts: dict[str, int] = {}
        relation_counts: dict[str, int] = {}
        direction_counts = {"outbound": 0, "inbound": 0}
        critical_paths: list[dict[str, object]] = []
        for row in combined_rows:
            direction = str(row.get("direction", "")).strip().lower()
            if direction in direction_counts:
                direction_counts[direction] += 1
            relation = str(row.get("relation", "")).strip()
            if relation:
                relation_counts[relation] = relation_counts.get(relation, 0) + 1
            node_ref = str(row.get("node_ref", "")).strip()
            if node_ref:
                symbol_counts[node_ref] = symbol_counts.get(node_ref, 0) + 1
            node_path = self._normalize_path_value(row.get("node_path"))
            if node_path:
                file_counts[node_path] = file_counts.get(node_path, 0) + 1
            critical_paths.append(
                {
                    "direction": direction,
                    "relation": relation,
                    "hop_count": self._coerce_int(row.get("hop_count", 0)),
                    "node_ref": node_ref,
                    "node_path": node_path,
                    "node_labels": row.get("node_labels", []),
                }
            )

        affected_symbols = [
            key
            for key, _ in sorted(
                symbol_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:15]
        ]
        affected_files = [
            key
            for key, _ in sorted(
                file_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:12]
        ]
        critical_paths.sort(
            key=lambda row: (
                -self._coerce_int(row.get("hop_count", 0)),
                str(row.get("direction", "")),
                str(row.get("node_ref", "")),
            )
        )
        recommended_reads = affected_files[:5]
        context7_enrichment: dict[str, object] | None = None
        normalized_context7_query = str(context7_query or "").strip() or None
        if include_context7 and normalized_context7_query:
            detected_library = self._context7_client.detect_library(
                normalized_context7_query
            )
            if detected_library:
                context7_enrichment = await self.context7_docs(
                    library=detected_library,
                    query=normalized_context7_query,
                )

        exact_next_calls = self._build_multi_hop_exact_next_calls(
            qualified_name=normalized_qualified_name,
            affected_files=recommended_reads,
            include_context7=include_context7,
            context7_query=normalized_context7_query,
        )
        next_best_action = self._project_next_best_action_from_exact_calls(
            exact_next_calls
        )
        hop_summary = {
            "depth": bounded_depth,
            "limit": bounded_limit,
            "total_edges": len(combined_rows),
            "directions": direction_counts,
            "relation_counts": relation_counts,
        }
        summary = (
            f"Multi-hop analysis for {normalized_qualified_name or normalized_file_path} "
            f"found {len(combined_rows)} traversed edges, {len(affected_symbols)} key symbols, "
            f"and {len(affected_files)} affected files."
        )
        self._record_tool_usefulness(
            cs.MCPToolName.MULTI_HOP_ANALYSIS,
            success=True,
            usefulness_score=1.0 if combined_rows else 0.6,
        )
        response: dict[str, object] = {
            "status": "ok",
            "target": {
                "qualified_name": normalized_qualified_name,
                "file_path": normalized_file_path,
                "project_name": project_name,
            },
            "summary": summary,
            "ui_summary": summary,
            "hop_summary": hop_summary,
            "affected_symbols": affected_symbols,
            "affected_files": affected_files,
            "recommended_reads": recommended_reads,
            "critical_paths": critical_paths[:12],
            "exact_next_calls": exact_next_calls,
            "next_best_action": next_best_action,
            "execution_state": self._build_execution_state_contract(),
        }
        self._session_state["last_multi_hop_bundle"] = {
            "qualified_name": normalized_qualified_name,
            "file_path": normalized_file_path,
            "affected_symbols": affected_symbols,
            "affected_files": affected_files,
            "recommended_reads": recommended_reads,
            "summary": summary,
        }
        if context7_enrichment is not None:
            response["context7_enrichment"] = context7_enrichment
        return response

    def _collect_recent_impact_context(self) -> dict[str, object]:
        impacted_files: list[str] = []
        impacted_symbols: list[str] = []
        sources = [
            self._session_state.get("last_multi_hop_bundle", {}),
            self._session_state.get("last_impact_bundle", {}),
        ]
        for bundle in sources:
            if not isinstance(bundle, dict):
                continue
            bundle_dict = cast(dict[str, object], bundle)
            raw_files = bundle_dict.get("affected_files", [])
            if isinstance(raw_files, list):
                for item in raw_files:
                    normalized = self._normalize_path_value(item)
                    if normalized and normalized not in impacted_files:
                        impacted_files.append(normalized)
            raw_symbols = bundle_dict.get("affected_symbols", [])
            if isinstance(raw_symbols, list):
                for item in raw_symbols:
                    symbol = str(item).strip()
                    if symbol and symbol not in impacted_symbols:
                        impacted_symbols.append(symbol)

        for source_key in ("last_graph_sync_paths", "last_mutation_paths"):
            raw_paths = self._session_state.get(source_key, [])
            if isinstance(raw_paths, list):
                for item in raw_paths:
                    normalized = self._normalize_path_value(item)
                    if normalized and normalized not in impacted_files:
                        impacted_files.append(normalized)

        return {
            "has_impact": bool(impacted_files or impacted_symbols),
            "impacted_files": impacted_files[:12],
            "impacted_symbols": impacted_symbols[:15],
        }

    def _build_evidence_bundle_packet(
        self,
        *,
        goal: str,
        context: str | None = None,
        qualified_name: str | None = None,
        file_path: str | None = None,
        include_architecture: bool = True,
        include_change: bool = False,
        include_risk: bool = False,
        include_test: bool = False,
    ) -> dict[str, object]:
        recent_impact = self._collect_recent_impact_context()
        impacted_files = cast(list[str], recent_impact.get("impacted_files", []))
        impacted_symbols = cast(list[str], recent_impact.get("impacted_symbols", []))
        resolved_qualified_name = str(qualified_name or "").strip() or (
            impacted_symbols[0] if impacted_symbols else ""
        )
        resolved_file_path = self._normalize_path_value(file_path) or (
            impacted_files[0] if impacted_files else ""
        )

        bundle_specs: list[tuple[str, str, dict[str, str]]] = [
            (
                "analysis_bundle",
                "analysis_bundle_for_goal",
                {"goal": goal, "context": str(context or "").strip()},
            )
        ]
        if include_architecture:
            bundle_specs.append(
                (
                    "architecture_bundle",
                    "architecture_bundle",
                    {"goal": goal, "context": str(context or "").strip()},
                )
            )
        if include_change:
            bundle_specs.append(
                (
                    "change_bundle",
                    "change_bundle",
                    {
                        "goal": goal,
                        "context": str(context or "").strip(),
                        "qualified_name": resolved_qualified_name,
                        "file_path": resolved_file_path,
                    },
                )
            )
        if include_risk:
            bundle_specs.append(
                (
                    "risk_bundle",
                    "risk_bundle",
                    {
                        "goal": goal,
                        "context": str(context or "").strip(),
                        "qualified_name": resolved_qualified_name,
                        "file_path": resolved_file_path,
                    },
                )
            )
        if include_test:
            bundle_specs.append(
                (
                    "test_bundle",
                    "test_bundle",
                    {
                        "goal": goal,
                        "context": str(context or "").strip(),
                        "qualified_name": resolved_qualified_name,
                        "file_path": resolved_file_path,
                    },
                )
            )

        bundles: dict[str, dict[str, object]] = {}
        resource_uris: list[str] = []
        for session_key, bundle_name, arguments in bundle_specs:
            bundle = self._analysis_evidence.build_bundle(
                bundle_name,
                goal=arguments.get("goal") or None,
                context=arguments.get("context") or None,
                qualified_name=arguments.get("qualified_name") or None,
                file_path=arguments.get("file_path") or None,
                session_state=self._session_state,
            )
            self._session_state[f"last_{session_key}"] = bundle
            bundles[session_key] = bundle
            raw_resource_uris = bundle.get("resource_uris", [])
            if isinstance(raw_resource_uris, list):
                for resource_uri in raw_resource_uris:
                    normalized_uri = str(resource_uri).strip()
                    if normalized_uri and normalized_uri not in resource_uris:
                        resource_uris.append(normalized_uri)

        repo_semantics = self._session_state.get("repo_semantics", {})
        repo_semantics_dict = (
            cast(dict[str, object], repo_semantics)
            if isinstance(repo_semantics, dict)
            else {}
        )
        runtime_signals = (
            cast(dict[str, object], repo_semantics_dict.get("runtime_signals", {}))
            if isinstance(repo_semantics_dict.get("runtime_signals", {}), dict)
            else {}
        )
        packet = {
            "goal": goal,
            "context": str(context or "").strip(),
            "target": {
                "qualified_name": resolved_qualified_name,
                "file_path": resolved_file_path,
            },
            "bundles": bundles,
            "resource_uris": resource_uris[:15],
            "repo_semantics": repo_semantics_dict,
            "runtime_scout": {
                "dynamic_analysis_present": bool(
                    runtime_signals.get("dynamic_analysis_present", False)
                ),
                "available_dirs": (
                    runtime_signals.get("available_dirs", [])
                    if isinstance(runtime_signals, dict)
                    else []
                ),
                "artifact_count": (
                    runtime_signals.get("artifact_count", 0)
                    if isinstance(runtime_signals, dict)
                    else 0
                ),
                "graph_dirty": bool(self._session_state.get("graph_dirty", False)),
            },
            "impact_context": recent_impact,
        }
        return packet

    def _bundle_findings_excerpt(
        self,
        bundle: dict[str, object],
        *,
        limit: int = 4,
    ) -> list[str]:
        findings = bundle.get("key_findings", [])
        if not isinstance(findings, list):
            return []
        excerpts: list[str] = []
        for item in findings[:limit]:
            if not isinstance(item, dict):
                continue
            item_dict = cast(dict[str, object], item)
            summary = self._compact_text(item_dict.get("summary", ""), limit=160)
            if not summary:
                continue
            paths = item_dict.get("paths", [])
            normalized_paths = (
                [
                    self._normalize_path_value(path)
                    for path in cast(list[object], paths)[:2]
                ]
                if isinstance(paths, list)
                else []
            )
            if normalized_paths:
                excerpts.append(
                    f"{summary} @ {', '.join(path for path in normalized_paths if path)}"
                )
            else:
                excerpts.append(summary)
        return excerpts

    def _format_evidence_packet_for_prompt(
        self,
        packet: dict[str, object],
        *,
        title: str,
    ) -> str:
        lines = [title]
        bundles = packet.get("bundles", {})
        if isinstance(bundles, dict):
            bundle_map = cast(dict[str, object], bundles)
            for bundle_name, bundle in bundle_map.items():
                if not isinstance(bundle, dict):
                    continue
                bundle_dict = cast(dict[str, object], bundle)
                lines.append(
                    f"{bundle_name}: {self._compact_text(bundle_dict.get('summary', ''), limit=220)}"
                )
                for finding in self._bundle_findings_excerpt(bundle_dict):
                    lines.append(f"- {finding}")
        repo_semantics = packet.get("repo_semantics", {})
        if isinstance(repo_semantics, dict):
            repo_semantics_dict = cast(dict[str, object], repo_semantics)
            semantics_summary = self._compact_text(
                repo_semantics_dict.get("summary", ""),
                limit=240,
            )
            if semantics_summary:
                lines.append(f"repo_semantics: {semantics_summary}")
        runtime_scout = packet.get("runtime_scout", {})
        if isinstance(runtime_scout, dict):
            runtime_scout_dict = cast(dict[str, object], runtime_scout)
            runtime_dirs = runtime_scout_dict.get("available_dirs", [])
            if isinstance(runtime_dirs, list) and runtime_dirs:
                lines.append(
                    "runtime_scout: "
                    + ", ".join(str(item) for item in runtime_dirs[:4])
                )
        impact_context = packet.get("impact_context", {})
        if isinstance(impact_context, dict):
            impact_context_dict = cast(dict[str, object], impact_context)
            impacted_files = impact_context_dict.get("impacted_files", [])
            impacted_symbols = impact_context_dict.get("impacted_symbols", [])
            if isinstance(impacted_files, list) and impacted_files:
                lines.append(
                    "impacted_files: "
                    + ", ".join(str(item) for item in impacted_files[:6])
                )
            if isinstance(impacted_symbols, list) and impacted_symbols:
                lines.append(
                    "impacted_symbols: "
                    + ", ".join(str(item) for item in impacted_symbols[:6])
                )
        resource_uris = packet.get("resource_uris", [])
        if isinstance(resource_uris, list) and resource_uris:
            lines.append(
                "resource_uris: " + ", ".join(str(item) for item in resource_uris[:8])
            )
        return "\n".join(lines)

    def _discover_candidate_test_files(
        self,
        impacted_files: list[str],
        *,
        limit: int = 8,
    ) -> list[str]:
        repo_root = Path(self.project_root)
        if not repo_root.exists():
            return []

        tokens: set[str] = set()
        for path in impacted_files:
            stem = Path(path).stem.lower()
            tokens.update(part for part in re.split(r"[^a-z0-9]+", stem) if part)

        candidates: list[str] = []
        for current_root, dirs, files in os.walk(repo_root):
            dirs[:] = [
                directory
                for directory in dirs
                if directory
                not in {".git", ".venv", "venv", "node_modules", "dist", "build"}
            ]
            for file_name in files:
                lowered = file_name.lower()
                if not (
                    lowered.startswith("test_")
                    or lowered.endswith("_test.py")
                    or ".spec." in lowered
                    or ".test." in lowered
                ):
                    continue
                full_path = Path(current_root) / file_name
                relative = full_path.relative_to(repo_root).as_posix()
                if tokens and not any(token in relative.lower() for token in tokens):
                    continue
                if relative not in candidates:
                    candidates.append(relative)
                if len(candidates) >= limit:
                    return candidates
        return candidates

    def _build_test_selection_bundle(self) -> dict[str, object]:
        impact_context = self._collect_recent_impact_context()
        impacted_files = cast(list[str], impact_context.get("impacted_files", []))
        impacted_symbols = cast(list[str], impact_context.get("impacted_symbols", []))
        candidate_tests = self._discover_candidate_test_files(impacted_files)

        new_test_hints: list[str] = []
        for impacted_file in impacted_files[:5]:
            stem = Path(impacted_file).stem
            if stem:
                new_test_hints.append(f"tests/test_{stem}.py")

        bundle = {
            "has_impact_context": bool(impact_context.get("has_impact", False)),
            "impacted_files": impacted_files,
            "impacted_symbols": impacted_symbols,
            "candidate_existing_tests": candidate_tests,
            "new_test_file_hints": new_test_hints,
            "selection_strategy": (
                "impact-first" if impacted_files or impacted_symbols else "goal-only"
            ),
        }
        self._session_state["last_test_selection"] = bundle
        return bundle

    def _normalize_test_generation_output(
        self,
        content: str,
        *,
        output_mode: str = "code",
    ) -> dict[str, object]:
        normalized_mode = str(output_mode or "code").strip().lower()
        if normalized_mode not in {"code", "plan_json", "both"}:
            normalized_mode = "code"
        raw_cleaned = content.strip()
        cleaned = decode_escaped_text(content).strip()
        output: dict[str, object] = {
            "format": "text",
            "content": cleaned,
            "output_mode": normalized_mode,
            "raw_content": cleaned,
            "ui_summary": "Generated test output is available.",
        }

        try:
            direct_payload = json.loads(raw_cleaned)
        except (json.JSONDecodeError, TypeError, ValueError):
            direct_payload = None
        if isinstance(direct_payload, dict):
            parsed_payload = direct_payload
        else:
            try:
                parsed_payload = self._json_output_parser.parse(raw_cleaned)
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed_payload = {}

        if parsed_payload:
            code = decode_escaped_text(str(parsed_payload.get("code", "")).strip())
            metadata = {
                key: value for key, value in parsed_payload.items() if key != "code"
            }
            if code:
                output.update(
                    {
                        "format": "code",
                        "content": code,
                        "code": code,
                        "language": str(
                            parsed_payload.get("language", "python")
                        ).strip()
                        or "python",
                        "metadata": metadata,
                        "ui_summary": "Generated runnable test code.",
                    }
                )
                if normalized_mode == "plan_json":
                    return {
                        "format": "json",
                        "output_mode": normalized_mode,
                        "content": json.dumps(
                            {
                                "language": output.get("language", "python"),
                                "code": code,
                                "metadata": metadata,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        "metadata": metadata,
                        "raw_content": cleaned,
                        "ui_summary": "Generated structured test payload.",
                    }
                if normalized_mode == "both":
                    output["plan_json"] = {
                        "language": output.get("language", "python"),
                        "code": code,
                        "metadata": metadata,
                    }
                return output
            output.update(
                {
                    "format": "json",
                    "content": json.dumps(
                        parsed_payload,
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "metadata": parsed_payload,
                    "ui_summary": "Generated structured test plan.",
                }
            )
            return output

        language, code_block = extract_code_block(
            cleaned,
            preferred_languages={"python", "pytest"},
        )
        if code_block:
            output.update(
                {
                    "format": "code",
                    "content": code_block,
                    "code": code_block,
                    "language": language or "python",
                    "ui_summary": "Generated runnable test code.",
                }
            )
            if normalized_mode == "plan_json":
                return {
                    "format": "json",
                    "output_mode": normalized_mode,
                    "content": json.dumps(
                        {
                            "language": language or "python",
                            "code": code_block,
                            "metadata": {"source": "fenced_code"},
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "metadata": {"source": "fenced_code"},
                    "raw_content": cleaned,
                    "ui_summary": "Generated structured test payload.",
                }
            if normalized_mode == "both":
                output["plan_json"] = {
                    "language": language or "python",
                    "code": code_block,
                    "metadata": {"source": "fenced_code"},
                }
        return output

    @staticmethod
    def _parse_failure_reasons(
        failure_reasons: str | None,
        normalized_issues: list[str],
    ) -> list[str]:
        parsed: list[str] = []
        if isinstance(failure_reasons, str) and failure_reasons.strip():
            raw = failure_reasons.strip()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, list):
                parsed.extend(
                    str(item).strip().lower() for item in payload if str(item).strip()
                )
            else:
                parsed.extend(
                    item.strip().lower() for item in raw.split(",") if item.strip()
                )

        heuristics = {
            "hallucinated_fixture": (
                "hallucinated fixture",
                "unknown fixture",
                "fixture not found",
            ),
            "unverified_assertion": (
                "unverified assertion",
                "unverified status code",
                "exact string assert",
            ),
            "missing_cleanup": ("missing cleanup", "cleanup missing", "no cleanup"),
            "layer_mismatch": ("layer mismatch", "wrong layer"),
            "overcoupled_test": ("overcoupled", "coupling", "too many behaviors"),
            "graph_sync_failed": ("graph sync failed", "stale graph"),
        }
        for reason, markers in heuristics.items():
            if any(
                marker in issue for issue in normalized_issues for marker in markers
            ):
                parsed.append(reason)

        return sorted(set(parsed))

    @staticmethod
    def _normalize_quality_score(raw_value: str) -> float:
        try:
            parsed = float(raw_value)
        except (TypeError, ValueError):
            parsed = 0.0
        return round(max(0.0, min(1.0, parsed)), 3)

    @classmethod
    def _normalize_optional_quality_score(cls, raw_value: str | None) -> float | None:
        if raw_value is None:
            return None
        candidate = str(raw_value).strip()
        if not candidate:
            return None
        return cls._normalize_quality_score(candidate)

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

    def _compute_context_confidence_model(
        self,
        *,
        graph_evidence_count: int,
        code_evidence_count: int,
        semantic_similarity_mean: float,
        manual_memory_add_count: int,
        exploration_summary: dict[str, object] | None = None,
    ) -> dict[str, object]:
        query_chunks_raw = self._session_state.get("query_result_chunks", [])
        graph_row_count = 0
        if isinstance(query_chunks_raw, list):
            for chunk in query_chunks_raw:
                if isinstance(chunk, list):
                    graph_row_count += len(
                        [row for row in chunk if isinstance(row, dict)]
                    )

        file_depth_sum = self._coerce_float(
            self._session_state.get("file_depth_sum", 0.0)
        )
        file_depth_count = self._coerce_int(
            self._session_state.get("file_depth_count", 0)
        )
        memory_pattern_query_count = self._coerce_int(
            self._session_state.get("memory_pattern_query_count", 0)
        )

        graph_density = max(
            0.0,
            min(
                1.0,
                max(
                    graph_evidence_count / 3.0,
                    graph_row_count / 50.0,
                ),
            ),
        )
        semantic_overlap = max(0.0, min(1.0, semantic_similarity_mean))
        if file_depth_count > 0:
            file_depth_mean = max(0.0, file_depth_sum / float(file_depth_count))
            file_depth = max(0.0, min(1.0, file_depth_mean / 4.0))
        elif code_evidence_count > 0:
            file_depth_mean = 1.0
            file_depth = max(0.0, min(1.0, code_evidence_count / 4.0))
        else:
            file_depth_mean = 0.0
            file_depth = 0.0

        memory_match_raw = (memory_pattern_query_count + manual_memory_add_count) / 2.0
        memory_match = max(0.0, min(1.0, memory_match_raw))

        weights = {
            "graph_density": 0.35,
            "semantic_overlap": 0.30,
            "file_depth": 0.20,
            "memory_match": 0.15,
        }
        score = (
            (weights["graph_density"] * graph_density)
            + (weights["semantic_overlap"] * semantic_overlap)
            + (weights["file_depth"] * file_depth)
            + (weights["memory_match"] * memory_match)
        )
        exploration_data = exploration_summary or {}
        exploration_calls = self._coerce_int(exploration_data.get("calls", 0))
        exploration_success_rate = max(
            0.0,
            min(1.0, self._coerce_float(exploration_data.get("success_rate", 0.0))),
        )
        exploration_avg_reward = max(
            0.0,
            min(1.0, self._coerce_float(exploration_data.get("avg_reward", 0.0))),
        )
        calibration_coverage = max(0.0, min(1.0, exploration_calls / 20.0))
        calibration_quality = (exploration_success_rate * 0.4) + (
            exploration_avg_reward * 0.6
        )
        calibration_delta = (calibration_quality - 0.5) * 0.10 * calibration_coverage
        score = max(0.0, min(1.0, score + calibration_delta))
        required = 0.6
        score_rounded = round(score, 3)
        return {
            "name": "context_confidence_v1",
            "score": score_rounded,
            "required": required,
            "pass": score >= required,
            "status": (
                "high" if score >= 0.8 else "medium" if score >= required else "low"
            ),
            "weights": weights,
            "components": {
                "graph_density": round(graph_density, 3),
                "semantic_overlap": round(semantic_overlap, 3),
                "file_depth": round(file_depth, 3),
                "memory_match": round(memory_match, 3),
                "exploration_calibration": round(calibration_delta, 3),
            },
            "signals": {
                "graph_row_count": graph_row_count,
                "file_depth_mean": round(file_depth_mean, 3),
                "file_depth_count": file_depth_count,
                "memory_pattern_query_count": memory_pattern_query_count,
                "manual_memory_add_count": manual_memory_add_count,
                "confidence_calibration": {
                    "exploration_calls": exploration_calls,
                    "coverage": round(calibration_coverage, 3),
                    "quality": round(calibration_quality, 3),
                    "delta": round(calibration_delta, 3),
                },
            },
        }

    def _build_guard_partition(
        self,
        readiness: dict[str, object],
    ) -> dict[str, object]:
        hard_guard_checks = {
            "preflight_gate": bool(
                self._session_state.get("preflight_project_selected", False)
            ),
            "phase_gate": self._current_execution_phase() in self._EXECUTION_PHASES,
            "scope_gate": True,
            "write_safety_gate": True,
            "tool_chain_guard": True,
        }
        hard_failed = [
            name for name, gate_pass in hard_guard_checks.items() if not gate_pass
        ]

        soft_guards = [
            "confidence_gate",
            "context_confidence_gate",
            "pattern_reuse_gate",
            "completion_gate",
            "test_quality_gate",
            "impact_graph_gate",
            "replan_gate",
        ]
        soft_failed: list[str] = []
        for gate_name in soft_guards:
            gate_payload = readiness.get(gate_name, {})
            if isinstance(gate_payload, dict):
                gate_payload_dict = cast(dict[str, object], gate_payload)
                if not bool(gate_payload_dict.get("pass", False)):
                    soft_failed.append(gate_name)

        return {
            "hard": {
                "required": list(hard_guard_checks.keys()),
                "failed": hard_failed,
                "pass": not hard_failed,
                "severity": "blocking",
            },
            "soft": {
                "required": soft_guards,
                "failed": soft_failed,
                "pass": not soft_failed,
                "severity": "advisory_or_done_decision",
            },
        }

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
            result_text = str(result)
            if "successfully" not in result_text.lower():
                return {"error": f"chunk_apply_failed_{idx}", "result": result_text}
            results.append(result_text)
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
        edit_success_count = self._coerce_int(
            self._session_state.get("edit_success_count", 0)
        )
        graph_dirty = bool(self._session_state.get("graph_dirty", False))
        last_graph_sync_status = str(
            self._session_state.get("last_graph_sync_status", "not_needed")
        ).strip()
        graph_sync_required = (
            bool(self._session_state.get("preflight_project_selected", False))
            and edit_success_count > 0
        )
        semantic_similarity_mean = self._coerce_float(
            self._session_state.get("semantic_similarity_mean", 0.0)
        )
        pattern_reuse_score = self._coerce_float(
            self._session_state.get("pattern_reuse_score", 0.0)
        )
        exploration_summary = self._build_exploration_summary()
        context_confidence = self._compute_context_confidence_model(
            graph_evidence_count=graph_evidence_count,
            code_evidence_count=code_evidence_count,
            semantic_similarity_mean=semantic_similarity_mean,
            manual_memory_add_count=manual_memory_add_count,
            exploration_summary=exploration_summary,
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
        if graph_sync_required:
            completion_requirements["graph_sync"] = (
                not graph_dirty and last_graph_sync_status == "ok"
            )
        completion_missing = [
            name for name, satisfied in completion_requirements.items() if not satisfied
        ]

        context_components_raw = context_confidence.get("components", {})
        context_components = (
            cast(dict[str, object], context_components_raw)
            if isinstance(context_components_raw, dict)
            else {}
        )
        context_score = self._coerce_float(context_confidence.get("score", 0.0))
        confidence_components = {
            "graph_density": self._coerce_float(
                context_components.get("graph_density", 0.0)
            ),
            "semantic_overlap": self._coerce_float(
                context_components.get("semantic_overlap", 0.0)
            ),
            "file_depth": self._coerce_float(context_components.get("file_depth", 0.0)),
            "memory_match": self._coerce_float(
                context_components.get("memory_match", 0.0)
            ),
        }
        confidence_total = context_score * 3.0

        confidence_required = 2.0
        pattern_required = 70.0
        impact_threshold = 25
        replan_required = bool(self._session_state.get("replan_required", False))
        replan_reasons = self._session_state.get("replan_reasons", [])
        if not isinstance(replan_reasons, list):
            replan_reasons = []

        readiness = {
            "state_machine_gate": {
                "enabled": True,
                "pass": bool(
                    self._session_state.get("last_phase_transition_allowed", True)
                ),
                "error": str(
                    self._session_state.get("last_phase_transition_error", "")
                ).strip(),
                "current_phase": self._current_execution_phase(),
            },
            "confidence_gate": {
                "score": round(confidence_total, 3),
                "required": confidence_required,
                "components": confidence_components,
                "model": "context_confidence_v1",
                "context_confidence_score": round(context_score, 3),
                "pass": confidence_total >= confidence_required,
            },
            "context_confidence_gate": context_confidence,
            "pattern_reuse_gate": {
                "score": round(pattern_reuse_score, 3),
                "required": pattern_required,
                "pass": pattern_reuse_score >= pattern_required,
            },
            "completion_gate": {
                "required": list(completion_requirements.keys()),
                "missing": completion_missing,
                "pass": not completion_missing,
            },
            "test_quality_gate": {
                "score": round(test_quality_total, 3),
                "required": 2.0,
                "pass": test_quality_pass,
            },
            "graph_sync_gate": {
                "required": graph_sync_required,
                "dirty": graph_dirty,
                "status": last_graph_sync_status,
                "pass": (not graph_sync_required)
                or (not graph_dirty and last_graph_sync_status == "ok"),
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
                "context_confidence_score": round(context_score, 3),
                "graph_dirty": graph_dirty,
                "last_graph_sync_status": last_graph_sync_status,
                "last_graph_sync_timestamp": self._coerce_int(
                    self._session_state.get("last_graph_sync_timestamp", 0)
                ),
                "execution_feedback_count": self._coerce_int(
                    self._session_state.get("execution_feedback_count", 0)
                ),
                "memory_pattern_query_count": self._coerce_int(
                    self._session_state.get("memory_pattern_query_count", 0)
                ),
                "tool_usefulness_ranking": self._compute_tool_usefulness_ranking(
                    limit=5
                ),
                "edit_success_count": edit_success_count,
                "policy_allow_count": self._coerce_int(
                    self._session_state.get("policy_allow_count", 0)
                ),
                "policy_deny_count": self._coerce_int(
                    self._session_state.get("policy_deny_count", 0)
                ),
                "fallback_exploration": self._build_exploration_summary(),
            },
        }
        readiness["guard_partition"] = self._build_guard_partition(readiness)
        return readiness

    async def get_execution_readiness(self) -> dict[str, object]:
        if self._current_execution_phase() == "retrieval":
            self._set_execution_phase(
                "validation",
                "get_execution_readiness_phase_recovery",
            )
        readiness = self._compute_execution_readiness()
        readiness["execution_state"] = self._build_execution_state_contract()
        return readiness

    @staticmethod
    def _done_protocol_checks(readiness: dict[str, object]) -> list[dict[str, object]]:
        check_specs = [
            ("state_machine_gate", "state machine gate detected an invalid transition"),
            ("confidence_gate", "confidence gate is below required threshold"),
            (
                "context_confidence_gate",
                "context confidence model score is below required threshold",
            ),
            ("pattern_reuse_gate", "pattern reuse score is below required threshold"),
            ("completion_gate", "required completion evidence is missing"),
            ("test_quality_gate", "test quality gate did not pass"),
            ("graph_sync_gate", "graph sync gate did not pass"),
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
        if "graph_read" in missing:
            return {
                "action": "collect_graph_evidence",
                "tool": "query_code_graph",
                "why": "Completion gate is missing graph-read evidence.",
                "params_hint": {
                    "natural_language_query": "dependencies of target module"
                },
            }
        if "code_source" in missing:
            return {
                "action": "collect_code_evidence",
                "tool": "read_file",
                "why": "Completion gate is missing code source evidence after graph evidence.",
                "params_hint": {"file_path": "path/to/file.py"},
            }
        if "impact_graph" in missing:
            return {
                "action": "run_impact_analysis",
                "tool": "impact_graph",
                "why": "Impact graph gate requires dependency impact data.",
                "params_hint": {"qualified_name": "module.Class.method", "depth": 3},
            }
        if "graph_sync" in missing:
            return {
                "action": "refresh_graph_after_edits",
                "tool": "sync_graph_updates",
                "why": "Source edits require a fresh graph sync before completion.",
                "params_hint": {
                    "user_requested": True,
                    "reason": "refresh graph after code edits",
                    "sync_mode": "fast",
                },
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
        if self._current_execution_phase() == "preflight":
            self._set_execution_phase(
                "retrieval",
                "validate_done_decision_phase_recovery",
            )
        self._set_execution_phase("validation", "validate_done_decision_start")
        readiness = self._compute_execution_readiness()
        checks = self._done_protocol_checks(readiness)
        blockers = [
            str(item.get("reason", ""))
            for item in checks
            if isinstance(item, dict) and item.get("pass") is False
        ]
        decision = "done" if not blockers else "not_done"
        evidence_packet = self._build_evidence_bundle_packet(
            goal=str(goal or "validate done decision").strip()
            or "validate done decision",
            context=context,
            include_architecture=True,
            include_change=True,
            include_risk=True,
            include_test=True,
        )

        validator_payload = {
            "goal": goal or "",
            "context": context or "",
            "decision": decision,
            "blockers": blockers,
            "readiness": readiness,
            "checks": checks,
            "evidence_packet": evidence_packet,
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
        response = {
            "status": "ok",
            "decision": final_decision,
            "protocol": {
                "checks": checks,
                "pass": len(blockers) == 0,
            },
            "blockers": blockers,
            "guard_partition": readiness.get("guard_partition", {}),
            "confidence_summary": confidence_summary,
            "next_best_action": next_best_action,
            "ui_summary": ui_summary,
            "validator": {
                "decision": validator_decision,
                "rationale": str(validator_output.get("rationale", "")).strip(),
                "required_actions": normalized_required_actions,
            },
            "deterministic_decision": decision,
            "evidence_packet": evidence_packet,
            "readiness": readiness,
            "execution_state": self._build_execution_state_contract(),
        }
        if final_decision == "done":
            self._set_execution_phase("execution", "validate_done_decision_done")
        else:
            self._set_execution_phase("retrieval", "validate_done_decision_not_done")
        response["execution_state"] = self._build_execution_state_contract()
        return response

    def get_tool_schemas(self) -> list[MCPToolSchema]:
        return [
            MCPToolSchema(
                name=metadata.name,
                description=(
                    f"{metadata.description} "
                    f"[Session stage: {self._tool_stage_name(metadata.name)}]"
                ).strip(),
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
