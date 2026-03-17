from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codebase_rag.core import constants as cs
from codebase_rag.infrastructure.parser_loader import load_parsers
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    ENV_FLAG_SECRET_FIXTURE,
    EVENT_FLOW_FIXTURE,
    FASTAPI_AUTH_CONTRACT_FIXTURE,
    FRONTEND_OPERATION_FIXTURE,
    QUERY_FINGERPRINT_FIXTURE,
    TEST_SEMANTICS_FIXTURE,
    TRANSACTION_FLOW_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    build_mock_graph_snapshot,
    materialize_fixture_repo,
    run_fixture_update,
)

pytestmark = [pytest.mark.integration]


def _require_typescript_parser() -> None:
    parsers, _queries = load_parsers()
    if "typescript" not in parsers:
        pytest.skip("typescript parser not available")


@pytest.mark.parametrize(
    ("fixture_spec", "required_labels", "required_relationships"),
    [
        (
            FASTAPI_AUTH_CONTRACT_FIXTURE,
            {
                cs.NodeLabel.DEPENDENCY_PROVIDER,
                cs.NodeLabel.AUTH_POLICY,
                cs.NodeLabel.CONTRACT,
            },
            {
                cs.RelationshipType.USES_DEPENDENCY,
                cs.RelationshipType.SECURED_BY,
                cs.RelationshipType.ACCEPTS_CONTRACT,
                cs.RelationshipType.RETURNS_CONTRACT,
            },
        ),
        (
            EVENT_FLOW_FIXTURE,
            {cs.NodeLabel.EVENT_FLOW, cs.NodeLabel.QUEUE},
            {
                cs.RelationshipType.WRITES_OUTBOX,
                cs.RelationshipType.PUBLISHES_EVENT,
                cs.RelationshipType.USES_HANDLER,
                cs.RelationshipType.REPLAYS_EVENT,
            },
        ),
        (
            TRANSACTION_FLOW_FIXTURE,
            {cs.NodeLabel.TRANSACTION_BOUNDARY, cs.NodeLabel.SIDE_EFFECT},
            {
                cs.RelationshipType.BEGINS_TRANSACTION,
                cs.RelationshipType.COMMITS_TRANSACTION,
                cs.RelationshipType.PERFORMS_SIDE_EFFECT,
                cs.RelationshipType.WITHIN_TRANSACTION,
            },
        ),
        (
            QUERY_FINGERPRINT_FIXTURE,
            {
                cs.NodeLabel.SQL_QUERY,
                cs.NodeLabel.CYPHER_QUERY,
                cs.NodeLabel.QUERY_FINGERPRINT,
            },
            {
                cs.RelationshipType.EXECUTES_SQL,
                cs.RelationshipType.EXECUTES_CYPHER,
                cs.RelationshipType.HAS_FINGERPRINT,
                cs.RelationshipType.READS_TABLE,
                cs.RelationshipType.WRITES_LABEL,
            },
        ),
        (
            FRONTEND_OPERATION_FIXTURE,
            {cs.NodeLabel.CLIENT_OPERATION, cs.NodeLabel.ENDPOINT},
            {
                cs.RelationshipType.USES_OPERATION,
                cs.RelationshipType.REQUESTS_ENDPOINT,
                cs.RelationshipType.BYPASSES_MANIFEST,
            },
        ),
        (
            TEST_SEMANTICS_FIXTURE,
            {
                cs.NodeLabel.TEST_SUITE,
                cs.NodeLabel.TEST_CASE,
                cs.NodeLabel.RUNTIME_EVENT,
            },
            {
                cs.RelationshipType.TESTS_SYMBOL,
                cs.RelationshipType.TESTS_ENDPOINT,
                cs.RelationshipType.ASSERTS_CONTRACT,
                cs.RelationshipType.COVERS_MODULE,
            },
        ),
        (
            ENV_FLAG_SECRET_FIXTURE,
            {cs.NodeLabel.ENV_VAR, cs.NodeLabel.FEATURE_FLAG, cs.NodeLabel.SECRET_REF},
            {
                cs.RelationshipType.READS_ENV,
                cs.RelationshipType.SETS_ENV,
                cs.RelationshipType.USES_SECRET,
                cs.RelationshipType.GATES_CODE_PATH,
            },
        ),
    ],
    ids=lambda item: getattr(item, "name", str(item)),
)
def test_full_semantic_reparse_smoke(
    temp_repo: Path,
    mock_ingestor: MagicMock,
    fixture_spec: object,
    required_labels: set[object],
    required_relationships: set[object],
) -> None:
    fixture_name = str(getattr(fixture_spec, "name", ""))
    if fixture_name == FRONTEND_OPERATION_FIXTURE.name:
        _require_typescript_parser()

    fixture_repo = materialize_fixture_repo(temp_repo, fixture_spec)
    mock_ingestor.fetch_all.return_value = []

    run_fixture_update(fixture_repo, mock_ingestor)
    snapshot = build_mock_graph_snapshot(
        mock_ingestor,
        node_labels={str(label) for label in required_labels},
        relationship_types={str(rel) for rel in required_relationships},
    )

    observed_labels = {str(node["label"]) for node in snapshot["nodes"]}
    observed_relationships = {
        str(relationship["relationship_type"])
        for relationship in snapshot["relationships"]
    }

    assert {str(label) for label in required_labels} <= observed_labels
    assert {str(rel) for rel in required_relationships} <= observed_relationships
