from pathlib import Path

import pytest

from codebase_rag.infrastructure.parser_loader import get_parser_and_language
from codebase_rag.parsers.query.query_engine import QueryEngine
from codebase_rag.parsers.query.query_engine_adapter import _QUERY_NAME_MAP


@pytest.fixture
def query_engine():
    repo_root = Path(__file__).parent.parent
    return QueryEngine(repo_root / "codebase_rag" / "parsers" / "queries")


def test_query_map_integrity(query_engine):
    """
    Verify that all queries defined in _QUERY_NAME_MAP exist in the corresponding .scm files.
    """
    missing_queries = []

    for language, mapping in _QUERY_NAME_MAP.items():
        parser, lang_obj = get_parser_and_language(language)
        if not parser or not lang_obj:
            continue
        try:
            available_queries = query_engine.load_queries(language)
        except Exception as e:
            missing_queries.append(f"Failed to load queries for {language}: {e}")
            continue

        available_query_names = set(available_queries.keys())

        for category, query_names in mapping.items():
            if isinstance(query_names, str):
                query_names = [query_names]

            for query_name in query_names:
                if query_name not in available_query_names:
                    missing_queries.append(
                        f"Language '{language}', Category '{category}': "
                        f"Query '{query_name}' not found in .scm file. "
                        f"Available: {sorted(list(available_query_names))}"
                    )

    assert not missing_queries, "\n".join(missing_queries)
