from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codebase_rag.core import constants as cs
from codebase_rag.mcp.tools import MCPToolsRegistry
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    TEST_SEMANTICS_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    build_mock_graph_snapshot,
    export_project_semantic_snapshot,
    materialize_fixture_repo,
    run_fixture_update,
)

pytestmark = [pytest.mark.integration]


SEMANTIC_NODE_LABELS = {
    cs.NodeLabel.ENDPOINT,
    cs.NodeLabel.CONTRACT,
    cs.NodeLabel.TEST_SUITE,
    cs.NodeLabel.TEST_CASE,
    cs.NodeLabel.RUNTIME_EVENT,
}
SEMANTIC_RELATIONSHIP_TYPES = {
    cs.RelationshipType.TESTS_SYMBOL,
    cs.RelationshipType.TESTS_ENDPOINT,
    cs.RelationshipType.ASSERTS_CONTRACT,
    cs.RelationshipType.COVERS_MODULE,
}


def test_static_test_graph_and_runtime_coverage_snapshot_coexist(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, TEST_SEMANTICS_FIXTURE)

    run_fixture_update(fixture_repo, mock_ingestor)
    snapshot = build_mock_graph_snapshot(
        mock_ingestor,
        node_labels={str(label) for label in SEMANTIC_NODE_LABELS},
        relationship_types={str(rel) for rel in SEMANTIC_RELATIONSHIP_TYPES},
    )

    node_labels = {str(node["label"]) for node in snapshot["nodes"]}
    assert str(cs.NodeLabel.TEST_SUITE) in node_labels
    assert str(cs.NodeLabel.TEST_CASE) in node_labels
    assert str(cs.NodeLabel.RUNTIME_EVENT) in node_labels

    relationship_types = {
        str(relationship["relationship_type"])
        for relationship in snapshot["relationships"]
    }
    assert str(cs.RelationshipType.TESTS_ENDPOINT) in relationship_types
    assert str(cs.RelationshipType.ASSERTS_CONTRACT) in relationship_types
    assert str(cs.RelationshipType.COVERS_MODULE) in relationship_types


@pytest.mark.anyio
async def test_test_bundle_surfaces_semantic_selection_context(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, TEST_SEMANTICS_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    registry = MCPToolsRegistry(
        project_root=str(fixture_repo),
        ingestor=memgraph_ingestor,
        cypher_gen=MagicMock(),
    )
    registry._session_state["preflight_project_selected"] = True
    registry._session_state["preflight_schema_summary_loaded"] = True
    registry._session_state["last_multi_hop_bundle"] = {
        "affected_files": ["app.py"],
        "affected_symbols": [
            f"{fixture_repo.name}.endpoint.fastapi.POST:/api/orders",
            f"{fixture_repo.name}.semantic.contract.{fixture_repo.name}.app.OrderResponse",
        ],
    }

    bundle = await registry.test_bundle(goal="Review order API test coverage")

    assert bundle.get("status") != "error"
    test_selection = bundle.get("test_selection", {})
    assert isinstance(test_selection, dict)
    selection_mode = str(test_selection.get("selection_mode", ""))
    assert selection_mode == "semantic-graph-primary"

    semantic_candidates = test_selection.get("semantic_candidate_testcases", [])
    assert isinstance(semantic_candidates, list)
    assert any(
        str(item.get("test_file", "")).endswith("tests/test_orders.py")
        for item in semantic_candidates
        if isinstance(item, dict)
    )

    runtime_matches = test_selection.get("runtime_coverage_matches", [])
    assert isinstance(runtime_matches, list)
    assert any(
        str(item.get("file_path", "")) == "app.py"
        for item in runtime_matches
        if isinstance(item, dict)
    )

    exported = export_project_semantic_snapshot(
        memgraph_ingestor,
        project_name=fixture_repo.name,
        node_labels=(
            str(cs.NodeLabel.TEST_CASE),
            str(cs.NodeLabel.RUNTIME_EVENT),
        ),
        relationship_types=(
            str(cs.RelationshipType.TESTS_ENDPOINT),
            str(cs.RelationshipType.COVERS_MODULE),
        ),
    )
    assert exported["nodes"]
    assert exported["relationships"]
