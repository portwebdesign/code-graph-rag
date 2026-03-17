from codebase_rag.graph_db.cypher_queries import (
    CYPHER_SEMANTIC_ENDPOINT_AUTH_COVERAGE,
    CYPHER_SEMANTIC_ENDPOINT_CONTRACT_GAPS,
    CYPHER_SEMANTIC_ENDPOINT_DEPENDENCY_VISIBILITY,
    CYPHER_SEMANTIC_UNPROTECTED_ENDPOINTS,
    build_semantic_auth_contract_query_pack,
)


def test_semantic_query_pack_is_project_scoped() -> None:
    query_pack = build_semantic_auth_contract_query_pack()

    assert len(query_pack) == 4
    for entry in query_pack:
        cypher = entry["cypher"]
        assert "$project_name" in cypher
        assert "project_name: $project_name" in cypher


def test_semantic_auth_query_family_uses_expected_relationships() -> None:
    assert "SECURED_BY" in CYPHER_SEMANTIC_ENDPOINT_AUTH_COVERAGE
    assert "REQUIRES_SCOPE" in CYPHER_SEMANTIC_ENDPOINT_AUTH_COVERAGE
    assert "USES_DEPENDENCY" in CYPHER_SEMANTIC_ENDPOINT_DEPENDENCY_VISIBILITY
    assert "ACCEPTS_CONTRACT" in CYPHER_SEMANTIC_ENDPOINT_CONTRACT_GAPS
    assert "RETURNS_CONTRACT" in CYPHER_SEMANTIC_ENDPOINT_CONTRACT_GAPS
    assert "REQUESTS_ENDPOINT" in CYPHER_SEMANTIC_ENDPOINT_CONTRACT_GAPS
    assert "SECURED_BY" in CYPHER_SEMANTIC_UNPROTECTED_ENDPOINTS
