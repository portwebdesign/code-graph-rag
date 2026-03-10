from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from codebase_rag.analysis.modules import AnalysisContext
from codebase_rag.analysis.modules.api_call_chain import ApiCallChainModule
from codebase_rag.analysis.modules.api_compliance import ApiComplianceModule
from codebase_rag.analysis.types import NodeRecord, RelationshipRecord
from codebase_rag.core import constants as cs


class _FakeRunner:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path
        self.project_name = repo_path.resolve().name
        self.reports: dict[str, object] = {}
        self.ingestor = object()

    def _write_json_report(self, filename: str, payload: object) -> Path:
        self.reports[filename] = payload
        report_path = self.repo_path / "output" / "analysis" / filename
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("{}", encoding="utf-8")
        return report_path


def test_api_compliance_ignores_vendor_paths_and_false_positive_gets(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    api_file = source_dir / "api.go"
    api_file.write_text(
        'router.GET("/users")\nvalue := payload.get("name")\n',
        encoding="utf-8",
    )
    noise_dir = tmp_path / ".venv" / "Lib" / "site-packages"
    noise_dir.mkdir(parents=True)
    (noise_dir / "noise.go").write_text('router.GET("/noise")', encoding="utf-8")

    files = ApiComplianceModule._iter_files(tmp_path, None)
    endpoints = ApiComplianceModule._extract_endpoints(
        api_file.read_text(encoding="utf-8"),
        api_file,
    )

    assert api_file in files
    assert all(".venv" not in str(path) for path in files)
    assert [endpoint["path"] for endpoint in endpoints] == ["/users"]


def test_api_call_chain_falls_back_to_source_scan_and_typed_infra(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    api_file = source_dir / "api.go"
    api_file.write_text('router.GET("/users")\n', encoding="utf-8")

    runner = _FakeRunner(tmp_path)
    handler_node = NodeRecord(
        node_id=1,
        labels=[cs.NodeLabel.FUNCTION.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.handler",
            cs.KEY_NAME: "handler",
            cs.KEY_PATH: "src/api.go",
        },
    )
    datastore_node = NodeRecord(
        node_id=2,
        labels=[cs.NodeLabel.DATA_STORE.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.database.postgres",
            cs.KEY_NAME: "postgres",
            cs.KEY_PATH: "infra/database.yml",
        },
    )
    relationship = RelationshipRecord(
        from_id=1,
        to_id=2,
        rel_type=cs.RelationshipType.CONNECTS_TO_DATASTORE,
        properties={},
    )
    context = AnalysisContext(
        runner=cast(Any, runner),
        nodes=[handler_node, datastore_node],
        relationships=[relationship],
        module_path_map={},
        node_by_id={1: handler_node, 2: datastore_node},
        module_paths=["src/api.go"],
        incremental_paths=None,
        use_db=False,
        summary={},
    )

    result = ApiCallChainModule().run(context)

    report = runner.reports["api_call_chain_report.json"]
    assert result["chains"] == 1
    assert result["endpoints"] == 1
    assert isinstance(report, dict)
    report_dict = cast(dict[str, object], report)
    chains = cast(list[dict[str, object]], report_dict["chains"])
    assert isinstance(chains, list)
    assert chains[0]["source_mode"] == "source_scan"
    infra_hits = cast(list[dict[str, object]], chains[0]["infra_hits"])
    assert isinstance(infra_hits, list)
    assert (
        infra_hits[0]["relationship_type"] == cs.RelationshipType.CONNECTS_TO_DATASTORE
    )
