from __future__ import annotations

from typing import Any

import pytest

from codebase_rag.agents.validator import ValidatorAgent

pytestmark = [pytest.mark.anyio]


@pytest.fixture(params=["asyncio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def _new_agent() -> ValidatorAgent:
    return object.__new__(ValidatorAgent)


class TestValidatorAgent:
    async def test_validate_enforces_required_actions_on_not_done(self) -> None:
        agent = _new_agent()

        async def fake_run_with_tools(prompt: str, max_steps: int = 6) -> str:
            _ = prompt
            _ = max_steps
            return (
                '{"decision":"not_done","rationale":"needs_more","required_actions":[]}'
            )

        agent._run_with_tools = fake_run_with_tools  # type: ignore[attr-defined]

        result = await agent.validate(
            {
                "goal": "finalize",
                "blockers": ["coverage missing"],
            }
        )

        assert result.status == "ok"
        assert result.content.get("decision") == "not_done"
        required_actions = result.content.get("required_actions", [])
        assert isinstance(required_actions, list)
        assert len(required_actions) > 0

    async def test_validate_forces_not_done_when_blockers_exist(self) -> None:
        agent = _new_agent()

        async def fake_run_with_tools(prompt: str, max_steps: int = 6) -> str:
            _ = prompt
            _ = max_steps
            return '{"decision":"done","rationale":"ok","required_actions":[]}'

        agent._run_with_tools = fake_run_with_tools  # type: ignore[attr-defined]

        result = await agent.validate(
            {
                "goal": "finalize",
                "blockers": ["confidence gate failed"],
            }
        )

        assert result.status == "ok"
        assert result.content.get("decision") == "not_done"

    def test_parse_json_payload_supports_code_fence(self) -> None:
        fenced = """```json
{
  \"decision\": \"not_done\",
  \"rationale\": \"more checks\",
  \"required_actions\": [\"run tests\"]
}
```"""

        parsed = ValidatorAgent._parse_json_payload(fenced)

        assert isinstance(parsed, dict)
        assert parsed.get("decision") == "not_done"
        assert parsed.get("required_actions") == ["run tests"]

    def test_enforce_contract_clears_actions_when_done(self) -> None:
        parsed: dict[str, Any] = {
            "decision": "done",
            "rationale": "all good",
            "required_actions": ["should be cleared"],
        }

        result = ValidatorAgent._enforce_contract(
            parsed=parsed,
            deterministic_decision="done",
            default_actions=["fallback"],
        )

        assert result.get("decision") == "done"
        assert result.get("required_actions") == []
