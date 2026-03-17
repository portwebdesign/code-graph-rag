from __future__ import annotations

import re

from codebase_rag.parsers.pipeline.semantic_metadata import sanitize_semantic_identity


def normalize_event_name(value: str | None) -> str:
    """Normalizes event names to a stable dotted form."""

    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    normalized = re.sub(r"[\s/_:|\\-]+", ".", raw)
    normalized = re.sub(r"\.+", ".", normalized)
    return normalized.strip(".")


def normalize_channel_name(value: str | None) -> str:
    """Normalizes queue/topic/channel names to a stable hyphenated form."""

    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    normalized = re.sub(r"[\s/._:|\\]+", "-", raw)
    normalized = re.sub(r"-+", "-", normalized)
    return normalized.strip("-")


def build_event_flow_canonical_key(
    *,
    event_name: str | None,
    channel_name: str | None,
    fallback_name: str | None = None,
) -> str:
    """Builds a canonical runtime/static identity for event-flow reconciliation."""

    normalized_event = normalize_event_name(event_name)
    normalized_channel = normalize_channel_name(channel_name)
    fallback = sanitize_semantic_identity(str(fallback_name or "").strip().lower())
    base = normalized_event or normalized_channel or fallback or "unknown"
    if normalized_channel and normalized_channel != base:
        return f"{base}@{normalized_channel}"
    return base
