from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from codebase_rag.mcp.tools import MCPToolsRegistry

pytestmark = [pytest.mark.anyio]


@pytest.fixture(params=["asyncio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    (tmp_path / "sample.py").write_text(
        "def hello():\n    return 1\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def mcp_registry(temp_repo: Path) -> MCPToolsRegistry:
    mock_ingestor = MagicMock()
    mock_cypher_gen = MagicMock()

    async def mock_generate(query: str) -> str:
        _ = query
        return (
            "MATCH (m:Module {project_name: $project_name}) "
            "RETURN m.name AS name LIMIT 5"
        )

    mock_cypher_gen.generate = mock_generate

    registry = MCPToolsRegistry(
        project_root=str(temp_repo),
        ingestor=mock_ingestor,
        cypher_gen=mock_cypher_gen,
    )
    registry._session_state["preflight_project_selected"] = True
    registry._session_state["preflight_schema_summary_loaded"] = True
    return registry


async def test_multi_hop_analysis_uses_runtime_observation_edges(
    mcp_registry: MCPToolsRegistry,
) -> None:
    ingestor = cast(MagicMock, mcp_registry.ingestor)
    ingestor.fetch_all.side_effect = [
        [
            {
                "direction": "outbound",
                "seed_ref": "demo.dispatch_invoice_created",
                "seed_path": "main.py",
                "node_ref": "demo.semantic.event_flow.invoice.created_invoice-events",
                "node_path": "main.py",
                "node_labels": ["EventFlow"],
                "relation": "PUBLISHES_EVENT",
                "hop_count": 1,
                "node_qualified_name": "demo.semantic.event_flow.invoice.created_invoice-events",
                "node_name": "invoice.created",
                "node_start_line": 10,
                "node_end_line": 20,
                "node_docstring": "",
                "node_signature": "",
                "node_visibility": "public",
                "node_module_qn": "demo.main",
                "node_namespace": "demo",
                "node_symbol_kind": "event_flow",
                "node_pagerank": 0.42,
                "node_community_id": 2,
                "node_has_cycle": False,
                "node_in_call_count": 1,
                "node_out_call_count": 2,
                "node_dead_code_score": 0.0,
                "node_is_reachable": True,
            },
            {
                "direction": "outbound",
                "seed_ref": "demo.dispatch_invoice_created",
                "seed_path": "main.py",
                "node_ref": "demo.runtime.output.runtime.events.ndjson.event.1",
                "node_path": "output/runtime/events.ndjson",
                "node_labels": ["RuntimeEvent"],
                "relation": "OBSERVED_IN_RUNTIME",
                "hop_count": 2,
                "node_qualified_name": "demo.runtime.output.runtime.events.ndjson.event.1",
                "node_name": "event_runtime",
                "node_start_line": None,
                "node_end_line": None,
                "node_docstring": "",
                "node_signature": "",
                "node_visibility": "public",
                "node_module_qn": "",
                "node_namespace": "demo",
                "node_symbol_kind": "runtime_event",
                "node_pagerank": 0.0,
                "node_community_id": 0,
                "node_has_cycle": False,
                "node_in_call_count": 0,
                "node_out_call_count": 1,
                "node_dead_code_score": 0.0,
                "node_is_reachable": True,
            },
            {
                "direction": "outbound",
                "seed_ref": "demo.dispatch_invoice_created",
                "seed_path": "main.py",
                "node_ref": "demo.InvoiceWorker.handle_invoice_created",
                "node_path": "main.py",
                "node_labels": ["Method"],
                "relation": "OBSERVED_IN_RUNTIME",
                "hop_count": 3,
                "node_qualified_name": "demo.InvoiceWorker.handle_invoice_created",
                "node_name": "handle_invoice_created",
                "node_start_line": 24,
                "node_end_line": 28,
                "node_docstring": "",
                "node_signature": "handle_invoice_created(message)",
                "node_visibility": "public",
                "node_module_qn": "demo.main",
                "node_namespace": "demo",
                "node_symbol_kind": "method",
                "node_pagerank": 0.67,
                "node_community_id": 4,
                "node_has_cycle": False,
                "node_in_call_count": 1,
                "node_out_call_count": 1,
                "node_dead_code_score": 0.0,
                "node_is_reachable": True,
            },
        ],
        [],
    ]

    result = await mcp_registry.multi_hop_analysis(
        qualified_name="demo.dispatch_invoice_created",
        depth=5,
        limit=20,
    )

    assert result.get("status") == "ok"
    affected_symbols = cast(list[str], result.get("affected_symbols", []))
    assert "demo.runtime.output.runtime.events.ndjson.event.1" in affected_symbols
    hop_summary = cast(dict[str, object], result.get("hop_summary", {}))
    relation_counts = cast(dict[str, object], hop_summary.get("relation_counts", {}))
    assert relation_counts.get("OBSERVED_IN_RUNTIME") == 2

    query_args = ingestor.fetch_all.call_args_list[0]
    cypher = str(query_args.args[0])
    assert "OBSERVED_IN_RUNTIME" in cypher
