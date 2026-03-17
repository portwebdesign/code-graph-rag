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


async def test_multi_hop_analysis_uses_transaction_flow_edges(
    mcp_registry: MCPToolsRegistry,
) -> None:
    ingestor = cast(MagicMock, mcp_registry.ingestor)
    ingestor.fetch_all.side_effect = [
        [
            {
                "direction": "outbound",
                "seed_ref": "demo.persist_invoice",
                "seed_path": "main.py",
                "node_ref": "demo.semantic.transaction.persist_invoice:explicit:10:1",
                "node_path": "main.py",
                "node_labels": ["TransactionBoundary"],
                "relation": "BEGINS_TRANSACTION",
                "hop_count": 1,
                "node_qualified_name": "demo.semantic.transaction.persist_invoice:explicit:10:1",
                "node_name": "persist_invoice:explicit:10:1",
                "node_start_line": 10,
                "node_end_line": 15,
                "node_docstring": "",
                "node_signature": "",
                "node_visibility": "public",
                "node_module_qn": "demo.main",
                "node_namespace": "demo",
                "node_symbol_kind": "transaction_boundary",
                "node_pagerank": 0.2,
                "node_community_id": 1,
                "node_has_cycle": False,
                "node_in_call_count": 0,
                "node_out_call_count": 1,
                "node_dead_code_score": 0.0,
                "node_is_reachable": True,
            },
            {
                "direction": "outbound",
                "seed_ref": "demo.persist_invoice",
                "seed_path": "main.py",
                "node_ref": "demo.semantic.side_effect.persist_invoice:db_write:11:1",
                "node_path": "main.py",
                "node_labels": ["SideEffect"],
                "relation": "PERFORMS_SIDE_EFFECT",
                "hop_count": 1,
                "node_qualified_name": "demo.semantic.side_effect.persist_invoice:db_write:11:1",
                "node_name": "db_write",
                "node_start_line": 11,
                "node_end_line": 11,
                "node_docstring": "",
                "node_signature": "",
                "node_visibility": "public",
                "node_module_qn": "demo.main",
                "node_namespace": "demo",
                "node_symbol_kind": "side_effect",
                "node_pagerank": 0.1,
                "node_community_id": 2,
                "node_has_cycle": False,
                "node_in_call_count": 0,
                "node_out_call_count": 1,
                "node_dead_code_score": 0.0,
                "node_is_reachable": True,
            },
            {
                "direction": "outbound",
                "seed_ref": "demo.persist_invoice",
                "seed_path": "main.py",
                "node_ref": "demo.semantic.side_effect.persist_invoice:outbox_write:12:2",
                "node_path": "main.py",
                "node_labels": ["SideEffect"],
                "relation": "BEFORE",
                "hop_count": 2,
                "node_qualified_name": "demo.semantic.side_effect.persist_invoice:outbox_write:12:2",
                "node_name": "outbox_write",
                "node_start_line": 12,
                "node_end_line": 12,
                "node_docstring": "",
                "node_signature": "",
                "node_visibility": "public",
                "node_module_qn": "demo.main",
                "node_namespace": "demo",
                "node_symbol_kind": "side_effect",
                "node_pagerank": 0.1,
                "node_community_id": 2,
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
        qualified_name="demo.persist_invoice",
        depth=4,
        limit=20,
    )

    assert result.get("status") == "ok"
    affected_symbols = cast(list[str], result.get("affected_symbols", []))
    assert "demo.semantic.transaction.persist_invoice:explicit:10:1" in affected_symbols
    assert "demo.semantic.side_effect.persist_invoice:db_write:11:1" in affected_symbols

    hop_summary = cast(dict[str, object], result.get("hop_summary", {}))
    relation_counts = cast(dict[str, object], hop_summary.get("relation_counts", {}))
    assert relation_counts.get("BEGINS_TRANSACTION") == 1
    assert relation_counts.get("PERFORMS_SIDE_EFFECT") == 1
    assert relation_counts.get("BEFORE") == 1

    query_args = ingestor.fetch_all.call_args_list[0]
    cypher = str(query_args.args[0])
    assert "BEGINS_TRANSACTION" in cypher
    assert "PERFORMS_SIDE_EFFECT" in cypher
    assert "WITHIN_TRANSACTION" in cypher
    assert "BEFORE" in cypher
    assert "AFTER" in cypher
