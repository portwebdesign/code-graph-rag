from __future__ import annotations

import json

from loguru import logger
from pydantic_ai import Agent

from codebase_rag.core.config import settings

from ..providers.base import get_provider_from_config


def _create_orchestrator_agent() -> Agent:
    config = settings.active_orchestrator_config
    provider = get_provider_from_config(config)
    model = provider.create_model(config.model_id)
    system_prompt = (
        "You are a senior code quality reviewer. Produce concise, actionable refactoring "
        "and improvement suggestions based on the provided analysis summary. Return a JSON "
        "array of suggestions with fields: title, rationale, impact, effort, and file_hints."
    )
    return Agent(model=model, system_prompt=system_prompt, output_type=str)


def generate_ml_insights(summary: dict[str, object]) -> dict[str, object]:
    try:
        agent = _create_orchestrator_agent()
        payload = json.dumps(summary, ensure_ascii=False)
        result = agent.run_sync(payload)
        content = result.output.strip() if isinstance(result.output, str) else ""
        return {
            "status": "ok",
            "model": settings.active_orchestrator_config.model_id,
            "suggestions": content,
        }
    except Exception as exc:
        logger.warning("ML insights generation failed: {}", exc)
        return {
            "status": "unavailable",
            "error": str(exc),
        }
