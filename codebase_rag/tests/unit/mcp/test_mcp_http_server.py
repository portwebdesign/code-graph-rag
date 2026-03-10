from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from codebase_rag.core.config import settings
from codebase_rag.mcp.http_server import MCPHTTPService


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
    assert "Graph query completed" in str(payload["formatted_text"])
    nested_payload = cast(dict[str, object], payload["payload"])
    assert nested_payload["status"] == "ok"


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
