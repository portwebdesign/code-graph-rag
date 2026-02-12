from __future__ import annotations

from pathlib import Path
from typing import cast

from codebase_rag.analysis.analysis_runner import AnalysisRunner
from codebase_rag.services.protocols import IngestorProtocol


class DummyIngestor:
    def fetch_all(self, query, params=None):
        return []

    def ensure_node_batch(self, label, props):
        return None

    def ensure_relationship_batch(self, *args, **kwargs):
        return None

    def flush_all(self):
        return None


def test_analysis_runner_runs_with_empty_graph(tmp_path: Path) -> None:
    runner = AnalysisRunner(cast(IngestorProtocol, DummyIngestor()), tmp_path)
    summary = runner.run_modules(modules={"complexity"})

    assert isinstance(summary, dict)
