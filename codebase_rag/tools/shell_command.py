"""
This module defines the `ShellCommander` class and a factory function for
creating a `pydantic-ai` tool that allows an LLM agent to execute shell commands.

Security is a primary concern. The tool implements a multi-layered defense:
-   **Allowlist**: Only commands from a predefined safe list can be executed.
-   **Blocklist**: A list of inherently dangerous commands are always blocked.
-   **Pattern Matching**: Regular expressions are used to detect dangerous
    command patterns, such as `rm -rf /`, subshell execution, and remote
    script execution via `curl` or `wget`.
-   **Approval Mechanism**: Commands that are not read-only or are potentially
    destructive (like `git push` or `rm`) require user approval before execution.
-   **Path Validation**: `rm` commands are checked to ensure they do not target
    system directories or paths outside the project root.

The module also parses complex commands involving pipes (`|`) and logical
operators (`&&`, `||`) into groups for sequential execution.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import sys
import time
from pathlib import Path

from loguru import logger
from pydantic_ai import ApprovalRequired, RunContext, Tool

from codebase_rag.core.config import settings
from codebase_rag.data_models.schemas import ShellCommandResult
from codebase_rag.infrastructure.decorators import async_timing_decorator

from ..core import constants as cs
from ..core import logs as ls
from ..infrastructure import tool_errors as te
from . import tool_descriptions as td

PIPELINE_PATTERNS_COMPILED = tuple(
    (re.compile(pattern, re.IGNORECASE), reason)
    for pattern, reason in cs.SHELL_DANGEROUS_PATTERNS_PIPELINE
)
SEGMENT_PATTERNS_COMPILED = tuple(
    (re.compile(pattern, re.IGNORECASE), reason)
    for pattern, reason in cs.SHELL_DANGEROUS_PATTERNS_SEGMENT
)


def _is_outside_single_quotes(command: str, pos: int) -> bool:
    """Checks if a position in a command string is outside of single quotes."""
    in_single = False
    i = 0
    while i < pos:
        char = command[i]
        if char == "\\" and not in_single and i + 1 < len(command):
            i += 2
            continue
        if char == "'":
            in_single = not in_single
        i += 1
    return not in_single


def _has_subshell(command: str) -> str | None:
    """
    Checks if a command string contains subshell execution patterns.

    Args:
        command (str): The command string to check.

    Returns:
        str | None: The detected subshell pattern, or None if not found.
    """
    for pattern in cs.SHELL_SUBSHELL_PATTERNS:
        start = 0
        while True:
            pos = command.find(pattern, start)
            if pos == -1:
                break
            if _is_outside_single_quotes(command, pos):
                return pattern
            start = pos + 1
    return None


class CommandGroup:
    """Represents a group of commands linked by a logical operator."""

    def __init__(self, commands: list[str], operator: str | None = None):
        """
        Initializes a CommandGroup.

        Args:
            commands (list[str]): A list of command segments in the group.
            operator (str | None): The logical operator (`&&`, `||`, `;`) that
                                   precedes this group.
        """
        self.commands = commands
        self.operator = operator


def _parse_command(command: str) -> list[CommandGroup]:
    """
    Parses a complex shell command string into a list of `CommandGroup` objects.

    Handles pipes (`|`), logical AND (`&&`), logical OR (`||`), and semicolons.

    Args:
        command (str): The full command string.

    Returns:
        list[CommandGroup]: A list of command groups to be executed.
    """
    groups: list[CommandGroup] = []
    current_pipeline: list[str] = []
    current_segment: list[str] = []
    in_single = False
    in_double = False
    pending_operator: str | None = None
    i = 0

    def finalize_segment() -> None:
        seg = "".join(current_segment).strip()
        if seg:
            current_pipeline.append(seg)
        current_segment.clear()

    def finalize_group(new_operator: str) -> None:
        nonlocal pending_operator
        finalize_segment()
        if current_pipeline:
            groups.append(CommandGroup(list(current_pipeline), pending_operator))
        current_pipeline.clear()
        pending_operator = new_operator

    while i < len(command):
        char = command[i]
        if char == "\\" and i + 1 < len(command):
            current_segment.append(char)
            current_segment.append(command[i + 1])
            i += 2
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            current_segment.append(char)
        elif char == '"' and not in_single:
            in_double = not in_double
            current_segment.append(char)
        elif char == "|" and not in_single and not in_double:
            if i + 1 < len(command) and command[i + 1] == "|":
                finalize_group("||")
                i += 2
                continue
            finalize_segment()
        elif char == "&" and not in_single and not in_double:
            if i + 1 < len(command) and command[i + 1] == "&":
                finalize_group("&&")
                i += 2
                continue
            current_segment.append(char)
        elif char == ";" and not in_single and not in_double:
            finalize_group(";")
        else:
            current_segment.append(char)
        i += 1

    finalize_segment()
    if current_pipeline:
        groups.append(CommandGroup(list(current_pipeline), pending_operator))

    return groups


def _is_blocked_command(cmd: str) -> bool:
    """Checks if a command is in the absolute blocklist."""
    return cmd in cs.SHELL_DANGEROUS_COMMANDS


def _is_dangerous_rm(cmd_parts: list[str]) -> bool:
    """Checks if an `rm` command uses dangerous flags like `-rf`."""
    if not cmd_parts or cmd_parts[0] != cs.SHELL_CMD_RM:
        return False
    flags = "".join(part for part in cmd_parts[1:] if part.startswith("-"))
    return "r" in flags and "f" in flags


def _is_dangerous_rm_path(cmd_parts: list[str], project_root: Path) -> tuple[bool, str]:
    """Checks if an `rm` command targets a dangerous or out-of-project path."""
    if not cmd_parts or cmd_parts[0] != cs.SHELL_CMD_RM:
        return False, ""
    path_args = [p for p in cmd_parts[1:] if not p.startswith("-")]
    for path_arg in path_args:
        if path_arg in ("*", ".", ".."):
            return True, f"rm targeting dangerous path: {path_arg}"
        try:
            if path_arg.startswith("/"):
                resolved = Path(path_arg).resolve()
            else:
                resolved = (project_root / path_arg).resolve()
        except (OSError, ValueError):
            return True, f"rm with invalid path: {path_arg}"
        resolved_str = str(resolved)
        if resolved == resolved.parent:
            return True, "rm targeting root directory"
        parts = resolved.parts
        if len(parts) >= 2 and parts[1] in cs.SHELL_SYSTEM_DIRECTORIES:
            return True, f"rm targeting system directory: {resolved_str}"
        try:
            resolved.relative_to(project_root)
        except ValueError:
            return True, f"rm targeting path outside project: {resolved_str}"
    return False, ""


def _check_pipeline_patterns(full_command: str) -> str | None:
    """Checks the full command pipeline against a list of dangerous patterns."""
    for pattern, reason in PIPELINE_PATTERNS_COMPILED:
        if pattern.search(full_command):
            return reason
    return None


def _check_segment_patterns(segment: str) -> str | None:
    """Checks a single command segment against a list of dangerous patterns."""
    for pattern, reason in SEGMENT_PATTERNS_COMPILED:
        if pattern.search(segment):
            return reason
    return None


def _is_dangerous_command(cmd_parts: list[str], full_segment: str) -> tuple[bool, str]:
    """Determines if a command is dangerous based on multiple checks."""
    if not cmd_parts:
        return False, ""

    base_cmd = cmd_parts[0]

    if _is_blocked_command(base_cmd):
        return True, f"blocked command: {base_cmd}"

    if _is_dangerous_rm(cmd_parts):
        return True, "rm with dangerous flags"

    if reason := _check_segment_patterns(full_segment):
        return True, reason

    return False, ""


def _validate_segment(segment: str, available_commands: str) -> str | None:
    """Validates a single command segment for safety."""
    try:
        cmd_parts = shlex.split(segment)
    except ValueError:
        return te.COMMAND_INVALID_SYNTAX.format(segment=segment)

    if not cmd_parts:
        return None

    base_cmd = cmd_parts[0]

    if base_cmd not in settings.SHELL_COMMAND_ALLOWLIST:
        suggestion = cs.GREP_SUGGESTION if base_cmd == cs.SHELL_CMD_GREP else ""
        return te.COMMAND_NOT_ALLOWED.format(
            cmd=base_cmd, suggestion=suggestion, available=available_commands
        )

    is_dangerous, reason = _is_dangerous_command(cmd_parts, segment)
    if is_dangerous:
        return te.COMMAND_DANGEROUS_BLOCKED.format(cmd=base_cmd, reason=reason)

    return None


def _has_redirect_operators(parts: list[str]) -> bool:
    """Checks if a list of command parts contains redirection operators."""
    return any(p in cs.SHELL_REDIRECT_OPERATORS for p in parts)


def _requires_approval(command: str) -> bool:
    """
    Determines if a command requires user approval before execution.

    Approval is required for non-read-only commands or commands with redirection.

    Args:
        command (str): The full command string.

    Returns:
        bool: True if the command requires approval, False otherwise.
    """
    if not command.strip():
        return True

    try:
        groups = _parse_command(command)
    except (ValueError, IndexError):
        return True

    has_commands = False
    for group in groups:
        for segment in group.commands:
            segment = segment.strip()
            if not segment:
                continue
            try:
                parts = shlex.split(segment)
            except ValueError:
                return True

            if not parts:
                continue

            if _has_redirect_operators(parts):
                return True

            has_commands = True
            base_cmd = parts[0]
            if base_cmd in settings.SHELL_READ_ONLY_COMMANDS:
                continue

            if base_cmd == cs.SHELL_CMD_GIT and len(parts) > 1:
                if parts[1] in settings.SHELL_SAFE_GIT_SUBCOMMANDS:
                    continue

            return True

    return not has_commands


class ShellCommander:
    """
    A tool for safely executing shell commands within the project root.
    """

    def __init__(self, project_root: str = ".", timeout: int = 30):
        """
        Initializes the ShellCommander.

        Args:
            project_root (str): The absolute path to the root of the project.
            timeout (int): The timeout in seconds for command execution.
        """
        self.project_root = Path(project_root).resolve()
        self.timeout = timeout
        logger.info(ls.SHELL_COMMANDER_INIT.format(root=self.project_root))

    async def _execute_pipeline(self, segments: list[str]) -> tuple[int, bytes, bytes]:
        """Executes a pipeline of commands (e.g., `cmd1 | cmd2`)."""
        start_time = time.monotonic()
        input_data: bytes | None = None
        all_stderr: list[bytes] = []
        last_return_code = 0

        env = os.environ.copy()
        if sys.platform == "win32":
            git_bin = r"C:\Program Files\Git\usr\bin"
            if os.path.isdir(git_bin) and git_bin not in env["PATH"]:
                env["PATH"] = f"{git_bin};{env['PATH']}"

        for segment in segments:
            elapsed = time.monotonic() - start_time
            remaining_timeout = self.timeout - elapsed
            if remaining_timeout <= 0:
                raise TimeoutError

            cmd_parts = shlex.split(segment)
            executable = shutil.which(cmd_parts[0], path=env["PATH"])
            if not executable:
                executable = cmd_parts[0]

            proc = await asyncio.create_subprocess_exec(
                executable,
                *cmd_parts[1:],
                stdin=asyncio.subprocess.PIPE if input_data is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.project_root,
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=input_data), timeout=remaining_timeout
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise

            last_return_code = (
                proc.returncode
                if proc.returncode is not None
                else cs.SHELL_RETURN_CODE_ERROR
            )
            input_data = stdout

            if stderr:
                all_stderr.append(stderr)

        return last_return_code, input_data or b"", b"".join(all_stderr)

    @async_timing_decorator
    async def execute(self, command: str) -> ShellCommandResult:
        """
        Validates and executes a shell command.

        Args:
            command (str): The command string to execute.

        Returns:
            ShellCommandResult: An object containing the return code, stdout, and stderr.
        """
        logger.info(ls.TOOL_SHELL_EXEC.format(cmd=command))
        try:
            if subshell_pattern := _has_subshell(command):
                err_msg = te.COMMAND_SUBSHELL_NOT_ALLOWED.format(
                    pattern=subshell_pattern
                )
                logger.error(err_msg)
                return ShellCommandResult(
                    return_code=cs.SHELL_RETURN_CODE_ERROR, stdout="", stderr=err_msg
                )

            if pattern_reason := _check_pipeline_patterns(command):
                err_msg = te.COMMAND_DANGEROUS_PATTERN.format(reason=pattern_reason)
                logger.error(err_msg)
                return ShellCommandResult(
                    return_code=cs.SHELL_RETURN_CODE_ERROR,
                    stdout="",
                    stderr=err_msg,
                )

            groups = _parse_command(command)
            if not groups:
                return ShellCommandResult(
                    return_code=cs.SHELL_RETURN_CODE_ERROR,
                    stdout="",
                    stderr=te.COMMAND_EMPTY,
                )

            available_commands = ", ".join(sorted(settings.SHELL_COMMAND_ALLOWLIST))
            for group in groups:
                for segment in group.commands:
                    if err_msg := _validate_segment(segment, available_commands):
                        logger.error(err_msg)
                        return ShellCommandResult(
                            return_code=cs.SHELL_RETURN_CODE_ERROR,
                            stdout="",
                            stderr=err_msg,
                        )
                    try:
                        cmd_parts = shlex.split(segment)
                    except ValueError:
                        continue
                    is_dangerous, reason = _is_dangerous_rm_path(
                        cmd_parts, self.project_root
                    )
                    if is_dangerous:
                        err_msg = te.COMMAND_DANGEROUS_BLOCKED.format(
                            cmd=cmd_parts[0], reason=reason
                        )
                        logger.error(err_msg)
                        return ShellCommandResult(
                            return_code=cs.SHELL_RETURN_CODE_ERROR,
                            stdout="",
                            stderr=err_msg,
                        )

            all_stdout: list[str] = []
            all_stderr: list[str] = []
            last_return_code = 0

            for group in groups:
                should_run = True
                if group.operator == "&&":
                    should_run = last_return_code == 0
                elif group.operator == "||":
                    should_run = last_return_code != 0

                if not should_run:
                    continue

                return_code, stdout, stderr = await self._execute_pipeline(
                    group.commands
                )
                last_return_code = return_code

                stdout_str = stdout.decode(cs.ENCODING_UTF8, errors="replace").strip()
                stderr_str = stderr.decode(cs.ENCODING_UTF8, errors="replace").strip()

                if stdout_str:
                    all_stdout.append(stdout_str)
                if stderr_str:
                    all_stderr.append(stderr_str)

            final_stdout = "\n".join(all_stdout)
            final_stderr = "\n".join(all_stderr)

            logger.info(ls.TOOL_SHELL_RETURN.format(code=last_return_code))
            if final_stdout:
                logger.info(ls.TOOL_SHELL_STDOUT.format(stdout=final_stdout))
            if final_stderr:
                logger.warning(ls.TOOL_SHELL_STDERR.format(stderr=final_stderr))

            return ShellCommandResult(
                return_code=last_return_code,
                stdout=final_stdout,
                stderr=final_stderr,
            )
        except TimeoutError:
            msg = te.COMMAND_TIMEOUT.format(cmd=command, timeout=self.timeout)
            logger.error(msg)
            return ShellCommandResult(
                return_code=cs.SHELL_RETURN_CODE_ERROR, stdout="", stderr=msg
            )
        except Exception as e:
            logger.error(ls.TOOL_SHELL_ERROR.format(error=e))
            return ShellCommandResult(
                return_code=cs.SHELL_RETURN_CODE_ERROR, stdout="", stderr=str(e)
            )


def create_shell_command_tool(shell_commander: ShellCommander) -> Tool:
    """
    Factory function to create a `pydantic-ai` Tool for executing shell commands.

    Args:
        shell_commander (ShellCommander): An instance of the ShellCommander class.

    Returns:
        Tool: An initialized `pydantic-ai` Tool.
    """

    async def run_shell_command(
        ctx: RunContext[None], command: str
    ) -> ShellCommandResult:
        """
        Executes a shell command within the project's root directory.

        This tool has a strict allowlist and security checks. Commands that are
        not read-only will require user approval.

        Args:
            ctx (RunContext): The Pydantic-AI run context, used for approvals.
            command (str): The shell command to execute.

        Returns:
            ShellCommandResult: An object containing the command's return code,
                                stdout, and stderr.
        """
        if _requires_approval(command) and not ctx.tool_call_approved:
            raise ApprovalRequired(metadata={"command": command})

        return await shell_commander.execute(command)

    return Tool(
        function=run_shell_command,
        name=td.AgenticToolName.EXECUTE_SHELL,
        description=td.SHELL_COMMAND,
    )
