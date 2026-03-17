from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from codebase_rag.core import constants as cs
from codebase_rag.mcp.tools import MCPToolsRegistry
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    TRANSACTION_FLOW_FIXTURE,
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
    cs.NodeLabel.TRANSACTION_BOUNDARY,
    cs.NodeLabel.SIDE_EFFECT,
}
SEMANTIC_RELATIONSHIP_TYPES = {
    cs.RelationshipType.BEGINS_TRANSACTION,
    cs.RelationshipType.COMMITS_TRANSACTION,
    cs.RelationshipType.ROLLBACKS_TRANSACTION,
    cs.RelationshipType.PERFORMS_SIDE_EFFECT,
    cs.RelationshipType.WITHIN_TRANSACTION,
    cs.RelationshipType.BEFORE,
    cs.RelationshipType.AFTER,
}


def test_transaction_flow_fixture_snapshot_is_deterministic(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, TRANSACTION_FLOW_FIXTURE)
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

    node_labels = {
        str(node["label"]) for node in first_snapshot["nodes"] if "label" in node
    }
    assert str(cs.NodeLabel.TRANSACTION_BOUNDARY) in node_labels
    assert str(cs.NodeLabel.SIDE_EFFECT) in node_labels

    relationship_types = {
        str(rel["relationship_type"])
        for rel in first_snapshot["relationships"]
        if "relationship_type" in rel
    }
    assert {
        str(cs.RelationshipType.BEGINS_TRANSACTION),
        str(cs.RelationshipType.COMMITS_TRANSACTION),
        str(cs.RelationshipType.PERFORMS_SIDE_EFFECT),
        str(cs.RelationshipType.WITHIN_TRANSACTION),
        str(cs.RelationshipType.BEFORE),
        str(cs.RelationshipType.AFTER),
    }.issubset(relationship_types)


@pytest.mark.anyio
async def test_transaction_flow_fixture_multi_hop_smoke(
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

    result = await registry.multi_hop_analysis(
        qualified_name="transaction_flow_semantic_fixture.main.persist_invoice",
        depth=5,
        limit=20,
    )

    assert result.get("status") == "ok"
    affected_symbols = cast(list[str], result.get("affected_symbols", []))
    assert any(".semantic.transaction." in symbol for symbol in affected_symbols)
    assert any(".semantic.side_effect." in symbol for symbol in affected_symbols)

    hop_summary = cast(dict[str, object], result.get("hop_summary", {}))
    relation_counts = cast(dict[str, object], hop_summary.get("relation_counts", {}))
    assert relation_counts.get("BEGINS_TRANSACTION", 0) >= 1
    assert relation_counts.get("PERFORMS_SIDE_EFFECT", 0) >= 1
    assert relation_counts.get("WITHIN_TRANSACTION", 0) >= 1
    assert relation_counts.get("BEFORE", 0) >= 1
