from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, Field
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, Tool

from codebase_rag.agents.mcp_prompt_pack import (
    MCP_PLANNER_PROMPT,
    compose_agent_prompt,
)
from codebase_rag.core.config import settings
from codebase_rag.services.llm import _create_provider_model


class PlannerContent(BaseModel):
    summary: str = ""
    steps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)


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
        self.agent = Agent(
            model=model,
            system_prompt=compose_agent_prompt(
                MCP_PLANNER_PROMPT,
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
            return PlannerResult(status="ok", content=output.model_dump())

        if isinstance(output, str):
            parsed = self._parse_json_payload(output)
            if parsed is not None:
                return PlannerResult(status="ok", content=parsed)

        return PlannerResult(
            status="ok",
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
    def _parse_json_payload(text: str) -> dict[str, object] | None:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None

        return {
            "summary": str(payload.get("summary", "")),
            "steps": [str(item) for item in payload.get("steps", []) or []],
            "risks": [str(item) for item in payload.get("risks", []) or []],
            "tests": [str(item) for item in payload.get("tests", []) or []],
        }
