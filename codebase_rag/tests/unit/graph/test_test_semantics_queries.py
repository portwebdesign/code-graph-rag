from codebase_rag.graph_db.cypher_queries import (
    CYPHER_TEST_CONTRACT_COVERAGE,
    CYPHER_TEST_UNTESTED_PUBLIC_ENDPOINTS,
    build_test_semantics_query_pack,
)


def test_test_semantics_query_pack_is_project_scoped() -> None:
    query_pack = build_test_semantics_query_pack()

    assert len(query_pack) == 2
    for entry in query_pack:
        cypher = entry["cypher"]
        assert "$project_name" in cypher
        assert "project_name: $project_name" in cypher


def test_test_semantics_queries_use_expected_relationships() -> None:
    assert "TESTS_ENDPOINT" in CYPHER_TEST_UNTESTED_PUBLIC_ENDPOINTS
    assert "ASSERTS_CONTRACT" in CYPHER_TEST_UNTESTED_PUBLIC_ENDPOINTS
    assert "Contract" in CYPHER_TEST_CONTRACT_COVERAGE
