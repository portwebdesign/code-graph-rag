from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from codebase_rag.core.config import settings
from codebase_rag.mcp.tools import MCPToolsRegistry

pytestmark = [pytest.mark.anyio]


@pytest.fixture(params=["asyncio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def temp_test_repo(tmp_path: Path) -> Path:
    sample_file = tmp_path / "sample.py"
    sample_file.write_text("def hello():\n    return 1\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def mcp_registry(temp_test_repo: Path) -> MCPToolsRegistry:
    mock_ingestor = MagicMock()
    mock_cypher_gen = MagicMock()

    async def mock_generate(query: str) -> str:
        _ = query
        return "MATCH (m:Module {project_name: $project_name}) RETURN m.name AS name LIMIT 5"

    mock_cypher_gen.generate = mock_generate

    registry = MCPToolsRegistry(
        project_root=str(temp_test_repo),
        ingestor=mock_ingestor,
        cypher_gen=mock_cypher_gen,
    )
    registry._session_state["preflight_project_selected"] = True
    registry._session_state["preflight_schema_summary_loaded"] = True
    return registry


class TestMCPWorkflowEnforcement:
    def test_workflow_gate_requires_memory_priming(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        previous = settings.MCP_ENFORCE_MEMORY_PRIMING_GATE
        settings.MCP_ENFORCE_MEMORY_PRIMING_GATE = True
        try:
            payload = mcp_registry.get_workflow_gate_payload(
                "query_code_graph",
                {"natural_language_query": "list core modules"},
            )

            assert payload is not None
            exact_next_calls = cast(
                list[dict[str, object]], payload.get("exact_next_calls", [])
            )
            assert exact_next_calls[0].get("tool") == "memory_query_patterns"
        finally:
            settings.MCP_ENFORCE_MEMORY_PRIMING_GATE = previous

    def test_workflow_gate_requires_plan_for_complex_query(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        mcp_registry._session_state["memory_primed"] = True

        payload = mcp_registry.get_workflow_gate_payload(
            "query_code_graph",
            {
                "natural_language_query": "analyze multi-file dependency chain refactor impact"
            },
        )

        assert payload is not None
        exact_next_calls = cast(
            list[dict[str, object]], payload.get("exact_next_calls", [])
        )
        assert exact_next_calls[0].get("tool") == "plan_task"

    async def test_run_cypher_parameterizes_literal_scope(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.return_value = [{"name": "mod"}]

        project_name = Path(mcp_registry.project_root).resolve().name
        literal_query = (
            f"MATCH (m:Module {{project_name: '{project_name}'}}) "
            "RETURN m.name AS name LIMIT 5"
        )

        result = await mcp_registry.run_cypher(
            literal_query,
            None,
            False,
            advanced_mode=True,
        )

        assert result.get("status") == "ok"
        scope_info = cast(dict[str, object], result.get("scope_normalization", {}))
        applied = cast(list[str], scope_info.get("applied", []))
        assert "parameterized_project_scope_literal" in applied
        params_used = cast(dict[str, object], scope_info.get("params_used", {}))
        assert params_used.get("project_name") == project_name

    async def test_run_cypher_rejects_missing_project_param_scope(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        result = await mcp_registry.run_cypher(
            "MATCH (n) RETURN n LIMIT 1",
            None,
            False,
            advanced_mode=True,
        )

        assert "error" in result
        assert "must use parameterized project scope" in str(result.get("error", ""))
