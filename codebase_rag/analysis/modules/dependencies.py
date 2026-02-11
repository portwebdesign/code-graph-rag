from __future__ import annotations

from typing import Any

from .base_module import AnalysisContext, AnalysisModule


class DependenciesModule(AnalysisModule):
    def get_name(self) -> str:
        return "dependency_risk"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        if context.nodes and context.relationships:
            return context.runner._dependency_risk(
                context.nodes,
                context.relationships,
                context.node_by_id,
            )
        return {}
