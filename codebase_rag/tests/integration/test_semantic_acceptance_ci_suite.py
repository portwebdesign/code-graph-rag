from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from codebase_rag.mcp.tools import MCPToolsRegistry
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    FASTAPI_AUTH_CONTRACT_FIXTURE,
    TRANSACTION_FLOW_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    materialize_fixture_repo,
    run_fixture_update,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture(params=["asyncio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


async def test_semantic_acceptance_ci_suite_multi_hop_uses_semantic_edges(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, FASTAPI_AUTH_CONTRACT_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    registry = MCPToolsRegistry(
        project_root=str(fixture_repo),
        ingestor=cast(object, memgraph_ingestor),
        cypher_gen=MagicMock(),
    )
    registry._session_state["preflight_project_selected"] = True
    registry._session_state["preflight_schema_summary_loaded"] = True

    result = await registry.multi_hop_analysis(
        qualified_name="fastapi_semantic_fixture.main.create_invoice",
        depth=4,
        limit=20,
    )

    assert result.get("status") == "ok"
    affected_symbols = cast(list[str], result.get("affected_symbols", []))
    assert any(".semantic.auth_policy." in symbol for symbol in affected_symbols)
    assert any(".semantic.contract." in symbol for symbol in affected_symbols)

    hop_summary = cast(dict[str, object], result.get("hop_summary", {}))
    relation_counts = cast(dict[str, object], hop_summary.get("relation_counts", {}))
    assert relation_counts.get("SECURED_BY", 0) >= 1
    assert relation_counts.get("ACCEPTS_CONTRACT", 0) >= 1


async def test_semantic_acceptance_ci_suite_impact_graph_uses_semantic_edges(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, TRANSACTION_FLOW_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    registry = MCPToolsRegistry(
        project_root=str(fixture_repo),
        ingestor=cast(object, memgraph_ingestor),
        cypher_gen=MagicMock(),
    )
    registry._session_state["preflight_project_selected"] = True
    registry._session_state["preflight_schema_summary_loaded"] = True

    result = await registry.impact_graph(
        qualified_name="transaction_flow_semantic_fixture.main.persist_invoice",
        depth=4,
        limit=20,
    )

    assert int(result.get("count", 0)) >= 1
    rows = cast(list[dict[str, object]], result.get("results", []))
    targets = [str(row.get("target", "")) for row in rows]
    assert any(".semantic.transaction." in target for target in targets)
    assert any(".semantic.side_effect." in target for target in targets)
