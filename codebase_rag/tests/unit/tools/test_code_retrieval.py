from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from codebase_rag.data_models.schemas import CodeSnippet
from codebase_rag.tools.code_retrieval import CodeRetriever, create_code_retrieval_tool


class TestCodeRetrieverInit:
    def test_init_resolves_project_root(self) -> None:
        mock_ingestor = MagicMock()

        retriever = CodeRetriever("/tmp/project", mock_ingestor)

        assert retriever.project_root == Path("/tmp/project").resolve()

    def test_init_stores_ingestor(self) -> None:
        mock_ingestor = MagicMock()

        retriever = CodeRetriever("/tmp/project", mock_ingestor)

        assert retriever.ingestor is mock_ingestor


class TestFindCodeSnippet:
    @pytest.fixture
    def mock_ingestor(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def retriever(self, mock_ingestor: MagicMock) -> CodeRetriever:
        return CodeRetriever("/tmp/project", mock_ingestor)

    @pytest.mark.asyncio
    async def test_returns_not_found_when_no_results(
        self, retriever: CodeRetriever, mock_ingestor: MagicMock
    ) -> None:
        mock_ingestor.fetch_all.return_value = []

        result = await retriever.find_code_snippet("module.func")

        assert result.found is False
        assert result.error_message == "Entity not found in graph."
        assert result.qualified_name == "module.func"

    @pytest.mark.asyncio
    async def test_returns_not_found_when_missing_path(
        self, retriever: CodeRetriever, mock_ingestor: MagicMock
    ) -> None:
        mock_ingestor.fetch_all.return_value = [
            {"path": None, "start": 1, "end": 10, "name": "func"}
        ]

        result = await retriever.find_code_snippet("module.func")

        assert result.found is False
        assert result.error_message is not None
        assert "missing location data" in result.error_message

    @pytest.mark.asyncio
    async def test_returns_not_found_when_missing_start_line(
        self, retriever: CodeRetriever, mock_ingestor: MagicMock
    ) -> None:
        mock_ingestor.fetch_all.return_value = [
            {"path": "src/mod.py", "start": None, "end": 10, "name": "func"}
        ]

        result = await retriever.find_code_snippet("module.func")

        assert result.found is False

    @pytest.mark.asyncio
    async def test_returns_not_found_when_missing_end_line(
        self, retriever: CodeRetriever, mock_ingestor: MagicMock
    ) -> None:
        mock_ingestor.fetch_all.return_value = [
            {"path": "src/mod.py", "start": 1, "end": None, "name": "func"}
        ]

        result = await retriever.find_code_snippet("module.func")

        assert result.found is False

    @pytest.mark.asyncio
    async def test_handles_ingestor_error(
        self, retriever: CodeRetriever, mock_ingestor: MagicMock
    ) -> None:
        mock_ingestor.fetch_all.side_effect = RuntimeError("Database error")

        result = await retriever.find_code_snippet("module.func")

        assert result.found is False
        assert result.error_message is not None
        assert "Database error" in result.error_message

    @pytest.mark.asyncio
    async def test_uses_cypher_query_constant(
        self, retriever: CodeRetriever, mock_ingestor: MagicMock
    ) -> None:
        mock_ingestor.fetch_all.return_value = []

        await retriever.find_code_snippet("module.func")

        call_args = mock_ingestor.fetch_all.call_args
        query = call_args[0][0]
        params = call_args[0][1]

        assert "qualified_name" in query
        assert "start_line" in query or "start" in query
        assert "end_line" in query or "end" in query
        assert params == {"qn": "module.func"}

    @pytest.mark.asyncio
    async def test_resolves_source_from_absolute_path(
        self, mock_ingestor: MagicMock, tmp_path: Path
    ) -> None:
        project_root = tmp_path / "code-graph-rag"
        project_root.mkdir(parents=True, exist_ok=True)

        external_file = tmp_path / "acente" / "src" / "report.py"
        external_file.parent.mkdir(parents=True, exist_ok=True)
        external_file.write_text(
            "def kasa_extre_pdf():\n    return 'ok'\n",
            encoding="utf-8",
        )

        retriever = CodeRetriever(str(project_root), mock_ingestor)
        mock_ingestor.fetch_all.return_value = [
            {
                "path": str(external_file),
                "start": 1,
                "end": 2,
                "name": "kasa_extre_pdf",
            }
        ]

        result = await retriever.find_code_snippet("acente.report.kasa_extre_pdf")

        assert result.found is True
        assert "def kasa_extre_pdf" in result.source_code

    @pytest.mark.asyncio
    async def test_resolves_source_from_sibling_repo_by_qualified_name(
        self, mock_ingestor: MagicMock, tmp_path: Path
    ) -> None:
        project_root = tmp_path / "code-graph-rag"
        project_root.mkdir(parents=True, exist_ok=True)

        sibling_file = (
            tmp_path
            / "acenterobotu-main"
            / "app"
            / "Http"
            / "Controllers"
            / "KasaRaporController.php"
        )
        sibling_file.parent.mkdir(parents=True, exist_ok=True)
        sibling_file.write_text(
            "<?php\nfunction kasaExtrePDF() {}\n",
            encoding="utf-8",
        )

        retriever = CodeRetriever(str(project_root), mock_ingestor)
        mock_ingestor.fetch_all.return_value = [
            {
                "path": "app/Http/Controllers/KasaRaporController.php",
                "start": 1,
                "end": 2,
                "name": "kasaExtrePDF",
            }
        ]

        qualified_name = (
            "acenterobotu-main.app.Http.Controllers."
            "KasaRaporController.KasaRaporController.kasaExtrePDF"
        )
        result = await retriever.find_code_snippet(qualified_name)

        assert result.found is True
        assert "kasaExtrePDF" in result.source_code
        assert result.file_path == "app/Http/Controllers/KasaRaporController.php"


class TestCreateCodeRetrievalTool:
    def test_creates_tool_with_description(self) -> None:
        mock_retriever = MagicMock(spec=CodeRetriever)

        tool = create_code_retrieval_tool(mock_retriever)

        assert tool is not None
        assert tool.description is not None
        assert "qualified name" in tool.description.lower()

    @pytest.mark.asyncio
    async def test_tool_calls_retriever(self) -> None:
        mock_retriever = MagicMock(spec=CodeRetriever)
        mock_retriever.find_code_snippet = AsyncMock(
            return_value=CodeSnippet(
                qualified_name="test.func",
                source_code="def func(): pass",
                file_path="test.py",
                line_start=1,
                line_end=1,
            )
        )

        tool = create_code_retrieval_tool(mock_retriever)
        result = await tool.function("test.func")

        mock_retriever.find_code_snippet.assert_called_once_with("test.func")
        assert result.qualified_name == "test.func"
