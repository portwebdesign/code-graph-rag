from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib.parse import urlparse
from uuid import uuid4

from loguru import logger

from codebase_rag.core import constants as cs
from codebase_rag.core import logs as lg
from codebase_rag.core.config import settings
from codebase_rag.data_models.types_defs import MCPToolArguments
from codebase_rag.mcp.server import (
    _build_tool_list,
    create_tools_runtime,
    execute_tool_call,
)
from codebase_rag.mcp.tools import MCPToolsRegistry, create_mcp_tools_registry
from codebase_rag.services.graph_service import MemgraphIngestor


class _HTTPSession:
    def __init__(self, tools: MCPToolsRegistry) -> None:
        self.tools = tools
        self.lock = threading.Lock()


class MCPHTTPService:
    def __init__(
        self,
        tools: MCPToolsRegistry,
        session_factory: Callable[[], MCPToolsRegistry] | None = None,
    ) -> None:
        self._catalog_tools = tools
        self._session_factory = session_factory or (lambda: tools)
        self._sessions: dict[str, _HTTPSession] = {}
        self._sessions_lock = threading.Lock()

    def list_tools_payload(self) -> dict[str, object]:
        tool_entries = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.inputSchema,
            }
            for tool in _build_tool_list(self._catalog_tools)
        ]
        return {
            "status": "ok",
            "server": cs.MCP_SERVER_NAME,
            "transport": "http",
            "session_support": {
                "required_for_stateful_workflows": True,
                "create_endpoint": "/sessions",
                "delete_endpoint": "/sessions/{session_id}",
                "pass_session_id_in": "POST body as session_id",
                "auto_create_on_first_call": True,
            },
            "tools": tool_entries,
        }

    def create_session_payload(self) -> dict[str, object]:
        session_id, _ = self._create_session()
        return {
            "status": "ok",
            "server": cs.MCP_SERVER_NAME,
            "transport": "http",
            "session_id": session_id,
            "ui_summary": "HTTP MCP session created.",
        }

    def delete_session_payload(
        self, session_id: str
    ) -> tuple[HTTPStatus, dict[str, object]]:
        normalized_session_id = session_id.strip()
        if not normalized_session_id:
            return (
                HTTPStatus.BAD_REQUEST,
                {"status": "error", "error": "session_id_required"},
            )

        with self._sessions_lock:
            session = self._sessions.pop(normalized_session_id, None)

        if session is None:
            return (
                HTTPStatus.NOT_FOUND,
                {
                    "status": "error",
                    "error": "unknown_session",
                    "session_id": normalized_session_id,
                },
            )

        return (
            HTTPStatus.OK,
            {
                "status": "ok",
                "transport": "http",
                "session_id": normalized_session_id,
                "ui_summary": "HTTP MCP session closed.",
            },
        )

    def _create_session(self) -> tuple[str, _HTTPSession]:
        session_id = uuid4().hex
        session = _HTTPSession(self._session_factory())
        with self._sessions_lock:
            self._sessions[session_id] = session
        return session_id, session

    def _resolve_session(
        self, session_id: str | None
    ) -> tuple[str, _HTTPSession, bool] | None:
        normalized_session_id = str(session_id or "").strip()
        if normalized_session_id:
            with self._sessions_lock:
                session = self._sessions.get(normalized_session_id)
            if session is None:
                return None
            return normalized_session_id, session, False

        created_session_id, session = self._create_session()
        return created_session_id, session, True

    async def call_tool_payload(
        self,
        name: str,
        arguments: MCPToolArguments | dict[str, object] | None,
        session_id: str | None = None,
    ) -> dict[str, object]:
        resolved = self._resolve_session(session_id)
        if resolved is None:
            return {
                "status": "error",
                "source": "http",
                "tool": name,
                "session_id": str(session_id or "").strip(),
                "formatted_text": "Unknown session_id. Create a session first or omit session_id to auto-create one.",
                "payload": {
                    "status": "error",
                    "error": "unknown_session",
                    "ui_summary": "HTTP MCP session was not found.",
                    "exact_next_calls": [
                        {
                            "tool": "http_create_session",
                            "copy_paste": 'POST /sessions {"status":"create"}',
                            "why": "stateful_mcp_workflows_require_session_isolation",
                            "when": "before the next tool call",
                        }
                    ],
                },
                "returns_json": True,
            }

        resolved_session_id, session, created = resolved
        with session.lock:
            execution = await execute_tool_call(session.tools, name, arguments)
        return {
            "status": execution.get("status", "error"),
            "source": execution.get("source", "tool"),
            "tool": name,
            "transport": "http",
            "session_id": resolved_session_id,
            "session_created": created,
            "formatted_text": execution.get("formatted_text", ""),
            "payload": execution.get("payload", {}),
            "returns_json": bool(execution.get("returns_json", True)),
        }


class MCPHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        service: MCPHTTPService,
    ) -> None:
        super().__init__(server_address, MCPHTTPRequestHandler)
        self.service = service


class MCPHTTPRequestHandler(BaseHTTPRequestHandler):
    server: MCPHTTPServer

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._write_json(
            HTTPStatus.NO_CONTENT,
            {"status": "ok"},
        )

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route == "/health":
            self._write_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "server": cs.MCP_SERVER_NAME,
                    "transport": "http",
                },
            )
            return
        if route == "/tools":
            self._write_json(HTTPStatus.OK, self.server.service.list_tools_payload())
            return
        self._write_json(
            HTTPStatus.NOT_FOUND,
            {"status": "error", "error": "route_not_found"},
        )

    def do_DELETE(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route.startswith("/sessions/"):
            status, payload = self.server.service.delete_session_payload(
                route.removeprefix("/sessions/")
            )
            self._write_json(status, payload)
            return
        self._write_json(
            HTTPStatus.NOT_FOUND,
            {"status": "error", "error": "route_not_found"},
        )

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        body = self._read_json_body()
        if body is None:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"status": "error", "error": "invalid_json_body"},
            )
            return

        if route == "/sessions":
            self._write_json(
                HTTPStatus.OK, self.server.service.create_session_payload()
            )
            return

        session_id = str(body.get("session_id", "")).strip() or None

        if route == "/call-tool":
            name = str(body.get("name", "")).strip()
            arguments = body.get("arguments", {})
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"status": "error", "error": "tool_arguments_must_be_object"},
                )
                return
        elif route.startswith("/tools/"):
            name = route.removeprefix("/tools/").strip()
            arguments = {
                key: value for key, value in body.items() if key != "session_id"
            }
        else:
            self._write_json(
                HTTPStatus.NOT_FOUND,
                {"status": "error", "error": "route_not_found"},
            )
            return

        if not name:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"status": "error", "error": "tool_name_required"},
            )
            return

        payload = asyncio.run(
            self.server.service.call_tool_payload(
                name,
                cast(dict[str, object] | None, arguments),
                session_id=session_id,
            )
        )
        payload_body = payload.get("payload")
        if payload.get("status") in {"ok", "blocked"}:
            http_status = HTTPStatus.OK
        elif (
            isinstance(payload_body, dict)
            and payload_body.get("error") == "unknown_session"
        ):
            http_status = HTTPStatus.NOT_FOUND
        else:
            http_status = HTTPStatus.BAD_REQUEST
        self._write_json(http_status, payload)

    def log_message(self, format: str, *args: object) -> None:
        message = format % args if args else format
        logger.info("[GraphCode MCP HTTP] {}", message)

    def _read_json_body(self) -> dict[str, object] | None:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(
            payload, indent=cs.MCP_JSON_INDENT, ensure_ascii=False
        ).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header(
            "Content-Length",
            "0" if status == HTTPStatus.NO_CONTENT else str(len(body)),
        )
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        if status != HTTPStatus.NO_CONTENT:
            self.wfile.write(body)


def create_http_server(
    host: str | None = None,
    port: int | None = None,
) -> tuple[MCPHTTPServer, MemgraphIngestor]:
    tools, ingestor = create_tools_runtime()

    def _session_factory() -> MCPToolsRegistry:
        return create_mcp_tools_registry(
            project_root=str(tools.project_root),
            ingestor=ingestor,
            cypher_gen=tools.cypher_gen,
            orchestrator_prompt=tools._orchestrator_prompt,
        )

    service = MCPHTTPService(tools, session_factory=_session_factory)
    server = MCPHTTPServer(
        (
            host or settings.MCP_HTTP_HOST,
            settings.MCP_HTTP_PORT if port is None else int(port),
        ),
        service,
    )
    return server, ingestor


def serve_http(
    host: str | None = None,
    port: int | None = None,
) -> None:
    logger.info(lg.MCP_SERVER_STARTING)
    server, ingestor = create_http_server(host=host, port=port)
    resolved_host = str(server.server_address[0])
    resolved_port = int(server.server_address[1])
    logger.info(
        "[GraphCode MCP HTTP] Listening on http://{}:{}",
        resolved_host,
        resolved_port,
    )

    with ingestor:
        logger.info(
            lg.MCP_SERVER_CONNECTED.format(
                host=settings.MEMGRAPH_HOST, port=settings.MEMGRAPH_PORT
            )
        )
        try:
            server.serve_forever()
        finally:
            server.server_close()
            logger.info(lg.MCP_SERVER_SHUTDOWN)
