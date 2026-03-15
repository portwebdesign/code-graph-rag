from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from codebase_rag.core import constants as cs
from codebase_rag.core.config import settings
from codebase_rag.mcp import http_server as http_server_module
from codebase_rag.mcp.http_server import MCPHTTPService, create_streamable_http_app
from codebase_rag.mcp.tools import MCPToolsRegistry


class FakeTools:
    def __init__(self) -> None:
        self.workflow_gate_payload: dict[str, object] | None = None
        self.call_count = 0

    def get_tool_schemas(self) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                name="query_code_graph",
                description="graph query",
                inputSchema={"type": "object", "properties": {}},
            )
        ]

    def get_preflight_gate_error(self, name: str) -> str | None:
        _ = name
        return None

    def get_phase_gate_error(self, name: str) -> str | None:
        _ = name
        return None

    def get_workflow_gate_payload(
        self, name: str, arguments: dict[str, object] | None
    ) -> dict[str, object] | None:
        _ = name, arguments
        return self.workflow_gate_payload

    def get_visibility_gate_payload(
        self, name: str, arguments: dict[str, object] | None
    ) -> dict[str, object] | None:
        _ = name, arguments
        return None

    def get_tool_handler(self, name: str) -> tuple[Any, bool] | None:
        if name != "query_code_graph":
            return None

        async def handler(**kwargs: object) -> dict[str, object]:
            _ = kwargs
            self.call_count += 1
            return {
                "status": "ok",
                "ui_summary": "Graph query completed",
                "results": [{"name": "AuthService", "call_count": self.call_count}],
            }

        return handler, True

    def build_gate_guidance_payload(
        self, tool_name: str, gate_error: str, gate_type: str
    ) -> dict[str, object]:
        return {
            "status": "blocked",
            "gate": gate_type,
            "error": gate_error,
            "ui_summary": f"{tool_name} blocked",
        }

    async def list_mcp_resources(self) -> list[dict[str, object]]:
        return [
            {
                "uri": "analysis://overview",
                "name": "analysis_overview",
                "description": "overview",
                "mime_type": "application/json",
            }
        ]

    async def read_mcp_resource(self, uri: str) -> dict[str, object]:
        return {"uri": uri, "artifact_count": 1}

    async def list_mcp_prompts(self) -> list[dict[str, object]]:
        return [
            {
                "name": "architecture_review",
                "description": "architecture",
                "arguments": [],
            }
        ]

    async def get_mcp_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> dict[str, object]:
        return {
            "name": name,
            "description": "architecture",
            "messages": [
                {
                    "role": "user",
                    "text": f"prompt for {name} with {arguments or {}}",
                }
            ],
        }


class ProfileAwareFakeTools(FakeTools):
    def __init__(self, client_profile: str | None = None) -> None:
        super().__init__()
        self._client_profile_value = client_profile or "balanced"

    def _client_profile(self) -> str:
        return self._client_profile_value


class FakeIngestor:
    def __enter__(self) -> FakeIngestor:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        _ = exc_type, exc, tb


class FakeSessionManager:
    def __init__(self, _server: object) -> None:
        self.started = False

    async def handle_request(
        self,
        scope: dict[str, object],
        receive: object,
        send: object,
    ) -> None:
        _ = receive
        response = JSONResponse({"status": "ok", "transport": "streamable-http-test"})
        await response(scope, receive, send)

    def run(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _runner():
            self.started = True
            yield

        return _runner()


class RegistryHTTPIngestor:
    def __init__(self, project_name: str) -> None:
        self.project_name = project_name

    def list_projects(self) -> list[str]:
        return [self.project_name]

    def fetch_all(
        self,
        query: str,
        params: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        _ = params
        normalized = " ".join(query.split())
        if "RETURN count(m) AS count" in normalized:
            return [{"count": 3}]
        if "RETURN count(c) AS count" in normalized:
            return [{"count": 1}]
        if "RETURN count(DISTINCT f) AS count" in normalized:
            return [{"count": 5}]
        if all(
            token in normalized
            for token in ("source_label", "relationship_type", "target_label")
        ):
            return [
                {
                    "source_label": "Module",
                    "relationship_type": "DEFINES",
                    "target_label": "Function",
                    "count": 5,
                }
            ]
        return [{"name": "AuthService", "path": "src/auth.py"}]

    def execute_write(
        self,
        query: str,
        params: dict[str, object] | None = None,
    ) -> None:
        _ = query, params


def _make_http_registry(
    project_root: Path, client_profile: str | None = None
) -> MCPToolsRegistry:
    ingestor = RegistryHTTPIngestor(project_root.resolve().name)
    cypher_gen = MagicMock()

    async def _generate(query: str) -> str:
        _ = query
        return (
            "MATCH (m:Module {project_name: $project_name}) "
            "RETURN m.name AS name, m.path AS path LIMIT 5"
        )

    cypher_gen.generate = _generate
    registry = MCPToolsRegistry(
        project_root=str(project_root),
        ingestor=cast(Any, ingestor),
        cypher_gen=cypher_gen,
    )
    registry.set_client_profile(client_profile)
    return registry


@pytest.mark.asyncio
async def test_http_service_lists_tools() -> None:
    service = MCPHTTPService(cast(Any, FakeTools()))

    payload = service.list_tools_payload()

    assert payload["status"] == "ok"
    assert payload["transport"] == "http"
    tools = cast(list[dict[str, object]], payload["tools"])
    assert tools[0]["name"] == "query_code_graph"
    session_support = cast(dict[str, object], payload["session_support"])
    assert session_support["client_profile_optional_on_create"] is True
    assert session_support["reuse_session_id_after_create"] is True
    assert "session_id" in str(session_support["warning"])


@pytest.mark.asyncio
async def test_http_service_returns_formatted_tool_payload() -> None:
    service = MCPHTTPService(cast(Any, FakeTools()))

    payload = await service.call_tool_payload(
        "query_code_graph",
        {"natural_language_query": "auth flow"},
    )

    assert payload["status"] == "ok"
    assert str(payload["session_id"]).strip()
    assert payload["session_created"] is True
    assert payload["session_reuse_required"] is True
    assert "Graph query completed" in str(payload["formatted_text"])
    nested_payload = cast(dict[str, object], payload["payload"])
    assert nested_payload["status"] == "ok"
    session_guidance = cast(dict[str, object], payload["session_guidance"])
    assert "session_id" in str(session_guidance["warning"])


@pytest.mark.asyncio
async def test_http_service_lists_resources_and_prompts() -> None:
    service = MCPHTTPService(cast(Any, FakeTools()))

    resources = await service.list_resources_payload()
    prompts = await service.list_prompts_payload()

    assert resources["status"] == "ok"
    resource_entries = cast(list[dict[str, object]], resources["resources"])
    assert resource_entries[0]["uri"] == "analysis://overview"
    assert prompts["status"] == "ok"
    prompt_entries = cast(list[dict[str, object]], prompts["prompts"])
    assert prompt_entries[0]["name"] == "architecture_review"


@pytest.mark.asyncio
async def test_http_service_reads_resource_and_prompt_with_session() -> None:
    service = MCPHTTPService(
        cast(Any, FakeTools()),
        session_factory=cast(Any, FakeTools),
    )
    session_payload = service.create_session_payload()
    session_id = str(session_payload["session_id"])

    resource_payload = await service.read_resource_payload(
        "analysis://overview",
        session_id=session_id,
    )
    prompt_payload = await service.get_prompt_payload(
        "architecture_review",
        {"goal": "map services"},
        session_id=session_id,
    )

    assert resource_payload["status"] == "ok"
    resource_nested = cast(dict[str, object], resource_payload["payload"])
    assert resource_nested["artifact_count"] == 1
    assert prompt_payload["status"] == "ok"
    prompt_nested = cast(dict[str, object], prompt_payload["payload"])
    assert prompt_nested["name"] == "architecture_review"


@pytest.mark.asyncio
async def test_http_service_preserves_blocked_gate_payloads() -> None:
    tools = FakeTools()
    tools.workflow_gate_payload = {
        "status": "blocked",
        "gate": "workflow",
        "error": "workflow_gate_blocked",
        "ui_summary": "workflow_gate_blocked: run plan_task first.",
        "exact_next_calls": [
            {
                "tool": "plan_task",
                "copy_paste": 'plan_task(goal="trace auth flow")',
                "why": "complex_task_plan_gate",
                "when": "before execution",
            }
        ],
    }
    service = MCPHTTPService(cast(Any, tools))

    payload = await service.call_tool_payload("query_code_graph", {})

    assert payload["status"] == "blocked"
    assert "Next actions:" in str(payload["formatted_text"])
    blocked_payload = cast(dict[str, object], payload["payload"])
    assert blocked_payload["gate"] == "workflow"


@pytest.mark.asyncio
async def test_http_service_isolates_state_per_session() -> None:
    service = MCPHTTPService(
        cast(Any, FakeTools()),
        session_factory=cast(Any, FakeTools),
    )

    session_one = service.create_session_payload()["session_id"]
    session_two = service.create_session_payload()["session_id"]

    payload_one = await service.call_tool_payload(
        "query_code_graph",
        {"natural_language_query": "auth flow"},
        session_id=str(session_one),
    )
    payload_two = await service.call_tool_payload(
        "query_code_graph",
        {"natural_language_query": "auth flow"},
        session_id=str(session_two),
    )
    payload_one_repeat = await service.call_tool_payload(
        "query_code_graph",
        {"natural_language_query": "auth flow"},
        session_id=str(session_one),
    )

    first_results = cast(
        list[dict[str, object]],
        cast(dict[str, object], payload_one["payload"])["results"],
    )
    second_results = cast(
        list[dict[str, object]],
        cast(dict[str, object], payload_two["payload"])["results"],
    )
    repeat_results = cast(
        list[dict[str, object]],
        cast(dict[str, object], payload_one_repeat["payload"])["results"],
    )

    assert first_results[0]["call_count"] == 1
    assert second_results[0]["call_count"] == 1
    assert repeat_results[0]["call_count"] == 2


@pytest.mark.asyncio
async def test_http_service_rejects_unknown_session() -> None:
    service = MCPHTTPService(
        cast(Any, FakeTools()),
        session_factory=cast(Any, FakeTools),
    )

    payload = await service.call_tool_payload(
        "query_code_graph",
        {"natural_language_query": "auth flow"},
        session_id="missing-session",
    )

    assert payload["status"] == "error"
    error_payload = cast(dict[str, object], payload["payload"])
    assert error_payload["error"] == "unknown_session"


@pytest.mark.asyncio
async def test_http_service_create_session_accepts_client_profile() -> None:
    service = MCPHTTPService(
        cast(Any, ProfileAwareFakeTools()),
        session_factory=cast(Any, ProfileAwareFakeTools),
    )

    payload = service.create_session_payload(client_profile="ollama")

    assert payload["status"] == "ok"
    assert payload["client_profile"] == "ollama"
    guidance = cast(dict[str, object], payload["session_guidance"])
    assert guidance["recommended_client_profile_for_memgraph_lab"] == "http"


@pytest.mark.asyncio
async def test_http_stateful_core_tools_remain_callable_after_project_selection(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "http_core_tools_repo"
    project_root.mkdir()
    (project_root / "sample.py").write_text(
        "def hello():\n    return 1\n", encoding="utf-8"
    )

    service = MCPHTTPService(
        cast(Any, _make_http_registry(project_root, "http")),
        session_factory=cast(
            Any,
            lambda client_profile=None: _make_http_registry(
                project_root, client_profile
            ),
        ),
    )

    session_payload = service.create_session_payload(client_profile="http")
    session_id = str(session_payload["session_id"])

    select_payload = await service.call_tool_payload(
        "select_active_project",
        {"project_name": project_root.resolve().name},
        session_id=session_id,
    )

    assert select_payload["status"] == "ok"
    registry = cast(Any, service._sessions[session_id].tools)
    complex_query = "analyze multi-file dependency chain refactor impact across services, repositories, handlers, and router composition"
    core_arguments = {
        cs.MCPToolName.LIST_PROJECTS: {},
        cs.MCPToolName.SELECT_ACTIVE_PROJECT: {
            "project_name": project_root.resolve().name
        },
        cs.MCPToolName.GET_SCHEMA_OVERVIEW: {"scope": "global"},
        cs.MCPToolName.QUERY_CODE_GRAPH: {
            "natural_language_query": complex_query,
            "output_format": "json",
        },
        cs.MCPToolName.MULTI_HOP_ANALYSIS: {"qualified_name": "demo.module.fn"},
        cs.MCPToolName.IMPACT_GRAPH: {"qualified_name": "demo.module.fn"},
        cs.MCPToolName.RUN_CYPHER: {
            "cypher": "MATCH (m:Module {project_name: $project_name}) RETURN m.name AS name LIMIT 1"
        },
        cs.MCPToolName.SEMANTIC_SEARCH: {"query": complex_query},
        cs.MCPToolName.PLAN_TASK: {"goal": "optional planning"},
        cs.MCPToolName.LIST_DIRECTORY: {"path": "."},
    }

    for tool_name, args in core_arguments.items():
        assert registry.get_visibility_gate_payload(tool_name, args) is None
        assert registry.get_workflow_gate_payload(tool_name, args) is None

    run_cypher_payload = await service.call_tool_payload(
        "run_cypher",
        {
            "cypher": "MATCH (m:Module {project_name: $project_name}) RETURN m.name AS name LIMIT 1",
            "params": '{"project_name":"http_core_tools_repo"}',
            "write": False,
        },
        session_id=session_id,
    )

    assert run_cypher_payload["status"] == "ok"
    run_cypher_result = cast(dict[str, object], run_cypher_payload["payload"])
    assert run_cypher_result["status"] == "ok"
    assert "flow_advisory" in run_cypher_result

    query_payload = await service.call_tool_payload(
        "query_code_graph",
        {
            "natural_language_query": complex_query,
            "output_format": "json",
        },
        session_id=session_id,
    )

    assert query_payload["status"] == "ok"


def test_http_service_authorization_requires_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MCP_HTTP_AUTH_TOKEN", "secret-token")
    service = MCPHTTPService(FakeTools())  # type: ignore[arg-type]

    denied, status, payload = service.authorize_request("127.0.0.1", None)
    allowed, ok_status, ok_payload = service.authorize_request(
        "127.0.0.1",
        "Bearer secret-token",
    )

    assert denied is False
    assert status.value == 401
    assert payload is not None
    assert payload["error"] == "unauthorized"
    assert allowed is True
    assert ok_status.value == 200
    assert ok_payload is None


def test_http_service_rate_limits_by_client_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MCP_HTTP_AUTH_TOKEN", "")
    monkeypatch.setattr(settings, "MCP_HTTP_RATE_LIMIT_WINDOW_SECONDS", 60)
    monkeypatch.setattr(settings, "MCP_HTTP_RATE_LIMIT_MAX_REQUESTS", 1)
    service = MCPHTTPService(FakeTools())  # type: ignore[arg-type]

    first_allowed, _, _ = service.authorize_request("127.0.0.1", None, now=100.0)
    second_allowed, status, payload = service.authorize_request(
        "127.0.0.1",
        None,
        now=101.0,
    )

    assert first_allowed is True
    assert second_allowed is False
    assert status.value == 429
    assert payload is not None
    assert payload["error"] == "rate_limited"


def test_http_service_cleans_up_expired_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MCP_HTTP_SESSION_TTL_SECONDS", 60)
    service = MCPHTTPService(FakeTools(), session_factory=FakeTools)  # type: ignore[arg-type]

    payload = service.create_session_payload()
    session_id = str(payload["session_id"])
    session = service._sessions[session_id]
    session.last_seen = 0.0

    removed = service.cleanup_expired_sessions(now=120.0)

    assert removed == 1
    assert session_id not in service._sessions


def test_create_streamable_http_app_exposes_health_tools_and_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        http_server_module,
        "create_tools_runtime",
        lambda: (FakeTools(), FakeIngestor()),
    )
    monkeypatch.setattr(
        http_server_module,
        "create_server_with_tools",
        lambda _tools: object(),
    )
    monkeypatch.setattr(
        http_server_module,
        "StreamableHTTPSessionManager",
        FakeSessionManager,
    )

    app = create_streamable_http_app("/mcp")

    with TestClient(app) as client:
        health = client.get("/health")
        tools = client.get("/tools")
        mcp = client.post("/mcp", json={})

    assert health.status_code == 200
    assert health.json()["transport"] == "streamable-http"
    assert health.json()["mcp_path"] == "/mcp"
    assert tools.status_code == 200
    assert tools.json()["transport"] == "http"
    assert mcp.status_code == 200
    assert mcp.json()["transport"] == "streamable-http-test"
