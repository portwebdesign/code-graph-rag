from __future__ import annotations

import asyncio
import difflib
import json
import os
import shlex
import shutil
import sys
import uuid
from collections import deque
from collections.abc import Coroutine
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from prompt_toolkit import prompt
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import print_formatted_text
from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolDenied
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from codebase_rag.ai.prompts import (
    OPTIMIZATION_PROMPT,
    OPTIMIZATION_PROMPT_WITH_REFERENCE,
)
from codebase_rag.data_models.models import AppContext
from codebase_rag.data_models.types_defs import (
    CHAT_LOOP_UI,
    OPTIMIZATION_LOOP_UI,
    ORANGE_STYLE,
    AgentLoopUI,
    CancelledResult,
    ConfirmationToolNames,
    CreateFileArgs,
    GraphData,
    RawToolArgs,
    ReplaceCodeArgs,
    ShellCommandArgs,
    ToolArgs,
)
from codebase_rag.providers.base import get_provider_from_config
from codebase_rag.services import QueryProtocol
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.services.llm import CypherGenerator, create_rag_orchestrator
from codebase_rag.tools.code_retrieval import CodeRetriever, create_code_retrieval_tool
from codebase_rag.tools.codebase_query import create_query_tool
from codebase_rag.tools.directory_lister import (
    DirectoryLister,
    create_directory_lister_tool,
)
from codebase_rag.tools.document_analyzer import (
    DocumentAnalyzer,
    create_document_analyzer_tool,
)
from codebase_rag.tools.file_editor import FileEditor, create_file_editor_tool
from codebase_rag.tools.file_reader import FileReader, create_file_reader_tool
from codebase_rag.tools.file_writer import FileWriter, create_file_writer_tool
from codebase_rag.tools.semantic_search import (
    create_get_function_source_tool,
    create_semantic_search_tool,
)
from codebase_rag.tools.shell_command import ShellCommander, create_shell_command_tool

from ..infrastructure import exceptions as ex
from . import constants as cs
from . import logs as ls
from .config import ModelConfig, load_cgrignore_patterns, settings

if TYPE_CHECKING:
    from prompt_toolkit.key_binding import KeyPressEvent
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model


def style(
    text: str, color: cs.Color, modifier: cs.StyleModifier = cs.StyleModifier.BOLD
) -> str:
    """Applies Rich styling to a text string.

    Args:
        text (str): The text to style.
        color (cs.Color): The color to apply.
        modifier (cs.StyleModifier): The style modifier (e.g., 'bold', 'dim').

    Returns:
        str: The Rich-formatted string.
    """
    if modifier == cs.StyleModifier.NONE:
        return f"[{color}]{text}[/{color}]"
    return f"[{modifier} {color}]{text}[/{modifier} {color}]"


def dim(text: str) -> str:
    """Applies a 'dim' style to the text.

    Args:
        text (str): The text to dim.

    Returns:
        str: The dimmed, Rich-formatted string.
    """
    return f"[{cs.StyleModifier.DIM}]{text}[/{cs.StyleModifier.DIM}]"


app_context = AppContext()


def init_session_log(project_root: Path) -> Path:
    """
    Initializes a log file for the current interactive session.

    Args:
        project_root (Path): The root directory of the project.

    Returns:
        Path: The path to the created log file.
    """
    log_dir = project_root / cs.TMP_DIR
    log_dir.mkdir(exist_ok=True)
    app_context.session.log_file = (
        log_dir / f"{cs.SESSION_LOG_PREFIX}{uuid.uuid4().hex[:8]}{cs.SESSION_LOG_EXT}"
    )
    with open(app_context.session.log_file, "w") as f:
        f.write(cs.SESSION_LOG_HEADER)
    return app_context.session.log_file


def log_session_event(event: str) -> None:
    """Writes an event to the session log file.

    Args:
        event (str): The event string to log.
    """
    if app_context.session.log_file:
        with open(app_context.session.log_file, "a") as f:
            f.write(f"{event}\n")


def get_session_context() -> str:
    """
    Retrieves the content of the session log to be used as context for the LLM.

    Returns:
        str: The formatted session context string, or an empty string if not available.
    """
    if app_context.session.log_file and app_context.session.log_file.exists():
        content = app_context.session.log_file.read_text(encoding="utf-8")
        return f"{cs.SESSION_CONTEXT_START}{content}{cs.SESSION_CONTEXT_END}"
    return ""


def _print_unified_diff(target: str, replacement: str, path: str) -> None:
    """Prints a unified diff of changes to the console.

    Args:
        target (str): The original text.
        replacement (str): The new text.
        path (str): The file path to display in the header.
    """
    separator = dim(cs.HORIZONTAL_SEPARATOR)
    app_context.console.print(f"\n{cs.UI_DIFF_FILE_HEADER.format(path=path)}")
    app_context.console.print(separator)

    diff = difflib.unified_diff(
        target.splitlines(keepends=True),
        replacement.splitlines(keepends=True),
        fromfile=cs.DIFF_LABEL_BEFORE,
        tofile=cs.DIFF_LABEL_AFTER,
        lineterm="",
    )

    for line in diff:
        line = line.rstrip("\n")
        match line[:1]:
            case cs.DiffMarker.ADD | cs.DiffMarker.DEL if line.startswith(
                cs.DiffMarker.HEADER_ADD
            ) or line.startswith(cs.DiffMarker.HEADER_DEL):
                app_context.console.print(dim(line))
            case cs.DiffMarker.HUNK:
                app_context.console.print(
                    style(line, cs.Color.CYAN, cs.StyleModifier.NONE)
                )
            case cs.DiffMarker.ADD:
                app_context.console.print(
                    style(line, cs.Color.GREEN, cs.StyleModifier.NONE)
                )
            case cs.DiffMarker.DEL:
                app_context.console.print(
                    style(line, cs.Color.RED, cs.StyleModifier.NONE)
                )
            case _:
                app_context.console.print(line)

    app_context.console.print(separator)


def _print_new_file_content(path: str, content: str) -> None:
    """Prints the content of a new file to be created.

    Args:
        path (str): The path of the new file.
        content (str): The content to be written to the file.
    """
    separator = dim(cs.HORIZONTAL_SEPARATOR)
    app_context.console.print(f"\n{cs.UI_NEW_FILE_HEADER.format(path=path)}")
    app_context.console.print(separator)

    for line in content.splitlines():
        app_context.console.print(
            style(f"{cs.DiffMarker.ADD} {line}", cs.Color.GREEN, cs.StyleModifier.NONE)
        )

    app_context.console.print(separator)


def _to_tool_args(
    tool_name: str, raw_args: RawToolArgs, tool_names: ConfirmationToolNames
) -> ToolArgs:
    """Converts raw tool arguments into a specific TypedDict for the tool.

    Args:
        tool_name (str): The name of the tool being called.
        raw_args (RawToolArgs): The raw arguments from the LLM.
        tool_names (ConfirmationToolNames): Standardized names for confirmation tools.

    Returns:
        ToolArgs: The strongly-typed arguments for the specific tool.
    """
    match tool_name:
        case tool_names.replace_code:
            return ReplaceCodeArgs(
                file_path=raw_args.file_path,
                target_code=raw_args.target_code,
                replacement_code=raw_args.replacement_code,
            )
        case tool_names.create_file:
            return CreateFileArgs(
                file_path=raw_args.file_path,
                content=raw_args.content,
            )
        case tool_names.shell_command:
            return ShellCommandArgs(command=raw_args.command)
        case _:
            return ShellCommandArgs()


def _display_tool_call_diff(
    tool_name: str,
    tool_args: ToolArgs,
    tool_names: ConfirmationToolNames,
    file_path: str | None = None,
) -> None:
    """Displays the proposed changes for a tool call that requires confirmation.

    Args:
        tool_name (str): The name of the tool.
        tool_args (ToolArgs): The arguments for the tool.
        tool_names (ConfirmationToolNames): Standardized names for confirmation tools.
        file_path (str | None): An optional fallback file path.
    """
    match tool_name:
        case tool_names.replace_code:
            target = str(tool_args.get(cs.ARG_TARGET_CODE, ""))
            replacement = str(tool_args.get(cs.ARG_REPLACEMENT_CODE, ""))
            path = str(
                tool_args.get(cs.ARG_FILE_PATH, file_path or cs.DIFF_FALLBACK_PATH)
            )
            _print_unified_diff(target, replacement, path)

        case tool_names.create_file:
            path = str(tool_args.get(cs.ARG_FILE_PATH, ""))
            content = str(tool_args.get(cs.ARG_CONTENT, ""))
            _print_new_file_content(path, content)

        case tool_names.shell_command:
            command = tool_args.get(cs.ARG_COMMAND, "")
            app_context.console.print(f"\n{cs.UI_SHELL_COMMAND_HEADER}")
            app_context.console.print(
                style(f"$ {command}", cs.Color.YELLOW, cs.StyleModifier.NONE)
            )

        case _:
            app_context.console.print(
                cs.UI_TOOL_ARGS_FORMAT.format(
                    args=json.dumps(tool_args, indent=cs.JSON_INDENT)
                )
            )


def _process_tool_approvals(
    requests: DeferredToolRequests,
    approval_prompt: str,
    denial_default: str,
    tool_names: ConfirmationToolNames,
) -> DeferredToolResults:
    """
    Handles the user approval workflow for deferred tool calls.

    It displays the proposed changes and prompts the user for confirmation.

    Args:
        requests (DeferredToolRequests): The tool requests requiring approval.
        approval_prompt (str): The message to show when asking for approval.
        denial_default (str): The default message to use if the user denies without feedback.
        tool_names (ConfirmationToolNames): Standardized names for confirmation tools.

    Returns:
        DeferredToolResults: The results of the approval process.
    """
    deferred_results = DeferredToolResults()

    for call in requests.approvals:
        tool_args = _to_tool_args(
            call.tool_name, RawToolArgs(**call.args_as_dict()), tool_names
        )
        app_context.console.print(
            f"\n{cs.UI_TOOL_APPROVAL.format(tool_name=call.tool_name)}"
        )
        _display_tool_call_diff(call.tool_name, tool_args, tool_names)

        if app_context.session.confirm_edits:
            if Confirm.ask(style(approval_prompt, cs.Color.CYAN)):
                deferred_results.approvals[call.tool_call_id] = True
            else:
                feedback = Prompt.ask(
                    cs.UI_FEEDBACK_PROMPT,
                    default="",
                )
                denial_msg = feedback.strip() or denial_default
                deferred_results.approvals[call.tool_call_id] = ToolDenied(denial_msg)
        else:
            deferred_results.approvals[call.tool_call_id] = True

    return deferred_results


def _setup_common_initialization(repo_path: str) -> Path:
    """Performs common setup tasks like logger and temp directory initialization.

    Args:
        repo_path (str): The path to the repository.

    Returns:
        Path: The resolved absolute path to the project root.
    """
    project_root = Path(repo_path).resolve()
    output_dir = project_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(sys.stdout, format=cs.LOG_FORMAT)
    logger.add(output_dir / "log.txt", format=cs.LOG_FORMAT, mode="w")
    tmp_dir = project_root / cs.TMP_DIR
    if tmp_dir.exists():
        if tmp_dir.is_dir():
            shutil.rmtree(tmp_dir)
        else:
            tmp_dir.unlink()
    tmp_dir.mkdir()

    return project_root


def _create_configuration_table(
    repo_path: str,
    title: str = cs.DEFAULT_TABLE_TITLE,
    language: str | None = None,
) -> Table:
    """Creates a Rich table to display the current application configuration.

    Args:
        repo_path (str): The path to the target repository.
        title (str): The title for the table.
        language (str | None): The target programming language, if applicable.

    Returns:
        Table: The configured Rich Table object.
    """
    table = Table(title=style(title, cs.Color.GREEN))
    table.add_column(cs.TABLE_COL_CONFIGURATION, style=cs.Color.CYAN)
    table.add_column(cs.TABLE_COL_VALUE, style=cs.Color.MAGENTA)

    if language:
        table.add_row(cs.TABLE_ROW_TARGET_LANGUAGE, language)

    orchestrator_config = settings.active_orchestrator_config
    table.add_row(
        cs.TABLE_ROW_ORCHESTRATOR_MODEL,
        f"{orchestrator_config.model_id} ({orchestrator_config.provider})",
    )

    cypher_config = settings.active_cypher_config
    table.add_row(
        cs.TABLE_ROW_CYPHER_MODEL,
        f"{cypher_config.model_id} ({cypher_config.provider})",
    )

    orch_endpoint = (
        orchestrator_config.endpoint
        if orchestrator_config.provider == cs.Provider.OLLAMA
        else None
    )
    cypher_endpoint = (
        cypher_config.endpoint if cypher_config.provider == cs.Provider.OLLAMA else None
    )

    if orch_endpoint and cypher_endpoint and orch_endpoint == cypher_endpoint:
        table.add_row(cs.TABLE_ROW_OLLAMA_ENDPOINT, orch_endpoint)
    else:
        if orch_endpoint:
            table.add_row(cs.TABLE_ROW_OLLAMA_ORCHESTRATOR, orch_endpoint)
        if cypher_endpoint:
            table.add_row(cs.TABLE_ROW_OLLAMA_CYPHER, cypher_endpoint)

    confirmation_status = (
        cs.CONFIRM_ENABLED if app_context.session.confirm_edits else cs.CONFIRM_DISABLED
    )
    table.add_row(cs.TABLE_ROW_EDIT_CONFIRMATION, confirmation_status)
    table.add_row(cs.TABLE_ROW_TARGET_REPOSITORY, repo_path)

    return table


async def run_optimization_loop(
    rag_agent: Agent[None, str | DeferredToolRequests],
    message_history: list[ModelMessage],
    project_root: Path,
    language: str,
    tool_names: ConfirmationToolNames,
    reference_document: str | None = None,
) -> None:
    """
    Runs the interactive loop specifically for the code optimization task.

    Args:
        rag_agent (Agent): The configured agent.
        message_history (list[ModelMessage]): The list of messages in the current conversation.
        project_root (Path): The root path of the project being optimized.
        language (str): The programming language of the project.
        tool_names (ConfirmationToolNames): Standardized names for confirmation tools.
        reference_document (str | None): An optional path to a document with optimization guidelines.
    """
    app_context.console.print(cs.UI_OPTIMIZATION_START.format(language=language))
    document_info = (
        cs.UI_REFERENCE_DOC_INFO.format(reference_document=reference_document)
        if reference_document
        else ""
    )
    app_context.console.print(
        Panel(
            cs.UI_OPTIMIZATION_PANEL.format(document_info=document_info),
            border_style=cs.Color.YELLOW,
        )
    )

    initial_question = (
        OPTIMIZATION_PROMPT_WITH_REFERENCE.format(
            language=language, reference_document=reference_document
        )
        if reference_document
        else OPTIMIZATION_PROMPT.format(language=language)
    )

    await _run_interactive_loop(
        rag_agent,
        message_history,
        project_root,
        OPTIMIZATION_LOOP_UI,
        style(cs.PROMPT_YOUR_RESPONSE, cs.Color.CYAN),
        tool_names,
        initial_question,
    )


async def run_with_cancellation[T](
    coro: Coroutine[None, None, T], timeout: float | None = None
) -> T | CancelledResult:
    """
    Runs a coroutine with support for cancellation via KeyboardInterrupt or timeout.

    Args:
        coro (Coroutine): The coroutine to run.
        timeout (float | None): An optional timeout in seconds.

    Returns:
        T | CancelledResult: The result of the coroutine, or a `CancelledResult` if it was cancelled.
    """
    task = asyncio.create_task(coro)

    try:
        return await asyncio.wait_for(task, timeout=timeout) if timeout else await task
    except TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        app_context.console.print(
            f"\n{style(cs.MSG_TIMEOUT_FORMAT.format(timeout=timeout), cs.Color.YELLOW)}"
        )
        return CancelledResult(cancelled=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        app_context.console.print(
            f"\n{style(cs.MSG_THINKING_CANCELLED, cs.Color.YELLOW)}"
        )
        return CancelledResult(cancelled=True)


async def _run_agent_response_loop(
    rag_agent: Agent[None, str | DeferredToolRequests],
    message_history: list[ModelMessage],
    question_with_context: str,
    config: AgentLoopUI,
    tool_names: ConfirmationToolNames,
    model_override: Model | None = None,
) -> None:
    """
    Manages the agent's response generation, including tool calls and approvals.

    This loop continues until the agent produces a final text response instead of
    requesting another tool call.

    Args:
        rag_agent (Agent): The configured agent.
        message_history (list[ModelMessage]): The current conversation history.
        question_with_context (str): The user's question, potentially with session context.
        config (AgentLoopUI): The UI configuration for the current loop.
        tool_names (ConfirmationToolNames): Standardized names for confirmation tools.
        model_override (Model | None): An optional override for the LLM.
    """
    deferred_results: DeferredToolResults | None = None

    while True:
        with app_context.console.status(config.status_message):
            response = await run_with_cancellation(
                rag_agent.run(
                    question_with_context,
                    message_history=message_history,
                    deferred_tool_results=deferred_results,
                    model=model_override,
                ),
            )

        if isinstance(response, CancelledResult):
            log_session_event(config.cancelled_log)
            app_context.session.cancelled = True
            break

        if isinstance(response.output, DeferredToolRequests):
            deferred_results = _process_tool_approvals(
                response.output,
                config.approval_prompt,
                config.denial_default,
                tool_names,
            )
            message_history.extend(response.new_messages())
            continue

        output_text = response.output
        if not isinstance(output_text, str):
            continue
        markdown_response = Markdown(output_text)
        app_context.console.print(
            Panel(
                markdown_response,
                title=config.panel_title,
                border_style=cs.Color.GREEN,
            )
        )

        log_session_event(f"{cs.SESSION_PREFIX_ASSISTANT}{output_text}")
        message_history.extend(response.new_messages())
        break


def _find_image_paths(question: str) -> list[Path]:
    """Finds potential image file paths within a user's question string.

    Args:
        question (str): The user's input string.

    Returns:
        list[Path]: A list of `Path` objects for found image files.
    """
    try:
        if os.name == "nt":
            # (H) On Windows, shlex.split with posix=False to preserve backslashes
            tokens = shlex.split(question, posix=False)
        else:
            tokens = shlex.split(question)
    except ValueError:
        tokens = question.split()

    image_paths: list[Path] = []
    for token in tokens:
        # (H) Strip quotes if they remain (shlex with posix=False might keep some)
        token = token.strip("'\"")
        # (H) Check if it looks like an image path
        if token.lower().endswith(cs.IMAGE_EXTENSIONS):
            # (H) On Windows, could be C:\... or \...
            # (H) On POSIX, starts with /
            p = Path(token)
            if p.is_absolute() or token.startswith("/") or token.startswith("\\"):
                image_paths.append(p)
    return image_paths


def _get_path_variants(path_str: str) -> tuple[str, ...]:
    """Generates different quoted and escaped variants of a path string.

    Args:
        path_str (str): The path string.

    Returns:
        tuple[str, ...]: A tuple of path variants.
    """
    return (
        path_str.replace(" ", r"\ "),
        f"'{path_str}'",
        f'"{path_str}"',
        path_str,
    )


def _replace_path_in_question(question: str, old_path: str, new_path: str) -> str:
    """Replaces a path in the user's question with a new path.

    Args:
        question (str): The original question string.
        old_path (str): The path string to replace.
        new_path (str): The new path string.

    Returns:
        str: The updated question string.
    """
    for variant in _get_path_variants(old_path):
        if variant in question:
            return question.replace(variant, new_path)
    logger.warning(ls.PATH_NOT_IN_QUESTION.format(path=old_path))
    return question


def _handle_chat_images(question: str, project_root: Path) -> str:
    """
    Handles image paths found in a user's question for multimodal chat.

    It copies the images to a temporary directory within the project and updates
    the paths in the question to point to the new locations.

    Args:
        question (str): The user's input string.
        project_root (Path): The root of the current project.

    Returns:
        str: The question with image paths updated to temporary locations.
    """
    image_files = _find_image_paths(question)
    if not image_files:
        return question

    tmp_dir = project_root / cs.TMP_DIR
    tmp_dir.mkdir(exist_ok=True)
    updated_question = question

    for original_path in image_files:
        if not original_path.exists() or not original_path.is_file():
            logger.warning(ls.IMAGE_NOT_FOUND.format(path=original_path))
            continue

        try:
            new_path = tmp_dir / f"{uuid.uuid4()}-{original_path.name}"
            shutil.copy(original_path, new_path)
            new_relative = str(new_path.relative_to(project_root))
            updated_question = _replace_path_in_question(
                updated_question, str(original_path), new_relative
            )
            logger.info(ls.IMAGE_COPIED.format(path=new_relative))
        except Exception as e:
            logger.error(ls.IMAGE_COPY_FAILED.format(error=e))

    return updated_question


def get_multiline_input(prompt_text: str = cs.PROMPT_ASK_QUESTION) -> str:
    """
    Prompts the user for multiline input using prompt-toolkit.

    Args:
        prompt_text (str): The text to display for the prompt.

    Returns:
        str: The user's input, stripped of leading/trailing whitespace.
    """
    bindings = KeyBindings()

    @bindings.add(cs.KeyBinding.CTRL_J)
    def submit(event: KeyPressEvent) -> None:
        event.app.exit(result=event.app.current_buffer.text)

    @bindings.add(cs.KeyBinding.ENTER)
    def new_line(event: KeyPressEvent) -> None:
        event.current_buffer.insert_text("\n")

    @bindings.add(cs.KeyBinding.CTRL_C)
    def keyboard_interrupt(event: KeyPressEvent) -> None:
        event.app.exit(exception=KeyboardInterrupt)

    clean_prompt = Text.from_markup(prompt_text).plain

    print_formatted_text(
        HTML(
            cs.UI_INPUT_PROMPT_HTML.format(
                prompt=clean_prompt, hint=cs.MULTILINE_INPUT_HINT
            )
        )
    )

    result = prompt(
        "",
        multiline=True,
        key_bindings=bindings,
        wrap_lines=True,
        style=ORANGE_STYLE,
    )
    if result is None:
        raise EOFError
    stripped: str = result.strip()
    return stripped


def _create_model_from_string(
    model_string: str, current_override_config: ModelConfig | None = None
) -> tuple[Model, str, ModelConfig]:
    """Creates a Pydantic-AI Model object from a 'provider:model_id' string.

    Args:
        model_string (str): The model identifier string (e.g., 'ollama:llama3').
        current_override_config (ModelConfig | None): The current override config, if any.

    Returns:
        tuple[Model, str, ModelConfig]: A tuple of the new model instance, its
                                        canonical string, and its configuration.
    """
    try:
        from codebase_rag import main as main_module

        settings_ref = getattr(main_module, "settings", settings)
        provider_factory = getattr(
            main_module, "get_provider_from_config", get_provider_from_config
        )
    except Exception:
        settings_ref = settings
        provider_factory = get_provider_from_config

    base_config = current_override_config or settings_ref.active_orchestrator_config

    if cs.CHAR_COLON not in model_string:
        raise ValueError(ex.MODEL_FORMAT_INVALID)
    provider_name, model_id = (
        p.strip() for p in settings_ref.parse_model_string(model_string)
    )
    if not model_id:
        raise ValueError(ex.MODEL_ID_EMPTY)
    if not provider_name:
        raise ValueError(ex.PROVIDER_EMPTY)

    if provider_name == base_config.provider:
        config = replace(base_config, model_id=model_id)
    elif provider_name == cs.Provider.OLLAMA:
        config = ModelConfig(
            provider=provider_name,
            model_id=model_id,
            endpoint=settings_ref.ollama_endpoint,
            api_key=cs.DEFAULT_API_KEY,
        )
    else:
        config = ModelConfig(provider=provider_name, model_id=model_id)

    canonical_string = f"{provider_name}{cs.CHAR_COLON}{model_id}"
    provider = provider_factory(config)
    return provider.create_model(model_id), canonical_string, config


def _handle_model_command(
    command: str,
    current_model: Model | None,
    current_model_string: str | None,
    current_config: ModelConfig | None,
) -> tuple[Model | None, str | None, ModelConfig | None]:
    """Handles the '/model' command to view or switch the LLM at runtime.

    Args:
        command (str): The full command string from the user.
        current_model (Model | None): The current overridden model instance.
        current_model_string (str | None): The string identifier for the current override.
        current_config (ModelConfig | None): The config for the current override.

    Returns:
        tuple[Model | None, str | None, ModelConfig | None]: The updated model,
            string, and config.
    """
    try:
        from codebase_rag import main as main_module

        ctx = getattr(main_module, "app_context", app_context)
        settings_ref = getattr(main_module, "settings", settings)
        create_model = getattr(
            main_module, "_create_model_from_string", _create_model_from_string
        )
        logger_ref = getattr(main_module, "logger", logger)
    except Exception:
        ctx = app_context
        settings_ref = settings
        create_model = _create_model_from_string
        logger_ref = logger
    parts = command.strip().split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else None

    if not arg:
        if current_model_string:
            display_model = current_model_string
        else:
            config = settings_ref.active_orchestrator_config
            display_model = f"{config.provider}{cs.CHAR_COLON}{config.model_id}"
        ctx.console.print(cs.UI_MODEL_CURRENT.format(model=display_model))
        return current_model, current_model_string, current_config

    if arg.lower() == cs.HELP_ARG:
        ctx.console.print(cs.UI_MODEL_USAGE)
        return current_model, current_model_string, current_config

    try:
        new_model, canonical_model_string, new_config = create_model(
            arg, current_config
        )
        logger_ref.info(ls.MODEL_SWITCHED.format(model=canonical_model_string))
        ctx.console.print(cs.UI_MODEL_SWITCHED.format(model=canonical_model_string))
        return new_model, canonical_model_string, new_config
    except (ValueError, AssertionError) as e:
        logger_ref.error(ls.MODEL_SWITCH_FAILED.format(error=e))
        ctx.console.print(cs.UI_MODEL_SWITCH_ERROR.format(error=e))
        return current_model, current_model_string, current_config


async def _run_interactive_loop(
    rag_agent: Agent[None, str | DeferredToolRequests],
    message_history: list[ModelMessage],
    project_root: Path,
    config: AgentLoopUI,
    input_prompt: str,
    tool_names: ConfirmationToolNames,
    initial_question: str | None = None,
) -> None:
    """The main interactive REPL for chatting with the agent.

    Args:
        rag_agent (Agent): The configured agent.
        message_history (list[ModelMessage]): The current conversation history.
        project_root (Path): The root of the current project.
        config (AgentLoopUI): The UI configuration for the loop.
        input_prompt (str): The text to display when asking for user input.
        tool_names (ConfirmationToolNames): Standardized names for confirmation tools.
        initial_question (str | None): An optional initial question to start the loop with.
    """
    init_session_log(project_root)
    question = initial_question or ""
    model_override: Model | None = None
    model_override_string: str | None = None
    model_override_config: ModelConfig | None = None

    while True:
        try:
            if not initial_question or question != initial_question:
                question = await asyncio.to_thread(get_multiline_input, input_prompt)

            stripped_question = question.strip()
            stripped_lower = stripped_question.lower()

            if stripped_lower in cs.EXIT_COMMANDS:
                break

            if not stripped_question:
                initial_question = None
                continue

            command_parts = stripped_lower.split(maxsplit=1)
            if command_parts[0] == cs.MODEL_COMMAND_PREFIX:
                model_override, model_override_string, model_override_config = (
                    _handle_model_command(
                        stripped_question,
                        model_override,
                        model_override_string,
                        model_override_config,
                    )
                )
                initial_question = None
                continue
            if command_parts[0] == cs.HELP_COMMAND:
                app_context.console.print(cs.UI_HELP_COMMANDS)
                initial_question = None
                continue

            log_session_event(f"{cs.SESSION_PREFIX_USER}{question}")

            if app_context.session.cancelled:
                question_with_context = question + get_session_context()
                app_context.session.reset_cancelled()
            else:
                question_with_context = question

            question_with_context = _handle_chat_images(
                question_with_context, project_root
            )

            await _run_agent_response_loop(
                rag_agent,
                message_history,
                question_with_context,
                config,
                tool_names,
                model_override,
            )

            initial_question = None

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.exception(ls.UNEXPECTED.format(error=e))
            app_context.console.print(cs.UI_ERR_UNEXPECTED.format(error=e))


async def run_chat_loop(
    rag_agent: Agent[None, str | DeferredToolRequests],
    message_history: list[ModelMessage],
    project_root: Path,
    tool_names: ConfirmationToolNames,
) -> None:
    """
    Initializes and runs the general-purpose chat loop.

    Args:
        rag_agent (Agent): The configured agent.
        message_history (list[ModelMessage]): The list of messages in the current conversation.
        project_root (Path): The root path of the project.
        tool_names (ConfirmationToolNames): Standardized names for confirmation tools.
    """
    await _run_interactive_loop(
        rag_agent,
        message_history,
        project_root,
        CHAT_LOOP_UI,
        style(cs.PROMPT_ASK_QUESTION, cs.Color.CYAN),
        tool_names,
    )


def _update_single_model_setting(role: cs.ModelRole, model_string: str) -> None:
    """Updates the configuration for a single model role.

    Args:
        role (cs.ModelRole): The role to update (e.g., ORCHESTRATOR).
        model_string (str): The model identifier string (e.g., 'ollama:llama3').
    """
    provider, model = settings.parse_model_string(model_string)

    match role:
        case cs.ModelRole.ORCHESTRATOR:
            current_config = settings.active_orchestrator_config
            set_method = settings.set_orchestrator
        case cs.ModelRole.CYPHER:
            current_config = settings.active_cypher_config
            set_method = settings.set_cypher

    kwargs = current_config.to_update_kwargs()

    if provider == cs.Provider.OLLAMA and not kwargs[cs.FIELD_ENDPOINT]:
        kwargs[cs.FIELD_ENDPOINT] = settings.ollama_endpoint
        kwargs[cs.FIELD_API_KEY] = cs.DEFAULT_API_KEY

    set_method(provider, model, **kwargs)


def update_model_settings(
    orchestrator: str | None,
    cypher: str | None,
) -> None:
    """
    Updates the application's model settings based on CLI arguments.

    Args:
        orchestrator (str | None): The model string for the orchestrator.
        cypher (str | None): The model string for the Cypher generator.
    """
    if orchestrator:
        _update_single_model_setting(cs.ModelRole.ORCHESTRATOR, orchestrator)
    if cypher:
        _update_single_model_setting(cs.ModelRole.CYPHER, cypher)


def _write_graph_json(ingestor: MemgraphIngestor, output_path: Path) -> GraphData:
    """Exports the graph to a JSON file.

    Args:
        ingestor (MemgraphIngestor): The ingestor connected to the database.
        output_path (Path): The path to write the JSON file to.

    Returns:
        GraphData: The exported graph data.
    """
    graph_data: GraphData = ingestor.export_graph_to_dict()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding=cs.ENCODING_UTF8) as f:
        json.dump(graph_data, f, indent=cs.JSON_INDENT, ensure_ascii=False)

    return graph_data


def connect_memgraph(batch_size: int) -> MemgraphIngestor:
    """
    Establishes a connection to the Memgraph database.

    Args:
        batch_size (int): The batch size for database ingestion.

    Returns:
        MemgraphIngestor: An initialized ingestor instance.
    """
    return MemgraphIngestor(
        host=settings.MEMGRAPH_HOST,
        port=settings.MEMGRAPH_PORT,
        batch_size=batch_size,
    )


def export_graph_to_file(ingestor: MemgraphIngestor, output: str) -> bool:
    """
    Exports the entire graph from Memgraph to a specified JSON file.

    Args:
        ingestor (MemgraphIngestor): The Memgraph ingestor instance.
        output (str): The path to the output JSON file.

    Returns:
        bool: True if the export was successful, False otherwise.
    """
    output_path = Path(output)

    try:
        graph_data = _write_graph_json(ingestor, output_path)
        metadata = graph_data[cs.KEY_METADATA]
        app_context.console.print(
            cs.UI_GRAPH_EXPORT_SUCCESS.format(path=output_path.absolute())
        )
        app_context.console.print(
            cs.UI_GRAPH_EXPORT_STATS.format(
                nodes=metadata[cs.KEY_TOTAL_NODES],
                relationships=metadata[cs.KEY_TOTAL_RELATIONSHIPS],
            )
        )
        return True

    except Exception as e:
        app_context.console.print(cs.UI_ERR_EXPORT_FAILED.format(error=e))
        logger.exception(ls.EXPORT_ERROR.format(error=e))
        return False


def detect_excludable_directories(repo_path: Path) -> set[str]:
    """
    Scans the repository to detect common directories that are often excluded.

    Args:
        repo_path (Path): The root path of the repository to scan.

    Returns:
        set[str]: A set of relative paths of directories to consider for exclusion.
    """
    detected: set[str] = set()
    queue: deque[tuple[Path, int]] = deque([(repo_path, 0)])
    while queue:
        current, depth = queue.popleft()
        if depth > cs.INTERACTIVE_BFS_MAX_DEPTH:
            continue
        try:
            entries = list(current.iterdir())
        except PermissionError:
            continue
        for path in entries:
            if not path.is_dir():
                continue
            if path.name in cs.IGNORE_PATTERNS:
                detected.add(path.relative_to(repo_path).as_posix())
            else:
                queue.append((path, depth + 1))
    return detected


def _get_grouping_key(path: str) -> str:
    """Determines the grouping key for a path based on common excludable patterns.

    Args:
        path (str): The path to group.

    Returns:
        str: The determined group key.
    """
    parts = Path(path).parts
    if not parts:
        return cs.INTERACTIVE_DEFAULT_GROUP
    for part in parts:
        if part in cs.IGNORE_PATTERNS:
            return part
    return parts[0]


def _group_paths_by_pattern(paths: set[str]) -> dict[str, list[str]]:
    """Groups a set of paths by their determined grouping key.

    Args:
        paths (set[str]): A set of paths to group.

    Returns:
        dict[str, list[str]]: A dictionary mapping group keys to lists of paths.
    """
    groups: dict[str, list[str]] = {}
    for path in paths:
        key = _get_grouping_key(path)
        if key not in groups:
            groups[key] = []
        groups[key].append(path)
    for group_paths in groups.values():
        group_paths.sort()
    return groups


def _format_nested_count(count: int) -> str:
    """Formats the count of nested paths for the interactive UI.

    Args:
        count (int): The number of nested paths.

    Returns:
        str: A formatted string (e.g., "1 dir", "5 dirs").
    """
    template = (
        cs.INTERACTIVE_NESTED_SINGULAR if count == 1 else cs.INTERACTIVE_NESTED_PLURAL
    )
    return template.format(count=count)


def _display_grouped_table(groups: dict[str, list[str]]) -> list[str]:
    """Displays the top-level groups of excludable directories.

    Args:
        groups (dict[str, list[str]]): The grouped directories.

    Returns:
        list[str]: A sorted list of the group root names.
    """
    sorted_roots = sorted(groups.keys())
    table = Table(title=style(cs.INTERACTIVE_TITLE_GROUPED, cs.Color.CYAN))
    table.add_column(cs.INTERACTIVE_COL_NUM, style=cs.Color.YELLOW, width=4)
    table.add_column(cs.INTERACTIVE_COL_PATTERN)
    table.add_column(cs.INTERACTIVE_COL_NESTED, style=cs.INTERACTIVE_STYLE_DIM)

    for i, root in enumerate(sorted_roots, 1):
        nested_count = len(groups[root])
        table.add_row(str(i), root, _format_nested_count(nested_count))

    app_context.console.print(table)
    app_context.console.print(
        style(
            cs.INTERACTIVE_INSTRUCTIONS_GROUPED, cs.Color.YELLOW, cs.StyleModifier.NONE
        )
    )
    return sorted_roots


def _display_nested_table(pattern: str, paths: list[str]) -> None:
    """Displays the nested paths within a selected group.

    Args:
        pattern (str): The name of the parent group.
        paths (list[str]): The list of nested paths to display.
    """
    title = cs.INTERACTIVE_TITLE_NESTED.format(pattern=pattern)
    table = Table(title=style(title, cs.Color.CYAN))
    table.add_column(cs.INTERACTIVE_COL_NUM, style=cs.Color.YELLOW, width=4)
    table.add_column(cs.INTERACTIVE_COL_PATH)

    for i, path in enumerate(paths, 1):
        table.add_row(str(i), path)

    app_context.console.print(table)
    app_context.console.print(
        style(
            cs.INTERACTIVE_INSTRUCTIONS_NESTED.format(pattern=pattern),
            cs.Color.YELLOW,
            cs.StyleModifier.NONE,
        )
    )


def _prompt_nested_selection(pattern: str, paths: list[str]) -> set[str]:
    """Prompts the user to select which nested paths to keep (i.e., not exclude).

    Args:
        pattern (str): The name of the parent group.
        paths (list[str]): The list of nested paths to choose from.

    Returns:
        set[str]: A set of paths the user chose to keep.
    """
    _display_nested_table(pattern, paths)

    response = Prompt.ask(
        style(cs.INTERACTIVE_PROMPT_KEEP, cs.Color.CYAN),
        default=cs.INTERACTIVE_KEEP_NONE,
    )

    if response.lower() == cs.INTERACTIVE_KEEP_ALL:
        return set(paths)
    if response.lower() == cs.INTERACTIVE_KEEP_NONE:
        return set()

    selected: set[str] = set()
    for part in response.split(","):
        part = part.strip()
        if not part:
            continue
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(paths):
                selected.add(paths[idx])
            else:
                logger.warning(ls.EXCLUDE_INVALID_INDEX.format(index=part))
        else:
            logger.warning(ls.EXCLUDE_INVALID_INPUT.format(input=part))

    return selected


def prompt_for_unignored_directories(
    repo_path: Path,
    cli_excludes: list[str] | None = None,
) -> frozenset[str]:
    """
    Interactively prompts the user to select which commonly excluded directories
    they wish to include in the analysis.

    Args:
        repo_path (Path): The root path of the repository.
        cli_excludes (list[str] | None): A list of exclusion patterns provided via the CLI.

    Returns:
        frozenset[str]: A frozenset of paths that the user has chosen to *unignore* (include).
    """
    detected = detect_excludable_directories(repo_path)
    cgrignore = load_cgrignore_patterns(repo_path)
    cli_patterns = frozenset(cli_excludes) if cli_excludes else frozenset()
    pre_excluded = cli_patterns | cgrignore.exclude

    if not detected and not pre_excluded:
        return cgrignore.unignore

    all_candidates = detected | pre_excluded
    groups = _group_paths_by_pattern(all_candidates)
    sorted_roots = _display_grouped_table(groups)

    response = Prompt.ask(
        style(cs.INTERACTIVE_PROMPT_KEEP, cs.Color.CYAN),
        default=cs.INTERACTIVE_KEEP_NONE,
    )

    if response.lower() == cs.INTERACTIVE_KEEP_ALL:
        return frozenset(all_candidates) | cgrignore.unignore

    if response.lower() == cs.INTERACTIVE_KEEP_NONE:
        return cgrignore.unignore

    selected: set[str] = set()
    expand_requests: list[int] = []
    regular_selections: list[int] = []

    for part in response.split(","):
        part = part.strip().lower()
        if not part:
            continue

        if part.endswith(cs.INTERACTIVE_EXPAND_SUFFIX) and part[:-1].isdigit():
            expand_requests.append(int(part[:-1]) - 1)
        elif part.isdigit():
            regular_selections.append(int(part) - 1)
        else:
            logger.warning(ls.EXCLUDE_INVALID_INPUT.format(input=part))

    for idx in expand_requests:
        if 0 <= idx < len(sorted_roots):
            root = sorted_roots[idx]
            nested_selected = _prompt_nested_selection(root, groups[root])
            selected.update(nested_selected)
        else:
            logger.warning(ls.EXCLUDE_INVALID_INDEX.format(index=idx + 1))

    for idx in regular_selections:
        if 0 <= idx < len(sorted_roots):
            root = sorted_roots[idx]
            selected.update(groups[root])
        else:
            logger.warning(ls.EXCLUDE_INVALID_INDEX.format(index=idx + 1))

    return frozenset(selected) | cgrignore.unignore


def _validate_provider_config(role: cs.ModelRole, config: ModelConfig) -> None:
    """Validates the configuration for a given model provider.

    Args:
        role (cs.ModelRole): The role of the model (e.g., Orchestrator).
        config (ModelConfig): The configuration for the model.

    Raises:
        ValueError: If the configuration is invalid.
    """
    from codebase_rag.providers.base import get_provider_from_config

    try:
        provider = get_provider_from_config(config)
        provider.validate_config()
    except Exception as e:
        raise ValueError(ex.CONFIG.format(role=role.value.title(), error=e)) from e


def _initialize_services_and_agent(
    repo_path: str, ingestor: QueryProtocol
) -> tuple[Agent[None, str | DeferredToolRequests], ConfirmationToolNames]:
    """
    Initializes all services, tools, and the main agent for an interactive session.

    Args:
        repo_path (str): The path to the target repository.
        ingestor (QueryProtocol): The database ingestor/query service.

    Returns:
        tuple: A tuple containing the initialized agent and the names of confirmation tools.
    """
    _validate_provider_config(
        cs.ModelRole.ORCHESTRATOR, settings.active_orchestrator_config
    )
    _validate_provider_config(cs.ModelRole.CYPHER, settings.active_cypher_config)

    cypher_generator = CypherGenerator()
    code_retriever = CodeRetriever(project_root=repo_path, ingestor=ingestor)
    file_reader = FileReader(project_root=repo_path)
    file_writer = FileWriter(project_root=repo_path)
    file_editor = FileEditor(project_root=repo_path)
    shell_commander = ShellCommander(
        project_root=repo_path, timeout=settings.SHELL_COMMAND_TIMEOUT
    )
    directory_lister = DirectoryLister(project_root=repo_path)
    document_analyzer = DocumentAnalyzer(project_root=repo_path)

    query_tool = create_query_tool(ingestor, cypher_generator, app_context.console)
    code_tool = create_code_retrieval_tool(code_retriever)
    file_reader_tool = create_file_reader_tool(file_reader)
    file_writer_tool = create_file_writer_tool(file_writer)
    file_editor_tool = create_file_editor_tool(file_editor)
    shell_command_tool = create_shell_command_tool(shell_commander)
    directory_lister_tool = create_directory_lister_tool(directory_lister)
    document_analyzer_tool = create_document_analyzer_tool(document_analyzer)
    semantic_search_tool = create_semantic_search_tool()
    function_source_tool = create_get_function_source_tool()

    confirmation_tool_names = ConfirmationToolNames(
        replace_code=file_editor_tool.name,
        create_file=file_writer_tool.name,
        shell_command=shell_command_tool.name,
    )

    rag_agent = create_rag_orchestrator(
        tools=[
            query_tool,
            code_tool,
            file_reader_tool,
            file_writer_tool,
            file_editor_tool,
            shell_command_tool,
            directory_lister_tool,
            document_analyzer_tool,
            semantic_search_tool,
            function_source_tool,
        ]
    )
    return rag_agent, confirmation_tool_names


async def main_async(repo_path: str, batch_size: int) -> None:
    """
    The main asynchronous entry point for the interactive chat mode.

    Args:
        repo_path (str): The path to the target repository.
        batch_size (int): The batch size for database ingestion.
    """
    project_root = _setup_common_initialization(repo_path)

    table = _create_configuration_table(repo_path)
    app_context.console.print(table)

    with connect_memgraph(batch_size) as ingestor:
        app_context.console.print(style(cs.MSG_CONNECTED_MEMGRAPH, cs.Color.GREEN))
        app_context.console.print(
            Panel(
                style(cs.MSG_CHAT_INSTRUCTIONS, cs.Color.YELLOW),
                border_style=cs.Color.YELLOW,
            )
        )

        rag_agent, tool_names = _initialize_services_and_agent(repo_path, ingestor)
        await run_chat_loop(rag_agent, [], project_root, tool_names)


async def main_optimize_async(
    language: str,
    target_repo_path: str,
    reference_document: str | None = None,
    orchestrator: str | None = None,
    cypher: str | None = None,
    batch_size: int | None = None,
) -> None:
    """
    The main asynchronous entry point for the code optimization mode.

    Args:
        language (str): The programming language of the codebase.
        target_repo_path (str): The path to the target repository.
        reference_document (str | None): Optional path to a reference document for guidance.
        orchestrator (str | None): Optional override for the orchestrator model.
        cypher (str | None): Optional override for the Cypher generation model.
        batch_size (int | None): Optional override for the database batch size.
    """
    project_root = _setup_common_initialization(target_repo_path)

    update_model_settings(orchestrator, cypher)

    app_context.console.print(
        cs.UI_OPTIMIZATION_INIT.format(language=language, path=project_root)
    )

    table = _create_configuration_table(
        str(project_root), cs.OPTIMIZATION_TABLE_TITLE, language
    )
    app_context.console.print(table)

    effective_batch_size = settings.resolve_batch_size(batch_size)

    with connect_memgraph(effective_batch_size) as ingestor:
        app_context.console.print(style(cs.MSG_CONNECTED_MEMGRAPH, cs.Color.GREEN))

        rag_agent, tool_names = _initialize_services_and_agent(
            target_repo_path, ingestor
        )
        await run_optimization_loop(
            rag_agent, [], project_root, language, tool_names, reference_document
        )
