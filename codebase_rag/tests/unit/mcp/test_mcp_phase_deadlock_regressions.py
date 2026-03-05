from __future__ import annotations

import json
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
    registry._session_state["memory_primed"] = True
    registry._session_state["query_code_graph_success_count"] = 1
    registry._session_state["graph_evidence_count"] = 1
    registry._session_state["last_graph_query_digest_id"] = "qd_test"
    return registry


class TestMCPPhaseDeadlockRegressions:
    async def test_memory_query_patterns_keeps_retrieval_phase(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        mcp_registry._set_execution_phase("retrieval", "test_start")

        result = await mcp_registry.memory_query_patterns(
            query="module architecture",
            success_only=True,
            limit=5,
        )

        assert result.get("count", 0) >= 0
        assert mcp_registry._current_execution_phase() == "retrieval"

    async def test_security_scan_allowed_after_validation_phase(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        mcp_registry._set_execution_phase("validation", "simulate_plan")
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.return_value = []

        result = await mcp_registry.security_scan()

        assert isinstance(result, dict)
        assert "phase_guard_blocked" not in str(result.get("error", ""))

    async def test_run_cypher_allowed_in_validation_phase(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        mcp_registry._set_execution_phase("validation", "simulate_plan")
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.return_value = [{"name": "mod"}]

        result = await mcp_registry.run_cypher(
            "MATCH (m:Module {project_name: $project_name}) RETURN m.name AS name LIMIT 1",
            params=json.dumps(
                {"project_name": Path(mcp_registry.project_root).resolve().name}
            ),
            write=False,
            advanced_mode=True,
        )

        assert result.get("status") == "ok"
