from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from codebase_rag.analysis.analysis_runner import AnalysisRunner, NodeRecord
from codebase_rag.services import IngestorProtocol


class NoopIngestor:
    def ensure_node_batch(self, label: str, props: dict[str, object]) -> None:
        return None

    def ensure_relationship_batch(self, *args: object, **kwargs: object) -> None:
        return None

    def flush_all(self) -> None:
        return None


def test_refactoring_candidates_ignores_non_runtime_artifacts(tmp_path: Path) -> None:
    runner = AnalysisRunner(cast(IngestorProtocol, NoopIngestor()), tmp_path)
    nodes = [
        NodeRecord(
            1,
            ["Function"],
            {
                "qualified_name": "proj.docker_compose.anonymous_0_0",
                "path": "docker-compose.yml",
                "start_line": 1,
                "end_line": 150,
            },
        ),
        NodeRecord(
            2,
            ["Function"],
            {
                "qualified_name": "proj.src.api.app_factory.create_app",
                "path": "src/api/app_factory.py",
                "start_line": 10,
                "end_line": 90,
            },
        ),
    ]

    summary = runner._refactoring_candidates(nodes)

    assert summary["candidates"] == 1
    report_path = (
        tmp_path / "output" / "analysis" / "refactoring_candidates_report.json"
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert [item["path"] for item in payload] == ["src/api/app_factory.py"]
