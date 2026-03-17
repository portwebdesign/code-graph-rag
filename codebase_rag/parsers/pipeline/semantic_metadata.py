from __future__ import annotations

import re
from typing import Any

from codebase_rag.core import constants as cs


def build_semantic_metadata(
    *,
    source_parser: str,
    evidence_kind: str,
    file_path: str | None,
    confidence: float = 0.9,
    language: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    """Builds a consistent metadata payload for semantic nodes and edges."""

    payload: dict[str, object] = {
        cs.KEY_SOURCE_PARSER: source_parser,
        cs.KEY_EVIDENCE_KIND: evidence_kind,
        cs.KEY_CONFIDENCE: round(max(0.0, min(confidence, 1.0)), 3),
    }
    if file_path:
        payload[cs.KEY_PATH] = file_path
    if language:
        payload[cs.KEY_LANGUAGE] = language
    if line_start is not None:
        payload[cs.KEY_START_LINE] = line_start
    if line_end is not None:
        payload[cs.KEY_END_LINE] = line_end
    if extra:
        payload.update(
            {key: value for key, value in extra.items() if value is not None}
        )
    return payload


def sanitize_semantic_identity(value: str) -> str:
    """Returns a graph-safe semantic identifier fragment."""

    cleaned = re.sub(r"[^A-Za-z0-9_.:\-]+", "_", value.strip())
    cleaned = cleaned.strip("_")
    return cleaned or "unknown"


def build_semantic_qn(project_name: str, family: str, value: str) -> str:
    """Builds a deterministic semantic qualified name."""

    safe_value = sanitize_semantic_identity(value)
    return f"{project_name}{cs.SEPARATOR_DOT}semantic.{family}.{safe_value}"


def build_placeholder_flag(
    *, placeholder_name: str, extra: dict[str, Any] | None = None
) -> dict[str, object]:
    """Returns placeholder marker properties for unresolved semantic targets."""

    payload: dict[str, object] = {
        cs.KEY_IS_PLACEHOLDER: True,
        "placeholder_name": placeholder_name,
    }
    if extra:
        payload.update(
            {key: value for key, value in extra.items() if value is not None}
        )
    return payload
