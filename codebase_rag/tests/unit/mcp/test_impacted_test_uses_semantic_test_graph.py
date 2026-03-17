from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
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
    (tmp_path / "app.py").write_text(
        "def create_order():\n    return True\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def mcp_registry(temp_repo: Path) -> MCPToolsRegistry:
    registry = MCPToolsRegistry(
        project_root=str(temp_repo),
        ingestor=MagicMock(),
        cypher_gen=MagicMock(),
    )
    registry._session_state["preflight_project_selected"] = True
    registry._session_state["preflight_schema_summary_loaded"] = True
    return registry


async def test_test_generate_prefers_semantic_graph_candidates(
    mcp_registry: MCPToolsRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp_registry._session_state["last_multi_hop_bundle"] = {
        "affected_files": ["app.py"],
        "affected_symbols": ["demo.endpoint.fastapi.POST:/api/orders"],
    }

    monkeypatch.setattr(
        mcp_registry,
        "_query_semantic_test_candidates",
        lambda **_: (
            [
                {
                    "coverage_kind": "endpoint_direct",
                    "testcase_qn": "demo.semantic.test_case.tests/test_orders.py:test_orders:test_create_order:1",
                    "testcase_name": "test_create_order",
                    "test_file": "tests/test_orders.py",
                    "framework": "python",
                    "suite_qn": "demo.semantic.test_suite.tests/test_orders.py:test_orders",
                    "suite_name": "test_orders",
                    "matched_target_qn": "demo.endpoint.fastapi.POST:/api/orders",
                    "matched_target_name": "POST /api/orders",
                    "matched_target_kind": "Endpoint",
                }
            ],
            {
                "symbols": [],
                "endpoints": [
                    {
                        "qualified_name": "demo.endpoint.fastapi.POST:/api/orders",
                        "name": "POST /api/orders",
                        "path": "app.py",
                        "http_method": "POST",
                        "route_path": "/api/orders",
                    }
                ],
                "contracts": [],
            },
        ),
    )
    monkeypatch.setattr(
        mcp_registry,
        "_query_runtime_coverage_matches",
        lambda **_: [
            {
                "file_path": "app.py",
                "runtime_event_qn": "demo.runtime.coverage.app",
                "covered_lines": 12,
                "total_lines": 20,
            }
        ],
    )

    async def fake_run(prompt: str) -> object:
        assert "Selection mode: semantic-graph-primary" in prompt
        assert "tests/test_orders.py" in prompt
        assert "Runtime coverage matches" in prompt
        return SimpleNamespace(
            status="ok", content="def test_generated():\n    assert True\n"
        )

    mcp_registry._test_agent = SimpleNamespace(run=fake_run)

    result = await mcp_registry.test_generate(goal="Add order API regression tests")

    assert result.get("status") == "ok"
    test_selection = cast(dict[str, object], result.get("test_selection", {}))
    assert test_selection.get("selection_mode") == "semantic-graph-primary"
    assert test_selection.get("candidate_existing_tests") == ["tests/test_orders.py"]
    runtime_matches = cast(
        list[dict[str, object]],
        test_selection.get("runtime_coverage_matches", []),
    )
    assert runtime_matches[0]["file_path"] == "app.py"


async def test_schema_overview_exposes_test_semantics_presets(
    mcp_registry: MCPToolsRegistry,
) -> None:
    ingestor = cast(MagicMock, mcp_registry.ingestor)
    ingestor.fetch_all.side_effect = [
        [
            {
                "from_node_type": "TestCase",
                "relationship_type": "TESTS_ENDPOINT",
                "to_node_type": "Endpoint",
            },
            {
                "from_node_type": "TestCase",
                "relationship_type": "ASSERTS_CONTRACT",
                "to_node_type": "Contract",
            },
        ],
        [
            {"label": "TestCase", "count": 5},
            {"label": "Endpoint", "count": 2},
            {"label": "Contract", "count": 3},
        ],
    ]

    result = await mcp_registry.get_schema_overview(scope="api")

    assert result.get("status") == "ok"
    presets = cast(list[dict[str, object]], result.get("semantic_cypher_presets", []))
    preset_names = {str(item.get("name", "")) for item in presets}
    assert "untested_public_endpoints" in preset_names
    assert "contract_test_coverage" in preset_names
