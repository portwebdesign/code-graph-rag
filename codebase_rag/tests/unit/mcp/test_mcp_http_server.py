from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

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


@pytest.mark.asyncio
async def test_http_service_lists_tools() -> None:
    service = MCPHTTPService(FakeTools())  # type: ignore[arg-type]

    payload = service.list_tools_payload()

    assert payload["status"] == "ok"
    assert payload["transport"] == "http"
    assert payload["tools"][0]["name"] == "query_code_graph"  # type: ignore[index]


@pytest.mark.asyncio
async def test_http_service_returns_formatted_tool_payload() -> None:
    service = MCPHTTPService(FakeTools())  # type: ignore[arg-type]

    payload = await service.call_tool_payload(
        "query_code_graph",
        {"natural_language_query": "auth flow"},
    )

    assert payload["status"] == "ok"
    assert str(payload["session_id"]).strip()
    assert payload["session_created"] is True
    assert "Graph query completed" in str(payload["formatted_text"])
    assert payload["payload"]["status"] == "ok"  # type: ignore[index]


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
    service = MCPHTTPService(tools)  # type: ignore[arg-type]

    payload = await service.call_tool_payload("query_code_graph", {})

    assert payload["status"] == "blocked"
    assert "Next actions:" in str(payload["formatted_text"])
    assert payload["payload"]["gate"] == "workflow"  # type: ignore[index]


@pytest.mark.asyncio
async def test_http_service_isolates_state_per_session() -> None:
    service = MCPHTTPService(FakeTools(), session_factory=FakeTools)  # type: ignore[arg-type]

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

    first_results = payload_one["payload"]["results"]  # type: ignore[index]
    second_results = payload_two["payload"]["results"]  # type: ignore[index]
    repeat_results = payload_one_repeat["payload"]["results"]  # type: ignore[index]

    assert first_results[0]["call_count"] == 1  # type: ignore[index]
    assert second_results[0]["call_count"] == 1  # type: ignore[index]
    assert repeat_results[0]["call_count"] == 2  # type: ignore[index]


@pytest.mark.asyncio
async def test_http_service_rejects_unknown_session() -> None:
    service = MCPHTTPService(FakeTools(), session_factory=FakeTools)  # type: ignore[arg-type]

    payload = await service.call_tool_payload(
        "query_code_graph",
        {"natural_language_query": "auth flow"},
        session_id="missing-session",
    )

    assert payload["status"] == "error"
    assert payload["payload"]["error"] == "unknown_session"  # type: ignore[index]
