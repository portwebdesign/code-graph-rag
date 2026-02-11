from __future__ import annotations

import json

from loguru import logger

from codebase_rag.core import constants as cs
from codebase_rag.graph_db.cypher_queries import CYPHER_GET_LATEST_METRIC

from ...services.protocols import QueryProtocol
from ..protocols import AnalysisRunnerProtocol


class TrendsMixin:
    def _api_stability_trend(
        self: AnalysisRunnerProtocol, api_stats: dict[str, int]
    ) -> dict[str, object]:
        current = api_stats.get("public_symbols", 0)
        previous = None
        delta = None

        if isinstance(self.ingestor, QueryProtocol):
            try:
                result = self.ingestor.fetch_all(
                    CYPHER_GET_LATEST_METRIC,
                    {
                        cs.KEY_PROJECT_NAME: self.project_name,
                        "metric_name": "public_api",
                    },
                )
                if result:
                    payload = result[0].get("metric_value")
                    if isinstance(payload, str):
                        parsed = json.loads(payload)
                        previous = parsed.get("public_symbols")
            except Exception as exc:
                logger.debug("API trend query failed: {}", exc)

        if previous is not None:
            delta = current - int(previous)

        return {
            "current": current,
            "previous": previous,
            "delta": delta,
        }
