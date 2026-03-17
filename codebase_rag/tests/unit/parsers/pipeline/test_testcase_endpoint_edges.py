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


def test_test_semantics_pass_emits_js_endpoint_and_contract_edges(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "test_case_endpoints"
    project.mkdir()

    _write(
        project / "src/contracts.ts",
        """export interface OrderResponse {
  id: string;
  status: string;
}
""",
    )
    _write(
        project / "src/client.ts",
        """export async function createOrder(payload: { customerId: string }) {
  void payload;
  return fetch("/api/orders", { method: "POST" });
}
""",
    )
    _write(
        project / "web/orders.spec.ts",
        """import { expect, test } from "vitest";

import { createOrder } from "../src/client";
import type { OrderResponse } from "../src/contracts";

test("submits order through e2e client", async () => {
  await createOrder({ customerId: "cus-3" });
  await page.request.post("/api/orders");
  const payload = {} as OrderResponse;
  expect(payload).toBeDefined();
});
""",
    )

    run_updater(project, mock_ingestor)

    case_nodes = _node_props(mock_ingestor, cs.NodeLabel.TEST_CASE)
    assert any(
        props.get(cs.KEY_NAME) == "submits order through e2e client"
        for props in case_nodes
    )

    endpoint_nodes = _node_props(mock_ingestor, cs.NodeLabel.ENDPOINT)
    assert any(
        props.get(cs.KEY_HTTP_METHOD) == "POST"
        and props.get(cs.KEY_ROUTE_PATH) == "/api/orders"
        for props in endpoint_nodes
    )

    endpoint_qn = next(
        str(props[cs.KEY_QUALIFIED_NAME])
        for props in endpoint_nodes
        if props.get(cs.KEY_HTTP_METHOD) == "POST"
        and props.get(cs.KEY_ROUTE_PATH) == "/api/orders"
    )
    tests_endpoint = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.TESTS_ENDPOINT)
    ]
    assert any(
        rel[0][0] == cs.NodeLabel.TEST_CASE
        and rel[2] == (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn)
        for rel in tests_endpoint
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
        rel[2] == (cs.NodeLabel.CONTRACT, cs.KEY_QUALIFIED_NAME, order_response_qn)
        for rel in asserts_contract
    )
