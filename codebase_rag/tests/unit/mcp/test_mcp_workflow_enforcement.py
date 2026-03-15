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
                "read_file",
                {"file_path": "src/services/auth_service.py"},
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
        previous = settings.MCP_ENFORCE_COMPLEX_PLAN_GATE
        settings.MCP_ENFORCE_COMPLEX_PLAN_GATE = True

        try:
            payload = mcp_registry.get_workflow_gate_payload(
                "read_file",
                {
                    "file_path": (
                        "src/very/long/path/that/represents/a/multi-file/architecture/dependency/refactor/impact/"
                        "analysis/request/for/non_core_read_file_gate_validation/example_module.py"
                    )
                },
            )

            assert payload is not None
            exact_next_calls = cast(
                list[dict[str, object]], payload.get("exact_next_calls", [])
            )
            assert exact_next_calls[0].get("tool") == "plan_task"
        finally:
            settings.MCP_ENFORCE_COMPLEX_PLAN_GATE = previous

    def test_workflow_gate_does_not_block_core_query_code_graph_for_complex_query(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        mcp_registry._session_state["memory_primed"] = True
        previous = settings.MCP_ENFORCE_COMPLEX_PLAN_GATE
        settings.MCP_ENFORCE_COMPLEX_PLAN_GATE = True

        try:
            payload = mcp_registry.get_workflow_gate_payload(
                "query_code_graph",
                {
                    "natural_language_query": "analyze multi-file dependency chain refactor impact across services, repositories, handlers, and router composition"
                },
            )

            assert payload is None
        finally:
            settings.MCP_ENFORCE_COMPLEX_PLAN_GATE = previous

    def test_workflow_gate_requires_repo_evidence_before_context7(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        mcp_registry._session_state["memory_primed"] = True

        payload = mcp_registry.get_workflow_gate_payload(
            "context7_docs",
            {
                "library": "fastapi",
                "query": "dependency injection lifecycle",
            },
        )

        assert payload is not None
        assert "context7_repo_evidence_required" in str(payload.get("error", ""))
        exact_next_calls = cast(
            list[dict[str, object]], payload.get("exact_next_calls", [])
        )
        assert exact_next_calls[0].get("tool") == "query_code_graph"

    def test_visibility_gate_blocks_context7_until_session_stage_unlocks(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        mcp_registry._session_state["memory_primed"] = True

        payload = mcp_registry.get_visibility_gate_payload(
            "context7_docs",
            {"library": "fastapi", "query": "routing"},
        )

        assert payload is not None
        assert payload.get("gate") == "visibility"
        assert payload.get("blocked_tool") == "context7_docs"
        assert "query_code_graph" in cast(list[str], payload.get("visible_tools", []))

    def test_visibility_gate_recovers_with_select_active_project_when_session_state_is_missing(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        mcp_registry._session_state["preflight_project_selected"] = False
        mcp_registry._session_state["preflight_schema_summary_loaded"] = False

        payload = mcp_registry.get_visibility_gate_payload(
            "query_code_graph",
            {"natural_language_query": "trace auth flow"},
        )

        assert payload is not None
        exact_next_calls = cast(
            list[dict[str, object]], payload.get("exact_next_calls", [])
        )
        assert exact_next_calls[0].get("tool") == "list_projects"
        assert exact_next_calls[1].get("tool") == "select_active_project"
        assert payload.get("tool_stage") == "graph_bootstrap"

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

    async def test_impact_graph_passes_project_scope_to_query_layer(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.return_value = []

        _ = await mcp_registry.impact_graph(qualified_name="pkg.Service.run")

        query_args = ingestor.fetch_all.call_args
        assert query_args is not None
        cypher = str(query_args.args[0])
        params = cast(dict[str, object], query_args.args[1])
        assert "all(node IN nodes(p)" in cypher
        assert (
            params.get("project_name") == Path(mcp_registry.project_root).resolve().name
        )
