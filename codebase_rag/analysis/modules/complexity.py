from __future__ import annotations

from typing import Any

from .base_module import AnalysisContext, AnalysisModule


class ComplexityModule(AnalysisModule):
    def get_name(self) -> str:
        return "complexity"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        if not context.nodes:
            return {}
        return context.runner._compute_complexity(
            context.nodes, context.module_path_map
        )
