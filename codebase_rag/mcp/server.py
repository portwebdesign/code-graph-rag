import json
import os
import sys
from pathlib import Path
from typing import Any, cast

from loguru import logger
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from codebase_rag.agents import MCP_SYSTEM_PROMPT
from codebase_rag.agents.output_parser import decode_escaped_text, extract_code_block
from codebase_rag.core import constants as cs
from codebase_rag.core import logs as lg
from codebase_rag.core.config import settings
from codebase_rag.data_models.types_defs import MCPToolArguments
from codebase_rag.infrastructure import tool_errors as te
from codebase_rag.mcp.tools import MCPToolsRegistry, create_mcp_tools_registry
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.services.llm import CypherGenerator


def _json_dumps_pretty(payload: Any) -> str:
    return json.dumps(payload, indent=cs.MCP_JSON_INDENT, ensure_ascii=False)


def _format_exact_next_calls(exact_next_calls: object) -> str:
    if not isinstance(exact_next_calls, list):
        return ""
    lines = ["Next actions:"]
    normalized_calls = cast(list[object], exact_next_calls)
    for index, item in enumerate(normalized_calls[:5], start=1):
        if not isinstance(item, dict):
            continue
        action_payload = cast(dict[str, object], item)
        tool_name = str(action_payload.get("tool", "")).strip()
        if not tool_name:
            continue
        copy_paste = str(action_payload.get("copy_paste", "")).strip()
        why = str(action_payload.get("why", "")).strip()
        when = str(action_payload.get("when", "")).strip()
        action_line = f"{index}. `{copy_paste or tool_name}`"
        if why:
            action_line += f" - {why}"
        lines.append(action_line)
        if when:
            lines.append(f"   when: {when}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _format_next_best_action(next_best_action: object) -> str:
    if not isinstance(next_best_action, dict):
        return ""
    action_payload = cast(dict[str, object], next_best_action)
    tool_name = str(
        action_payload.get("tool") or action_payload.get("action") or ""
    ).strip()
    if not tool_name:
        return ""

    why = str(action_payload.get("why", "")).strip()
    params_hint = action_payload.get("params_hint", {})
    params_text = ""
    if isinstance(params_hint, dict) and params_hint:
        params_text = f" {json.dumps(params_hint, ensure_ascii=False)}"

    line = f"Next best action: `{tool_name}`{params_text}"
    if why:
        line += f" - {why}"
    return line


def _format_tool_result_text(result: object, returns_json: bool) -> str:
    if not returns_json or isinstance(result, str):
        text = decode_escaped_text(str(result))
        _, code_block = extract_code_block(text)
        return code_block if code_block else text

    if not isinstance(result, dict):
        return _json_dumps_pretty(result)

    payload = cast(dict[str, object], result)
    sections: list[str] = []
    ui_summary = decode_escaped_text(str(payload.get("ui_summary", "")).strip())
    if ui_summary:
        sections.append(ui_summary)
    else:
        error_text = decode_escaped_text(str(payload.get("error", "")).strip())
        if error_text:
            sections.append(error_text)

    query_used = str(payload.get("query_used", "")).strip()
    if query_used:
        sections.append(f"Cypher:\n```cypher\n{query_used}\n```")

    code = decode_escaped_text(str(payload.get("code", "")).strip())
    content = decode_escaped_text(str(payload.get("content", "")).strip())
    code_block_lang: str | None = None
    code_block_body: str | None = None

    if code:
        code_block_lang = str(payload.get("language", "")).strip().lower() or None
        code_block_body = code
    elif content:
        code_block_lang, code_block_body = extract_code_block(content)
        if code_block_body is None and "\n" in content:
            code_block_lang = str(payload.get("language", "")).strip().lower() or None
            code_block_body = content

    if code_block_body:
        sections.append(
            "Generated content:\n```"
            + (code_block_lang or "text")
            + "\n"
            + code_block_body
            + "\n```"
        )
    elif content:
        sections.append(content)

    next_actions = _format_exact_next_calls(payload.get("exact_next_calls"))
    if next_actions:
        sections.append(next_actions)

    next_best_action = _format_next_best_action(payload.get("next_best_action"))
    if next_best_action:
        sections.append(next_best_action)

    details = dict(payload)
    for key in (
        "ui_summary",
        "content",
        "code",
        "query_used",
        "exact_next_calls",
        "next_best_action",
        "session_contract",
        "execution_state",
    ):
        details.pop(key, None)
    if details:
        sections.append(f"Details:\n```json\n{_json_dumps_pretty(details)}\n```")

    return "\n\n".join(section for section in sections if section).strip()


def setup_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=cs.MCP_LOG_LEVEL_INFO,
        format=cs.MCP_LOG_FORMAT,
    )


def get_project_root() -> Path:
    repo_path: str | None = (
        os.environ.get(cs.MCPEnvVar.TARGET_REPO_PATH) or settings.TARGET_REPO_PATH
    )

    if not repo_path:
        repo_path = os.environ.get(cs.MCPEnvVar.CLAUDE_PROJECT_ROOT) or os.environ.get(
            cs.MCPEnvVar.PWD
        )

        if repo_path:
            logger.info(lg.MCP_SERVER_INFERRED_ROOT.format(path=repo_path))
        else:
            repo_path = str(Path.cwd())
            logger.info(lg.MCP_SERVER_NO_ROOT.format(path=repo_path))

    project_root = Path(repo_path).resolve()

    if not project_root.exists():
        raise ValueError(te.MCP_PATH_NOT_EXISTS.format(path=project_root))

    if not project_root.is_dir():
        raise ValueError(te.MCP_PATH_NOT_DIR.format(path=project_root))

    logger.info(lg.MCP_SERVER_ROOT_RESOLVED.format(path=project_root))
    return project_root


def create_tools_runtime() -> tuple[MCPToolsRegistry, MemgraphIngestor]:
    setup_logging()

    try:
        project_root = get_project_root()
        logger.info(lg.MCP_SERVER_USING_ROOT.format(path=project_root))
    except ValueError as e:
        logger.error(lg.MCP_SERVER_CONFIG_ERROR.format(error=e))
        raise

    logger.info(lg.MCP_SERVER_INIT_SERVICES)

    ingestor = MemgraphIngestor(
        host=settings.MEMGRAPH_HOST,
        port=settings.MEMGRAPH_PORT,
        batch_size=settings.MEMGRAPH_BATCH_SIZE,
        username=settings.MEMGRAPH_USERNAME,
        password=settings.MEMGRAPH_PASSWORD,
    )

    cypher_generator = CypherGenerator()

    tools = create_mcp_tools_registry(
        project_root=str(project_root),
        ingestor=ingestor,
        cypher_gen=cypher_generator,
        orchestrator_prompt=MCP_SYSTEM_PROMPT,
    )

    logger.info(lg.MCP_SERVER_INIT_SUCCESS)
    return tools, ingestor


def _build_tool_list(tools: MCPToolsRegistry) -> list[Tool]:
    schemas = tools.get_tool_schemas()
    return [
        Tool(
            name=schema.name,
            description=schema.description,
            inputSchema={**schema.inputSchema},
        )
        for schema in schemas
    ]


async def execute_tool_call(
    tools: MCPToolsRegistry,
    name: str,
    arguments: MCPToolArguments | dict[str, object] | None,
) -> dict[str, object]:
    logger.info(lg.MCP_SERVER_CALLING_TOOL.format(name=name))
    normalized_arguments = cast(dict[str, object], arguments or {})

    preflight_error = tools.get_preflight_gate_error(name)
    if preflight_error is not None:
        logger.warning(preflight_error)
        payload = tools.build_gate_guidance_payload(
            tool_name=name,
            gate_error=preflight_error,
            gate_type="preflight",
        )
        return {
            "status": "blocked",
            "source": "preflight",
            "payload": payload,
            "formatted_text": _format_tool_result_text(payload, True),
        }

    phase_error = tools.get_phase_gate_error(name)
    if phase_error is not None:
        logger.warning(phase_error)
        payload = tools.build_gate_guidance_payload(
            tool_name=name,
            gate_error=phase_error,
            gate_type="phase",
        )
        return {
            "status": "blocked",
            "source": "phase",
            "payload": payload,
            "formatted_text": _format_tool_result_text(payload, True),
        }

    workflow_gate_payload = tools.get_workflow_gate_payload(name, normalized_arguments)
    if workflow_gate_payload is not None:
        logger.warning(str(workflow_gate_payload.get("error", "workflow_gate")))
        return {
            "status": "blocked",
            "source": "workflow",
            "payload": workflow_gate_payload,
            "formatted_text": _format_tool_result_text(workflow_gate_payload, True),
        }

    visibility_gate_payload = tools.get_visibility_gate_payload(
        name, normalized_arguments
    )
    if visibility_gate_payload is not None:
        logger.warning(str(visibility_gate_payload.get("error", "visibility_gate")))
        return {
            "status": "blocked",
            "source": "visibility",
            "payload": visibility_gate_payload,
            "formatted_text": _format_tool_result_text(visibility_gate_payload, True),
        }

    handler_info = tools.get_tool_handler(name)
    if not handler_info:
        error_msg = cs.MCP_UNKNOWN_TOOL_ERROR.format(name=name)
        logger.error(lg.MCP_SERVER_UNKNOWN_TOOL.format(name=name))
        return {
            "status": "error",
            "source": "registry",
            "payload": {"error": error_msg},
            "formatted_text": te.ERROR_WRAPPER.format(message=error_msg),
        }

    handler, returns_json = handler_info

    try:
        result = await handler(**normalized_arguments)
        return {
            "status": "ok",
            "source": "tool",
            "payload": result,
            "returns_json": returns_json,
            "formatted_text": _format_tool_result_text(result, returns_json),
        }
    except Exception as e:
        error_msg = cs.MCP_TOOL_EXEC_ERROR.format(name=name, error=e)
        logger.exception(lg.MCP_SERVER_TOOL_ERROR.format(name=name, error=e))
        return {
            "status": "error",
            "source": "tool",
            "payload": {"error": error_msg},
            "formatted_text": te.ERROR_WRAPPER.format(message=error_msg),
        }


def create_server() -> tuple[Server, MemgraphIngestor]:
    tools, ingestor = create_tools_runtime()

    server = Server(cs.MCP_SERVER_NAME)

    def _create_error_content(message: str) -> list[TextContent]:
        return [
            TextContent(
                type=cs.MCP_CONTENT_TYPE_TEXT,
                text=te.ERROR_WRAPPER.format(message=message),
            )
        ]

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return _build_tool_list(tools)

    @server.call_tool()
    async def call_tool(name: str, arguments: MCPToolArguments) -> list[TextContent]:
        try:
            execution = await execute_tool_call(tools, name, arguments)
            return [
                TextContent(
                    type=cs.MCP_CONTENT_TYPE_TEXT,
                    text=str(execution.get("formatted_text", "")),
                )
            ]
        except Exception as e:
            error_msg = cs.MCP_TOOL_EXEC_ERROR.format(name=name, error=e)
            logger.exception(lg.MCP_SERVER_TOOL_ERROR.format(name=name, error=e))
            return _create_error_content(error_msg)

    return server, ingestor


async def main() -> None:
    logger.info(lg.MCP_SERVER_STARTING)

    server, ingestor = create_server()
    logger.info(lg.MCP_SERVER_CREATED)

    with ingestor:
        logger.info(
            lg.MCP_SERVER_CONNECTED.format(
                host=settings.MEMGRAPH_HOST, port=settings.MEMGRAPH_PORT
            )
        )
        try:
            async with stdio_server() as (read_stream, write_stream):
                await server.run(
                    read_stream, write_stream, server.create_initialization_options()
                )
        except Exception as e:
            logger.error(lg.MCP_SERVER_FATAL_ERROR.format(error=e))
            raise
        finally:
            logger.info(lg.MCP_SERVER_SHUTDOWN)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
