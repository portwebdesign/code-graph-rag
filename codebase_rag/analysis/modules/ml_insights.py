from __future__ import annotations

from typing import Any

from ..ml_insights import generate_ml_insights
from .base_module import AnalysisContext, AnalysisModule


class MLInsightsModule(AnalysisModule):
    def get_name(self) -> str:
        return "ml_insights"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        migration_plan = context.summary.get("migration_plan")
        if not migration_plan:
            return {}
        return generate_ml_insights(migration_plan)
