from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    FASTAPI_AUTH_CONTRACT_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    build_mock_graph_snapshot,
    materialize_fixture_repo,
    run_fixture_update,
)

SEMANTIC_NODE_LABELS = {
    cs.NodeLabel.ENDPOINT,
    cs.NodeLabel.DEPENDENCY_PROVIDER,
    cs.NodeLabel.AUTH_POLICY,
    cs.NodeLabel.AUTH_SCOPE,
    cs.NodeLabel.CONTRACT,
    cs.NodeLabel.CONTRACT_FIELD,
}
SEMANTIC_RELATIONSHIP_TYPES = {
    cs.RelationshipType.USES_DEPENDENCY,
    cs.RelationshipType.SECURED_BY,
    cs.RelationshipType.REQUIRES_SCOPE,
    cs.RelationshipType.ACCEPTS_CONTRACT,
    cs.RelationshipType.RETURNS_CONTRACT,
    cs.RelationshipType.DECLARES_FIELD,
}


def test_fastapi_semantic_fixture_snapshot_is_deterministic(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, FASTAPI_AUTH_CONTRACT_FIXTURE)
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
    assert {
        str(cs.NodeLabel.ENDPOINT),
        str(cs.NodeLabel.DEPENDENCY_PROVIDER),
        str(cs.NodeLabel.AUTH_POLICY),
        str(cs.NodeLabel.AUTH_SCOPE),
        str(cs.NodeLabel.CONTRACT),
        str(cs.NodeLabel.CONTRACT_FIELD),
    }.issubset(node_labels)

    endpoint_identities = {
        str(node["identity_value"])
        for node in first_snapshot["nodes"]
        if node["label"] == str(cs.NodeLabel.ENDPOINT)
    }
    assert (
        "fastapi_semantic_fixture.endpoint.fastapi.GET:/api/health"
        in endpoint_identities
    )
    assert (
        "fastapi_semantic_fixture.endpoint.fastapi.POST:/api/invoices"
        in endpoint_identities
    )

    relationship_types = {
        str(rel["relationship_type"])
        for rel in first_snapshot["relationships"]
        if "relationship_type" in rel
    }
    assert {
        str(cs.RelationshipType.USES_DEPENDENCY),
        str(cs.RelationshipType.SECURED_BY),
        str(cs.RelationshipType.REQUIRES_SCOPE),
        str(cs.RelationshipType.ACCEPTS_CONTRACT),
        str(cs.RelationshipType.RETURNS_CONTRACT),
        str(cs.RelationshipType.DECLARES_FIELD),
    }.issubset(relationship_types)
