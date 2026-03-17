from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.tests.conftest import get_nodes, get_relationships, run_updater


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _node_props(mock_ingestor: MagicMock, node_type: str) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], call[0][1])
        for call in get_nodes(mock_ingestor, node_type)
    ]


def test_materializes_transaction_boundaries_side_effects_and_ordering(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "transaction_flow_semantics"
    project.mkdir()

    _write(
        project / "main.py",
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
""",
    )

    run_updater(project, mock_ingestor)

    boundary_nodes = _node_props(mock_ingestor, cs.NodeLabel.TRANSACTION_BOUNDARY)
    assert any(
        props.get("symbol_qn") == "persist_invoice"
        and props.get("boundary_kind") == "explicit"
        and props.get("has_commit") is True
        for props in boundary_nodes
    )
    assert any(
        props.get("symbol_qn") == "persist_with_context"
        and props.get("boundary_kind") == "context_manager"
        and props.get("has_commit") is True
        for props in boundary_nodes
    )
    assert any(
        props.get("symbol_qn") == "persist_with_rollback"
        and props.get("has_rollback") is True
        for props in boundary_nodes
    )

    side_effect_nodes = _node_props(mock_ingestor, cs.NodeLabel.SIDE_EFFECT)
    assert any(
        props.get("symbol_qn") == "persist_invoice"
        and props.get("effect_kind") == "db_write"
        and props.get("operation_name") == "db.insert"
        for props in side_effect_nodes
    )
    assert any(
        props.get("symbol_qn") == "persist_invoice"
        and props.get("effect_kind") == "outbox_write"
        and props.get("operation_name") == "outbox.save"
        for props in side_effect_nodes
    )
    assert any(
        props.get("symbol_qn") == "persist_with_context"
        and props.get("effect_kind") == "external_http"
        for props in side_effect_nodes
    )

    tx_qn_by_symbol = {
        str(props["symbol_qn"]): str(props[cs.KEY_QUALIFIED_NAME])
        for props in boundary_nodes
    }
    effect_qn_by_key = {
        (str(props["symbol_qn"]), str(props["effect_kind"])): str(
            props[cs.KEY_QUALIFIED_NAME]
        )
        for props in side_effect_nodes
    }

    begins = [
        call.args
        for call in get_relationships(
            mock_ingestor, cs.RelationshipType.BEGINS_TRANSACTION
        )
    ]
    assert any(
        rel[0]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "transaction_flow_semantics.main.persist_invoice",
        )
        and rel[2]
        == (
            cs.NodeLabel.TRANSACTION_BOUNDARY,
            cs.KEY_QUALIFIED_NAME,
            tx_qn_by_symbol["persist_invoice"],
        )
        for rel in begins
    )

    commits = [
        call.args
        for call in get_relationships(
            mock_ingestor, cs.RelationshipType.COMMITS_TRANSACTION
        )
    ]
    assert any(
        rel[0]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "transaction_flow_semantics.main.persist_invoice",
        )
        and rel[2]
        == (
            cs.NodeLabel.TRANSACTION_BOUNDARY,
            cs.KEY_QUALIFIED_NAME,
            tx_qn_by_symbol["persist_invoice"],
        )
        for rel in commits
    )

    rollbacks = [
        call.args
        for call in get_relationships(
            mock_ingestor, cs.RelationshipType.ROLLBACKS_TRANSACTION
        )
    ]
    assert any(
        rel[0]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "transaction_flow_semantics.main.persist_with_rollback",
        )
        and rel[2]
        == (
            cs.NodeLabel.TRANSACTION_BOUNDARY,
            cs.KEY_QUALIFIED_NAME,
            tx_qn_by_symbol["persist_with_rollback"],
        )
        for rel in rollbacks
    )

    performs = [
        call.args
        for call in get_relationships(
            mock_ingestor, cs.RelationshipType.PERFORMS_SIDE_EFFECT
        )
    ]
    assert any(
        rel[0]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "transaction_flow_semantics.main.persist_invoice",
        )
        and rel[2]
        == (
            cs.NodeLabel.SIDE_EFFECT,
            cs.KEY_QUALIFIED_NAME,
            effect_qn_by_key[("persist_invoice", "db_write")],
        )
        for rel in performs
    )

    within = [
        call.args
        for call in get_relationships(
            mock_ingestor, cs.RelationshipType.WITHIN_TRANSACTION
        )
    ]
    assert any(
        rel[0]
        == (
            cs.NodeLabel.SIDE_EFFECT,
            cs.KEY_QUALIFIED_NAME,
            effect_qn_by_key[("persist_invoice", "outbox_write")],
        )
        and rel[2]
        == (
            cs.NodeLabel.TRANSACTION_BOUNDARY,
            cs.KEY_QUALIFIED_NAME,
            tx_qn_by_symbol["persist_invoice"],
        )
        for rel in within
    )

    before_edges = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.BEFORE)
    ]
    assert any(
        rel[0]
        == (
            cs.NodeLabel.SIDE_EFFECT,
            cs.KEY_QUALIFIED_NAME,
            effect_qn_by_key[("persist_invoice", "db_write")],
        )
        and rel[2]
        == (
            cs.NodeLabel.SIDE_EFFECT,
            cs.KEY_QUALIFIED_NAME,
            effect_qn_by_key[("persist_invoice", "outbox_write")],
        )
        for rel in before_edges
    )

    after_edges = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.AFTER)
    ]
    assert any(
        rel[0]
        == (
            cs.NodeLabel.SIDE_EFFECT,
            cs.KEY_QUALIFIED_NAME,
            effect_qn_by_key[("persist_invoice", "outbox_write")],
        )
        and rel[2]
        == (
            cs.NodeLabel.SIDE_EFFECT,
            cs.KEY_QUALIFIED_NAME,
            effect_qn_by_key[("persist_invoice", "db_write")],
        )
        for rel in after_edges
    )
