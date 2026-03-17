from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codebase_rag.core import constants as cs
from codebase_rag.infrastructure.parser_loader import load_parsers
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    FRONTEND_CONTRACT_FIXTURE,
    OPENAPI_CONTRACT_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    build_mock_graph_snapshot,
    materialize_fixture_repo,
    run_fixture_update,
)

SEMANTIC_NODE_LABELS = {
    cs.NodeLabel.CONTRACT,
    cs.NodeLabel.CONTRACT_FIELD,
    cs.NodeLabel.FUNCTION,
}
SEMANTIC_RELATIONSHIP_TYPES = {
    cs.RelationshipType.ACCEPTS_CONTRACT,
    cs.RelationshipType.RETURNS_CONTRACT,
    cs.RelationshipType.DECLARES_FIELD,
}


def _require_typescript_parser() -> None:
    parsers, _queries = load_parsers()
    if "typescript" not in parsers:
        pytest.skip("typescript parser not available")


def test_contract_field_graph_shape_covers_frontend_and_openapi_sources(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    _require_typescript_parser()
    frontend_repo = materialize_fixture_repo(temp_repo, FRONTEND_CONTRACT_FIXTURE)
    openapi_repo = materialize_fixture_repo(temp_repo, OPENAPI_CONTRACT_FIXTURE)
    openapi_ingestor = MagicMock(spec=MemgraphIngestor)

    run_fixture_update(frontend_repo, mock_ingestor)
    run_fixture_update(openapi_repo, openapi_ingestor)

    frontend_snapshot = build_mock_graph_snapshot(
        mock_ingestor,
        node_labels={str(label) for label in SEMANTIC_NODE_LABELS},
        relationship_types={str(rel) for rel in SEMANTIC_RELATIONSHIP_TYPES},
    )
    openapi_snapshot = build_mock_graph_snapshot(
        openapi_ingestor,
        node_labels={str(label) for label in SEMANTIC_NODE_LABELS},
        relationship_types={str(rel) for rel in SEMANTIC_RELATIONSHIP_TYPES},
    )

    frontend_contract_nodes = {
        node["props"]["name"]: node["props"]
        for node in frontend_snapshot["nodes"]
        if node["label"] == str(cs.NodeLabel.CONTRACT)
    }
    assert (
        frontend_contract_nodes["Customer"]["contract_kind"] == "typescript_interface"
    )
    assert (
        frontend_contract_nodes["CreateOrderRequest"]["contract_kind"]
        == "typescript_type_alias"
    )
    assert frontend_contract_nodes["OrderResponseSchema"]["contract_kind"] == "zod"

    frontend_field_nodes = {
        node["props"]["name"]: node["props"]
        for node in frontend_snapshot["nodes"]
        if node["label"] == str(cs.NodeLabel.CONTRACT_FIELD)
    }
    assert frontend_field_nodes["loyaltyPoints"]["required"] is False
    assert frontend_field_nodes["warnings"]["field_type"] == "string[]"

    frontend_edges = frontend_snapshot["relationships"]
    assert any(
        rel["relationship_type"] == str(cs.RelationshipType.ACCEPTS_CONTRACT)
        and rel["from"]["value"]
        == "frontend_contract_semantic_fixture.src.lib.raw.orders.createOrder"
        and rel["to"]["value"].endswith("CreateOrderRequest")
        for rel in frontend_edges
    )
    assert any(
        rel["relationship_type"] == str(cs.RelationshipType.RETURNS_CONTRACT)
        and rel["from"]["value"]
        == "frontend_contract_semantic_fixture.src.lib.generated.client.listCustomers"
        and rel["to"]["value"].endswith("Customer")
        for rel in frontend_edges
    )

    openapi_field_nodes = {
        node["props"]["name"]: node["props"]
        for node in openapi_snapshot["nodes"]
        if node["label"] == str(cs.NodeLabel.CONTRACT_FIELD)
    }
    assert openapi_field_nodes["items"]["field_type"] == "Customer[]"
