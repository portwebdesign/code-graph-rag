from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.parsers.pipeline.semantic_guardrails import (
    SEMANTIC_GUARDRAIL_LIMITS,
)
from codebase_rag.tests.conftest import get_nodes, get_qualified_names, run_updater


def test_query_fingerprint_dedup_prevents_explosion(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "query_fingerprint_explosion_fixture"
    project.mkdir(parents=True, exist_ok=True)
    repeated_sql = "\n".join(
        f'    session.execute("SELECT * FROM invoices WHERE id = {index}")'
        for index in range(80)
    )
    repeated_cypher = "\n".join(
        f'    graph.run("MATCH (n:Invoice) WHERE n.id = {index} RETURN n")'
        for index in range(80)
    )
    (project / "queries.py").write_text(
        f"""def run_sql_queries(session) -> None:
{repeated_sql}


def run_cypher_queries(graph) -> None:
{repeated_cypher}
""",
        encoding="utf-8",
    )
    mock_ingestor.fetch_all.return_value = []

    run_updater(project, mock_ingestor)

    sql_queries = get_nodes(mock_ingestor, cs.NodeLabel.SQL_QUERY)
    cypher_queries = get_nodes(mock_ingestor, cs.NodeLabel.CYPHER_QUERY)
    fingerprint_nodes = get_nodes(mock_ingestor, cs.NodeLabel.QUERY_FINGERPRINT)
    unique_sql_queries = get_qualified_names(sql_queries)
    unique_cypher_queries = get_qualified_names(cypher_queries)
    unique_fingerprints = get_qualified_names(fingerprint_nodes)

    assert (
        len(unique_sql_queries)
        <= SEMANTIC_GUARDRAIL_LIMITS["query_observations_per_symbol"]
    )
    assert (
        len(unique_cypher_queries)
        <= SEMANTIC_GUARDRAIL_LIMITS["query_observations_per_symbol"]
    )
    assert len(unique_fingerprints) == 2
