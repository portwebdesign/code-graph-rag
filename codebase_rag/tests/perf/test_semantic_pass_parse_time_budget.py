from __future__ import annotations

import gc
import time
from pathlib import Path
from unittest.mock import MagicMock

from codebase_rag.parsers.core.performance_optimizer import (
    ParserPerformanceOptimizer,
)
from codebase_rag.parsers.pipeline.semantic_guardrails import (
    SEMANTIC_PERFORMANCE_BUDGETS,
)
from codebase_rag.tests.conftest import run_updater
from codebase_rag.tests.perf.helpers import materialize_semantic_stress_repo


def test_semantic_pass_parse_time_and_heap_budget(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    fixture_repo = materialize_semantic_stress_repo(temp_repo)
    mock_ingestor.fetch_all.return_value = []

    gc.collect()
    before_memory_mb = ParserPerformanceOptimizer._get_process_memory_mb()
    started_at = time.perf_counter()
    run_updater(fixture_repo, mock_ingestor)
    elapsed_seconds = time.perf_counter() - started_at
    gc.collect()
    after_memory_mb = ParserPerformanceOptimizer._get_process_memory_mb()

    assert elapsed_seconds <= float(SEMANTIC_PERFORMANCE_BUDGETS["parse_time_seconds"])
    if before_memory_mb is not None and after_memory_mb is not None:
        rss_growth_kib = max(0.0, after_memory_mb - before_memory_mb) * 1024
        assert rss_growth_kib <= float(SEMANTIC_PERFORMANCE_BUDGETS["rss_growth_kib"])
