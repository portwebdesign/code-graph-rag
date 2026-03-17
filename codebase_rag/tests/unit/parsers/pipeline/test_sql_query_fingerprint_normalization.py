from __future__ import annotations

from codebase_rag.parsers.pipeline.query_fingerprints import build_query_observation


def test_normalizes_sql_literals_and_extracts_read_write_targets() -> None:
    observation = build_query_observation(
        symbol_name="load_customer_invoice",
        symbol_kind="function",
        raw_query="""
        SELECT invoices.id, customers.id
        FROM invoices
        JOIN customers ON customers.id = invoices.customer_id
        WHERE customers.id = 42 AND invoices.status = 'paid'
        """,
    )

    assert observation is not None
    assert observation.query_kind == "sql"
    assert observation.normalized_query == (
        "SELECT INVOICES.ID, CUSTOMERS.ID FROM INVOICES "
        "JOIN CUSTOMERS ON CUSTOMERS.ID = INVOICES.CUSTOMER_ID "
        "WHERE CUSTOMERS.ID = ? AND INVOICES.STATUS = ?"
    )
    assert observation.read_targets == ("invoices", "customers")
    assert observation.write_targets == ()
    assert observation.join_targets == ("customers",)
    assert len(observation.fingerprint) == 16


def test_classifies_sql_write_queries() -> None:
    observation = build_query_observation(
        symbol_name="mark_invoice_paid",
        symbol_kind="function",
        raw_query="UPDATE invoices SET status = 'paid' WHERE id = 42",
    )

    assert observation is not None
    assert observation.query_intent == "WRITE"
    assert observation.write_targets == ("invoices",)
