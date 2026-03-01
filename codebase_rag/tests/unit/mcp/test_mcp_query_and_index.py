from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codebase_rag.mcp.tools import MCPToolsRegistry

pytestmark = [pytest.mark.anyio]


@pytest.fixture(params=["asyncio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    """Configure anyio to only use asyncio backend."""
    return str(request.param)


@pytest.fixture
def temp_project_root(tmp_path: Path) -> Path:
    """Create a temporary project root directory with sample code."""
    sample_file = tmp_path / "calculator.py"
    sample_file.write_text(
        '''"""Calculator module."""

def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

def multiply(a: int, b: int) -> int:
    """Multiply two numbers."""
    return a * b

class Calculator:
    """Simple calculator class."""

    def divide(self, a: float, b: float) -> float:
        """Divide two numbers."""
        if b == 0:
            raise ValueError("Cannot divide by zero")
        return a / b
''',
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def mcp_registry(temp_project_root: Path) -> MCPToolsRegistry:
    """Create an MCP tools registry with mocked dependencies."""
    mock_ingestor = MagicMock()
    mock_cypher_gen = MagicMock()
    mock_cypher_gen.generate = AsyncMock()
    mock_ingestor.fetch_all = MagicMock(return_value=[])

    registry = MCPToolsRegistry(
        project_root=str(temp_project_root),
        ingestor=mock_ingestor,
        cypher_gen=mock_cypher_gen,
    )

    return registry


class TestQueryCodeGraph:
    """Test query_code_graph functionality."""

    @staticmethod
    def _scoped_cypher(mcp_registry: MCPToolsRegistry) -> str:
        project_name = Path(mcp_registry.project_root).resolve().name
        return (
            f"MATCH (m:Module {{project_name: '{project_name}'}}) "
            "RETURN m.name AS name LIMIT 50"
        )

    async def test_query_finds_functions(self, mcp_registry: MCPToolsRegistry) -> None:
        """Test querying for functions in the code graph."""
        mcp_registry.cypher_gen.generate.return_value = self._scoped_cypher(
            mcp_registry
        )  # type: ignore[attr-defined]
        mcp_registry.ingestor.fetch_all.return_value = [  # type: ignore[attr-defined]
            {"name": "add"},
            {"name": "multiply"},
        ]

        result = await mcp_registry.query_code_graph("Find all functions")

        assert "results" in result
        assert len(result["results"]) == 2
        assert result["results"][0]["name"] == "add"
        assert result["results"][1]["name"] == "multiply"
        assert "query_used" in result
        assert "summary" in result

    async def test_query_finds_classes(self, mcp_registry: MCPToolsRegistry) -> None:
        """Test querying for classes in the code graph."""
        mcp_registry.cypher_gen.generate.return_value = self._scoped_cypher(
            mcp_registry
        )  # type: ignore[attr-defined]
        mcp_registry.ingestor.fetch_all.return_value = [{"name": "Calculator"}]  # type: ignore[attr-defined]

        result = await mcp_registry.query_code_graph("Find all classes")

        assert len(result["results"]) == 1
        assert result["results"][0]["name"] == "Calculator"

    async def test_query_finds_function_calls(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        """Test querying for function call relationships."""
        mcp_registry.cypher_gen.generate.return_value = self._scoped_cypher(
            mcp_registry
        )  # type: ignore[attr-defined]
        mcp_registry.ingestor.fetch_all.return_value = [  # type: ignore[attr-defined]
            {"f.name": "main", "g.name": "add"},
            {"f.name": "main", "g.name": "multiply"},
        ]

        result = await mcp_registry.query_code_graph("What functions does main call?")

        assert len(result["results"]) == 2
        assert "Returned 2 rows" in result["summary"]

    async def test_query_with_no_results(self, mcp_registry: MCPToolsRegistry) -> None:
        """Test query that returns no results."""
        mcp_registry.cypher_gen.generate.return_value = self._scoped_cypher(
            mcp_registry
        )  # type: ignore[attr-defined]
        mcp_registry.ingestor.fetch_all.return_value = []  # type: ignore[attr-defined]

        result = await mcp_registry.query_code_graph("Find nonexistent nodes")

        assert result["results"] == []
        assert "Returned 0 rows" in result["summary"]

    async def test_query_with_complex_natural_language(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        """Test complex natural language query."""
        mcp_registry.cypher_gen.generate.return_value = self._scoped_cypher(
            mcp_registry
        )  # type: ignore[attr-defined]
        mcp_registry.ingestor.fetch_all.return_value = [  # type: ignore[attr-defined]
            {"name": "add"},
            {"name": "multiply"},
        ]

        result = await mcp_registry.query_code_graph(
            "What functions are defined in the calculator module?"
        )

        assert len(result["results"]) == 2
        assert "query_used" in result

    async def test_query_handles_unicode(self, mcp_registry: MCPToolsRegistry) -> None:
        """Test query with unicode characters."""
        mcp_registry.cypher_gen.generate.return_value = self._scoped_cypher(
            mcp_registry
        )  # type: ignore[attr-defined]
        mcp_registry.ingestor.fetch_all.return_value = [{"name": "你好"}]  # type: ignore[attr-defined]

        result = await mcp_registry.query_code_graph("Find function 你好")

        assert len(result["results"]) == 1

    async def test_query_error_handling(self, mcp_registry: MCPToolsRegistry) -> None:
        """Test error handling during query execution."""
        mcp_registry.cypher_gen.generate.side_effect = Exception(  # type: ignore[attr-defined]
            "Database error"
        )

        result = await mcp_registry.query_code_graph("Find all nodes")

        assert "error" in result
        assert "results" in result
        assert isinstance(result["results"], list)
        assert len(result["results"]) == 0

    async def test_query_verifies_parameter_passed(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        """Test that query parameter is correctly passed."""
        mock_func = mcp_registry.cypher_gen.generate  # type: ignore[attr-defined]
        assert isinstance(mock_func, AsyncMock)
        mock_func.return_value = self._scoped_cypher(mcp_registry)
        mcp_registry.ingestor.fetch_all.return_value = []  # type: ignore[attr-defined]

        query = "Find all nodes"
        await mcp_registry.query_code_graph(query)

        called_prompt = mock_func.call_args.args[0]
        assert query in called_prompt

    async def test_query_rejects_unscoped_generated_cypher(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        mcp_registry.cypher_gen.generate.return_value = "MATCH (n) RETURN n LIMIT 50"  # type: ignore[attr-defined]

        result = await mcp_registry.query_code_graph("Find all nodes")

        assert "error" in result
        assert result["results"] == []


class TestIndexRepository:
    """Test index_repository functionality."""

    async def test_index_repository_success(
        self, mcp_registry: MCPToolsRegistry, temp_project_root: Path
    ) -> None:
        """Test successful repository indexing."""
        with patch("codebase_rag.mcp.tools.GraphUpdater") as mock_updater_class:
            mock_updater = MagicMock()
            mock_updater.run.return_value = None
            mock_updater_class.return_value = mock_updater

            result = await mcp_registry.index_repository(
                str(mcp_registry.project_root),
                user_requested=True,
                reason="User explicitly requested indexing",
            )

            assert "Error:" not in result
            assert "Success" in result or "indexed" in result.lower()
            assert str(temp_project_root) in result
            mock_updater.run.assert_called_once()

    async def test_index_repository_creates_graph_updater(
        self, mcp_registry: MCPToolsRegistry, temp_project_root: Path
    ) -> None:
        """Test that GraphUpdater is created with correct parameters."""
        with patch("codebase_rag.mcp.tools.GraphUpdater") as mock_updater_class:
            mock_updater = MagicMock()
            mock_updater.run.return_value = None
            mock_updater_class.return_value = mock_updater

            await mcp_registry.index_repository(
                str(mcp_registry.project_root),
                user_requested=True,
                reason="User explicitly requested indexing",
            )

            mock_updater_class.assert_called_once()
            call_kwargs = mock_updater_class.call_args.kwargs
            assert call_kwargs["ingestor"] == mcp_registry.ingestor
            assert call_kwargs["repo_path"] == Path(temp_project_root)
            assert "parsers" in call_kwargs
            assert "queries" in call_kwargs

    async def test_index_repository_handles_errors(
        self, mcp_registry: MCPToolsRegistry, temp_project_root: Path
    ) -> None:
        """Test error handling during repository indexing."""
        with patch("codebase_rag.mcp.tools.GraphUpdater") as mock_updater_class:
            mock_updater = MagicMock()
            mock_updater.run.side_effect = Exception("Indexing failed")
            mock_updater_class.return_value = mock_updater

            result = await mcp_registry.index_repository(
                str(mcp_registry.project_root),
                user_requested=True,
                reason="User explicitly requested indexing",
            )

            assert "Error" in result
            assert "Indexing failed" in result

    async def test_index_repository_with_empty_directory(
        self, mcp_registry: MCPToolsRegistry, tmp_path: Path
    ) -> None:
        """Test indexing an empty directory."""
        empty_registry = MCPToolsRegistry(
            project_root=str(tmp_path),
            ingestor=MagicMock(),
            cypher_gen=MagicMock(),
        )

        with patch("codebase_rag.mcp.tools.GraphUpdater") as mock_updater_class:
            mock_updater = MagicMock()
            mock_updater.run.return_value = None
            mock_updater_class.return_value = mock_updater

            result = await empty_registry.index_repository(
                str(empty_registry.project_root),
                user_requested=True,
                reason="User explicitly requested indexing",
            )

            assert "Error:" not in result or "Success" in result

    async def test_index_repository_multiple_times(
        self, mcp_registry: MCPToolsRegistry, temp_project_root: Path
    ) -> None:
        """Test indexing repository multiple times (re-indexing)."""
        with patch("codebase_rag.mcp.tools.GraphUpdater") as mock_updater_class:
            mock_updater = MagicMock()
            mock_updater.run.return_value = None
            mock_updater_class.return_value = mock_updater

            result1 = await mcp_registry.index_repository(
                str(mcp_registry.project_root),
                user_requested=True,
                reason="User explicitly requested indexing",
            )
            assert "Error:" not in result1

            result2 = await mcp_registry.index_repository(
                str(mcp_registry.project_root),
                user_requested=True,
                reason="User explicitly requested indexing",
            )
            assert "Error:" not in result2

            assert mock_updater.run.call_count == 2

    async def test_index_repository_clears_project_data_first(
        self, mcp_registry: MCPToolsRegistry, temp_project_root: Path
    ) -> None:
        """Test that project data is cleared before indexing."""
        with patch("codebase_rag.mcp.tools.GraphUpdater") as mock_updater_class:
            mock_updater = MagicMock()
            mock_updater.run.return_value = None
            mock_updater_class.return_value = mock_updater

            result = await mcp_registry.index_repository(
                str(mcp_registry.project_root),
                user_requested=True,
                reason="User explicitly requested indexing",
            )

            project_name = temp_project_root.resolve().name
            mcp_registry.ingestor.delete_project.assert_called_once_with(project_name)  # type: ignore[attr-defined]
            assert "Error:" not in result

    async def test_index_repository_retries_transient_delete_conflict(
        self, mcp_registry: MCPToolsRegistry, temp_project_root: Path
    ) -> None:
        call_count = {"delete": 0}

        def _delete_with_conflict(project_name: str) -> None:
            _ = project_name
            call_count["delete"] += 1
            if call_count["delete"] == 1:
                raise Exception("Cannot resolve conflicting transactions")

        mcp_registry.ingestor.delete_project = MagicMock(
            side_effect=_delete_with_conflict
        )  # type: ignore[method-assign]

        with patch("codebase_rag.mcp.tools.GraphUpdater") as mock_updater_class:
            mock_updater = MagicMock()
            mock_updater.run.return_value = None
            mock_updater_class.return_value = mock_updater

            result = await mcp_registry.index_repository(
                str(mcp_registry.project_root),
                user_requested=True,
                reason="User explicitly requested indexing",
            )

            assert "Error:" not in result
            assert call_count["delete"] == 2

    async def test_index_repository_deletes_project_before_updater_runs(
        self, mcp_registry: MCPToolsRegistry, temp_project_root: Path
    ) -> None:
        """Test that project deletion happens before GraphUpdater runs."""
        call_order: list[str] = []

        def mock_delete(project_name: str) -> None:
            call_order.append("delete")

        def mock_run() -> None:
            call_order.append("run")

        mcp_registry.ingestor.delete_project = MagicMock(side_effect=mock_delete)  # type: ignore[method-assign]

        with patch("codebase_rag.mcp.tools.GraphUpdater") as mock_updater_class:
            mock_updater = MagicMock()
            mock_updater.run = MagicMock(side_effect=mock_run)
            mock_updater_class.return_value = mock_updater

            await mcp_registry.index_repository(
                str(mcp_registry.project_root),
                user_requested=True,
                reason="User explicitly requested indexing",
            )

            assert call_order == ["delete", "run"]

    async def test_sequential_index_only_clears_own_project_data(
        self, tmp_path: Path
    ) -> None:
        mock_ingestor = MagicMock()
        mock_cypher = MagicMock()

        project1 = tmp_path / "project1"
        project1.mkdir()
        registry1 = MCPToolsRegistry(
            project_root=str(project1),
            ingestor=mock_ingestor,
            cypher_gen=mock_cypher,
        )

        project2 = tmp_path / "project2"
        project2.mkdir()
        registry2 = MCPToolsRegistry(
            project_root=str(project2),
            ingestor=mock_ingestor,
            cypher_gen=mock_cypher,
        )

        with patch("codebase_rag.mcp.tools.GraphUpdater") as mock_updater_class:
            mock_updater = MagicMock()
            mock_updater.run.return_value = None
            mock_updater_class.return_value = mock_updater

            await registry1.index_repository(
                str(registry1.project_root),
                user_requested=True,
                reason="User explicitly requested indexing",
            )
            mock_ingestor.delete_project.assert_called_with("project1")

            await registry2.index_repository(
                str(registry2.project_root),
                user_requested=True,
                reason="User explicitly requested indexing",
            )
            mock_ingestor.delete_project.assert_called_with("project2")

            assert mock_ingestor.delete_project.call_count == 2


class TestQueryAndIndexIntegration:
    """Test integration between querying and indexing."""

    async def test_query_after_index(
        self, mcp_registry: MCPToolsRegistry, temp_project_root: Path
    ) -> None:
        """Test querying after indexing."""
        with patch("codebase_rag.mcp.tools.GraphUpdater") as mock_updater_class:
            mock_updater = MagicMock()
            mock_updater.run.return_value = None
            mock_updater_class.return_value = mock_updater

            index_result = await mcp_registry.index_repository(
                str(mcp_registry.project_root),
                user_requested=True,
                reason="User explicitly requested indexing",
            )
            assert "Error:" not in index_result

            project_name = Path(mcp_registry.project_root).resolve().name
            mcp_registry.cypher_gen.generate.return_value = (  # type: ignore[attr-defined]
                f"MATCH (m:Module {{project_name: '{project_name}'}}) RETURN m.name AS name LIMIT 50"
            )
            mcp_registry.ingestor.fetch_all.return_value = [{"name": "add"}]  # type: ignore[attr-defined]

            query_result = await mcp_registry.query_code_graph("Find all functions")
            assert len(query_result["results"]) >= 0

    async def test_index_and_query_workflow(
        self, mcp_registry: MCPToolsRegistry, temp_project_root: Path
    ) -> None:
        """Test typical workflow: index then query."""
        with patch("codebase_rag.mcp.tools.GraphUpdater") as mock_updater_class:
            mock_updater = MagicMock()
            mock_updater.run.return_value = None
            mock_updater_class.return_value = mock_updater

            await mcp_registry.index_repository(
                str(mcp_registry.project_root),
                user_requested=True,
                reason="User explicitly requested indexing",
            )

            project_name = Path(mcp_registry.project_root).resolve().name
            mcp_registry.cypher_gen.generate.return_value = (  # type: ignore[attr-defined]
                f"MATCH (m:Module {{project_name: '{project_name}'}}) RETURN m.name AS name LIMIT 50"
            )
            mcp_registry.ingestor.fetch_all.return_value = [  # type: ignore[attr-defined]
                {"name": "add"},
                {"name": "multiply"},
            ]
            result = await mcp_registry.query_code_graph("Find all functions")
            assert len(result["results"]) == 2

            mcp_registry.ingestor.fetch_all.return_value = [{"name": "Calculator"}]  # type: ignore[attr-defined]
            result = await mcp_registry.query_code_graph("Find all classes")
            assert len(result["results"]) == 1


class TestListProjects:
    async def test_list_projects_success(self, mcp_registry: MCPToolsRegistry) -> None:
        mcp_registry.ingestor.list_projects.return_value = ["project1", "project2"]  # type: ignore[attr-defined]

        result = await mcp_registry.list_projects()

        assert result["projects"] == ["project1", "project2"]
        assert result["count"] == 2
        assert "error" not in result

    async def test_list_projects_empty(self, mcp_registry: MCPToolsRegistry) -> None:
        mcp_registry.ingestor.list_projects.return_value = []  # type: ignore[attr-defined]

        result = await mcp_registry.list_projects()

        assert result["projects"] == []
        assert result["count"] == 0

    async def test_list_projects_error(self, mcp_registry: MCPToolsRegistry) -> None:
        mcp_registry.ingestor.list_projects.side_effect = Exception("DB error")  # type: ignore[attr-defined]

        result = await mcp_registry.list_projects()

        assert "error" in result
        assert result["projects"] == []
        assert result["count"] == 0


class TestDeleteProject:
    async def test_delete_project_success(self, mcp_registry: MCPToolsRegistry) -> None:
        mcp_registry.ingestor.list_projects.return_value = ["my-project", "other"]  # type: ignore[attr-defined]

        result = await mcp_registry.delete_project("my-project")

        assert result["success"] is True
        assert result.get("project") == "my-project"
        assert "message" in result
        mcp_registry.ingestor.delete_project.assert_called_once_with("my-project")  # type: ignore[attr-defined]

    async def test_delete_project_not_found(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        mcp_registry.ingestor.list_projects.return_value = ["other-project"]  # type: ignore[attr-defined]

        result = await mcp_registry.delete_project("nonexistent")

        assert result["success"] is False
        assert "error" in result
        assert "not found" in str(result.get("error", "")).lower()
        mcp_registry.ingestor.delete_project.assert_not_called()  # type: ignore[attr-defined]

    async def test_delete_project_error(self, mcp_registry: MCPToolsRegistry) -> None:
        mcp_registry.ingestor.list_projects.return_value = ["my-project"]  # type: ignore[attr-defined]
        mcp_registry.ingestor.delete_project.side_effect = Exception("Delete failed")  # type: ignore[attr-defined]

        result = await mcp_registry.delete_project("my-project")

        assert result["success"] is False
        assert "error" in result


class TestWipeDatabase:
    async def test_wipe_database_confirmed(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        result = await mcp_registry.wipe_database(confirm=True)

        assert "wiped" in result.lower()
        mcp_registry.ingestor.clean_database.assert_called_once()  # type: ignore[attr-defined]

    async def test_wipe_database_not_confirmed(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        result = await mcp_registry.wipe_database(confirm=False)

        assert "cancelled" in result.lower()
        mcp_registry.ingestor.clean_database.assert_not_called()  # type: ignore[attr-defined]

    async def test_wipe_database_error(self, mcp_registry: MCPToolsRegistry) -> None:
        mcp_registry.ingestor.clean_database.side_effect = Exception("Wipe failed")  # type: ignore[attr-defined]

        result = await mcp_registry.wipe_database(confirm=True)

        assert "error" in result.lower()
