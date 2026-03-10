from __future__ import annotations

import asyncio
import json
import threading
import time
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
        now = time.time()
        self.created_at = now
        self.last_seen = now

    def touch(self) -> None:
        self.last_seen = time.time()


class MCPHTTPService:
    def __init__(
        self,
        tools: MCPToolsRegistry,
        session_factory: Callable[[str | None], MCPToolsRegistry] | None = None,
    ) -> None:
        self._catalog_tools = tools
        self._session_factory = session_factory or (lambda _client_profile=None: tools)
        self._sessions: dict[str, _HTTPSession] = {}
        self._sessions_lock = threading.Lock()
        self._execution_lock = threading.Lock()
        self._rate_limit_lock = threading.Lock()
        self._audit_lock = threading.Lock()
        self._rate_limit_events: dict[str, list[float]] = {}
        self._audit_entries: list[dict[str, object]] = []

    def _build_session_tools(self, client_profile: str | None) -> MCPToolsRegistry:
        try:
            return self._session_factory(client_profile)
        except TypeError:
            return cast(Callable[[], MCPToolsRegistry], self._session_factory)()

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
                "client_profile_optional_on_create": True,
                "supported_client_profiles": [
                    str(cs.MCPClientProfile.BALANCED),
                    str(cs.MCPClientProfile.VSCODE),
                    str(cs.MCPClientProfile.CLINE),
                    str(cs.MCPClientProfile.COPILOT),
                    str(cs.MCPClientProfile.OLLAMA),
                    str(cs.MCPClientProfile.HTTP),
                ],
            },
            "tools": tool_entries,
        }

    async def list_resources_payload(self) -> dict[str, object]:
        resources = await self._catalog_tools.list_mcp_resources()
        return {
            "status": "ok",
            "server": cs.MCP_SERVER_NAME,
            "transport": "http",
            "resources": resources,
        }

    async def read_resource_payload(
        self,
        uri: str,
        *,
        session_id: str | None = None,
        client_profile: str | None = None,
    ) -> dict[str, object]:
        resolved = self._resolve_session(session_id, client_profile=client_profile)
        if resolved is None:
            return {
                "status": "error",
                "error": "unknown_session",
                "ui_summary": "HTTP MCP session was not found.",
            }
        resolved_session_id, session, created = resolved
        with self._execution_lock:
            with session.lock:
                payload = await session.tools.read_mcp_resource(uri)
        return {
            "status": "ok",
            "transport": "http",
            "session_id": resolved_session_id,
            "session_created": created,
            "resource_uri": uri,
            "payload": payload,
        }

    async def list_prompts_payload(self) -> dict[str, object]:
        prompts = await self._catalog_tools.list_mcp_prompts()
        return {
            "status": "ok",
            "server": cs.MCP_SERVER_NAME,
            "transport": "http",
            "prompts": prompts,
        }

    async def get_prompt_payload(
        self,
        name: str,
        arguments: dict[str, str] | None = None,
        *,
        session_id: str | None = None,
        client_profile: str | None = None,
    ) -> dict[str, object]:
        resolved = self._resolve_session(session_id, client_profile=client_profile)
        if resolved is None:
            return {
                "status": "error",
                "error": "unknown_session",
                "ui_summary": "HTTP MCP session was not found.",
            }
        resolved_session_id, session, created = resolved
        with self._execution_lock:
            with session.lock:
                payload = await session.tools.get_mcp_prompt(name, arguments)
        return {
            "status": "ok",
            "transport": "http",
            "session_id": resolved_session_id,
            "session_created": created,
            "prompt": name,
            "payload": payload,
        }

    @staticmethod
    def _auth_token() -> str:
        return str(settings.MCP_HTTP_AUTH_TOKEN).strip()

    @staticmethod
    def _session_ttl_seconds() -> int:
        return max(60, int(settings.MCP_HTTP_SESSION_TTL_SECONDS))

    @staticmethod
    def _rate_limit_window_seconds() -> int:
        return max(1, int(settings.MCP_HTTP_RATE_LIMIT_WINDOW_SECONDS))

    @staticmethod
    def _rate_limit_max_requests() -> int:
        return max(1, int(settings.MCP_HTTP_RATE_LIMIT_MAX_REQUESTS))

    def authorize_request(
        self,
        client_ip: str,
        auth_header: str | None,
        *,
        now: float | None = None,
    ) -> tuple[bool, HTTPStatus, dict[str, object] | None]:
        current_time = time.time() if now is None else float(now)
        token = self._auth_token()
        if token:
            expected = f"Bearer {token}"
            if str(auth_header or "").strip() != expected:
                return (
                    False,
                    HTTPStatus.UNAUTHORIZED,
                    {
                        "status": "error",
                        "error": "unauthorized",
                        "ui_summary": "Missing or invalid Authorization header.",
                    },
                )

        window = self._rate_limit_window_seconds()
        max_requests = self._rate_limit_max_requests()
        normalized_ip = str(client_ip or "unknown").strip() or "unknown"
        with self._rate_limit_lock:
            recent_events = [
                timestamp
                for timestamp in self._rate_limit_events.get(normalized_ip, [])
                if (current_time - timestamp) <= window
            ]
            if len(recent_events) >= max_requests:
                retry_after = max(1, int(window - (current_time - recent_events[0])))
                return (
                    False,
                    HTTPStatus.TOO_MANY_REQUESTS,
                    {
                        "status": "error",
                        "error": "rate_limited",
                        "retry_after_seconds": retry_after,
                        "ui_summary": "HTTP MCP rate limit exceeded.",
                    },
                )
            recent_events.append(current_time)
            self._rate_limit_events[normalized_ip] = recent_events
        self.cleanup_expired_sessions(now=current_time)
        return True, HTTPStatus.OK, None

    def cleanup_expired_sessions(self, *, now: float | None = None) -> int:
        current_time = time.time() if now is None else float(now)
        ttl_seconds = self._session_ttl_seconds()
        expired_ids: list[str] = []
        with self._sessions_lock:
            for session_id, session in self._sessions.items():
                if (current_time - session.last_seen) > ttl_seconds:
                    expired_ids.append(session_id)
            for session_id in expired_ids:
                self._sessions.pop(session_id, None)
        return len(expired_ids)

    def audit_event(
        self,
        *,
        method: str,
        route: str,
        client_ip: str,
        status: int,
        session_id: str | None = None,
    ) -> None:
        if not bool(settings.MCP_HTTP_AUDIT_LOG_ENABLED):
            return
        entry = {
            "timestamp": int(time.time()),
            "method": method,
            "route": route,
            "client_ip": client_ip,
            "status": status,
            "session_id": str(session_id or "").strip(),
        }
        with self._audit_lock:
            self._audit_entries.insert(0, entry)
            self._audit_entries = self._audit_entries[:200]
        logger.info(
            "[GraphCode MCP HTTP] {} {} {} status={}",
            method,
            route,
            client_ip,
            status,
        )

    def create_session_payload(
        self, client_profile: str | None = None
    ) -> dict[str, object]:
        session_id, session = self._create_session(client_profile=client_profile)
        client_profile_getter = getattr(session.tools, "_client_profile", None)
        resolved_client_profile = (
            client_profile_getter()
            if callable(client_profile_getter)
            else str(client_profile or cs.MCPClientProfile.BALANCED)
        )
        return {
            "status": "ok",
            "server": cs.MCP_SERVER_NAME,
            "transport": "http",
            "session_id": session_id,
            "client_profile": resolved_client_profile,
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

    def _create_session(
        self, client_profile: str | None = None
    ) -> tuple[str, _HTTPSession]:
        session_id = uuid4().hex
        session = _HTTPSession(self._build_session_tools(client_profile))
        with self._sessions_lock:
            self._sessions[session_id] = session
        return session_id, session

    def _resolve_session(
        self, session_id: str | None, client_profile: str | None = None
    ) -> tuple[str, _HTTPSession, bool] | None:
        normalized_session_id = str(session_id or "").strip()
        if normalized_session_id:
            with self._sessions_lock:
                session = self._sessions.get(normalized_session_id)
            if session is None:
                return None
            session.touch()
            return normalized_session_id, session, False

        created_session_id, session = self._create_session(
            client_profile=client_profile
        )
        session.touch()
        return created_session_id, session, True

    async def call_tool_payload(
        self,
        name: str,
        arguments: MCPToolArguments | dict[str, object] | None,
        session_id: str | None = None,
        client_profile: str | None = None,
    ) -> dict[str, object]:
        resolved = self._resolve_session(session_id, client_profile=client_profile)
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
        with self._execution_lock:
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

    def _client_ip(self) -> str:
        forwarded = str(self.headers.get("X-Forwarded-For", "")).strip()
        if forwarded:
            return forwarded.split(",")[0].strip()
        return str(self.client_address[0] if self.client_address else "unknown")

    def _authorize_request(self, method: str, route: str) -> str | None:
        client_ip = self._client_ip()
        allowed, status, payload = self.server.service.authorize_request(
            client_ip,
            self.headers.get("Authorization"),
        )
        if not allowed:
            response_payload = payload or {"status": "error", "error": "unauthorized"}
            self.server.service.audit_event(
                method=method,
                route=route,
                client_ip=client_ip,
                status=int(status),
            )
            self._write_json(status, response_payload)
            return None
        return client_ip

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._write_json(
            HTTPStatus.NO_CONTENT,
            {"status": "ok"},
        )

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        client_ip = self._authorize_request("GET", route)
        if client_ip is None:
            return
        if route == "/health":
            payload = {
                "status": "ok",
                "server": cs.MCP_SERVER_NAME,
                "transport": "http",
            }
            self.server.service.audit_event(
                method="GET",
                route=route,
                client_ip=client_ip,
                status=int(HTTPStatus.OK),
            )
            self._write_json(
                HTTPStatus.OK,
                payload,
            )
            return
        if route == "/tools":
            payload = self.server.service.list_tools_payload()
            self.server.service.audit_event(
                method="GET",
                route=route,
                client_ip=client_ip,
                status=int(HTTPStatus.OK),
            )
            self._write_json(HTTPStatus.OK, payload)
            return
        if route == "/resources":
            payload = asyncio.run(self.server.service.list_resources_payload())
            self.server.service.audit_event(
                method="GET",
                route=route,
                client_ip=client_ip,
                status=int(HTTPStatus.OK),
            )
            self._write_json(HTTPStatus.OK, payload)
            return
        if route == "/prompts":
            payload = asyncio.run(self.server.service.list_prompts_payload())
            self.server.service.audit_event(
                method="GET",
                route=route,
                client_ip=client_ip,
                status=int(HTTPStatus.OK),
            )
            self._write_json(HTTPStatus.OK, payload)
            return
        self.server.service.audit_event(
            method="GET",
            route=route,
            client_ip=client_ip,
            status=int(HTTPStatus.NOT_FOUND),
        )
        self._write_json(
            HTTPStatus.NOT_FOUND,
            {"status": "error", "error": "route_not_found"},
        )

    def do_DELETE(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        client_ip = self._authorize_request("DELETE", route)
        if client_ip is None:
            return
        if route.startswith("/sessions/"):
            status, payload = self.server.service.delete_session_payload(
                route.removeprefix("/sessions/")
            )
            self.server.service.audit_event(
                method="DELETE",
                route=route,
                client_ip=client_ip,
                status=int(status),
                session_id=route.removeprefix("/sessions/"),
            )
            self._write_json(status, payload)
            return
        self.server.service.audit_event(
            method="DELETE",
            route=route,
            client_ip=client_ip,
            status=int(HTTPStatus.NOT_FOUND),
        )
        self._write_json(
            HTTPStatus.NOT_FOUND,
            {"status": "error", "error": "route_not_found"},
        )

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        client_ip = self._authorize_request("POST", route)
        if client_ip is None:
            return
        body = self._read_json_body()
        if body is None:
            self.server.service.audit_event(
                method="POST",
                route=route,
                client_ip=client_ip,
                status=int(HTTPStatus.BAD_REQUEST),
            )
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"status": "error", "error": "invalid_json_body"},
            )
            return

        if route == "/sessions":
            client_profile = str(body.get("client_profile", "")).strip() or None
            payload = self.server.service.create_session_payload(
                client_profile=client_profile
            )
            self.server.service.audit_event(
                method="POST",
                route=route,
                client_ip=client_ip,
                status=int(HTTPStatus.OK),
                session_id=str(payload.get("session_id", "")),
            )
            self._write_json(
                HTTPStatus.OK,
                payload,
            )
            return

        if route == "/resources/read":
            session_id = str(body.get("session_id", "")).strip() or None
            client_profile = str(body.get("client_profile", "")).strip() or None
            uri = str(body.get("uri", "")).strip()
            if not uri:
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"status": "error", "error": "resource_uri_required"},
                )
                return
            payload = asyncio.run(
                self.server.service.read_resource_payload(
                    uri,
                    session_id=session_id,
                    client_profile=client_profile,
                )
            )
            self.server.service.audit_event(
                method="POST",
                route=route,
                client_ip=client_ip,
                status=int(HTTPStatus.OK),
                session_id=session_id,
            )
            self._write_json(HTTPStatus.OK, payload)
            return

        if route == "/prompts/get":
            session_id = str(body.get("session_id", "")).strip() or None
            client_profile = str(body.get("client_profile", "")).strip() or None
            name = str(body.get("name", "")).strip()
            arguments = body.get("arguments", {})
            if not name:
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"status": "error", "error": "prompt_name_required"},
                )
                return
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"status": "error", "error": "prompt_arguments_must_be_object"},
                )
                return
            payload = asyncio.run(
                self.server.service.get_prompt_payload(
                    name,
                    cast(dict[str, str], arguments),
                    session_id=session_id,
                    client_profile=client_profile,
                )
            )
            self.server.service.audit_event(
                method="POST",
                route=route,
                client_ip=client_ip,
                status=int(HTTPStatus.OK),
                session_id=session_id,
            )
            self._write_json(HTTPStatus.OK, payload)
            return

        session_id = str(body.get("session_id", "")).strip() or None
        client_profile = str(body.get("client_profile", "")).strip() or None

        if route == "/call-tool":
            name = str(body.get("name", "")).strip()
            arguments = body.get("arguments", {})
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                self.server.service.audit_event(
                    method="POST",
                    route=route,
                    client_ip=client_ip,
                    status=int(HTTPStatus.BAD_REQUEST),
                    session_id=session_id,
                )
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"status": "error", "error": "tool_arguments_must_be_object"},
                )
                return
        elif route.startswith("/tools/"):
            name = route.removeprefix("/tools/").strip()
            arguments = {
                key: value
                for key, value in body.items()
                if key not in {"session_id", "client_profile"}
            }
        else:
            self.server.service.audit_event(
                method="POST",
                route=route,
                client_ip=client_ip,
                status=int(HTTPStatus.NOT_FOUND),
                session_id=session_id,
            )
            self._write_json(
                HTTPStatus.NOT_FOUND,
                {"status": "error", "error": "route_not_found"},
            )
            return

        if not name:
            self.server.service.audit_event(
                method="POST",
                route=route,
                client_ip=client_ip,
                status=int(HTTPStatus.BAD_REQUEST),
                session_id=session_id,
            )
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
                client_profile=client_profile,
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
        self.server.service.audit_event(
            method="POST",
            route=route,
            client_ip=client_ip,
            status=int(http_status),
            session_id=str(payload.get("session_id", session_id or "")),
        )
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
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        if status != HTTPStatus.NO_CONTENT:
            self.wfile.write(body)


def create_http_server(
    host: str | None = None,
    port: int | None = None,
) -> tuple[MCPHTTPServer, MemgraphIngestor]:
    tools, ingestor = create_tools_runtime()

    def _session_factory(client_profile: str | None = None) -> MCPToolsRegistry:
        registry = create_mcp_tools_registry(
            project_root=str(tools.project_root),
            ingestor=ingestor,
            cypher_gen=tools.cypher_gen,
            orchestrator_prompt=tools._orchestrator_prompt,
        )
        registry.set_client_profile(client_profile)
        return registry

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
