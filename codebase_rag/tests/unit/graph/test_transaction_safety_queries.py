from codebase_rag.graph_db.cypher_queries import (
    CYPHER_TRANSACTION_EXTERNAL_CALL_BEFORE_COMMIT,
    build_event_reliability_query_pack,
)


def test_transaction_safety_query_family_uses_expected_relationships() -> None:
    assert "COMMITS_TRANSACTION" in CYPHER_TRANSACTION_EXTERNAL_CALL_BEFORE_COMMIT
    assert "PERFORMS_SIDE_EFFECT" in CYPHER_TRANSACTION_EXTERNAL_CALL_BEFORE_COMMIT
    assert "WITHIN_TRANSACTION" in CYPHER_TRANSACTION_EXTERNAL_CALL_BEFORE_COMMIT
    assert "external_http" in CYPHER_TRANSACTION_EXTERNAL_CALL_BEFORE_COMMIT


def test_transaction_safety_query_is_present_in_event_reliability_pack() -> None:
    query_pack = build_event_reliability_query_pack()
    query_names = {entry["name"] for entry in query_pack}

    assert "external_call_before_commit" in query_names
