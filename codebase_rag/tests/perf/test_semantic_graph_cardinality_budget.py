from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.parsers.pipeline.semantic_guardrails import (
    SEMANTIC_PERFORMANCE_BUDGETS,
)
from codebase_rag.tests.conftest import run_updater
from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    build_mock_graph_snapshot,
)
from codebase_rag.tests.perf.helpers import materialize_semantic_stress_repo


def _dedupe_nodes(
    nodes: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    deduped: dict[tuple[str, str], dict[str, object]] = {}
    for node in nodes:
        key = (
            str(node["label"]),
            str(node["identity_value"]),
        )
        deduped.setdefault(key, node)
    return list(deduped.values())


def _dedupe_relationships(
    relationships: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    deduped: dict[tuple[str, str, str], dict[str, object]] = {}
    for relationship in relationships:
        source = cast(dict[str, object], relationship["from"])
        target = cast(dict[str, object], relationship["to"])
        key = (
            str(relationship["relationship_type"]),
            str(source["value"]),
            str(target["value"]),
        )
        deduped.setdefault(key, relationship)
    return list(deduped.values())


def test_semantic_graph_cardinality_budget(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    fixture_repo = materialize_semantic_stress_repo(temp_repo)
    mock_ingestor.fetch_all.return_value = []

    run_updater(fixture_repo, mock_ingestor)

    snapshot = build_mock_graph_snapshot(
        mock_ingestor,
        node_labels={
            str(cs.NodeLabel.SQL_QUERY),
            str(cs.NodeLabel.CYPHER_QUERY),
            str(cs.NodeLabel.EVENT_FLOW),
            str(cs.NodeLabel.ENV_VAR),
            str(cs.NodeLabel.SIDE_EFFECT),
            str(cs.NodeLabel.QUERY_FINGERPRINT),
        },
        relationship_types={
            str(cs.RelationshipType.EXECUTES_SQL),
            str(cs.RelationshipType.EXECUTES_CYPHER),
            str(cs.RelationshipType.PUBLISHES_EVENT),
            str(cs.RelationshipType.READS_ENV),
            str(cs.RelationshipType.PERFORMS_SIDE_EFFECT),
        },
    )
    nodes = _dedupe_nodes(snapshot["nodes"])
    relationships = _dedupe_relationships(snapshot["relationships"])

    label_counts = Counter(str(node["label"]) for node in nodes)

    assert len(nodes) <= int(SEMANTIC_PERFORMANCE_BUDGETS["stress_fixture_total_nodes"])
    assert len(relationships) <= int(
        SEMANTIC_PERFORMANCE_BUDGETS["stress_fixture_total_relationships"]
    )
    assert label_counts[str(cs.NodeLabel.SQL_QUERY)] <= int(
        SEMANTIC_PERFORMANCE_BUDGETS["stress_fixture_sql_queries"]
    )
    assert label_counts[str(cs.NodeLabel.CYPHER_QUERY)] <= int(
        SEMANTIC_PERFORMANCE_BUDGETS["stress_fixture_cypher_queries"]
    )
    assert label_counts[str(cs.NodeLabel.EVENT_FLOW)] <= int(
        SEMANTIC_PERFORMANCE_BUDGETS["stress_fixture_event_flows"]
    )
    assert label_counts[str(cs.NodeLabel.ENV_VAR)] <= int(
        SEMANTIC_PERFORMANCE_BUDGETS["stress_fixture_env_vars"]
    )
    assert label_counts[str(cs.NodeLabel.SIDE_EFFECT)] <= int(
        SEMANTIC_PERFORMANCE_BUDGETS["stress_fixture_side_effects"]
    )
