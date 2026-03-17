from codebase_rag.graph_db.cypher_queries import (
    CYPHER_FRONTEND_BYPASSES_MANIFEST,
    CYPHER_FRONTEND_CLIENT_OPERATIONS,
    build_frontend_operation_query_pack,
)


def test_frontend_operation_query_pack_is_project_scoped() -> None:
    query_pack = build_frontend_operation_query_pack()

    assert len(query_pack) == 2
    for entry in query_pack:
        cypher = entry["cypher"]
        assert "$project_name" in cypher
        assert "project_name: $project_name" in cypher


def test_frontend_operation_queries_use_expected_relationships() -> None:
    assert "REQUESTS_ENDPOINT" in CYPHER_FRONTEND_CLIENT_OPERATIONS
    assert "ClientOperation" in CYPHER_FRONTEND_CLIENT_OPERATIONS
    assert "BYPASSES_MANIFEST" in CYPHER_FRONTEND_BYPASSES_MANIFEST
