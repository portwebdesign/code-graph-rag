from __future__ import annotations

from ..protocols import AnalysisRunnerProtocol
from ..types import NodeRecord, RelationshipRecord


class SupplementalAnalysisMixin:
    def _run_supplemental_analyses(
        self: AnalysisRunnerProtocol,
        summary: dict[str, object],
        modules: set[str] | None,
        module_names: set[str],
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
        module_path_map: dict[str, str],
        module_paths: list[str] | None,
        incremental_paths: list[str] | None,
        use_db: bool,
    ) -> None:
        if self._should_run("parameters", modules) and nodes:
            summary["parameters"] = self._extract_parameters(nodes)
        if self._should_run("nested_functions", modules) and nodes:
            summary["nested_functions"] = self._detect_nested_functions(
                nodes, module_path_map
            )
        if (
            self._should_run("complexity", modules)
            and "complexity" not in module_names
            and nodes
        ):
            summary["complexity"] = self._compute_complexity(nodes, module_path_map)

        if self._should_run("usage", modules):
            if use_db:
                summary["usage"] = self._symbol_usage_db(module_paths)
            elif nodes and relationships:
                summary["usage"] = self._symbol_usage(nodes, relationships, node_by_id)

        if self._should_run("cycles", modules) and nodes and relationships:
            summary["cycles"] = self._cycle_detection(nodes, relationships, node_by_id)
        if self._should_run("fan_in_out", modules) and nodes and relationships:
            summary["fan_in_out"] = self._fan_in_out(nodes, relationships, node_by_id)
        if self._should_run("churn", modules) and nodes:
            summary["churn"] = self._churn_ownership(nodes)
        if self._should_run("public_api", modules) and nodes:
            summary["public_api"] = self._public_api_surface(nodes)
        if self._should_run("duplicates", modules) and nodes:
            summary["duplicates"] = self._duplicate_code_report(nodes, module_path_map)
        if (
            self._should_run("security", modules)
            and "security" not in module_names
            and nodes
        ):
            summary["security"] = self._security_scan(nodes)
        if self._should_run("test_coverage_proxy", modules) and nodes:
            summary["test_coverage_proxy"] = self._test_coverage_proxy(nodes)
        if self._should_run("blast_radius", modules) and nodes and relationships:
            summary["blast_radius"] = self._blast_radius(
                nodes, relationships, node_by_id
            )
        if self._should_run("layering_violations", modules) and nodes and relationships:
            summary["layering_violations"] = self._layering_violations(
                nodes, relationships, node_by_id
            )
        if (
            self._should_run("dependency_risk", modules)
            and "dependency_risk" not in module_names
            and nodes
            and relationships
        ):
            summary["dependency_risk"] = self._dependency_risk(
                nodes, relationships, node_by_id
            )
        if (
            self._should_run("performance_hotspots", modules)
            and "performance_hotspots" not in module_names
            and nodes
            and relationships
        ):
            summary["performance_hotspots"] = self._performance_hotspots(
                nodes, relationships, node_by_id
            )
        if self._should_run("sast_taint_tracking", modules) and nodes:
            summary["sast_taint_tracking"] = self._sast_taint_tracking(nodes)
        if self._should_run("license_compliance", modules):
            summary["license_compliance"] = self._license_compliance()
        if self._should_run("arch_drift", modules) and nodes and relationships:
            summary["arch_drift"] = self._arch_drift(nodes, relationships, node_by_id)

        if self._should_run("unused_imports", modules):
            if use_db:
                summary["unused_imports"] = self._unused_imports_db(module_paths)
            elif nodes:
                summary["unused_imports"] = self._unused_imports(
                    nodes, incremental_paths
                )

        if self._should_run("unused_variables", modules) and nodes:
            summary["unused_variables"] = self._unused_variables(
                nodes, incremental_paths
            )
        if self._should_run("unreachable_code", modules) and nodes:
            summary["unreachable_code"] = self._unreachable_code(
                nodes, incremental_paths
            )
        if self._should_run("refactoring_candidates", modules) and nodes:
            summary["refactoring_candidates"] = self._refactoring_candidates(nodes)
        if self._should_run("secret_scan", modules) and nodes:
            summary["secret_scan"] = self._secret_scan(nodes, incremental_paths)

        if self._should_run("api_stability_trend", modules):
            api_stats = summary.get("public_api") if "public_api" in summary else {}
            summary["api_stability_trend"] = self._api_stability_trend(api_stats)

        if (
            self._should_run("migration_plan", modules)
            and "migration_plan" not in module_names
            and nodes
            and relationships
        ):
            summary["migration_plan"] = self._migration_plan(
                nodes,
                relationships,
                node_by_id,
                summary.get("test_coverage_proxy", {}),
                summary.get("dependency_risk", {}),
                summary.get("performance_hotspots", {}),
                summary.get("layering_violations", {}),
            )
