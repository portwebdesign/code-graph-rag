from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from codebase_rag.analysis.analysis_runner import AnalysisRunner
from codebase_rag.analysis.modules import AnalysisContext
from codebase_rag.analysis.modules.api_call_chain import ApiCallChainModule
from codebase_rag.analysis.modules.api_compliance import ApiComplianceModule
from codebase_rag.analysis.types import NodeRecord, RelationshipRecord
from codebase_rag.core import constants as cs
from codebase_rag.services import IngestorProtocol


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

    def _is_runtime_source_path(self, path: str) -> bool:
        return path.endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".go"))


class _NoopIngestor:
    def ensure_node_batch(self, label: str, props: dict[str, object]) -> None:
        return None

    def ensure_relationship_batch(self, *args: object, **kwargs: object) -> None:
        return None

    def flush_all(self) -> None:
        return None


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


def test_api_call_chain_source_scan_maps_endpoint_to_matching_handler(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    api_file = source_dir / "router.py"
    api_file.write_text(
        '@router.get("/items")\n'
        "async def list_items():\n"
        "    return []\n\n"
        '@router.post("/items")\n'
        "async def create_item():\n"
        "    return {}\n",
        encoding="utf-8",
    )

    runner = _FakeRunner(tmp_path)
    list_node = NodeRecord(
        node_id=1,
        labels=[cs.NodeLabel.FUNCTION.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.src.router.list_items",
            cs.KEY_NAME: "list_items",
            cs.KEY_PATH: "src/router.py",
        },
    )
    create_node = NodeRecord(
        node_id=2,
        labels=[cs.NodeLabel.FUNCTION.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.src.router.create_item",
            cs.KEY_NAME: "create_item",
            cs.KEY_PATH: "src/router.py",
        },
    )
    context = AnalysisContext(
        runner=cast(Any, runner),
        nodes=[list_node, create_node],
        relationships=[],
        module_path_map={},
        node_by_id={1: list_node, 2: create_node},
        module_paths=["src/router.py"],
        incremental_paths=None,
        use_db=False,
        summary={},
    )

    ApiCallChainModule().run(context)

    report = cast(dict[str, object], runner.reports["api_call_chain_report.json"])
    chains = cast(list[dict[str, object]], report["chains"])
    get_chain = next(chain for chain in chains if chain["endpoint"]["method"] == "GET")
    post_chain = next(
        chain for chain in chains if chain["endpoint"]["method"] == "POST"
    )

    assert [handler["name"] for handler in get_chain["handlers"]] == ["list_items"]
    assert [handler["name"] for handler in post_chain["handlers"]] == ["create_item"]


def test_api_call_chain_ignores_test_nodes_in_call_chain(tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    api_file = source_dir / "api.py"
    api_file.write_text(
        '@router.get("/users")\nasync def list_users():\n    return []\n',
        encoding="utf-8",
    )

    runner = _FakeRunner(tmp_path)
    handler_node = NodeRecord(
        node_id=1,
        labels=[cs.NodeLabel.FUNCTION.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.src.api.list_users",
            cs.KEY_NAME: "list_users",
            cs.KEY_PATH: "src/api.py",
        },
    )
    test_double = NodeRecord(
        node_id=2,
        labels=[cs.NodeLabel.METHOD.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.tests.unit.test_api._FakePool.acquire",
            cs.KEY_NAME: "acquire",
            cs.KEY_PATH: "tests/unit/test_api.py",
        },
    )
    datastore = NodeRecord(
        node_id=3,
        labels=[cs.NodeLabel.DATA_STORE.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.data.postgres",
            cs.KEY_NAME: "postgres",
            cs.KEY_PATH: "infra/postgres.yml",
        },
    )
    relationships = [
        RelationshipRecord(1, 2, cs.RelationshipType.CALLS, {}),
        RelationshipRecord(1, 3, cs.RelationshipType.CONNECTS_TO_DATASTORE, {}),
    ]
    context = AnalysisContext(
        runner=cast(Any, runner),
        nodes=[handler_node, test_double, datastore],
        relationships=relationships,
        module_path_map={},
        node_by_id={1: handler_node, 2: test_double, 3: datastore},
        module_paths=["src/api.py"],
        incremental_paths=None,
        use_db=False,
        summary={},
    )

    ApiCallChainModule().run(context)

    report = cast(dict[str, object], runner.reports["api_call_chain_report.json"])
    chains = cast(list[dict[str, object]], report["chains"])
    call_chain = cast(list[dict[str, object]], chains[0]["call_chain"])
    assert all(item["path"] != "tests/unit/test_api.py" for item in call_chain)
    assert any(item["qualified_name"] == "demo.data.postgres" for item in call_chain)


def test_public_api_surface_includes_entry_points(tmp_path: Path) -> None:
    runner = AnalysisRunner(cast(IngestorProtocol, _NoopIngestor()), tmp_path)
    nodes = [
        NodeRecord(
            node_id=1,
            labels=[cs.NodeLabel.FUNCTION.value],
            properties={
                cs.KEY_QUALIFIED_NAME: "demo.src.api.routes.list_items",
                cs.KEY_NAME: "list_items",
                cs.KEY_PATH: "src/api/routes.py",
                cs.KEY_IS_ENTRY_POINT: True,
            },
        ),
        NodeRecord(
            node_id=2,
            labels=[cs.NodeLabel.FUNCTION.value],
            properties={
                cs.KEY_QUALIFIED_NAME: "demo.config.compose.anonymous_0_0",
                cs.KEY_NAME: "anonymous_0_0",
                cs.KEY_PATH: "docker-compose.yml",
                cs.KEY_IS_ENTRY_POINT: True,
            },
        ),
    ]

    result = runner._public_api_surface(nodes)
    payload = json.loads(
        (tmp_path / "output" / "analysis" / "public_api_report.json").read_text(
            encoding="utf-8"
        )
    )

    assert result["public_symbols"] == 1
    assert payload["summary"]["public_symbols"] == 1
    assert payload["symbols"][0]["path"] == "src/api/routes.py"
