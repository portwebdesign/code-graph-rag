from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    ENV_FLAG_SECRET_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    build_mock_graph_snapshot,
    materialize_fixture_repo,
    run_fixture_update,
)

SEMANTIC_NODE_LABELS = {
    cs.NodeLabel.ENV_VAR,
    cs.NodeLabel.FEATURE_FLAG,
    cs.NodeLabel.SECRET_REF,
    cs.NodeLabel.INFRA_RESOURCE,
}
SEMANTIC_RELATIONSHIP_TYPES = {
    cs.RelationshipType.READS_ENV,
    cs.RelationshipType.GATES_CODE_PATH,
    cs.RelationshipType.USES_SECRET,
    cs.RelationshipType.SETS_ENV,
}


def test_env_flag_secret_fixture_snapshot_is_deterministic(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, ENV_FLAG_SECRET_FIXTURE)
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

    env_names = {
        str(node["identity_value"])
        for node in first_snapshot["nodes"]
        if node["label"] == str(cs.NodeLabel.ENV_VAR)
    }
    assert "env_flag_secret_semantic_fixture.semantic.env_var.APP_SECRET" in env_names
    assert (
        "env_flag_secret_semantic_fixture.semantic.env_var.FEATURE_BILLING" in env_names
    )
    assert (
        "env_flag_secret_semantic_fixture.semantic.env_var.MISSING_ANALYTICS_KEY"
        in env_names
    )

    feature_flag_names = {
        str(node["identity_value"])
        for node in first_snapshot["nodes"]
        if node["label"] == str(cs.NodeLabel.FEATURE_FLAG)
    }
    assert (
        "env_flag_secret_semantic_fixture.semantic.feature_flag.FEATURE_BILLING"
        in feature_flag_names
    )

    secret_names = {
        str(node["identity_value"])
        for node in first_snapshot["nodes"]
        if node["label"] == str(cs.NodeLabel.SECRET_REF)
    }
    assert (
        "env_flag_secret_semantic_fixture.semantic.secret_ref.APP_SECRET"
        in secret_names
    )

    relationship_types = {
        str(rel["relationship_type"]) for rel in first_snapshot["relationships"]
    }
    assert str(cs.RelationshipType.READS_ENV) in relationship_types
    assert str(cs.RelationshipType.GATES_CODE_PATH) in relationship_types
    assert str(cs.RelationshipType.USES_SECRET) in relationship_types
    assert str(cs.RelationshipType.SETS_ENV) in relationship_types

    node_props = [
        node["props"]
        for node in first_snapshot["nodes"]
        if node["label"] == str(cs.NodeLabel.SECRET_REF)
    ]
    assert all("super-secret" not in str(props) for props in node_props)

    infra_resources = {
        str(node["identity_value"])
        for node in first_snapshot["nodes"]
        if node["label"] == str(cs.NodeLabel.INFRA_RESOURCE)
    }
    assert any("compose_service.api" in identity for identity in infra_resources)
    assert any("k8s.deployment.api" in identity for identity in infra_resources)
