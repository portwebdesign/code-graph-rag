from __future__ import annotations

import json
import re
from typing import Any


def _strip_markdown_blocks(text: str) -> str:
    return re.sub(r"```(?:json)?\s*", "", text).replace("```", "")


def _load_json_with_fallback(payload: str, defaults: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        trimmed = re.sub(r",\s*([}\]])", r"\1", payload)
        try:
            return json.loads(trimmed)
        except json.JSONDecodeError:
            return defaults


def safe_parse_json(
    text: str | dict | Any, defaults: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Safely parse JSON from LLM output with fallbacks.

    Handles markdown blocks, extra text, and trailing commas.
    """
    if defaults is None:
        defaults = {}

    if isinstance(text, dict):
        return text

    if not isinstance(text, str):
        return defaults

    cleaned = _strip_markdown_blocks(text)
    match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
    if not match:
        return defaults

    return _load_json_with_fallback(match.group(1), defaults)
