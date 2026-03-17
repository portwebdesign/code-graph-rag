from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    FASTAPI_AUTH_CONTRACT_FIXTURE,
    OPENAPI_CONTRACT_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    build_mock_graph_snapshot,
    materialize_fixture_repo,
    run_fixture_update,
)

SEMANTIC_NODE_LABELS = {
    cs.NodeLabel.ENDPOINT,
    cs.NodeLabel.CONTRACT,
}
SEMANTIC_RELATIONSHIP_TYPES = {
    cs.RelationshipType.ACCEPTS_CONTRACT,
    cs.RelationshipType.RETURNS_CONTRACT,
}


def test_backend_and_openapi_endpoint_contract_edges_are_emitted(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, FASTAPI_AUTH_CONTRACT_FIXTURE)
    second_repo = materialize_fixture_repo(temp_repo, OPENAPI_CONTRACT_FIXTURE)
    second_ingestor = MagicMock(spec=MemgraphIngestor)

    run_fixture_update(fixture_repo, mock_ingestor)
    run_fixture_update(second_repo, second_ingestor)

    fastapi_snapshot = build_mock_graph_snapshot(
        mock_ingestor,
        node_labels={str(label) for label in SEMANTIC_NODE_LABELS},
        relationship_types={str(rel) for rel in SEMANTIC_RELATIONSHIP_TYPES},
    )
    openapi_snapshot = build_mock_graph_snapshot(
        second_ingestor,
        node_labels={str(label) for label in SEMANTIC_NODE_LABELS},
        relationship_types={str(rel) for rel in SEMANTIC_RELATIONSHIP_TYPES},
    )

    assert any(
        rel["relationship_type"] == str(cs.RelationshipType.ACCEPTS_CONTRACT)
        and rel["from"]["value"]
        == "fastapi_semantic_fixture.endpoint.fastapi.POST:/api/invoices"
        for rel in fastapi_snapshot["relationships"]
    )
    assert any(
        rel["relationship_type"] == str(cs.RelationshipType.RETURNS_CONTRACT)
        and rel["from"]["value"]
        == "fastapi_semantic_fixture.endpoint.fastapi.POST:/api/invoices"
        for rel in fastapi_snapshot["relationships"]
    )

    assert any(
        rel["relationship_type"] == str(cs.RelationshipType.ACCEPTS_CONTRACT)
        and rel["from"]["value"]
        == "openapi_contract_surface_fixture.endpoint.openapi.POST:/api/orders"
        and rel["to"]["value"].endswith("CreateOrderRequest")
        for rel in openapi_snapshot["relationships"]
    )
    assert any(
        rel["relationship_type"] == str(cs.RelationshipType.RETURNS_CONTRACT)
        and rel["from"]["value"]
        == "openapi_contract_surface_fixture.endpoint.openapi.POST:/api/orders"
        and rel["to"]["value"].endswith("OrderResponse")
        for rel in openapi_snapshot["relationships"]
    )
