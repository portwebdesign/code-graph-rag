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


class _StaticQueryIngestor(_NoopIngestor):
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def fetch_all(
        self,
        query: str,
        params: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        _ = query, params
        return list(self._rows)


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


def test_api_compliance_prefers_relation_propagated_mounted_paths(
    tmp_path: Path,
) -> None:
    runner = _FakeRunner(tmp_path)
    runner.ingestor = _StaticQueryIngestor(
        [
            {
                "qualified_name": "demo.endpoint.fastapi.GET:/users",
                "method": "GET",
                "path": "/users",
                "local_route_path": "/users",
                "file": "src/api/routes/v1/identity.py",
                "framework": "fastapi",
                "handler_qns": ["demo.src.api.routes.v1.identity.list_users"],
                "exposed_module_paths": [],
                "prefix_module_paths": [],
                "expose_count": 0,
                "prefix_count": 0,
            },
            {
                "qualified_name": "demo.endpoint.fastapi.GET:/api/v1/users",
                "method": "GET",
                "path": "/api/v1/users",
                "local_route_path": "/users",
                "file": "src/api/routes/v1/identity.py",
                "framework": "fastapi",
                "handler_qns": ["demo.src.api.routes.v1.identity.list_users"],
                "exposed_module_paths": ["src/api/routes/v1/__init__.py"],
                "prefix_module_paths": ["src/api/routes/v1/identity.py"],
                "expose_count": 1,
                "prefix_count": 1,
            },
        ]
    )
    context = AnalysisContext(
        runner=cast(Any, runner),
        nodes=[],
        relationships=[],
        module_path_map={},
        node_by_id={},
        module_paths=None,
        incremental_paths=None,
        use_db=False,
        summary={},
    )

    ApiComplianceModule().run(context)

    report = cast(dict[str, object], runner.reports["api_compliance_report.json"])
    endpoints = cast(list[dict[str, object]], report["endpoints"])
    assert len(endpoints) == 1
    assert endpoints[0]["path"] == "/api/v1/users"
    assert endpoints[0]["canonical_route_layer"] == "relation_propagated"


def test_api_compliance_ignores_http_requester_endpoints_from_frontend_files(
    tmp_path: Path,
) -> None:
    runner = _FakeRunner(tmp_path)
    runner.ingestor = _StaticQueryIngestor(
        [
            {
                "qualified_name": "demo.endpoint.fastapi.POST:/api/v1/auth/session-cookie",
                "method": "POST",
                "path": "/api/v1/auth/session-cookie",
                "local_route_path": "/session-cookie",
                "file": "src/api/routes/v1/auth.py",
                "framework": "fastapi",
                "handler_qns": ["demo.src.api.routes.v1.auth.exchange_session_cookie"],
                "exposed_module_paths": ["src/api/app_factory.py"],
                "prefix_module_paths": ["src/api/routes/v1/__init__.py"],
                "expose_count": 1,
                "prefix_count": 1,
            },
            {
                "qualified_name": "demo.endpoint.http.POST:/{param}/api/v1/auth/session-cookie",
                "method": "POST",
                "path": "/{param}/api/v1/auth/session-cookie",
                "local_route_path": "",
                "file": "frontend/e2e/live_contract_smoke.spec.ts",
                "framework": "http",
                "handler_qns": [],
                "exposed_module_paths": [],
                "prefix_module_paths": [],
                "expose_count": 1,
                "prefix_count": 0,
            },
        ]
    )
    context = AnalysisContext(
        runner=cast(Any, runner),
        nodes=[],
        relationships=[],
        module_path_map={},
        node_by_id={},
        module_paths=None,
        incremental_paths=None,
        use_db=False,
        summary={},
    )

    ApiComplianceModule().run(context)

    report = cast(dict[str, object], runner.reports["api_compliance_report.json"])
    endpoints = cast(list[dict[str, object]], report["endpoints"])

    assert len(endpoints) == 1
    assert endpoints[0]["framework"] == "fastapi"
    assert endpoints[0]["file"] == "src/api/routes/v1/auth.py"
    assert endpoints[0]["path"] == "/api/v1/auth/session-cookie"


def test_api_call_chain_prefers_canonical_endpoint_and_filters_graphql_noise(
    tmp_path: Path,
) -> None:
    runner = _FakeRunner(tmp_path)
    endpoint_local = NodeRecord(
        node_id=1,
        labels=[cs.NodeLabel.ENDPOINT.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.endpoint.fastapi.GET:/generated-documents/{param}/preview",
            cs.KEY_NAME: "GET /generated-documents/{param}/preview",
            cs.KEY_PATH: "src/api/routes/v1/documents/artifact_routes.py",
            cs.KEY_FRAMEWORK: "fastapi",
            cs.KEY_HTTP_METHOD: "GET",
            cs.KEY_ROUTE_PATH: "/generated-documents/{param}/preview",
        },
    )
    endpoint_canonical = NodeRecord(
        node_id=2,
        labels=[cs.NodeLabel.ENDPOINT.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.endpoint.fastapi.GET:/api/v1/generated-documents/{param}/preview",
            cs.KEY_NAME: "GET /api/v1/generated-documents/{param}/preview",
            cs.KEY_PATH: "src/api/routes/v1/documents/artifact_routes.py",
            cs.KEY_FRAMEWORK: "fastapi",
            cs.KEY_HTTP_METHOD: "GET",
            cs.KEY_ROUTE_PATH: "/api/v1/generated-documents/{param}/preview",
            "local_route_path": "/generated-documents/{param}/preview",
        },
    )
    handler = NodeRecord(
        node_id=3,
        labels=[cs.NodeLabel.FUNCTION.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.src.api.routes.v1.documents.artifact_routes.get_preview",
            cs.KEY_NAME: "get_preview",
            cs.KEY_PATH: "src/api/routes/v1/documents/artifact_routes.py",
        },
    )
    expose_module = NodeRecord(
        node_id=4,
        labels=[cs.NodeLabel.MODULE.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.src.api.routes.v1.documents",
            cs.KEY_NAME: "documents",
            cs.KEY_PATH: "src/api/routes/v1/documents/__init__.py",
        },
    )
    prefix_module = NodeRecord(
        node_id=5,
        labels=[cs.NodeLabel.MODULE.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.src.api.routes.v1.documents.artifact_routes",
            cs.KEY_NAME: "artifact_routes",
            cs.KEY_PATH: "src/api/routes/v1/documents/artifact_routes.py",
        },
    )
    graphql_query = NodeRecord(
        node_id=6,
        labels=[cs.NodeLabel.CLASS.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.src.api.routes.internal.graphql_schema.Query",
            cs.KEY_NAME: "Query",
            cs.KEY_PATH: "src/api/routes/internal/graphql_schema.py",
        },
    )
    service = NodeRecord(
        node_id=7,
        labels=[cs.NodeLabel.METHOD.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.src.domain.documents.services.ArtifactService.list_artifacts",
            cs.KEY_NAME: "list_artifacts",
            cs.KEY_PATH: "src/domain/documents/services/artifact_service.py",
        },
    )
    relationships = [
        RelationshipRecord(3, 1, cs.RelationshipType.HAS_ENDPOINT, {}),
        RelationshipRecord(3, 2, cs.RelationshipType.HAS_ENDPOINT, {}),
        RelationshipRecord(4, 2, cs.RelationshipType.EXPOSES_ENDPOINT, {}),
        RelationshipRecord(
            5, 2, cs.RelationshipType.PREFIXES_ENDPOINT, {"prefix": "/api/v1"}
        ),
        RelationshipRecord(3, 6, cs.RelationshipType.CALLS, {}),
        RelationshipRecord(3, 7, cs.RelationshipType.CALLS_SERVICE, {}),
    ]
    context = AnalysisContext(
        runner=cast(Any, runner),
        nodes=[
            endpoint_local,
            endpoint_canonical,
            handler,
            expose_module,
            prefix_module,
            graphql_query,
            service,
        ],
        relationships=relationships,
        module_path_map={},
        node_by_id={
            1: endpoint_local,
            2: endpoint_canonical,
            3: handler,
            4: expose_module,
            5: prefix_module,
            6: graphql_query,
            7: service,
        },
        module_paths=None,
        incremental_paths=None,
        use_db=False,
        summary={},
    )

    ApiCallChainModule().run(context)

    report = cast(dict[str, object], runner.reports["api_call_chain_report.json"])
    summary = cast(dict[str, object], report["summary"])
    chains = cast(list[dict[str, object]], report["chains"])
    assert summary["endpoints"] == 1
    assert (
        chains[0]["endpoint"]["route_path"]
        == "/api/v1/generated-documents/{param}/preview"
    )
    assert chains[0]["endpoint"]["canonical_route_layer"] == "relation_propagated"
    call_chain = cast(list[dict[str, object]], chains[0]["call_chain"])
    assert all(
        item["path"] != "src/api/routes/internal/graphql_schema.py"
        for item in call_chain
    )
    assert any(
        item["qualified_name"]
        == "demo.src.domain.documents.services.ArtifactService.list_artifacts"
        for item in call_chain
    )


def test_api_call_chain_ignores_test_endpoint_nodes(tmp_path: Path) -> None:
    runner = _FakeRunner(tmp_path)
    leaked_test_endpoint = NodeRecord(
        node_id=1,
        labels=[cs.NodeLabel.ENDPOINT.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.endpoint.fastapi.GET:/query",
            cs.KEY_NAME: "GET /query",
            cs.KEY_PATH: "tests/unit/test_internal_core_routes.py",
            cs.KEY_FRAMEWORK: "fastapi",
            cs.KEY_HTTP_METHOD: "GET",
            cs.KEY_ROUTE_PATH: "/query",
        },
    )
    real_endpoint = NodeRecord(
        node_id=2,
        labels=[cs.NodeLabel.ENDPOINT.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.endpoint.fastapi.GET:/api/internal/graphql/query",
            cs.KEY_NAME: "GET /api/internal/graphql/query",
            cs.KEY_PATH: "src/api/routes/internal/graphql.py",
            cs.KEY_FRAMEWORK: "fastapi",
            cs.KEY_HTTP_METHOD: "GET",
            cs.KEY_ROUTE_PATH: "/api/internal/graphql/query",
        },
    )
    test_handler = NodeRecord(
        node_id=3,
        labels=[cs.NodeLabel.FUNCTION.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.tests.unit.test_internal_core_routes._graph_query",
            cs.KEY_NAME: "_graph_query",
            cs.KEY_PATH: "tests/unit/test_internal_core_routes.py",
        },
    )
    real_handler = NodeRecord(
        node_id=4,
        labels=[cs.NodeLabel.FUNCTION.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.src.api.routes.internal.graphql.query",
            cs.KEY_NAME: "query",
            cs.KEY_PATH: "src/api/routes/internal/graphql.py",
        },
    )
    relationships = [
        RelationshipRecord(3, 1, cs.RelationshipType.HAS_ENDPOINT, {}),
        RelationshipRecord(4, 2, cs.RelationshipType.HAS_ENDPOINT, {}),
    ]
    context = AnalysisContext(
        runner=cast(Any, runner),
        nodes=[leaked_test_endpoint, real_endpoint, test_handler, real_handler],
        relationships=relationships,
        module_path_map={},
        node_by_id={
            1: leaked_test_endpoint,
            2: real_endpoint,
            3: test_handler,
            4: real_handler,
        },
        module_paths=None,
        incremental_paths=None,
        use_db=False,
        summary={},
    )

    ApiCallChainModule().run(context)

    report = cast(dict[str, object], runner.reports["api_call_chain_report.json"])
    summary = cast(dict[str, object], report["summary"])
    chains = cast(list[dict[str, object]], report["chains"])

    assert summary["endpoints"] == 1
    assert len(chains) == 1
    assert chains[0]["endpoint"]["route_path"] == "/api/internal/graphql/query"
    assert chains[0]["endpoint"]["path"] == "src/api/routes/internal/graphql.py"


def test_api_call_chain_emits_frontend_requester_context(tmp_path: Path) -> None:
    runner = _FakeRunner(tmp_path)
    endpoint = NodeRecord(
        node_id=1,
        labels=[cs.NodeLabel.ENDPOINT.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.endpoint.fastapi.GET:/api/customers",
            cs.KEY_NAME: "GET /api/customers",
            cs.KEY_PATH: "src/api/routes/customers.py",
            cs.KEY_FRAMEWORK: "fastapi",
            cs.KEY_HTTP_METHOD: "GET",
            cs.KEY_ROUTE_PATH: "/api/customers",
        },
    )
    helper = NodeRecord(
        node_id=2,
        labels=[cs.NodeLabel.FUNCTION.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.frontend.lib.generated.listCustomers",
            cs.KEY_NAME: "listCustomers",
            cs.KEY_PATH: "frontend/src/lib/generated/client.ts",
        },
    )
    widget = NodeRecord(
        node_id=3,
        labels=[cs.NodeLabel.COMPONENT.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.frontend.src.components.CustomerWidget.CustomerWidget",
            cs.KEY_NAME: "CustomerWidget",
            cs.KEY_PATH: "frontend/src/components/CustomerWidget.tsx",
            cs.KEY_FRAMEWORK: "react",
            "hooks_used": ["useQuery"],
        },
    )
    page = NodeRecord(
        node_id=4,
        labels=[cs.NodeLabel.COMPONENT.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.frontend.src.app.customers.page.CustomersPage",
            cs.KEY_NAME: "CustomersPage",
            cs.KEY_PATH: "frontend/src/app/customers/page.tsx",
            cs.KEY_FRAMEWORK: "next",
            "next_kind": "page",
            "next_route_path": "/customers",
        },
    )
    handler = NodeRecord(
        node_id=5,
        labels=[cs.NodeLabel.FUNCTION.value],
        properties={
            cs.KEY_QUALIFIED_NAME: "demo.src.api.routes.customers.list_customers",
            cs.KEY_NAME: "list_customers",
            cs.KEY_PATH: "src/api/routes/customers.py",
        },
    )
    relationships = [
        RelationshipRecord(5, 1, cs.RelationshipType.HAS_ENDPOINT, {}),
        RelationshipRecord(2, 1, cs.RelationshipType.REQUESTS_ENDPOINT, {}),
        RelationshipRecord(3, 2, cs.RelationshipType.CALLS, {}),
        RelationshipRecord(4, 3, cs.RelationshipType.USES_COMPONENT, {}),
    ]
    context = AnalysisContext(
        runner=cast(Any, runner),
        nodes=[endpoint, helper, widget, page, handler],
        relationships=relationships,
        module_path_map={},
        node_by_id={1: endpoint, 2: helper, 3: widget, 4: page, 5: handler},
        module_paths=None,
        incremental_paths=None,
        use_db=False,
        summary={},
    )

    ApiCallChainModule().run(context)

    report = cast(dict[str, object], runner.reports["api_call_chain_report.json"])
    chains = cast(list[dict[str, object]], report["chains"])
    requester_components = cast(
        list[dict[str, object]], chains[0]["requester_components"]
    )
    requester_pages = cast(list[dict[str, object]], chains[0]["requester_pages"])
    request_path_chain = cast(list[dict[str, object]], chains[0]["request_path_chain"])

    component_qns = {cast(str, item["qualified_name"]) for item in requester_components}
    assert "demo.frontend.src.components.CustomerWidget.CustomerWidget" in component_qns
    assert "demo.frontend.src.app.customers.page.CustomersPage" in component_qns
    assert (
        requester_pages[0]["qualified_name"]
        == "demo.frontend.src.app.customers.page.CustomersPage"
    )
    assert requester_pages[0]["next_route_path"] == "/customers"
    assert request_path_chain[0]["relationships"] == [
        cs.RelationshipType.USES_COMPONENT,
        cs.RelationshipType.CALLS,
        cs.RelationshipType.REQUESTS_ENDPOINT,
    ]
    assert [node["qualified_name"] for node in request_path_chain[0]["nodes"]] == [
        "demo.frontend.src.app.customers.page.CustomersPage",
        "demo.frontend.src.components.CustomerWidget.CustomerWidget",
        "demo.frontend.lib.generated.listCustomers",
        "demo.endpoint.fastapi.GET:/api/customers",
    ]
