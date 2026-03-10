from __future__ import annotations

from codebase_rag.agents.mcp_prompt_pack import (
    LOCAL_MCP_PLANNER_PROMPT,
    LOCAL_MCP_SYSTEM_PROMPT,
    MCP_PLANNER_PROMPT,
    MCP_SYSTEM_PROMPT,
    compose_agent_prompt,
    compose_agent_prompt_for_provider,
    normalize_orchestrator_prompt,
)


class TestMcpPromptPack:
    def test_system_prompt_requires_exact_next_calls_priority_and_when(self) -> None:
        assert "exact_next_calls" in MCP_SYSTEM_PROMPT
        assert "ascending priority" in MCP_SYSTEM_PROMPT
        assert "when condition" in MCP_SYSTEM_PROMPT
        assert "ad-hoc tool switching" in MCP_SYSTEM_PROMPT

    def test_planner_prompt_requires_exact_next_call_follow_through(self) -> None:
        assert "exact_next_calls" in MCP_PLANNER_PROMPT
        assert "ascending priority" in MCP_PLANNER_PROMPT
        assert "exact_next_call" in MCP_PLANNER_PROMPT
        assert "deterministic next action" in MCP_PLANNER_PROMPT

    def test_normalize_orchestrator_prompt_rejects_drift(self) -> None:
        try:
            normalize_orchestrator_prompt("custom")
        except ValueError as exc:
            assert str(exc) == "orchestrator_prompt_must_match_mcp_system_prompt"
        else:
            raise AssertionError(
                "Expected normalize_orchestrator_prompt to reject drift"
            )

    def test_compose_agent_prompt_uses_canonical_system_prompt(self) -> None:
        composed = compose_agent_prompt("Agent instructions")

        assert composed.startswith(MCP_SYSTEM_PROMPT.strip())
        assert composed.endswith("Agent instructions")

    def test_normalize_orchestrator_prompt_accepts_local_variant(self) -> None:
        normalized = normalize_orchestrator_prompt(LOCAL_MCP_SYSTEM_PROMPT)

        assert normalized == LOCAL_MCP_SYSTEM_PROMPT.strip()

    def test_compose_agent_prompt_for_provider_uses_local_prompt_for_ollama(
        self,
    ) -> None:
        composed = compose_agent_prompt_for_provider(
            provider="ollama",
            default_agent_prompt=MCP_PLANNER_PROMPT,
            local_agent_prompt=LOCAL_MCP_PLANNER_PROMPT,
            system_prompt=LOCAL_MCP_SYSTEM_PROMPT,
        )

        assert composed.startswith(LOCAL_MCP_SYSTEM_PROMPT.strip())
        assert LOCAL_MCP_PLANNER_PROMPT.strip() in composed
