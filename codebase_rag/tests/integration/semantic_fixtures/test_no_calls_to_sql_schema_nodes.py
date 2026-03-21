from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    SemanticFixtureSpec,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    build_mock_graph_snapshot,
    materialize_fixture_repo,
    run_fixture_update,
)

SQL_CALL_GUARD_FIXTURE = SemanticFixtureSpec(
    name="sql_call_guard_fixture",
    files={
        "src/handlers.py": """def handler() -> None:\n    users()\n""",
        "migrations/001_init.sql": """CREATE TABLE users (id INTEGER PRIMARY KEY);\n""",
    },
)


def test_sql_migration_symbols_do_not_receive_calls_edges(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, SQL_CALL_GUARD_FIXTURE)
    second_ingestor = MagicMock(spec=MemgraphIngestor)

    run_fixture_update(fixture_repo, mock_ingestor)
    first_snapshot = build_mock_graph_snapshot(
        mock_ingestor,
        node_labels={str(cs.NodeLabel.CLASS), str(cs.NodeLabel.FUNCTION)},
        relationship_types={str(cs.RelationshipType.CALLS)},
    )

    run_fixture_update(fixture_repo, second_ingestor)
    second_snapshot = build_mock_graph_snapshot(
        second_ingestor,
        node_labels={str(cs.NodeLabel.CLASS), str(cs.NodeLabel.FUNCTION)},
        relationship_types={str(cs.RelationshipType.CALLS)},
    )

    assert first_snapshot == second_snapshot

    class_targets = [
        rel
        for rel in first_snapshot["relationships"]
        if rel["relationship_type"] == str(cs.RelationshipType.CALLS)
        and rel["to"]["label"] == str(cs.NodeLabel.CLASS)
    ]
    assert class_targets == []
