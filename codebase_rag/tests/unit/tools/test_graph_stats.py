from __future__ import annotations

from unittest.mock import MagicMock

from codebase_rag.tools.graph_stats import get_dependency_stats, get_graph_stats


def test_get_graph_stats_reads_expected_queries() -> None:
    ingestor = MagicMock()
    ingestor.fetch_all.side_effect = [
        [{"count": 10}],
        [{"count": 20}],
        [{"label": "Function", "count": 5}],
        [{"type": "CALLS", "count": 3}],
    ]

    result = get_graph_stats(ingestor)

    assert result == {
        "nodes": 10,
        "relationships": 20,
        "labels": [{"label": "Function", "count": 5}],
        "relationship_types": [{"type": "CALLS", "count": 3}],
    }


def test_get_dependency_stats_reads_expected_queries() -> None:
    ingestor = MagicMock()
    ingestor.fetch_all.side_effect = [
        [{"count": 7}],
        [{"module": "mod1", "count": 4}],
        [{"target": "lib1", "count": 2}],
    ]

    result = get_dependency_stats(ingestor)

    assert result == {
        "total_imports": 7,
        "top_importers": [{"module": "mod1", "count": 4}],
        "top_dependents": [{"target": "lib1", "count": 2}],
    }
