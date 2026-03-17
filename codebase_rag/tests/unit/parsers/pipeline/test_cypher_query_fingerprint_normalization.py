from __future__ import annotations

from codebase_rag.parsers.pipeline.query_fingerprints import build_query_observation


def test_normalizes_cypher_literals_and_extracts_label_targets() -> None:
    observation = build_query_observation(
        symbol_name="read_invoice_graph",
        symbol_kind="function",
        raw_query=(
            "MATCH (i:Invoice)-[:FOR_CUSTOMER]->(c:Customer) "
            "WHERE i.id = 'inv-1' RETURN i, c"
        ),
    )

    assert observation is not None
    assert observation.query_kind == "cypher"
    assert observation.normalized_query == (
        "MATCH (I:INVOICE)-[:FOR_CUSTOMER]->(C:CUSTOMER) WHERE I.ID = ? RETURN I, C"
    )
    assert observation.read_targets == ("Invoice", "Customer")
    assert observation.write_targets == ()
    assert observation.query_intent == "READ"


def test_classifies_cypher_write_queries() -> None:
    observation = build_query_observation(
        symbol_name="create_invoice_graph",
        symbol_kind="function",
        raw_query="CREATE (:Invoice {id: 'inv-1'})",
    )

    assert observation is not None
    assert observation.write_targets == ("Invoice",)
    assert observation.query_intent == "WRITE"
