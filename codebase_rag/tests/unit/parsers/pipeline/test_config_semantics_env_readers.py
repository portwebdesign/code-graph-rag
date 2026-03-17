from __future__ import annotations

from codebase_rag.parsers.pipeline.config_semantics import (
    extract_python_env_observations,
    extract_typescript_env_observations,
)


def test_extracts_python_env_readers_and_settings_fields() -> None:
    observations = extract_python_env_observations(
        """import os
from pydantic_settings import BaseSettings


class AppSettings(BaseSettings):
    analytics_dsn: str
    feature_billing: bool = False


def read_secret() -> str | None:
    return os.getenv("APP_SECRET")


def billing_enabled() -> bool:
    if os.environ["FEATURE_BILLING"] == "1":
        return True
    return False
"""
    )

    summary = {
        (
            item.env_name,
            item.source_name,
            item.source_kind,
            item.evidence_kind,
            item.gates_flag,
            item.uses_secret,
        )
        for item in observations
    }

    assert (
        "APP_SECRET",
        "read_secret",
        "function",
        "python_env_read",
        False,
        True,
    ) in summary
    assert (
        "FEATURE_BILLING",
        "billing_enabled",
        "function",
        "feature_flag_gate",
        True,
        False,
    ) in summary
    assert (
        "ANALYTICS_DSN",
        "AppSettings",
        "class",
        "settings_class_field",
        False,
        True,
    ) in summary


def test_extracts_typescript_process_env_reads() -> None:
    observations = extract_typescript_env_observations(
        """export function frontendApiUrl(): string | undefined {
    return process.env.NEXT_PUBLIC_API_URL;
}

export function cacheUrl(): string | undefined {
    return process.env["PUBLIC_CACHE_URL"];
}
""",
        relative_path="frontend/src/env.ts",
    )

    summary = {
        (item.env_name, item.source_name, item.source_kind, item.evidence_kind)
        for item in observations
    }

    assert (
        "NEXT_PUBLIC_API_URL",
        "frontendApiUrl",
        "function",
        "env_read",
    ) in summary
    assert (
        "PUBLIC_CACHE_URL",
        "cacheUrl",
        "function",
        "env_read",
    ) in summary
