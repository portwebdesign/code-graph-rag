"""
This module contains functions to dynamically generate various sections of the
project's README.md file.

It extracts information from different parts of the codebase, such as Makefile
commands, language specifications, graph schemas, and project dependencies, and
formats this information into Markdown tables and lists. This ensures that the
documentation stays in sync with the project's current state.

Key functionalities include:
-   Parsing `Makefile` to list available commands.
-   Generating tables for supported languages, graph schemas (nodes and relationships),
    and command-line interface (CLI) commands.
-   Listing available tools for different agent modes.
-   Extracting project dependencies from `pyproject.toml` and fetching their
    summaries from PyPI with caching.
"""

from __future__ import annotations

import json
import re
import time
import tomllib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import NamedTuple

from loguru import logger

from codebase_rag.core import cli_help as ch
from codebase_rag.core.constants import (
    ENCODING_UTF8,
    LANGUAGE_METADATA,
    LanguageStatus,
    SupportedLanguage,
)
from codebase_rag.data_models.types_defs import NODE_SCHEMAS, RELATIONSHIP_SCHEMAS
from codebase_rag.tools.tool_descriptions import AGENTIC_TOOLS, MCP_TOOLS

from .language_spec import LANGUAGE_SPECS

PYPI_CACHE_FILE = Path(__file__).parent.parent / ".pypi_cache.json"
PYPI_CACHE_TTL_SECONDS = 86400
_PYPI_CACHE_LOCK = Lock()

CHECK_MARK = "\u2713"
DASH = "-"


class MakeCommand(NamedTuple):
    """Represents a command extracted from a Makefile."""

    name: str
    description: str


MAKEFILE_PATTERN = re.compile(r"^([a-zA-Z_-]+):.*?## (.+)$")


def format_markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    """
    Formats a list of headers and rows into a Markdown table.

    Args:
        headers (list[str]): The table headers.
        rows (list[list[str]]): The table rows, where each row is a list of cells.

    Returns:
        str: The formatted Markdown table as a string.
    """
    esc_headers = [str(h).replace("|", "\\|") for h in headers]
    esc_rows = [[str(cell).replace("|", "\\|") for cell in row] for row in rows]
    separator = "|".join("-" * max(len(h), 3) for h in esc_headers)
    lines = [
        "| " + " | ".join(esc_headers) + " |",
        "|" + separator + "|",
    ]
    for row in esc_rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def extract_makefile_commands(makefile_path: Path) -> list[MakeCommand]:
    """
    Extracts commands and their descriptions from a Makefile.

    It uses a regex pattern to find lines formatted like: `command: ## Description`.

    Args:
        makefile_path (Path): The path to the Makefile.

    Returns:
        list[MakeCommand]: A list of extracted commands.
    """
    commands: list[MakeCommand] = []
    content = makefile_path.read_text(encoding="utf-8")
    for line in content.splitlines():
        if match := MAKEFILE_PATTERN.match(line):
            commands.append(
                MakeCommand(name=match.group(1), description=match.group(2))
            )
    return commands


def format_makefile_table(commands: list[MakeCommand]) -> str:
    """
    Formats a list of Makefile commands into a Markdown table.

    Args:
        commands (list[MakeCommand]): The commands to format.

    Returns:
        str: The formatted Markdown table.
    """
    rows = [[f"`make {cmd.name}`", cmd.description] for cmd in commands]
    return format_markdown_table(["Command", "Description"], rows)


def format_full_languages_table() -> str:
    """
    Generates a Markdown table detailing supported programming languages and their features.

    Returns:
        str: The formatted Markdown table.
    """
    headers = [
        "Language",
        "Status",
        "Extensions",
        "Functions",
        "Classes/Structs",
        "Modules",
        "Package Detection",
        "Additional Features",
    ]
    sorted_langs = sorted(
        SupportedLanguage,
        key=lambda lang: (
            LANGUAGE_METADATA[lang].status != LanguageStatus.FULL,
            lang.value,
        ),
    )
    rows: list[list[str]] = []
    for lang in sorted_langs:
        spec = LANGUAGE_SPECS[lang]
        meta = LANGUAGE_METADATA[lang]
        rows.append(
            [
                meta.display_name,
                meta.status.value,
                ", ".join(spec.file_extensions),
                CHECK_MARK if spec.function_node_types else DASH,
                CHECK_MARK if spec.class_node_types else DASH,
                CHECK_MARK if spec.module_node_types else DASH,
                CHECK_MARK if spec.package_indicators else DASH,
                meta.additional_features,
            ]
        )
    return format_markdown_table(headers, rows)


def extract_node_schemas() -> list[tuple[str, str]]:
    """
    Extracts node schema information from the global schema definitions.

    Returns:
        list[tuple[str, str]]: A list of (label, properties_string) tuples.
    """
    return [(schema.label.value, schema.properties) for schema in NODE_SCHEMAS]


def format_node_schemas_table(schemas: list[tuple[str, str]]) -> str:
    """
    Formats node schema information into a Markdown table.

    Args:
        schemas (list[tuple[str, str]]): The node schemas to format.

    Returns:
        str: The formatted Markdown table.
    """
    rows = [[label, f"`{props}`"] for label, props in schemas]
    return format_markdown_table(["Label", "Properties"], rows)


def extract_relationship_schemas() -> list[tuple[str, str, str]]:
    """
    Extracts relationship schema information from the global schema definitions.

    Returns:
        list[tuple[str, str, str]]: A list of (source_labels, rel_type, target_labels) tuples.
    """
    result: list[tuple[str, str, str]] = []
    for schema in RELATIONSHIP_SCHEMAS:
        sources = ", ".join(s.value for s in schema.sources)
        targets = ", ".join(t.value for t in schema.targets)
        result.append((sources, schema.rel_type.value, targets))
    return result


def format_relationship_schemas_table(schemas: list[tuple[str, str, str]]) -> str:
    """
    Formats relationship schema information into a Markdown table.

    Args:
        schemas (list[tuple[str, str, str]]): The relationship schemas to format.

    Returns:
        str: The formatted Markdown table.
    """
    rows = [[source, rel, target] for source, rel, target in schemas]
    return format_markdown_table(["Source", "Relationship", "Target"], rows)


def format_cli_commands_table() -> str:
    """
    Generates a Markdown table of the application's CLI commands.

    Returns:
        str: The formatted Markdown table.
    """
    rows = [
        [f"`codebase-rag {cmd.value}`", desc] for cmd, desc in ch.CLI_COMMANDS.items()
    ]
    return format_markdown_table(["Command", "Description"], rows)


def format_language_mappings() -> str:
    """
    Generates a Markdown list mapping languages to their recognized tree-sitter node types.

    Returns:
        str: The formatted Markdown list.
    """
    sorted_langs = sorted(
        SupportedLanguage,
        key=lambda lang: (
            LANGUAGE_METADATA[lang].status != LanguageStatus.FULL,
            lang.value,
        ),
    )
    lines: list[str] = []
    for lang in sorted_langs:
        spec = LANGUAGE_SPECS[lang]
        meta = LANGUAGE_METADATA[lang]
        node_types = list(spec.function_node_types) + list(spec.class_node_types)
        if not node_types:
            continue
        formatted_types = ", ".join(f"`{t}`" for t in sorted(node_types))
        lines.append(f"- **{meta.display_name}**: {formatted_types}")
    return "\n".join(lines)


def format_mcp_tools_table() -> str:
    """
    Generates a Markdown table of tools available in Multi-turn Conversation Protocol (MCP) mode.

    Returns:
        str: The formatted Markdown table.
    """
    rows = [[f"`{name.value}`", desc] for name, desc in MCP_TOOLS.items()]
    return format_markdown_table(["Tool", "Description"], rows)


def format_agentic_tools_table() -> str:
    """
    Generates a Markdown table of tools available in agentic (autonomous) mode.

    Returns:
        str: The formatted Markdown table.
    """
    rows = [[f"`{name.value}`", desc] for name, desc in AGENTIC_TOOLS.items()]
    return format_markdown_table(["Tool", "Description"], rows)


def extract_dependencies(pyproject_path: Path) -> list[str]:
    """
    Extracts the list of main dependencies from a `pyproject.toml` file.

    Args:
        pyproject_path (Path): The path to the `pyproject.toml` file.

    Returns:
        list[str]: A list of dependency names.
    """
    content = pyproject_path.read_bytes()
    data = tomllib.loads(content.decode(ENCODING_UTF8))
    deps = data.get("project", {}).get("dependencies", [])
    return [re.split(r"[<>=!~\[]", dep)[0].strip() for dep in deps]


def _load_pypi_cache() -> dict[str, tuple[str, float]]:
    """Loads the PyPI summary cache from a JSON file."""
    if not PYPI_CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(PYPI_CACHE_FILE.read_text(encoding="utf-8"))
        return {k: (v[0], v[1]) for k, v in data.items()}
    except (json.JSONDecodeError, KeyError, IndexError):
        return {}


def _save_pypi_cache(cache: dict[str, tuple[str, float]]) -> None:
    """Saves the PyPI summary cache to a JSON file."""
    PYPI_CACHE_FILE.write_text(
        json.dumps({k: list(v) for k, v in cache.items()}), encoding="utf-8"
    )


def fetch_pypi_summary(package_name: str, cache: dict[str, tuple[str, float]]) -> str:
    """
    Fetches the summary for a package from PyPI, using a local cache to avoid repeated requests.

    Args:
        package_name (str): The name of the package.
        cache (dict): The cache dictionary to use for storing and retrieving results.

    Returns:
        str: The package summary, or an empty string if it cannot be fetched.
    """
    now = time.time()
    with _PYPI_CACHE_LOCK:
        cached = cache.get(package_name)
        if cached and now - cached[1] < PYPI_CACHE_TTL_SECONDS:
            return cached[0]

    url = f"https://pypi.org/pypi/{package_name}/json"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            charset = response.headers.get_content_charset() or ENCODING_UTF8
            data = json.loads(response.read().decode(charset))
            summary = data.get("info", {}).get("summary", "") or ""
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"Could not fetch PyPI summary for {package_name}: {e}")
        return ""

    with _PYPI_CACHE_LOCK:
        cache[package_name] = (summary, now)
    return summary


def format_dependencies(deps: list[str]) -> str:
    """
    Formats a list of dependencies into a Markdown list with summaries from PyPI.

    Args:
        deps (list[str]): A list of dependency names.

    Returns:
        str: The formatted Markdown list.
    """
    cache = _load_pypi_cache()
    try:
        with ThreadPoolExecutor() as executor:
            summaries = list(
                executor.map(lambda dep: fetch_pypi_summary(dep, cache), deps)
            )
        lines: list[str] = []
        for name, summary in zip(deps, summaries):
            if summary:
                lines.append(f"- **{name}**: {summary}")
            else:
                lines.append(f"- **{name}**")
        return "\n".join(lines)
    finally:
        _save_pypi_cache(cache)


def generate_all_sections(project_root: Path) -> dict[str, str]:
    """
    Generates all dynamic sections for the README file.

    Args:
        project_root (Path): The root directory of the project.

    Returns:
        dict[str, str]: A dictionary mapping section names to their generated
                        Markdown content.
    """
    makefile_commands = extract_makefile_commands(project_root / "Makefile")
    node_schemas = extract_node_schemas()
    rel_schemas = extract_relationship_schemas()
    deps = extract_dependencies(project_root / "pyproject.toml")

    return {
        "makefile_commands": format_makefile_table(makefile_commands),
        "supported_languages": format_full_languages_table(),
        "language_mappings": format_language_mappings(),
        "node_schemas": format_node_schemas_table(node_schemas),
        "relationship_schemas": format_relationship_schemas_table(rel_schemas),
        "cli_commands": format_cli_commands_table(),
        "mcp_tools": format_mcp_tools_table(),
        "agentic_tools": format_agentic_tools_table(),
        "dependencies": format_dependencies(deps),
    }
