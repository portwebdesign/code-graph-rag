from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, Field
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, Tool

from codebase_rag.agents.mcp_prompt_pack import (
    LOCAL_MCP_PLANNER_PROMPT,
    MCP_PLANNER_PROMPT,
    compose_agent_prompt_for_provider,
)
from codebase_rag.agents.output_parser import JSONOutputParser
from codebase_rag.core.config import settings
from codebase_rag.services.llm import _create_provider_model


class PlannerContent(BaseModel):
    summary: str = ""
    objective: str = ""
    active_project: str = ""
    task_type: str = ""
    steps: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    evidence_priority: list[str] = Field(default_factory=list)
    multi_hop_plan: list[str] = Field(default_factory=list)
    affected_symbols: list[str] = Field(default_factory=list)
    recommended_tool_chain: list[str] = Field(default_factory=list)
    copy_paste_calls: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    test_strategy: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)


@dataclass
class PlannerResult:
    status: str
    content: dict[str, object]


class PlannerAgent:
    def __init__(
        self,
        tools: list[Tool] | None = None,
        system_prompt: str | None = None,
    ) -> None:
        config = settings.active_orchestrator_config
        model = _create_provider_model(config)
        self._json_parser = JSONOutputParser()
        self.agent = Agent(
            model=model,
            system_prompt=compose_agent_prompt_for_provider(
                provider=str(config.provider),
                default_agent_prompt=MCP_PLANNER_PROMPT,
                local_agent_prompt=LOCAL_MCP_PLANNER_PROMPT,
                system_prompt=system_prompt,
            ),
            tools=tools or [],
            output_type=[PlannerContent, str, DeferredToolRequests],
            retries=settings.AGENT_RETRIES,
        )

    async def plan(self, goal: str, context: str | None = None) -> PlannerResult:
        prompt = f"Goal: {goal}\n"
        if context:
            prompt += f"Context: {context}\n"
        output = await self._run_with_tools(prompt)

        if isinstance(output, PlannerContent):
            content = output.model_dump()
            status = "ok" if self._is_actionable_payload(content) else "empty"
            return PlannerResult(status=status, content=content)

        if isinstance(output, str):
            parsed = self._parse_json_payload(output)
            if parsed is not None:
                status = "ok" if self._is_actionable_payload(parsed) else "empty"
                return PlannerResult(status=status, content=parsed)

        return PlannerResult(
            status="empty",
            content={"summary": "", "steps": [], "risks": [], "tests": []},
        )

    async def _run_with_tools(
        self, prompt: str, max_steps: int | None = None
    ) -> PlannerContent | str:
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
            return response.output

        return ""

    @staticmethod
    def _approve_all(requests: DeferredToolRequests) -> DeferredToolResults:
        results = DeferredToolResults()
        for call in requests.approvals:
            results.approvals[call.tool_call_id] = True
        return results

    @staticmethod
    def _normalize_list_field(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]

    @staticmethod
    def _normalize_payload(payload: dict[str, object]) -> dict[str, object]:
        return {
            "summary": str(payload.get("summary", "")),
            "objective": str(payload.get("objective", "")),
            "active_project": str(payload.get("active_project", "")),
            "task_type": str(payload.get("task_type", "")),
            "steps": PlannerAgent._normalize_list_field(payload.get("steps")),
            "required_evidence": PlannerAgent._normalize_list_field(
                payload.get("required_evidence")
            ),
            "evidence_priority": PlannerAgent._normalize_list_field(
                payload.get("evidence_priority")
            ),
            "multi_hop_plan": PlannerAgent._normalize_list_field(
                payload.get("multi_hop_plan")
            ),
            "affected_symbols": PlannerAgent._normalize_list_field(
                payload.get("affected_symbols")
            ),
            "recommended_tool_chain": PlannerAgent._normalize_list_field(
                payload.get("recommended_tool_chain")
            ),
            "copy_paste_calls": PlannerAgent._normalize_list_field(
                payload.get("copy_paste_calls")
            ),
            "risks": PlannerAgent._normalize_list_field(payload.get("risks")),
            "tests": PlannerAgent._normalize_list_field(payload.get("tests")),
            "test_strategy": PlannerAgent._normalize_list_field(
                payload.get("test_strategy")
            ),
            "stop_conditions": PlannerAgent._normalize_list_field(
                payload.get("stop_conditions")
            ),
        }

    def _parse_json_payload(self, text: str) -> dict[str, object] | None:
        try:
            payload = self._json_parser.parse(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        if not payload:
            return None
        return self._normalize_payload(payload)

    @staticmethod
    def _is_actionable_payload(payload: dict[str, object]) -> bool:
        actionable_list_fields = (
            "steps",
            "required_evidence",
            "evidence_priority",
            "multi_hop_plan",
            "recommended_tool_chain",
            "copy_paste_calls",
        )

        for key in actionable_list_fields:
            value = payload.get(key, [])
            if isinstance(value, list) and any(str(item).strip() for item in value):
                return True

        return False
