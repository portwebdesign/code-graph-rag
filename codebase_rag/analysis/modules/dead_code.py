from __future__ import annotations

from typing import Any

from .base_module import AnalysisContext, AnalysisModule


class DeadCodeModule(AnalysisModule):
    def get_name(self) -> str:
        return "dead_code"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        if context.use_db:
            return context.runner._dead_code_report_db(context.module_paths)
        if context.nodes and context.relationships:
            return context.runner._dead_code_report(
                context.nodes,
                context.relationships,
                context.node_by_id,
            )
        return {}
