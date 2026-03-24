from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from codebase_rag.analysis.analysis_runner import AnalysisRunner
from codebase_rag.core import constants as cs
from codebase_rag.graph_db.cypher_queries import (
    CYPHER_ANALYSIS_DEAD_CODE,
    CYPHER_ANALYSIS_DEAD_CODE_FILTERED,
    CYPHER_ANALYSIS_TOTAL_FUNCTIONS,
    CYPHER_ANALYSIS_TOTAL_FUNCTIONS_FILTERED,
)
from codebase_rag.services.protocols import IngestorProtocol
from codebase_rag.tests.conftest import run_updater


class FastAPITruthfulnessAnalysisIngestor:
    def __init__(self) -> None:
        self.nodes: list[tuple[str, dict[str, Any]]] = []
        self.relationships: list[
            tuple[
                tuple[str, str, Any],
                str,
                tuple[str, str, Any],
                dict[str, Any] | None,
            ]
        ] = []

    def ensure_node_batch(self, label: str, properties: dict[str, Any]) -> None:
        self.nodes.append((label, dict(properties)))

    def ensure_relationship_batch(
        self,
        from_spec: tuple[str, str, Any],
        rel_type: str,
        to_spec: tuple[str, str, Any],
        properties: dict[str, Any] | None = None,
    ) -> None:
        self.relationships.append(
            (from_spec, rel_type, to_spec, dict(properties) if properties else None)
        )

    def flush_all(self) -> None:
        return None

    def execute_write(self, query: str, params: dict[str, Any] | None = None) -> None:
        return None

    def fetch_all(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        if query in {
            CYPHER_ANALYSIS_TOTAL_FUNCTIONS,
            CYPHER_ANALYSIS_TOTAL_FUNCTIONS_FILTERED,
        }:
            return [{"total_functions": len(self._function_nodes())}]
        if query in {CYPHER_ANALYSIS_DEAD_CODE, CYPHER_ANALYSIS_DEAD_CODE_FILTERED}:
            return self._build_dead_code_rows(params or {})
        return []

    def _function_nodes(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (label, props)
            for label, props in self.nodes
            if label in {cs.NodeLabel.FUNCTION, cs.NodeLabel.METHOD}
        ]

    def _build_dead_code_rows(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        module_paths = set(cast(list[str] | None, params.get("module_paths")) or [])
        registration_rel_types = {
            cs.RelationshipType.HAS_ENDPOINT,
            cs.RelationshipType.ROUTES_TO_ACTION,
            cs.RelationshipType.REQUESTS_ENDPOINT,
            cs.RelationshipType.REGISTERS_SERVICE,
            cs.RelationshipType.REGISTERS_CALLBACK,
            cs.RelationshipType.HOOKS,
            cs.RelationshipType.REGISTERS_BLOCK,
            cs.RelationshipType.USES_HANDLER,
            cs.RelationshipType.USES_SERVICE,
            cs.RelationshipType.PROVIDES_SERVICE,
        }

        rows: list[dict[str, Any]] = []
        for label, props in self._function_nodes():
            path = str(props.get(cs.KEY_PATH) or "")
            if module_paths and path not in module_paths:
                continue

            qualified_name = str(props.get(cs.KEY_QUALIFIED_NAME) or "")
            call_in_degree = self._count_inbound(
                qualified_name, {cs.RelationshipType.CALLS}
            )
            dispatch_in_degree = self._count_inbound(
                qualified_name,
                {cs.RelationshipType.DISPATCHES_TO},
            )
            registration_links = self._count_inbound(
                qualified_name,
                registration_rel_types,
            )
            semantic_registration_links = self._count_semantic_registration_links(
                qualified_name
            )
            if registration_links > 0 or semantic_registration_links > 0:
                continue
            combined_in_degree = call_in_degree + dispatch_in_degree
            if combined_in_degree > 0:
                continue

            rows.append(
                {
                    "qualified_name": qualified_name,
                    "name": props.get(cs.KEY_NAME),
                    "path": path,
                    "start_line": props.get(cs.KEY_START_LINE),
                    "label": label,
                    "call_in_degree": call_in_degree,
                    "dispatch_in_degree": dispatch_in_degree,
                    "combined_in_degree": combined_in_degree,
                    "out_call_count": self._count_outbound(
                        qualified_name,
                        {cs.RelationshipType.CALLS},
                    ),
                    "is_entrypoint_name": False,
                    "has_entry_decorator": False,
                    "decorator_links": 0,
                    "registration_links": registration_links,
                    "semantic_registration_links": semantic_registration_links,
                    "imported_by_cli_links": 0,
                    "config_reference_links": 0,
                    "decorators": props.get(cs.KEY_DECORATORS) or [],
                    "is_exported": bool(props.get(cs.KEY_IS_EXPORTED) or False),
                }
            )

        return sorted(
            rows,
            key=lambda row: (str(row["path"]), int(row.get("start_line") or 0)),
        )

    def _count_inbound(self, qualified_name: str, rel_types: set[str]) -> int:
        return sum(
            1
            for _, recorded_rel_type, to_spec, _ in self.relationships
            if recorded_rel_type in rel_types
            and to_spec[1] == cs.KEY_QUALIFIED_NAME
            and to_spec[2] == qualified_name
        )

    def _count_outbound(self, qualified_name: str, rel_types: set[str]) -> int:
        return sum(
            1
            for from_spec, recorded_rel_type, _, _ in self.relationships
            if recorded_rel_type in rel_types
            and from_spec[1] == cs.KEY_QUALIFIED_NAME
            and from_spec[2] == qualified_name
        )

    def _count_semantic_registration_links(self, qualified_name: str) -> int:
        semantic_nodes = {
            from_spec[2]
            for from_spec, recorded_rel_type, to_spec, _ in self.relationships
            if recorded_rel_type == cs.RelationshipType.RESOLVES_TO
            and to_spec[1] == cs.KEY_QUALIFIED_NAME
            and to_spec[2] == qualified_name
            and from_spec[0]
            in {cs.NodeLabel.DEPENDENCY_PROVIDER, cs.NodeLabel.AUTH_POLICY}
        }
        if not semantic_nodes:
            return 0
        return sum(
            1
            for _, recorded_rel_type, to_spec, _ in self.relationships
            if recorded_rel_type
            in {cs.RelationshipType.USES_DEPENDENCY, cs.RelationshipType.SECURED_BY}
            and to_spec[1] == cs.KEY_QUALIFIED_NAME
            and to_spec[2] in semantic_nodes
        )


def test_dead_code_pipeline_drops_fastapi_dependency_and_callback_false_positives(
    temp_repo: Path,
) -> None:
    project = temp_repo / "fastapi_dead_code_truthfulness"
    project.mkdir()
    (project / "main.py").write_text(
        """from fastapi import APIRouter, Depends, FastAPI

app = FastAPI(generate_unique_id_function=generate_operation_id)
router = APIRouter()


def get_current_principal() -> str:
    return "principal"


def require_authenticated_principal(
    principal: str = Depends(get_current_principal),
) -> str:
    return principal


def generate_operation_id(route=None) -> str:
    return "op-id"


def _get_ai_graph_service() -> str:
    return "graph"


@router.get("/ai", dependencies=[Depends(require_authenticated_principal)])
async def ai_status(service: str = Depends(_get_ai_graph_service)) -> dict[str, str]:
    return {"service": service}


app.include_router(router)
""",
        encoding="utf-8",
    )

    ingestor = FastAPITruthfulnessAnalysisIngestor()
    run_updater(project, ingestor)

    runner = AnalysisRunner(cast(IngestorProtocol, ingestor), project)
    dead_code_result = runner._dead_code_report_db(module_paths=None)

    report_path = project / "output" / "analysis" / "dead-code-except-test.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    reported_symbols = {
        symbol["qualified_name"]
        for file_entry in report["files"]
        for symbol in file_entry["dead_symbols"]
    }

    assert (
        "fastapi_dead_code_truthfulness.main.get_current_principal"
        not in reported_symbols
    )
    assert (
        "fastapi_dead_code_truthfulness.main.require_authenticated_principal"
        not in reported_symbols
    )
    assert (
        "fastapi_dead_code_truthfulness.main.generate_operation_id"
        not in reported_symbols
    )
    assert (
        "fastapi_dead_code_truthfulness.main._get_ai_graph_service"
        not in reported_symbols
    )
    assert dead_code_result["dead_code_except_test"]["filtered_dead_symbols"] == 0


def test_dead_code_pipeline_drops_cross_module_fastapi_dependency_false_positives(
    temp_repo: Path,
) -> None:
    project = temp_repo / "fastapi_cross_module_dead_code_truthfulness"
    project.mkdir()
    (project / "helpers.py").write_text(
        """from fastapi import Depends\n\n\ndef get_current_principal() -> str:\n    return "principal"\n\n\ndef require_authenticated_principal(\n    principal: str = Depends(get_current_principal),\n) -> str:\n    return principal\n""",
        encoding="utf-8",
    )
    (project / "main.py").write_text(
        """from fastapi import APIRouter, Depends\n\nfrom .helpers import require_authenticated_principal\n\nrouter = APIRouter()\n\n\n@router.get("/secure", dependencies=[Depends(require_authenticated_principal)])\nasync def secure_status() -> dict[str, str]:\n    return {"status": "ok"}\n""",
        encoding="utf-8",
    )

    ingestor = FastAPITruthfulnessAnalysisIngestor()
    run_updater(project, ingestor)

    runner = AnalysisRunner(cast(IngestorProtocol, ingestor), project)
    dead_code_result = runner._dead_code_report_db(module_paths=None)

    report_path = project / "output" / "analysis" / "dead-code-except-test.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    reported_symbols = {
        symbol["qualified_name"]
        for file_entry in report["files"]
        for symbol in file_entry["dead_symbols"]
    }

    assert (
        "fastapi_cross_module_dead_code_truthfulness.helpers.get_current_principal"
        not in reported_symbols
    )
    assert (
        "fastapi_cross_module_dead_code_truthfulness.helpers.require_authenticated_principal"
        not in reported_symbols
    )
    assert dead_code_result["dead_code_except_test"]["filtered_dead_symbols"] == 0
