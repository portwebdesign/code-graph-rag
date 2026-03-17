from codebase_rag.graph_db.cypher_queries import (
    CYPHER_EVENT_CONSUMER_WITHOUT_DLQ,
    CYPHER_EVENT_DUPLICATE_PUBLISHERS,
    CYPHER_EVENT_OUTBOX_WITHOUT_TRANSACTION,
    CYPHER_EVENT_REPLAY_PATHS,
    build_event_reliability_query_pack,
)


def test_event_reliability_query_pack_is_project_scoped() -> None:
    query_pack = build_event_reliability_query_pack()

    assert len(query_pack) == 5
    for entry in query_pack:
        cypher = entry["cypher"]
        assert "$project_name" in cypher
        assert "project_name: $project_name" in cypher


def test_event_reliability_query_family_uses_expected_relationships() -> None:
    assert "WRITES_OUTBOX" in CYPHER_EVENT_OUTBOX_WITHOUT_TRANSACTION
    assert "WITHIN_TRANSACTION" in CYPHER_EVENT_OUTBOX_WITHOUT_TRANSACTION
    assert "CONSUMES_EVENT" in CYPHER_EVENT_CONSUMER_WITHOUT_DLQ
    assert "WRITES_DLQ" in CYPHER_EVENT_CONSUMER_WITHOUT_DLQ
    assert "REPLAYS_EVENT" in CYPHER_EVENT_REPLAY_PATHS
    assert "USES_HANDLER" in CYPHER_EVENT_REPLAY_PATHS
    assert "USES_QUEUE" in CYPHER_EVENT_REPLAY_PATHS
    assert "PUBLISHES_EVENT" in CYPHER_EVENT_DUPLICATE_PUBLISHERS
