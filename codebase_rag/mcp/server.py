import inspect
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from loguru import logger
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    Resource,
    TextContent,
    Tool,
)
from pydantic import AnyUrl

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


def _coerce_float(value: object) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _coerce_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _format_execution_readiness_summary(payload: dict[str, object]) -> str:
    state_machine = payload.get("state_machine_gate", {})
    confidence_gate = payload.get("confidence_gate", {})
    context_gate = payload.get("context_confidence_gate", {})
    completion_gate = payload.get("completion_gate", {})
    impact_gate = payload.get("impact_graph_gate", {})
    test_quality_gate = payload.get("test_quality_gate", {})
    guard_partition = payload.get("guard_partition", {})

    current_phase = ""
    if isinstance(state_machine, dict):
        state_machine_dict = cast(dict[str, object], state_machine)
        current_phase = str(state_machine_dict.get("current_phase") or "").strip()

    confidence_score = 0.0
    confidence_required = 0.0
    if isinstance(confidence_gate, dict):
        confidence_gate_dict = cast(dict[str, object], confidence_gate)
        confidence_score = _coerce_float(confidence_gate_dict.get("score"))
        confidence_required = _coerce_float(confidence_gate_dict.get("required"))

    context_score = 0.0
    context_required = 0.0
    if isinstance(context_gate, dict):
        context_gate_dict = cast(dict[str, object], context_gate)
        context_score = _coerce_float(context_gate_dict.get("score"))
        context_required = _coerce_float(context_gate_dict.get("required"))

    missing: list[str] = []
    if isinstance(completion_gate, dict):
        completion_gate_dict = cast(dict[str, object], completion_gate)
        raw_missing = completion_gate_dict.get("missing", [])
        if isinstance(raw_missing, list):
            missing = [str(item).strip() for item in raw_missing if str(item).strip()]
    normalized_missing = [
        "test_quality_gate" if item == "test_quality" else item for item in missing
    ]

    soft_failed: list[str] = []
    hard_failed: list[str] = []
    if isinstance(guard_partition, dict):
        guard_partition_dict = cast(dict[str, object], guard_partition)
        soft = guard_partition_dict.get("soft", {})
        hard = guard_partition_dict.get("hard", {})
        if isinstance(soft, dict):
            soft_dict = cast(dict[str, object], soft)
            raw_failed = soft_dict.get("failed", [])
            if isinstance(raw_failed, list):
                soft_failed = [
                    str(item).strip() for item in raw_failed if str(item).strip()
                ]
        if isinstance(hard, dict):
            hard_dict = cast(dict[str, object], hard)
            raw_failed = hard_dict.get("failed", [])
            if isinstance(raw_failed, list):
                hard_failed = [
                    str(item).strip() for item in raw_failed if str(item).strip()
                ]

    impact_called = False
    impact_affected = 0
    if isinstance(impact_gate, dict):
        impact_gate_dict = cast(dict[str, object], impact_gate)
        impact_called = bool(impact_gate_dict.get("called", False))
        impact_affected = _coerce_int(impact_gate_dict.get("affected"))

    test_quality_score = 0.0
    test_quality_required = 0.0
    if isinstance(test_quality_gate, dict):
        test_quality_gate_dict = cast(dict[str, object], test_quality_gate)
        test_quality_score = _coerce_float(test_quality_gate_dict.get("score"))
        test_quality_required = _coerce_float(test_quality_gate_dict.get("required"))

    lines = [
        "Execution readiness summary:",
        f"- phase: {current_phase or 'unknown'}",
        f"- confidence: {confidence_score:.3f}/{confidence_required:.3f}",
        f"- context confidence: {context_score:.3f}/{context_required:.3f}",
        f"- hard guard failures: {', '.join(hard_failed) if hard_failed else 'none'}",
        f"- soft guard failures: {', '.join(soft_failed) if soft_failed else 'none'}",
        (
            "- missing completion evidence: "
            + (", ".join(normalized_missing) if normalized_missing else "none")
        ),
        (
            "- impact graph: "
            + ("called" if impact_called else "not called")
            + f" (affected={impact_affected})"
        ),
        f"- test quality: {test_quality_score:.3f}/{test_quality_required:.3f}",
    ]
    return "\n".join(lines)


def _normalize_tool_name(name: str) -> str:
    aliases = {
        "test_quality": cs.MCPToolName.TEST_QUALITY_GATE,
    }
    normalized = str(name or "").strip()
    return aliases.get(normalized, normalized)


def _normalize_tool_arguments(
    name: str,
    arguments: dict[str, object],
    handler: object,
) -> dict[str, object]:
    normalized_arguments = dict(arguments)

    if name in {cs.MCPToolName.PLAN_TASK, cs.MCPToolName.TEST_GENERATE}:
        if not str(normalized_arguments.get("goal", "")).strip():
            alias_goal = str(
                normalized_arguments.get("natural_language_query")
                or normalized_arguments.get("query")
                or ""
            ).strip()
            if alias_goal:
                normalized_arguments["goal"] = alias_goal

    if name == cs.MCPToolName.TEST_GENERATE:
        output_mode = str(normalized_arguments.get("output_mode", "")).strip()
        if not output_mode:
            alias_mode = str(normalized_arguments.get("output_format", "")).strip()
            if alias_mode:
                normalized_arguments["output_mode"] = alias_mode

    if name == cs.MCPToolName.PLAN_TASK:
        normalized_arguments.pop("output_format", None)

    try:
        callable_handler = cast(Callable[..., object], handler)
        allowed_params = set(inspect.signature(callable_handler).parameters.keys())
    except (TypeError, ValueError):
        return normalized_arguments

    return {
        key: value
        for key, value in normalized_arguments.items()
        if key in allowed_params
    }


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
    elif {
        "state_machine_gate",
        "confidence_gate",
        "completion_gate",
    }.issubset(payload.keys()):
        sections.append(_format_execution_readiness_summary(payload))
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


async def _build_resource_list(tools: MCPToolsRegistry) -> list[Resource]:
    resources = await tools.list_mcp_resources()
    resource_items: list[Resource] = []
    for entry in resources:
        if not isinstance(entry, dict):
            continue
        entry_dict = entry
        uri = str(entry_dict.get("uri", "")).strip()
        if not uri:
            continue
        resource_items.append(
            Resource(
                uri=cast(AnyUrl, uri),
                name=str(entry_dict.get("name", "")),
                description=str(entry_dict.get("description", "")).strip() or None,
                mimeType=str(entry_dict.get("mime_type", "")).strip()
                or "application/json",
            )
        )
    return resource_items


async def _build_prompt_list(tools: MCPToolsRegistry) -> list[Prompt]:
    prompts = await tools.list_mcp_prompts()
    return [
        Prompt(
            name=str(entry.get("name", "")),
            description=str(entry.get("description", "")).strip() or None,
            arguments=[
                PromptArgument(
                    name=str(argument.get("name", "")),
                    description=str(argument.get("description", "")).strip() or None,
                    required=bool(argument.get("required", False)),
                )
                for argument in cast(
                    list[dict[str, object]], entry.get("arguments", [])
                )
                if isinstance(argument, dict) and str(argument.get("name", "")).strip()
            ]
            or None,
        )
        for entry in prompts
        if isinstance(entry, dict) and str(entry.get("name", "")).strip()
    ]


async def execute_tool_call(
    tools: MCPToolsRegistry,
    name: str,
    arguments: MCPToolArguments | dict[str, object] | None,
) -> dict[str, object]:
    canonical_name = _normalize_tool_name(name)
    logger.info(lg.MCP_SERVER_CALLING_TOOL.format(name=canonical_name))
    raw_arguments = cast(dict[str, object], arguments or {})

    handler_info = tools.get_tool_handler(canonical_name)
    if not handler_info:
        error_msg = cs.MCP_UNKNOWN_TOOL_ERROR.format(name=canonical_name)
        logger.error(lg.MCP_SERVER_UNKNOWN_TOOL.format(name=canonical_name))
        return {
            "status": "error",
            "source": "registry",
            "payload": {"error": error_msg},
            "formatted_text": te.ERROR_WRAPPER.format(message=error_msg),
        }

    handler, returns_json = handler_info
    normalized_arguments = _normalize_tool_arguments(
        canonical_name, raw_arguments, handler
    )

    preflight_error = tools.get_preflight_gate_error(canonical_name)
    if preflight_error is not None:
        logger.warning(preflight_error)
        payload = tools.build_gate_guidance_payload(
            tool_name=canonical_name,
            gate_error=preflight_error,
            gate_type="preflight",
        )
        return {
            "status": "blocked",
            "source": "preflight",
            "payload": payload,
            "formatted_text": _format_tool_result_text(payload, True),
        }

    phase_error = tools.get_phase_gate_error(canonical_name)
    if phase_error is not None:
        logger.warning(phase_error)
        payload = tools.build_gate_guidance_payload(
            tool_name=canonical_name,
            gate_error=phase_error,
            gate_type="phase",
        )
        return {
            "status": "blocked",
            "source": "phase",
            "payload": payload,
            "formatted_text": _format_tool_result_text(payload, True),
        }

    workflow_gate_payload = tools.get_workflow_gate_payload(
        canonical_name, normalized_arguments
    )
    if workflow_gate_payload is not None:
        logger.warning(str(workflow_gate_payload.get("error", "workflow_gate")))
        return {
            "status": "blocked",
            "source": "workflow",
            "payload": workflow_gate_payload,
            "formatted_text": _format_tool_result_text(workflow_gate_payload, True),
        }

    visibility_gate_payload = tools.get_visibility_gate_payload(
        canonical_name, normalized_arguments
    )
    if visibility_gate_payload is not None:
        logger.warning(str(visibility_gate_payload.get("error", "visibility_gate")))
        return {
            "status": "blocked",
            "source": "visibility",
            "payload": visibility_gate_payload,
            "formatted_text": _format_tool_result_text(visibility_gate_payload, True),
        }

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
        error_msg = cs.MCP_TOOL_EXEC_ERROR.format(name=canonical_name, error=e)
        logger.exception(lg.MCP_SERVER_TOOL_ERROR.format(name=canonical_name, error=e))
        return {
            "status": "error",
            "source": "tool",
            "payload": {"error": error_msg},
            "formatted_text": te.ERROR_WRAPPER.format(message=error_msg),
        }


def create_server_with_tools(tools: MCPToolsRegistry) -> Server:
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

    if bool(settings.MCP_ENABLE_RESOURCES):

        @server.list_resources()
        async def list_resources() -> list[Resource]:
            return await _build_resource_list(tools)

        @server.read_resource()
        async def read_resource(uri: object) -> str:
            payload = await tools.read_mcp_resource(str(uri))
            return _json_dumps_pretty(payload)

    if bool(settings.MCP_ENABLE_PROMPTS):

        @server.list_prompts()
        async def list_prompts() -> list[Prompt]:
            return await _build_prompt_list(tools)

        @server.get_prompt()
        async def get_prompt(
            name: str,
            arguments: dict[str, str] | None,
        ) -> GetPromptResult:
            payload = await tools.get_mcp_prompt(name, arguments)
            if payload.get("error"):
                return GetPromptResult(
                    description="Prompt lookup failed.",
                    messages=[
                        PromptMessage(
                            role="user",
                            content=TextContent(
                                type=cs.MCP_CONTENT_TYPE_TEXT,
                                text=_json_dumps_pretty(payload),
                            ),
                        )
                    ],
                )

            messages = cast(list[dict[str, object]], payload.get("messages", []))
            prompt_messages: list[PromptMessage] = []
            for item in messages:
                if not isinstance(item, dict):
                    continue
                item_dict = item
                prompt_role: Literal["user", "assistant"] = (
                    "assistant"
                    if str(item_dict.get("role", "user")).strip().lower() == "assistant"
                    else "user"
                )
                prompt_messages.append(
                    PromptMessage(
                        role=prompt_role,
                        content=TextContent(
                            type=cs.MCP_CONTENT_TYPE_TEXT,
                            text=str(item_dict.get("text", "")),
                        ),
                    )
                )

            return GetPromptResult(
                description=str(payload.get("description", "")).strip() or None,
                messages=prompt_messages,
            )

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

    return server


def create_server() -> tuple[Server, MemgraphIngestor]:
    tools, ingestor = create_tools_runtime()
    return create_server_with_tools(tools), ingestor


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
