from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from codebase_rag.core import constants as cs
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    EVENT_FLOW_RUNTIME_FIXTURE,
    TEST_SEMANTICS_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    export_project_semantic_snapshot,
    materialize_fixture_repo,
    run_fixture_update,
)

pytestmark = [pytest.mark.integration]


def test_runtime_event_plane_coexists_with_static_event_graph(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, EVENT_FLOW_RUNTIME_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    snapshot = export_project_semantic_snapshot(
        memgraph_ingestor,
        project_name=fixture_repo.name,
        node_labels=(
            str(cs.NodeLabel.EVENT_FLOW),
            str(cs.NodeLabel.QUEUE),
            str(cs.NodeLabel.RUNTIME_ARTIFACT),
            str(cs.NodeLabel.RUNTIME_EVENT),
        ),
        relationship_types=(
            str(cs.RelationshipType.PUBLISHES_EVENT),
            str(cs.RelationshipType.USES_HANDLER),
            str(cs.RelationshipType.OBSERVED_IN_RUNTIME),
            str(cs.RelationshipType.CONTAINS),
        ),
    )

    labels = {
        label
        for node in snapshot["nodes"]
        for label in cast(list[str], node.get("labels", []))
    }
    relationship_types = {
        str(relationship["relationship_type"])
        for relationship in snapshot["relationships"]
    }

    assert str(cs.NodeLabel.EVENT_FLOW) in labels
    assert str(cs.NodeLabel.RUNTIME_EVENT) in labels
    assert str(cs.RelationshipType.PUBLISHES_EVENT) in relationship_types
    assert str(cs.RelationshipType.OBSERVED_IN_RUNTIME) in relationship_types


def test_runtime_coverage_plane_coexists_with_static_test_graph(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, TEST_SEMANTICS_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    snapshot = export_project_semantic_snapshot(
        memgraph_ingestor,
        project_name=fixture_repo.name,
        node_labels=(
            str(cs.NodeLabel.TEST_SUITE),
            str(cs.NodeLabel.TEST_CASE),
            str(cs.NodeLabel.ENDPOINT),
            str(cs.NodeLabel.RUNTIME_EVENT),
        ),
        relationship_types=(
            str(cs.RelationshipType.TESTS_ENDPOINT),
            str(cs.RelationshipType.ASSERTS_CONTRACT),
            str(cs.RelationshipType.COVERS_MODULE),
        ),
    )

    labels = {
        label
        for node in snapshot["nodes"]
        for label in cast(list[str], node.get("labels", []))
    }
    relationship_types = {
        str(relationship["relationship_type"])
        for relationship in snapshot["relationships"]
    }

    assert str(cs.NodeLabel.TEST_CASE) in labels
    assert str(cs.NodeLabel.RUNTIME_EVENT) in labels
    assert str(cs.RelationshipType.TESTS_ENDPOINT) in relationship_types
    assert str(cs.RelationshipType.COVERS_MODULE) in relationship_types
