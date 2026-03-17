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


async def test_schema_overview_exposes_semantic_auth_contract_presets(
    mcp_registry: MCPToolsRegistry,
) -> None:
    ingestor = cast(MagicMock, mcp_registry.ingestor)
    ingestor.fetch_all.side_effect = [
        [
            {
                "from_node_type": "Endpoint",
                "relationship_type": "SECURED_BY",
                "to_node_type": "AuthPolicy",
            },
            {
                "from_node_type": "Endpoint",
                "relationship_type": "USES_DEPENDENCY",
                "to_node_type": "DependencyProvider",
            },
            {
                "from_node_type": "Endpoint",
                "relationship_type": "RETURNS_CONTRACT",
                "to_node_type": "Contract",
            },
        ],
        [
            {"label": "Endpoint", "count": 8},
            {"label": "AuthPolicy", "count": 4},
            {"label": "DependencyProvider", "count": 6},
            {"label": "Contract", "count": 9},
        ],
    ]

    result = await mcp_registry.get_schema_overview(scope="api")

    assert result.get("status") == "ok"
    presets = cast(list[dict[str, object]], result.get("semantic_cypher_presets", []))
    preset_names = {str(item.get("name", "")) for item in presets}
    assert "endpoint_auth_coverage" in preset_names
    assert "endpoint_dependency_visibility" in preset_names
    assert "endpoint_contract_gaps" in preset_names
    assert "unprotected_endpoints" in preset_names
    assert any("$project_name" in str(item.get("query", "")) for item in presets)


def test_tool_schemas_mention_semantic_auth_and_contract_capabilities(
    mcp_registry: MCPToolsRegistry,
) -> None:
    schema_descriptions = {
        schema.name: schema.description for schema in mcp_registry.get_tool_schemas()
    }

    query_description = str(schema_descriptions.get("query_code_graph", "")).lower()
    run_cypher_description = str(schema_descriptions.get("run_cypher", "")).lower()

    assert "auth coverage" in query_description
    assert "request/response contract" in query_description
    assert "unprotected endpoints" in run_cypher_description
    assert "contract-gap" in run_cypher_description


async def test_multi_hop_analysis_uses_semantic_auth_and_contract_edges(
    mcp_registry: MCPToolsRegistry,
) -> None:
    ingestor = cast(MagicMock, mcp_registry.ingestor)
    ingestor.fetch_all.side_effect = [
        [
            {
                "direction": "outbound",
                "seed_ref": "demo.endpoint.fastapi.POST:/api/invoices",
                "seed_path": "main.py",
                "node_ref": "demo.semantic.contract.InvoiceResponse",
                "node_path": "main.py",
                "node_labels": ["Contract"],
                "relation": "RETURNS_CONTRACT",
                "hop_count": 1,
                "node_qualified_name": "demo.semantic.contract.InvoiceResponse",
                "node_name": "InvoiceResponse",
                "node_start_line": 10,
                "node_end_line": 16,
                "node_docstring": "",
                "node_signature": "",
                "node_visibility": "public",
                "node_module_qn": "demo.main",
                "node_namespace": "demo",
                "node_symbol_kind": "contract",
                "node_pagerank": 0.41,
                "node_community_id": 2,
                "node_has_cycle": False,
                "node_in_call_count": 1,
                "node_out_call_count": 2,
                "node_dead_code_score": 0.0,
                "node_is_reachable": True,
            }
        ],
        [
            {
                "direction": "inbound",
                "seed_ref": "demo.endpoint.fastapi.POST:/api/invoices",
                "seed_path": "main.py",
                "node_ref": "demo.client.create_invoice",
                "node_path": "client.ts",
                "node_labels": ["Function"],
                "relation": "REQUESTS_ENDPOINT",
                "hop_count": 1,
                "node_qualified_name": "demo.client.create_invoice",
                "node_name": "create_invoice",
                "node_start_line": 2,
                "node_end_line": 7,
                "node_docstring": "",
                "node_signature": "create_invoice(payload)",
                "node_visibility": "public",
                "node_module_qn": "demo.client",
                "node_namespace": "demo",
                "node_symbol_kind": "function",
                "node_pagerank": 0.62,
                "node_community_id": 3,
                "node_has_cycle": False,
                "node_in_call_count": 0,
                "node_out_call_count": 1,
                "node_dead_code_score": 0.0,
                "node_is_reachable": True,
            }
        ],
    ]

    result = await mcp_registry.multi_hop_analysis(
        qualified_name="demo.endpoint.fastapi.POST:/api/invoices",
        depth=2,
        limit=20,
    )

    assert result.get("status") == "ok"
    assert "demo.semantic.contract.InvoiceResponse" in cast(
        list[str], result.get("affected_symbols", [])
    )
    hop_summary = cast(dict[str, object], result.get("hop_summary", {}))
    relation_counts = cast(dict[str, object], hop_summary.get("relation_counts", {}))
    assert relation_counts.get("RETURNS_CONTRACT") == 1
    assert relation_counts.get("REQUESTS_ENDPOINT") == 1

    query_args = ingestor.fetch_all.call_args_list[0]
    cypher = str(query_args.args[0])
    assert "USES_DEPENDENCY" in cypher
    assert "SECURED_BY" in cypher
    assert "ACCEPTS_CONTRACT" in cypher
    assert "RETURNS_CONTRACT" in cypher
    assert "DECLARES_FIELD" in cypher
    assert "REQUESTS_ENDPOINT" in cypher
