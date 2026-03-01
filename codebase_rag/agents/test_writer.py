from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, Tool

from codebase_rag.agents.mcp_prompt_pack import MCP_TEST_PROMPT, compose_agent_prompt
from codebase_rag.core.config import settings
from codebase_rag.services.llm import _create_provider_model


@dataclass
class TestResult:
    status: str
    content: str


class TestAgent:
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
                MCP_TEST_PROMPT,
                system_prompt=system_prompt,
            ),
            tools=tools or [],
            output_type=[str, DeferredToolRequests],
            retries=settings.AGENT_RETRIES,
        )

    async def run(self, task: str) -> TestResult:
        output = await self._run_with_tools(task)
        return TestResult(
            status="ok", content=output if isinstance(output, str) else ""
        )

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
