from __future__ import annotations

from codebase_rag.parsers.pipeline.config_semantics import (
    extract_dotenv_definitions,
    extract_python_env_observations,
    extract_typescript_env_observations,
)


def test_extract_dotenv_definitions_captures_feature_flag_defaults() -> None:
    definitions = extract_dotenv_definitions(
        """FEATURE_BILLING=1
FF_EXPERIMENT=off
APP_ENV=production
"""
    )

    by_name = {item.env_name: item for item in definitions}

    assert by_name["FEATURE_BILLING"].default_enabled is True
    assert by_name["FF_EXPERIMENT"].default_enabled is False
    assert by_name["APP_ENV"].default_enabled is None


def test_feature_flag_gate_detection_covers_python_and_typescript() -> None:
    python_observations = extract_python_env_observations(
        """import os


def billing_enabled() -> bool:
    if os.getenv("FEATURE_BILLING"):
        return True
    return False
"""
    )
    typescript_observations = extract_typescript_env_observations(
        """export function billingEnabled(): boolean {
    return process.env.FEATURE_BILLING === "1";
}
""",
        relative_path="frontend/src/env.ts",
    )

    assert any(
        item.env_name == "FEATURE_BILLING" and item.gates_flag
        for item in python_observations
    )
    assert any(
        item.env_name == "FEATURE_BILLING" and item.gates_flag
        for item in typescript_observations
    )
