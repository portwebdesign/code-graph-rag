from __future__ import annotations

from codebase_rag.graph_db.cypher_queries import build_semantic_validation_query_pack


def test_validation_query_pack_stays_backward_compatible() -> None:
    query_pack = build_semantic_validation_query_pack()

    names = [str(entry["name"]) for entry in query_pack]
    assert names == [
        "fastapi_auth_contract_minimum",
        "event_flow_minimum",
        "transaction_flow_minimum",
        "query_fingerprint_minimum",
        "frontend_operation_minimum",
        "test_semantics_minimum",
        "config_control_plane_minimum",
    ]

    for entry in query_pack:
        cypher = str(entry["cypher"])
        assert "$project_name" in cypher
        assert "project_name: $project_name" in cypher
        assert "matched_rows" in cypher
        assert int(entry["minimum_rows"]) >= 1
        assert str(entry["fixture_name"]).strip()
