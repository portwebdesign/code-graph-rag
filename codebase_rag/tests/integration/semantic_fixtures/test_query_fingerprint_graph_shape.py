from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codebase_rag.core import constants as cs
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    QUERY_FINGERPRINT_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    build_mock_graph_snapshot,
    materialize_fixture_repo,
    run_fixture_update,
)

pytestmark = [pytest.mark.integration]

SEMANTIC_NODE_LABELS = {
    cs.NodeLabel.SQL_QUERY,
    cs.NodeLabel.CYPHER_QUERY,
    cs.NodeLabel.QUERY_FINGERPRINT,
    cs.NodeLabel.DATA_STORE,
    cs.NodeLabel.GRAPH_NODE_LABEL,
}
SEMANTIC_RELATIONSHIP_TYPES = {
    cs.RelationshipType.EXECUTES_SQL,
    cs.RelationshipType.EXECUTES_CYPHER,
    cs.RelationshipType.HAS_FINGERPRINT,
    cs.RelationshipType.READS_TABLE,
    cs.RelationshipType.WRITES_TABLE,
    cs.RelationshipType.JOINS_TABLE,
    cs.RelationshipType.READS_LABEL,
    cs.RelationshipType.WRITES_LABEL,
}


def test_query_fingerprint_fixture_snapshot_is_deterministic(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, QUERY_FINGERPRINT_FIXTURE)
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

    node_labels = [node["label"] for node in first_snapshot["nodes"]]
    assert node_labels.count(str(cs.NodeLabel.SQL_QUERY)) >= 2
    assert node_labels.count(str(cs.NodeLabel.CYPHER_QUERY)) >= 2
    assert node_labels.count(str(cs.NodeLabel.QUERY_FINGERPRINT)) >= 4

    relationships = first_snapshot["relationships"]
    assert any(
        rel["relationship_type"] == str(cs.RelationshipType.READS_TABLE)
        and rel["to"]["label"] == str(cs.NodeLabel.DATA_STORE)
        and rel["to"]["value"].endswith(".semantic.sql_table.invoices")
        for rel in relationships
    )
    assert any(
        rel["relationship_type"] == str(cs.RelationshipType.WRITES_LABEL)
        and rel["to"]["label"] == str(cs.NodeLabel.GRAPH_NODE_LABEL)
        and rel["to"]["value"].endswith(".semantic.graph_label.Invoice")
        for rel in relationships
    )
