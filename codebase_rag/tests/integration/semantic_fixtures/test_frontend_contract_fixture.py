from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from codebase_rag.core import constants as cs
from codebase_rag.infrastructure.parser_loader import load_parsers
from codebase_rag.mcp.tools import MCPToolsRegistry
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    FRONTEND_CONTRACT_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    build_mock_graph_snapshot,
    materialize_fixture_repo,
    run_fixture_update,
)

pytestmark = [pytest.mark.integration]


@pytest.fixture(params=["asyncio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


SEMANTIC_NODE_LABELS = {
    cs.NodeLabel.COMPONENT,
    cs.NodeLabel.FUNCTION,
    cs.NodeLabel.ENDPOINT,
}
SEMANTIC_RELATIONSHIP_TYPES = {
    cs.RelationshipType.CALLS,
    cs.RelationshipType.REQUESTS_ENDPOINT,
    cs.RelationshipType.DEFINES,
}


def _require_typescript_parser() -> None:
    parsers, _queries = load_parsers()
    if "typescript" not in parsers:
        pytest.skip("typescript parser not available")


def test_frontend_contract_fixture_snapshot_is_deterministic(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    _require_typescript_parser()
    fixture_repo = materialize_fixture_repo(temp_repo, FRONTEND_CONTRACT_FIXTURE)
    second_ingestor = MagicMock(spec=MemgraphIngestor)

    run_fixture_update(fixture_repo, mock_ingestor)
    first_snapshot = build_mock_graph_snapshot(
        mock_ingestor,
        node_labels={str(label) for label in SEMANTIC_NODE_LABELS},
        relationship_types={str(rel) for rel in SEMANTIC_RELATIONSHIP_TYPES},
    )

    run_fixture_update(fixture_repo, second_ingestor)
    second_snapshot = build_mock_graph_snapshot(
        second_ingestor,
        node_labels={str(label) for label in SEMANTIC_NODE_LABELS},
        relationship_types={str(rel) for rel in SEMANTIC_RELATIONSHIP_TYPES},
    )

    assert first_snapshot == second_snapshot

    request_edges = [
        rel
        for rel in first_snapshot["relationships"]
        if rel["relationship_type"] == str(cs.RelationshipType.REQUESTS_ENDPOINT)
    ]
    assert any(
        rel["from"]["value"]
        == "frontend_contract_semantic_fixture.src.lib.generated.client.listCustomers"
        and rel["to"]["value"]
        == "frontend_contract_semantic_fixture.endpoint.http.GET:/api/customers"
        and rel["props"].get("client_kind") == "http_client_member"
        for rel in request_edges
    )
    assert any(
        rel["from"]["value"]
        == "frontend_contract_semantic_fixture.src.lib.raw.orders.createOrder"
        and rel["to"]["value"]
        == "frontend_contract_semantic_fixture.endpoint.http.POST:/api/orders"
        and rel["props"].get("client_kind") == "fetch"
        for rel in request_edges
    )


@pytest.mark.anyio
async def test_frontend_contract_fixture_multi_hop_smoke(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    _require_typescript_parser()
    fixture_repo = materialize_fixture_repo(temp_repo, FRONTEND_CONTRACT_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    registry = MCPToolsRegistry(
        project_root=str(fixture_repo),
        ingestor=cast(object, memgraph_ingestor),
        cypher_gen=MagicMock(),
    )
    registry._session_state["preflight_project_selected"] = True
    registry._session_state["preflight_schema_summary_loaded"] = True

    result = await registry.multi_hop_analysis(
        qualified_name=(
            "frontend_contract_semantic_fixture.src.app.customers.page.CustomersPage"
        ),
        depth=4,
        limit=20,
    )

    assert result.get("status") == "ok"
    affected_symbols = cast(list[str], result.get("affected_symbols", []))
    assert any("listCustomers" in symbol for symbol in affected_symbols)
    assert any("/api/customers" in symbol for symbol in affected_symbols)

    hop_summary = cast(dict[str, object], result.get("hop_summary", {}))
    relation_counts = cast(dict[str, object], hop_summary.get("relation_counts", {}))
    assert relation_counts.get("CALLS", 0) >= 1
    assert relation_counts.get("REQUESTS_ENDPOINT", 0) >= 1
