from __future__ import annotations

import re

from codebase_rag.parsers.pipeline.semantic_metadata import build_semantic_qn

_SECRET_TOKEN_RE = re.compile(
    r"(secret|token|password|passwd|credential|client_secret|api_key|access_key|private_key|jwt|dsn)",
    re.IGNORECASE,
)
_FEATURE_TOKEN_RE = re.compile(
    r"(^FEATURE_|^FLAG_|^FF_|feature|flag|toggle|experiment)",
    re.IGNORECASE,
)
_TRUTHY_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSY_VALUES = {"0", "false", "no", "off", "disabled"}


def normalize_env_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", str(value).strip().upper())
    normalized = normalized.strip("_")
    return normalized or "UNKNOWN_ENV"


def is_secret_like_name(name: str) -> bool:
    normalized = normalize_env_name(name)
    return bool(_SECRET_TOKEN_RE.search(normalized))


def is_feature_flag_name(name: str) -> bool:
    normalized = normalize_env_name(name)
    return bool(_FEATURE_TOKEN_RE.search(normalized))


def parse_env_truthiness(value: object) -> bool | None:
    normalized = str(value).strip().lower()
    if normalized in _TRUTHY_VALUES:
        return True
    if normalized in _FALSY_VALUES:
        return False
    return None


def build_env_var_qn(project_name: str, env_name: str) -> str:
    return build_semantic_qn(project_name, "env_var", normalize_env_name(env_name))


def build_feature_flag_qn(project_name: str, env_name: str) -> str:
    return build_semantic_qn(
        project_name,
        "feature_flag",
        normalize_env_name(env_name),
    )


def build_secret_ref_qn(project_name: str, secret_name: str) -> str:
    return build_semantic_qn(
        project_name,
        "secret_ref",
        normalize_env_name(secret_name),
    )


def redact_secret_value(value: object) -> str:
    _ = value
    return "<redacted>"
