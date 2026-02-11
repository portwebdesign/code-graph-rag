from __future__ import annotations

from typing import Any

from .base_module import AnalysisContext, AnalysisModule


class MigrationModule(AnalysisModule):
    def get_name(self) -> str:
        return "migration_plan"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        if not context.nodes or not context.relationships:
            return {}

        summary = context.summary
        test_coverage_proxy = summary.get("test_coverage_proxy")
        if not test_coverage_proxy:
            test_coverage_proxy = context.runner._test_coverage_proxy(context.nodes)
            summary["test_coverage_proxy"] = test_coverage_proxy

        dependency_risk = summary.get("dependency_risk")
        if not dependency_risk:
            dependency_risk = context.runner._dependency_risk(
                context.nodes,
                context.relationships,
                context.node_by_id,
            )
            summary["dependency_risk"] = dependency_risk

        performance_hotspots = summary.get("performance_hotspots")
        if not performance_hotspots:
            performance_hotspots = context.runner._performance_hotspots(
                context.nodes,
                context.relationships,
                context.node_by_id,
            )
            summary["performance_hotspots"] = performance_hotspots

        layering_violations = summary.get("layering_violations")
        if not layering_violations:
            layering_violations = context.runner._layering_violations(
                context.nodes,
                context.relationships,
                context.node_by_id,
            )
            summary["layering_violations"] = layering_violations

        return context.runner._migration_plan(
            context.nodes,
            context.relationships,
            context.node_by_id,
            test_coverage_proxy,
            dependency_risk,
            performance_hotspots,
            layering_violations,
        )
