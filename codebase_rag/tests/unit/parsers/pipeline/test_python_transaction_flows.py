from __future__ import annotations

from codebase_rag.parsers.pipeline.python_transaction_flows import (
    extract_python_transaction_flows,
)


def test_extract_python_transaction_flows_detects_boundaries_and_side_effects() -> None:
    boundaries, side_effects = extract_python_transaction_flows(
        """from requests import post


class Session:
    def begin(self):
        return self

    def commit(self):
        return None

    def rollback(self):
        return None

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class Outbox:
    def save(self, name: str, payload: dict[str, object]) -> None:
        return None


class Cache:
    def set(self, key: str, value: str) -> None:
        return None


session = Session()
outbox = Outbox()
cache = Cache()


def persist_invoice(db, graph) -> None:
    tx = session.begin()
    db.insert({"id": "inv-1"})
    outbox.save("invoice.created", {"id": "inv-1"})
    graph.execute("CREATE (:Invoice {id: 'inv-1'})")
    tx.commit()


def persist_with_context(db) -> None:
    with session.transaction():
        db.update({"id": "inv-1"})
        cache.set("invoice:inv-1", "cached")
        post("https://example.com/hooks")


def persist_with_rollback(db) -> None:
    tx = session.begin()
    db.delete({"id": "inv-1"})
    tx.rollback()
"""
    )

    boundary_summary = {
        (
            item.symbol_name,
            item.boundary_kind,
            item.has_commit,
            item.has_rollback,
        )
        for item in boundaries
    }
    assert ("persist_invoice", "explicit", True, False) in boundary_summary
    assert ("persist_with_context", "context_manager", True, False) in boundary_summary
    assert ("persist_with_rollback", "explicit", False, True) in boundary_summary

    effect_summary = {
        (
            item.symbol_name,
            item.effect_kind,
            item.operation_name,
            bool(item.boundary_name),
        )
        for item in side_effects
    }
    assert ("persist_invoice", "db_write", "db.insert", True) in effect_summary
    assert ("persist_invoice", "outbox_write", "outbox.save", True) in effect_summary
    assert ("persist_invoice", "graph_write", "graph.execute", True) in effect_summary
    assert ("persist_with_context", "cache_write", "cache.set", True) in effect_summary
    assert (
        "persist_with_context",
        "external_http",
        "requests.post",
        True,
    ) in effect_summary
    assert ("persist_with_rollback", "db_write", "db.delete", True) in effect_summary
