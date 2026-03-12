from codebase_rag.graph_db.cypher_queries import (
    CYPHER_ANALYSIS_DEAD_CODE,
    CYPHER_ANALYSIS_DEAD_CODE_FILTERED,
    CYPHER_ANALYSIS_TOTAL_FUNCTIONS,
    CYPHER_ANALYSIS_TOTAL_FUNCTIONS_FILTERED,
)


def test_dead_code_queries_avoid_exists_subqueries_for_memgraph() -> None:
    queries = (
        CYPHER_ANALYSIS_DEAD_CODE,
        CYPHER_ANALYSIS_DEAD_CODE_FILTERED,
        CYPHER_ANALYSIS_TOTAL_FUNCTIONS,
        CYPHER_ANALYSIS_TOTAL_FUNCTIONS_FILTERED,
    )

    for query in queries:
        assert "EXISTS {" not in query
        assert "size([" not in query
        assert "OPTIONAL MATCH" in query


def test_dead_code_queries_use_optional_match_counts_for_memgraph() -> None:
    assert (
        "count(DISTINCT decorator_src) AS decorator_links" in CYPHER_ANALYSIS_DEAD_CODE
    )
    assert (
        "count(DISTINCT registration_src) AS registration_links"
        in CYPHER_ANALYSIS_DEAD_CODE
    )
    assert "count(DISTINCT caller) AS call_in_degree" in CYPHER_ANALYSIS_DEAD_CODE
    assert "count(DISTINCT callee) AS out_call_count" in CYPHER_ANALYSIS_DEAD_CODE
    assert (
        "count(DISTINCT decorator_src) AS decorator_links"
        in CYPHER_ANALYSIS_DEAD_CODE_FILTERED
    )
    assert (
        "count(DISTINCT registration_src) AS registration_links"
        in CYPHER_ANALYSIS_DEAD_CODE_FILTERED
    )
