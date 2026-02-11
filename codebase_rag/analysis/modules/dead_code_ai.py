from __future__ import annotations

from typing import Any, cast

from .base_module import AnalysisContext, AnalysisModule


class DeadCodeAIModule(AnalysisModule):
    def get_name(self) -> str:
        return "dead_code_ai"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        report = {}
        if context.use_db:
            report = context.runner._dead_code_report_db(context.module_paths)
        elif context.nodes and context.relationships:
            report = context.runner._dead_code_report(
                context.nodes,
                context.relationships,
                context.node_by_id,
            )

        candidates = (
            report.get("dead_functions", []) if isinstance(report, dict) else []
        )
        if not candidates:
            return {
                "status": "no_candidates",
                "verified_dead_code": [],
                "candidates": [],
            }

        verified: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for candidate in cast(list[dict[str, Any]], candidates):
            if context.dead_code_verifier is None:
                skipped.append(candidate)
                continue
            result = context.dead_code_verifier(candidate)
            if not result:
                skipped.append(candidate)
                continue
            if result.get("is_dead"):
                verified.append({**candidate, **result})
            else:
                skipped.append({**candidate, **result})

        status = "verified" if verified else "skipped"
        return {
            "status": status,
            "candidates": candidates,
            "verified_dead_code": verified,
            "skipped": skipped,
        }
