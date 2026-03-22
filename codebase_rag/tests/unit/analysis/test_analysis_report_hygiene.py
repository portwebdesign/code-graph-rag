from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from codebase_rag.analysis.analysis_runner import (
    AnalysisRunner,
    NodeRecord,
    RelationshipRecord,
)
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


def test_duplicate_code_report_prioritizes_cross_file_actionable_groups(
    tmp_path: Path,
) -> None:
    ingestor = SpyIngestor()
    runner = AnalysisRunner(cast(IngestorProtocol, ingestor), tmp_path)

    src_dir = tmp_path / "src"
    tools_dir = tmp_path / "tools"
    src_dir.mkdir(parents=True)
    tools_dir.mkdir(parents=True)

    (src_dir / "a.py").write_text(
        """def sync_version_cache():
    payload = {"status": "ok", "count": 1, "ready": True, "source": "runtime"}
    return payload
""",
        encoding="utf-8",
    )
    (src_dir / "b.py").write_text(
        """def sync_version_cache():
    payload = {"status": "ok", "count": 1, "ready": True, "source": "runtime"}
    return payload
""",
        encoding="utf-8",
    )
    (src_dir / "screen.py").write_text(
        """class ColumnPrimary:
    def render_columns(self):
        payload = {"status": "ok", "columns": ["id", "name"], "screen": True}
        return payload


class ColumnFallback:
    def render_columns(self):
        payload = {"status": "ok", "columns": ["id", "name"], "screen": True}
        return payload
""",
        encoding="utf-8",
    )
    (tools_dir / "one.py").write_text(
        """def _load_json(path):
    payload = {"format": "json", "source": path, "strict": False}
    return payload
""",
        encoding="utf-8",
    )
    (tools_dir / "two.py").write_text(
        """def _load_json(path):
    payload = {"format": "json", "source": path, "strict": False}
    return payload
""",
        encoding="utf-8",
    )
    (src_dir / "synthetic_a.py").write_text(
        """def anonymous_1_1():
    payload = {"view": "callback", "anonymous": True, "slot": 1}
    return payload
""",
        encoding="utf-8",
    )
    (src_dir / "synthetic_b.py").write_text(
        """def anonymous_1_1():
    payload = {"view": "callback", "anonymous": True, "slot": 1}
    return payload
""",
        encoding="utf-8",
    )

    nodes = [
        NodeRecord(
            1,
            [cs.NodeLabel.FUNCTION.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.src.a.sync_version_cache",
                cs.KEY_NAME: "sync_version_cache",
                cs.KEY_PATH: "src/a.py",
                cs.KEY_START_LINE: 1,
                cs.KEY_END_LINE: 3,
            },
        ),
        NodeRecord(
            2,
            [cs.NodeLabel.FUNCTION.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.src.b.sync_version_cache",
                cs.KEY_NAME: "sync_version_cache",
                cs.KEY_PATH: "src/b.py",
                cs.KEY_START_LINE: 1,
                cs.KEY_END_LINE: 3,
            },
        ),
        NodeRecord(
            3,
            [cs.NodeLabel.METHOD.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.src.screen.ColumnPrimary.render_columns",
                cs.KEY_NAME: "render_columns",
                cs.KEY_PATH: "src/screen.py",
                cs.KEY_START_LINE: 2,
                cs.KEY_END_LINE: 4,
            },
        ),
        NodeRecord(
            4,
            [cs.NodeLabel.METHOD.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.src.screen.ColumnFallback.render_columns",
                cs.KEY_NAME: "render_columns",
                cs.KEY_PATH: "src/screen.py",
                cs.KEY_START_LINE: 8,
                cs.KEY_END_LINE: 10,
            },
        ),
        NodeRecord(
            5,
            [cs.NodeLabel.FUNCTION.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.tools.one._load_json",
                cs.KEY_NAME: "_load_json",
                cs.KEY_PATH: "tools/one.py",
                cs.KEY_START_LINE: 1,
                cs.KEY_END_LINE: 3,
            },
        ),
        NodeRecord(
            6,
            [cs.NodeLabel.FUNCTION.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.tools.two._load_json",
                cs.KEY_NAME: "_load_json",
                cs.KEY_PATH: "tools/two.py",
                cs.KEY_START_LINE: 1,
                cs.KEY_END_LINE: 3,
            },
        ),
        NodeRecord(
            7,
            [cs.NodeLabel.FUNCTION.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.src.synthetic_a.anonymous_1_1",
                cs.KEY_NAME: "anonymous_1_1",
                cs.KEY_PATH: "src/synthetic_a.py",
                cs.KEY_START_LINE: 1,
                cs.KEY_END_LINE: 3,
            },
        ),
        NodeRecord(
            8,
            [cs.NodeLabel.FUNCTION.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.src.synthetic_b.anonymous_1_1",
                cs.KEY_NAME: "anonymous_1_1",
                cs.KEY_PATH: "src/synthetic_b.py",
                cs.KEY_START_LINE: 1,
                cs.KEY_END_LINE: 3,
            },
        ),
    ]

    result = runner._duplicate_code_report(nodes, module_path_map={})
    report = json.loads(
        (tmp_path / "output" / "analysis" / "duplicate_code_report.json").read_text(
            encoding="utf-8"
        )
    )

    assert result["duplicate_groups"] == 1
    assert result["raw_duplicate_groups"] == 4
    assert report["summary"]["actionable_groups"] == 1
    assert report["summary"]["ignored_groups"] == 3
    assert report["summary"]["category_totals"]["high_value_duplicate"] == 1
    assert report["summary"]["category_totals"]["same_file_overlap"] == 1
    assert report["summary"]["category_totals"]["low_value_duplicate"] == 1
    assert report["summary"]["category_totals"]["anonymous_callback"] == 1
    assert report["duplicate_groups"][0]["category"] == "high_value_duplicate"
    assert report["duplicate_groups"][0]["severity"] in {"medium", "high"}
    assert {
        symbol["qualified_name"] for symbol in report["duplicate_groups"][0]["symbols"]
    } == {
        "proj.src.a.sync_version_cache",
        "proj.src.b.sync_version_cache",
    }


def test_fan_report_adds_production_and_semantic_views(tmp_path: Path) -> None:
    ingestor = SpyIngestor()
    runner = AnalysisRunner(cast(IngestorProtocol, ingestor), tmp_path)

    nodes = [
        NodeRecord(
            1,
            [cs.NodeLabel.FUNCTION.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.src.runtime.resolve",
                cs.KEY_NAME: "resolve",
                cs.KEY_PATH: "src/runtime.py",
            },
        ),
        NodeRecord(
            2,
            [cs.NodeLabel.FUNCTION.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.frontend.session.useSession",
                cs.KEY_NAME: "useSession",
                cs.KEY_PATH: "frontend/session.ts",
            },
        ),
        NodeRecord(
            3,
            [cs.NodeLabel.FUNCTION.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.tests.test_helper.exercise_surface",
                cs.KEY_NAME: "exercise_surface",
                cs.KEY_PATH: "tests/test_helper.py",
            },
        ),
        NodeRecord(
            4,
            [cs.NodeLabel.COMPONENT.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.frontend.screens.DocumentTemplateStudio",
                cs.KEY_NAME: "DocumentTemplateStudio",
                cs.KEY_PATH: "frontend/screens/DocumentTemplateStudio.tsx",
            },
        ),
        NodeRecord(
            5,
            [cs.NodeLabel.ENDPOINT.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.endpoint.fastapi.GET:/ai",
                cs.KEY_NAME: "GET:/ai",
                cs.KEY_PATH: "src/api/routes/ai.py",
            },
        ),
        NodeRecord(
            6,
            [cs.NodeLabel.DEPENDENCY_PROVIDER.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.semantic.dependency_provider.get_ai_graph_service",
                cs.KEY_NAME: "get_ai_graph_service",
                cs.KEY_PATH: "src/api/routes/ai.py",
            },
        ),
        NodeRecord(
            7,
            [cs.NodeLabel.AUTH_POLICY.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.semantic.auth_policy.require_authenticated_principal",
                cs.KEY_NAME: "require_authenticated_principal",
                cs.KEY_PATH: "src/api/dependencies/auth.py",
            },
        ),
        NodeRecord(
            8,
            [cs.NodeLabel.COMPONENT.value],
            {
                cs.KEY_QUALIFIED_NAME: "proj.frontend.synthetic.anonymous_7_1",
                cs.KEY_NAME: "anonymous_7_1",
                cs.KEY_PATH: "frontend/screens/ShellLayoutFrame.tsx",
            },
        ),
    ]
    relationships = [
        RelationshipRecord(3, 1, cs.RelationshipType.CALLS, {}),
        RelationshipRecord(3, 1, cs.RelationshipType.CALLS, {}),
        RelationshipRecord(3, 2, cs.RelationshipType.CALLS, {}),
        RelationshipRecord(4, 1, cs.RelationshipType.CALLS, {}),
        RelationshipRecord(4, 2, cs.RelationshipType.CALLS, {}),
        RelationshipRecord(4, 2, cs.RelationshipType.CALLS, {}),
        RelationshipRecord(4, 8, cs.RelationshipType.CALLS, {}),
        RelationshipRecord(5, 6, cs.RelationshipType.USES_DEPENDENCY, {}),
        RelationshipRecord(5, 7, cs.RelationshipType.SECURED_BY, {}),
        RelationshipRecord(4, 2, cs.RelationshipType.USES_COMPONENT, {}),
    ]
    node_by_id = {node.node_id: node for node in nodes}

    result = runner._fan_in_out(nodes, relationships, node_by_id)
    report = json.loads(
        (tmp_path / "output" / "analysis" / "fan_report.json").read_text(
            encoding="utf-8"
        )
    )

    assert result["fan_in_nodes"] >= 2
    assert result["production_fan_in_nodes"] >= 2
    assert (
        report["top_fan_out"][0]["qualified_name"]
        == "proj.frontend.screens.DocumentTemplateStudio"
    )
    assert (
        report["top_fan_out_production"][0]["qualified_name"]
        == "proj.frontend.screens.DocumentTemplateStudio"
    )
    assert all(
        entry["qualified_name"] != "proj.tests.test_helper.exercise_surface"
        for entry in report["top_fan_out_production"]
    )
    semantic_targets = {
        entry["qualified_name"]: entry.get("relation_breakdown", {})
        for entry in report["top_semantic_fan_in"]
    }
    assert (
        semantic_targets["proj.semantic.dependency_provider.get_ai_graph_service"][
            "USES_DEPENDENCY"
        ]
        == 1
    )
    assert (
        semantic_targets["proj.semantic.auth_policy.require_authenticated_principal"][
            "SECURED_BY"
        ]
        == 1
    )
