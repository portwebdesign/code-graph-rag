from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codebase_rag.core import constants as cs
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    FRONTEND_OPERATION_FIXTURE,
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


def test_client_operation_fixture_snapshot_is_deterministic(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, FRONTEND_OPERATION_FIXTURE)
    second_ingestor = MagicMock(spec=MemgraphIngestor)

    run_fixture_update(fixture_repo, mock_ingestor)
    first_snapshot = build_mock_graph_snapshot(
        mock_ingestor,
        node_labels={
            str(cs.NodeLabel.CLIENT_OPERATION),
            str(cs.NodeLabel.ENDPOINT),
            str(cs.NodeLabel.FUNCTION),
        },
        relationship_types={
            str(cs.RelationshipType.USES_OPERATION),
            str(cs.RelationshipType.REQUESTS_ENDPOINT),
            str(cs.RelationshipType.GENERATED_FROM_SPEC),
            str(cs.RelationshipType.BYPASSES_MANIFEST),
        },
    )

    run_fixture_update(fixture_repo, second_ingestor)
    second_snapshot = build_mock_graph_snapshot(
        second_ingestor,
        node_labels={
            str(cs.NodeLabel.CLIENT_OPERATION),
            str(cs.NodeLabel.ENDPOINT),
            str(cs.NodeLabel.FUNCTION),
        },
        relationship_types={
            str(cs.RelationshipType.USES_OPERATION),
            str(cs.RelationshipType.REQUESTS_ENDPOINT),
            str(cs.RelationshipType.GENERATED_FROM_SPEC),
            str(cs.RelationshipType.BYPASSES_MANIFEST),
        },
    )

    assert first_snapshot == second_snapshot
    assert any(
        node["label"] == str(cs.NodeLabel.CLIENT_OPERATION)
        and node["props"].get("operation_id") == "listCustomers"
        and node["props"].get("governance_kind") == "generated"
        for node in first_snapshot["nodes"]
    )
    assert any(
        node["label"] == str(cs.NodeLabel.CLIENT_OPERATION)
        and node["props"].get("operation_id") == "createOrder"
        and node["props"].get("governance_kind") == "bypass"
        for node in first_snapshot["nodes"]
    )
    assert any(
        rel["relationship_type"] == str(cs.RelationshipType.GENERATED_FROM_SPEC)
        for rel in first_snapshot["relationships"]
    )
    assert any(
        rel["relationship_type"] == str(cs.RelationshipType.BYPASSES_MANIFEST)
        for rel in first_snapshot["relationships"]
    )
    assert any(
        rel["relationship_type"] == str(cs.RelationshipType.REQUESTS_ENDPOINT)
        and rel["from"]["value"]
        == "frontend_operation_semantic_fixture.src.lib.generated.client.listCustomers"
        and rel["to"]["value"]
        == "frontend_operation_semantic_fixture.endpoint.http.GET:/api/customers"
        and rel["props"].get("evidence_kind") == "source_symbol_request_shortcut"
        for rel in first_snapshot["relationships"]
    )
