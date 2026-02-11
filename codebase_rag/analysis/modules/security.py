from __future__ import annotations

from typing import Any

from .base_module import AnalysisContext, AnalysisModule


class SecurityModule(AnalysisModule):
    def get_name(self) -> str:
        return "security"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        if context.nodes:
            return context.runner._security_scan(context.nodes)
        return {}
