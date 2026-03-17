from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from codebase_rag.mcp.tools import MCPToolsRegistry
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    TEST_SEMANTICS_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    materialize_fixture_repo,
    run_fixture_update,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture(params=["asyncio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


async def test_test_generate_prefers_semantic_test_graph_candidates(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, TEST_SEMANTICS_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    registry = MCPToolsRegistry(
        project_root=str(fixture_repo),
        ingestor=memgraph_ingestor,
        cypher_gen=MagicMock(),
    )
    registry._session_state["preflight_project_selected"] = True
    registry._session_state["preflight_schema_summary_loaded"] = True
    registry._session_state["last_multi_hop_bundle"] = {
        "affected_files": ["app.py"],
        "affected_symbols": [f"{fixture_repo.name}.endpoint.fastapi.POST:/api/orders"],
    }

    async def fake_run(prompt: str) -> object:
        assert "Selection mode: semantic-graph-primary" in prompt
        assert "tests/test_orders.py" in prompt
        assert "semantic_candidate_testcases" not in prompt
        return SimpleNamespace(
            status="ok",
            content="def test_order_api_semantics():\n    assert True\n",
        )

    registry._test_agent = SimpleNamespace(run=fake_run)

    result = await registry.test_generate(goal="Add regression tests for the order API")

    assert result.get("status") == "ok"
    test_selection = cast(dict[str, object], result.get("test_selection", {}))
    assert test_selection.get("selection_mode") == "semantic-graph-primary"
    existing_tests = cast(list[str], test_selection.get("candidate_existing_tests", []))
    assert "tests/test_orders.py" in existing_tests
    semantic_candidates = cast(
        list[dict[str, object]],
        test_selection.get("semantic_candidate_testcases", []),
    )
    assert any(
        str(item.get("coverage_kind", "")) in {"endpoint_contract", "endpoint_direct"}
        for item in semantic_candidates
    )
