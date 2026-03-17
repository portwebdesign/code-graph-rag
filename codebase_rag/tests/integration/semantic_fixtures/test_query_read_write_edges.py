from __future__ import annotations

from pathlib import Path

import pytest

from codebase_rag.tests.integration.semantic_fixtures.fixtures import (
    QUERY_FINGERPRINT_FIXTURE,
)
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    execute_project_cypher,
    materialize_fixture_repo,
    run_fixture_update,
)

pytestmark = [pytest.mark.integration]


def test_query_read_write_edges_smoke(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    fixture_repo = materialize_fixture_repo(temp_repo, QUERY_FINGERPRINT_FIXTURE)
    run_fixture_update(fixture_repo, memgraph_ingestor)

    table_rows = execute_project_cypher(
        memgraph_ingestor,
        """
MATCH (query {project_name: $project_name})-[rel:READS_TABLE|WRITES_TABLE|JOINS_TABLE]->(store {project_name: $project_name})
RETURN labels(query)[0] AS query_label,
       type(rel) AS rel_type,
       store.name AS store_name
ORDER BY rel_type, store_name
""",
        project_name=fixture_repo.name,
    )
    assert any(
        row["rel_type"] == "READS_TABLE" and row["store_name"] == "invoices"
        for row in table_rows
    )
    assert any(
        row["rel_type"] == "WRITES_TABLE" and row["store_name"] == "invoices"
        for row in table_rows
    )
    assert any(
        row["rel_type"] == "JOINS_TABLE" and row["store_name"] == "customers"
        for row in table_rows
    )

    label_rows = execute_project_cypher(
        memgraph_ingestor,
        """
MATCH (query {project_name: $project_name})-[rel:READS_LABEL|WRITES_LABEL]->(label {project_name: $project_name})
RETURN labels(query)[0] AS query_label,
       type(rel) AS rel_type,
       label.name AS label_name
ORDER BY rel_type, label_name
""",
        project_name=fixture_repo.name,
    )
    assert any(
        row["rel_type"] == "READS_LABEL" and row["label_name"] == "Invoice"
        for row in label_rows
    )
    assert any(
        row["rel_type"] == "WRITES_LABEL" and row["label_name"] == "Invoice"
        for row in label_rows
    )
