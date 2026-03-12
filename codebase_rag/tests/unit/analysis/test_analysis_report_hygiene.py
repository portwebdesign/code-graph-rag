from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from codebase_rag.analysis.analysis_runner import AnalysisRunner, NodeRecord
from codebase_rag.analysis.modules import AnalysisContext
from codebase_rag.analysis.modules.documentation_quality import (
    DocumentationQualityModule,
)
from codebase_rag.analysis.modules.performance_analysis import PerformanceAnalysisModule
from codebase_rag.core import constants as cs
from codebase_rag.core.config import settings
from codebase_rag.services import IngestorProtocol


class SpyIngestor:
    def __init__(self) -> None:
        self.node_calls: list[tuple[str, dict[str, object]]] = []

    def ensure_node_batch(self, label: str, props: dict[str, object]) -> None:
        self.node_calls.append((label, props))

    def ensure_relationship_batch(self, *args: object, **kwargs: object) -> None:
        return None

    def flush_all(self) -> None:
        return None


def test_analysis_report_graph_nodes_can_be_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ingestor = SpyIngestor()
    runner = AnalysisRunner(cast(IngestorProtocol, ingestor), tmp_path)

    monkeypatch.setattr(settings, "CODEGRAPH_WRITE_ANALYSIS_GRAPH_NODES", False)
    runner._write_analysis_report({"hotspots": 3})

    assert ingestor.node_calls == []


def test_unreachable_code_ignores_multiline_return_expression(tmp_path: Path) -> None:
    src = tmp_path / "sample.py"
    src.write_text(
        "def build():\n    return cls(\n        value=1,\n    )\n",
        encoding="utf-8",
    )
    ingestor = SpyIngestor()
    runner = AnalysisRunner(cast(IngestorProtocol, ingestor), tmp_path)
    node = NodeRecord(1, [cs.NodeLabel.FILE.value], {cs.KEY_PATH: "sample.py"})

    result = runner._unreachable_code([node], ["sample.py"])
    report = json.loads(
        (tmp_path / "output" / "analysis" / "unreachable_code_report.json").read_text(
            encoding="utf-8"
        )
    )

    assert result["unreachable_blocks"] == 0
    assert report == []


def test_documentation_quality_ignores_non_runtime_artifacts(tmp_path: Path) -> None:
    code_file = tmp_path / "src" / "app.py"
    code_file.parent.mkdir(parents=True)
    code_file.write_text("def run():\n    pass\n", encoding="utf-8")

    ingestor = SpyIngestor()
    runner = AnalysisRunner(cast(IngestorProtocol, ingestor), tmp_path)
    nodes = [
        NodeRecord(
            1,
            [cs.NodeLabel.FUNCTION.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.compose.anonymous_0_0",
                cs.KEY_PATH: "docker-compose.yml",
                cs.KEY_NAME: "anonymous_0_0",
            },
        ),
        NodeRecord(
            2,
            [cs.NodeLabel.FUNCTION.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.src.app.run",
                cs.KEY_PATH: "src/app.py",
                cs.KEY_NAME: "run",
            },
        ),
    ]
    context = AnalysisContext(
        runner=cast(Any, runner),
        nodes=nodes,
        relationships=[],
        module_path_map={
            "proj.src.app": "src/app.py",
            "proj.compose": "docker-compose.yml",
        },
        node_by_id={1: nodes[0], 2: nodes[1]},
        module_paths=None,
        incremental_paths=None,
        use_db=False,
        summary={},
    )

    result = DocumentationQualityModule().run(context)

    assert result["total_symbols"] == 1
    assert result["missing_docstrings"] == 1


def test_performance_analysis_ignores_non_runtime_artifacts(tmp_path: Path) -> None:
    code_file = tmp_path / "src" / "app.py"
    code_file.parent.mkdir(parents=True)
    code_file.write_text(
        "def run(items):\n    for item in items:\n        print(item)\n",
        encoding="utf-8",
    )
    ingestor = SpyIngestor()
    runner = AnalysisRunner(cast(IngestorProtocol, ingestor), tmp_path)
    compose_node = NodeRecord(
        1,
        [cs.NodeLabel.FUNCTION.value],
        {
            cs.KEY_QUALIFIED_NAME: "proj.compose.anonymous_0_0",
            cs.KEY_NAME: "anonymous_0_0",
            cs.KEY_PATH: "docker-compose.yml",
            cs.KEY_START_LINE: 1,
            cs.KEY_END_LINE: 40,
        },
    )
    code_node = NodeRecord(
        2,
        [cs.NodeLabel.FUNCTION.value],
        {
            cs.KEY_QUALIFIED_NAME: "proj.src.app.run",
            cs.KEY_NAME: "run",
            cs.KEY_PATH: "src/app.py",
            cs.KEY_START_LINE: 1,
            cs.KEY_END_LINE: 3,
        },
    )
    context = AnalysisContext(
        runner=cast(Any, runner),
        nodes=[compose_node, code_node],
        relationships=[],
        module_path_map={
            "proj.src.app": "src/app.py",
            "proj.compose": "docker-compose.yml",
        },
        node_by_id={1: compose_node, 2: code_node},
        module_paths=None,
        incremental_paths=None,
        use_db=False,
        summary={},
    )

    result = PerformanceAnalysisModule().run(context)

    assert result["functions_scanned"] == 1
