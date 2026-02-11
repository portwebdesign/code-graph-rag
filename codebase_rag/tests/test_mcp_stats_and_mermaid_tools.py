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
def temp_test_repo(tmp_path: Path) -> Path:
    sample_file = tmp_path / "sample.py"
    sample_file.write_text("def hello():\n    return 1\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def mcp_registry(temp_test_repo: Path) -> MCPToolsRegistry:
    mock_ingestor = MagicMock()
    mock_cypher_gen = MagicMock()

    async def mock_generate(query: str) -> str:
        return "MATCH (n) RETURN n"

    mock_cypher_gen.generate = mock_generate

    return MCPToolsRegistry(
        project_root=str(temp_test_repo),
        ingestor=mock_ingestor,
        cypher_gen=mock_cypher_gen,
    )


class TestMCPStatsAndMermaidTools:
    async def test_run_cypher_read_and_write(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.return_value = [{"name": "n1"}]

        read_result = await mcp_registry.run_cypher("MATCH (n) RETURN n", None, False)

        assert read_result.get("status") == "ok"
        assert read_result.get("results") == [{"name": "n1"}]

        write_result = await mcp_registry.run_cypher(
            "CREATE (n:Test)", '{"name": "ok"}', True
        )

        assert write_result.get("status") == "ok"
        ingestor.execute_write.assert_called()

    async def test_get_graph_stats(self, mcp_registry: MCPToolsRegistry) -> None:
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.side_effect = [
            [{"count": 10}],
            [{"count": 20}],
            [{"label": "Function", "count": 5}],
            [{"type": "CALLS", "count": 3}],
        ]

        result = await mcp_registry.get_graph_stats()

        assert result.get("nodes") == 10
        assert result.get("relationships") == 20
        assert result.get("labels") == [{"label": "Function", "count": 5}]
        assert result.get("relationship_types") == [{"type": "CALLS", "count": 3}]

    async def test_get_dependency_stats(self, mcp_registry: MCPToolsRegistry) -> None:
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.side_effect = [
            [{"count": 7}],
            [{"module": "mod1", "count": 4}],
            [{"target": "lib1", "count": 2}],
        ]

        result = await mcp_registry.get_dependency_stats()

        assert result.get("total_imports") == 7
        assert result.get("top_importers") == [{"module": "mod1", "count": 4}]
        assert result.get("top_dependents") == [{"target": "lib1", "count": 2}]

    async def test_export_mermaid(
        self, mcp_registry: MCPToolsRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.export_graph_to_dict.return_value = {"nodes": []}

        class DummyExporter:
            def __init__(self, graph_file: str, config: object | None = None) -> None:
                self.graph_file = graph_file
                self.config = config

            def export(self, diagram: str, output_path: str) -> Path:
                output = Path(output_path)
                output.write_text("graph TD;\nA-->B\n", encoding="utf-8")
                return output

        monkeypatch.setattr("codebase_rag.mcp.tools.MermaidExporter", DummyExporter)

        result = await mcp_registry.export_mermaid("module")

        assert result.get("status") == "ok"
        assert "graph TD" in str(result.get("content", ""))
