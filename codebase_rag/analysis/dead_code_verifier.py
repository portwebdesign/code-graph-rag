from __future__ import annotations

import json
from typing import Any

from loguru import logger
from pydantic_ai import Agent

from codebase_rag.core.config import settings

from ..providers.base import get_provider_from_config
from ..utils.llm_utils import safe_parse_json


def _create_verifier_agent() -> Agent:
    config = settings.active_orchestrator_config
    provider = get_provider_from_config(config)
    model = provider.create_model(config.model_id)
    system_prompt = (
        "You verify whether a function is dead code."
        "Return JSON with keys: is_dead (bool), confidence (0-1), reason (string)."
    )
    return Agent(model=model, system_prompt=system_prompt, output_type=str)


def verify_dead_code(candidate: dict[str, Any]) -> dict[str, Any] | None:
    try:
        agent = _create_verifier_agent()
        payload = json.dumps(candidate, ensure_ascii=False)
        result = agent.run_sync(payload)
        content = result.output.strip() if isinstance(result.output, str) else ""
        parsed = safe_parse_json(
            content,
            defaults={"is_dead": False, "confidence": 0.0, "reason": ""},
        )
        is_dead = bool(parsed.get("is_dead"))
        confidence = float(parsed.get("confidence") or 0.0)
        reason = str(parsed.get("reason") or "")
        return {
            "is_dead": is_dead,
            "confidence": confidence,
            "reason": reason,
        }
    except Exception as exc:
        logger.warning("Dead code verification failed: {}", exc)
        return None
