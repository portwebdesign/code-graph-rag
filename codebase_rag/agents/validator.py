from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, Tool

from codebase_rag.agents.mcp_prompt_pack import (
    MCP_VALIDATOR_PROMPT,
    compose_agent_prompt,
)
from codebase_rag.core.config import settings
from codebase_rag.services.llm import _create_provider_model


@dataclass
class ValidatorResult:
    status: str
    content: dict[str, object]


class ValidatorAgent:
    def __init__(
        self,
        tools: list[Tool] | None = None,
        system_prompt: str | None = None,
    ) -> None:
        config = settings.active_orchestrator_config
        model = _create_provider_model(config)
        self.agent = Agent(
            model=model,
            system_prompt=compose_agent_prompt(
                MCP_VALIDATOR_PROMPT,
                system_prompt=system_prompt,
            ),
            tools=tools or [],
            output_type=[str, DeferredToolRequests],
            retries=settings.AGENT_RETRIES,
        )

    async def validate(self, payload: dict[str, object]) -> ValidatorResult:
        blockers = self._extract_blockers(payload)
        deterministic_decision = "not_done" if blockers else "done"
        default_actions = self._default_required_actions(blockers)

        enriched_payload = {
            **payload,
            "deterministic_decision": deterministic_decision,
            "output_contract": {
                "decision": ["done", "not_done"],
                "required_actions_rule": {
                    "not_done": "non_empty_list",
                    "done": "empty_list",
                },
            },
        }
        prompt = json.dumps(enriched_payload, ensure_ascii=False)
        output = await self._run_with_tools(prompt)
        parsed = self._parse_json_payload(output if isinstance(output, str) else "")
        if parsed is None:
            parsed = {
                "decision": "not_done",
                "rationale": "validator_parse_failed",
                "required_actions": default_actions,
            }
        parsed = self._enforce_contract(
            parsed=parsed,
            deterministic_decision=deterministic_decision,
            default_actions=default_actions,
        )
        return ValidatorResult(status="ok", content=parsed)

    async def _run_with_tools(self, prompt: str, max_steps: int | None = None) -> str:
        if max_steps is None:
            max_steps = max(1, int(settings.AGENT_MAX_STEPS))
        message_history = []
        deferred_results: DeferredToolResults | None = None

        for _ in range(max_steps):
            response = await self.agent.run(
                prompt,
                message_history=message_history,
                deferred_tool_results=deferred_results,
            )
            if isinstance(response.output, DeferredToolRequests):
                deferred_results = self._approve_all(response.output)
                message_history.extend(response.new_messages())
                continue
            message_history.extend(response.new_messages())
            if isinstance(response.output, str):
                return response.output
            return ""

        return ""

    @staticmethod
    def _approve_all(requests: DeferredToolRequests) -> DeferredToolResults:
        results = DeferredToolResults()
        for call in requests.approvals:
            results.approvals[call.tool_call_id] = True
        return results

    @staticmethod
    def _parse_json_payload(text: str) -> dict[str, object] | None:
        parsed_text = ValidatorAgent._extract_json_object(text)
        try:
            payload = json.loads(parsed_text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None

        decision = str(payload.get("decision", "not_done")).strip().lower()
        if decision not in {"done", "not_done"}:
            decision = "not_done"

        required_actions_raw = payload.get("required_actions", [])
        required_actions = []
        if isinstance(required_actions_raw, list):
            required_actions = [
                str(item).strip() for item in required_actions_raw if str(item).strip()
            ]
        elif isinstance(required_actions_raw, str):
            normalized = required_actions_raw.strip()
            if normalized:
                required_actions = [normalized]

        return {
            "decision": decision,
            "rationale": str(payload.get("rationale", "")).strip(),
            "required_actions": required_actions,
        }

    @staticmethod
    def _extract_json_object(text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return "{}"
        if stripped.startswith("```"):
            fence_match = re.search(
                r"```(?:json)?\s*(\{.*\})\s*```",
                stripped,
                flags=re.DOTALL,
            )
            if fence_match:
                return fence_match.group(1).strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end >= start:
            candidate = stripped[start : end + 1].strip()
            if candidate:
                return candidate
        return stripped

    @staticmethod
    def _extract_blockers(payload: dict[str, object]) -> list[str]:
        raw_blockers = payload.get("blockers", [])
        if not isinstance(raw_blockers, list):
            return []
        return [str(item).strip() for item in raw_blockers if str(item).strip()]

    @staticmethod
    def _default_required_actions(blockers: list[str]) -> list[str]:
        if blockers:
            return blockers
        return ["resolve_validation_findings_before_marking_done"]

    @staticmethod
    def _enforce_contract(
        parsed: dict[str, object],
        deterministic_decision: str,
        default_actions: list[str],
    ) -> dict[str, object]:
        decision = str(parsed.get("decision", "not_done")).strip().lower()
        if decision not in {"done", "not_done"}:
            decision = "not_done"
        if deterministic_decision == "not_done":
            decision = "not_done"

        required_actions_raw = parsed.get("required_actions", [])
        required_actions: list[str] = []
        if isinstance(required_actions_raw, list):
            required_actions = [
                str(item).strip() for item in required_actions_raw if str(item).strip()
            ]

        if decision == "done":
            required_actions = []
        elif not required_actions:
            required_actions = default_actions

        rationale = str(parsed.get("rationale", "")).strip()
        if not rationale:
            rationale = "validator_contract_enforced"

        return {
            "decision": decision,
            "rationale": rationale,
            "required_actions": required_actions,
        }
