from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..services.protocols import IngestorProtocol
from .types import NodeRecord, RelationshipRecord


@runtime_checkable
class AnalysisRunnerProtocol(Protocol):
    ingestor: IngestorProtocol
    repo_path: Path
    project_name: str

    def _should_run(self, name: str, modules: set[str] | None) -> bool: ...

    def _resolve_node_path(
        self, node: NodeRecord, module_path_map: dict[str, str]
    ) -> str | None: ...

    def _collect_cycles(self, graph: dict[int, set[int]]) -> list[list[int]]: ...

    def _write_json_report(self, filename: str, payload: object) -> Path: ...

    def _write_text_report(self, filename: str, content: str) -> Path: ...

    def _collect_file_paths(self, nodes: list[NodeRecord]) -> list[str]: ...

    def _primary_label(self, node: NodeRecord) -> str: ...

    def _analysis_output_dir(self) -> Path: ...

    def _extract_parameters(self, nodes: list[NodeRecord]) -> dict[str, Any]: ...

    def _detect_nested_functions(
        self, nodes: list[NodeRecord], module_path_map: dict[str, str]
    ) -> dict[str, Any]: ...

    def _compute_complexity(
        self, nodes: list[NodeRecord], module_path_map: dict[str, str]
    ) -> dict[str, Any]: ...

    def _symbol_usage_db(self, module_paths: list[str] | None) -> dict[str, Any]: ...

    def _symbol_usage(
        self,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
    ) -> dict[str, Any]: ...

    def _cycle_detection(
        self,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
    ) -> dict[str, Any]: ...

    def _fan_in_out(
        self,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
    ) -> dict[str, Any]: ...

    def _churn_ownership(self, nodes: list[NodeRecord]) -> dict[str, Any]: ...

    def _public_api_surface(self, nodes: list[NodeRecord]) -> dict[str, Any]: ...

    def _duplicate_code_report(
        self, nodes: list[NodeRecord], module_path_map: dict[str, str]
    ) -> dict[str, Any]: ...

    def _security_scan(self, nodes: list[NodeRecord]) -> dict[str, Any]: ...

    def _test_coverage_proxy(self, nodes: list[NodeRecord]) -> dict[str, Any]: ...

    def _blast_radius(
        self,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
    ) -> dict[str, Any]: ...

    def _layering_violations(
        self,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
    ) -> dict[str, Any]: ...

    def _dependency_risk(
        self,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
    ) -> dict[str, Any]: ...

    def _performance_hotspots(
        self,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
    ) -> dict[str, Any]: ...

    def _sast_taint_tracking(self, nodes: list[NodeRecord]) -> dict[str, Any]: ...

    def _license_compliance(self) -> dict[str, Any]: ...

    def _arch_drift(
        self,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
    ) -> dict[str, Any]: ...

    def _unused_imports_db(self, module_paths: list[str] | None) -> dict[str, Any]: ...

    def _unused_imports(
        self, nodes: list[NodeRecord], incremental_paths: list[str] | None
    ) -> dict[str, Any]: ...

    def _unused_variables(
        self, nodes: list[NodeRecord], file_paths: list[str] | None = None
    ) -> dict[str, Any]: ...

    def _unreachable_code(
        self, nodes: list[NodeRecord], file_paths: list[str] | None = None
    ) -> dict[str, Any]: ...

    def _refactoring_candidates(self, nodes: list[NodeRecord]) -> dict[str, Any]: ...

    def _secret_scan(
        self, nodes: list[NodeRecord], file_paths: list[str] | None = None
    ) -> dict[str, Any]: ...

    def _api_stability_trend(self, api_stats: Any) -> dict[str, Any]: ...

    def _format_migration_prompt(
        self, summary: dict[str, Any], phases: list[Any], modules: list[Any]
    ) -> str: ...

    def _migration_plan(
        self,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
        coverage: Any,
        risk: Any,
        hotspots: Any,
        violations: Any,
    ) -> dict[str, Any]: ...
