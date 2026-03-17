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


def test_test_semantics_pass_emits_pytest_and_unittest_symbol_edges(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "test_case_symbols"
    project.mkdir()

    _write(
        project / "app.py",
        """from pydantic import BaseModel


class OrderCreate(BaseModel):
    customer_id: str


class OrderResponse(BaseModel):
    id: str
    status: str


def persist_order(payload: OrderCreate) -> OrderResponse:
    return OrderResponse(id="ord-1", status="queued")
""",
    )
    _write(
        project / "tests/test_orders.py",
        """from app import OrderCreate, OrderResponse, persist_order


def test_create_order():
    result = persist_order(OrderCreate(customer_id="cus-1"))
    assert isinstance(result, OrderResponse)
""",
    )
    _write(
        project / "tests/test_orders_unittest.py",
        """import unittest

from app import OrderCreate, persist_order


class TestOrderService(unittest.TestCase):
    def test_persist_order(self):
        result = persist_order(OrderCreate(customer_id="cus-2"))
        self.assertEqual(result.status, "queued")
""",
    )

    run_updater(project, mock_ingestor)

    suite_nodes = _node_props(mock_ingestor, cs.NodeLabel.TEST_SUITE)
    assert {str(props.get(cs.KEY_NAME, "")) for props in suite_nodes} >= {
        "test_orders",
        "TestOrderService",
    }

    case_nodes = _node_props(mock_ingestor, cs.NodeLabel.TEST_CASE)
    case_names = {str(props.get(cs.KEY_NAME, "")) for props in case_nodes}
    assert {"test_create_order", "test_persist_order"}.issubset(case_names)

    tests_symbol = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.TESTS_SYMBOL)
    ]
    assert any(
        rel[0][0] == cs.NodeLabel.TEST_CASE
        and rel[2]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "test_case_symbols.app.persist_order",
        )
        for rel in tests_symbol
    )

    contract_nodes = _node_props(mock_ingestor, cs.NodeLabel.CONTRACT)
    order_response_qn = next(
        str(props[cs.KEY_QUALIFIED_NAME])
        for props in contract_nodes
        if props.get(cs.KEY_NAME) == "OrderResponse"
    )
    asserts_contract = [
        call.args
        for call in get_relationships(
            mock_ingestor, cs.RelationshipType.ASSERTS_CONTRACT
        )
    ]
    assert any(
        rel[0][0] == cs.NodeLabel.TEST_CASE
        and rel[2] == (cs.NodeLabel.CONTRACT, cs.KEY_QUALIFIED_NAME, order_response_qn)
        for rel in asserts_contract
    )
