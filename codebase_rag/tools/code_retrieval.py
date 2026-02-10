"""
This module defines the `CodeRetriever` class and a factory function for creating
a `pydantic-ai` tool to retrieve code snippets from the codebase.

The `CodeRetriever` uses the knowledge graph to find the location (file path and
line numbers) of a code entity (like a function or class) based on its fully
qualified name (FQN). It then reads the corresponding lines from the source file
to construct a `CodeSnippet` object.

This tool is essential for the LLM agent to inspect the implementation details
of specific code elements it discovers through graph queries or other means.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from pydantic_ai import Tool

from codebase_rag.core.constants import ENCODING_UTF8
from codebase_rag.data_models.schemas import CodeSnippet
from codebase_rag.graph_db.cypher_queries import CYPHER_FIND_BY_QUALIFIED_NAME

from ..core import logs as ls
from ..infrastructure import tool_errors as te
from ..services import QueryProtocol
from . import tool_descriptions as td


class CodeRetriever:
    """
    A tool for retrieving specific code snippets from the codebase using the knowledge graph.
    """

    def __init__(self, project_root: str, ingestor: QueryProtocol):
        """
        Initializes the CodeRetriever.

        Args:
            project_root (str): The absolute path to the root of the project.
            ingestor (QueryProtocol): The service for querying the graph database.
        """
        self.project_root = Path(project_root).resolve()
        self.ingestor = ingestor
        logger.info(ls.CODE_RETRIEVER_INIT.format(root=self.project_root))

    async def find_code_snippet(self, qualified_name: str) -> CodeSnippet:
        """
        Finds and retrieves a code snippet based on its fully qualified name.

        It queries the graph to get the file path and line numbers, then reads
        the content from the file.

        Args:
            qualified_name (str): The fully qualified name of the entity to retrieve.

        Returns:
            CodeSnippet: A Pydantic model containing the source code, file path,
                         line numbers, and other metadata. Returns a snippet with
                         `found=False` and an error message if retrieval fails.
        """
        logger.info(ls.CODE_RETRIEVER_SEARCH.format(name=qualified_name))

        params = {"qn": qualified_name}
        try:
            results = self.ingestor.fetch_all(CYPHER_FIND_BY_QUALIFIED_NAME, params)

            if not results:
                return CodeSnippet(
                    qualified_name=qualified_name,
                    source_code="",
                    file_path="",
                    line_start=0,
                    line_end=0,
                    found=False,
                    error_message=te.CODE_ENTITY_NOT_FOUND,
                )

            res = results[0]
            file_path_str = res.get("path")
            start_line = res.get("start")
            end_line = res.get("end")

            if not all([file_path_str, start_line, end_line]):
                return CodeSnippet(
                    qualified_name=qualified_name,
                    source_code="",
                    file_path=file_path_str or "",
                    line_start=0,
                    line_end=0,
                    found=False,
                    error_message=te.CODE_MISSING_LOCATION,
                )

            full_path = self.project_root / file_path_str
            with full_path.open("r", encoding=ENCODING_UTF8) as f:
                all_lines = f.readlines()

            snippet_lines = all_lines[start_line - 1 : end_line]
            source_code = "".join(snippet_lines)

            return CodeSnippet(
                qualified_name=qualified_name,
                source_code=source_code,
                file_path=file_path_str,
                line_start=start_line,
                line_end=end_line,
                docstring=res.get("docstring"),
            )
        except Exception as e:
            logger.exception(ls.CODE_RETRIEVER_ERROR.format(error=e))
            return CodeSnippet(
                qualified_name=qualified_name,
                source_code="",
                file_path="",
                line_start=0,
                line_end=0,
                found=False,
                error_message=str(e),
            )


def create_code_retrieval_tool(code_retriever: CodeRetriever) -> Tool:
    """
    Factory function to create a `pydantic-ai` Tool for code retrieval.

    Args:
        code_retriever (CodeRetriever): An instance of the CodeRetriever class.

    Returns:
        Tool: An initialized `pydantic-ai` Tool.
    """

    async def get_code_snippet(qualified_name: str) -> CodeSnippet:
        """
        Retrieves the source code of a specific function, method, or class.

        Args:
            qualified_name (str): The fully qualified name of the code entity
                                  (e.g., 'my_project.my_module.MyClass.my_method').

        Returns:
            CodeSnippet: An object containing the source code and metadata.
        """
        logger.info(ls.CODE_TOOL_RETRIEVE.format(name=qualified_name))
        return await code_retriever.find_code_snippet(qualified_name)

    return Tool(
        function=get_code_snippet,
        name=td.AgenticToolName.GET_CODE_SNIPPET,
        description=td.CODE_RETRIEVAL,
    )
