"""
This module defines the `FileReader` class and a factory function for creating
a `pydantic-ai` tool that allows an LLM agent to read the contents of a file.

The tool is designed with security in mind, using a decorator to validate that
any requested file path is within the project's root directory, preventing
directory traversal attacks. It also handles potential errors like file-not-found
and attempts to read binary files as text.

This is a fundamental tool for the agent to access the content of source code
and other text-based files in the repository.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from pydantic_ai import Tool

from codebase_rag.data_models.schemas import FileReadResult
from codebase_rag.infrastructure.decorators import validate_project_path

from ..core import constants as cs
from ..core import logs as ls
from ..infrastructure import tool_errors as te
from . import tool_descriptions as td


class FileReader:
    """
    A tool for safely reading the contents of a file within the project root.
    """

    def __init__(self, project_root: str = "."):
        """
        Initializes the FileReader.

        Args:
            project_root (str): The absolute path to the root of the project.
        """
        self.project_root = Path(project_root).resolve()
        logger.info(ls.FILE_READER_INIT.format(root=self.project_root))

    async def read_file(self, file_path: str) -> FileReadResult:
        """
        Reads the content of a file. This is the entry point before path validation.

        Args:
            file_path (str): The path to the file, relative to the project root.

        Returns:
            FileReadResult: An object containing the file content or an error message.
        """
        logger.info(ls.TOOL_FILE_READ.format(path=file_path))
        return await self._read_validated(file_path)

    @validate_project_path(FileReadResult, path_arg_name="file_path")
    async def _read_validated(self, file_path: Path) -> FileReadResult:
        """
        Reads the content of a file after its path has been validated.

        Args:
            file_path (Path): The validated, absolute path to the file.

        Returns:
            FileReadResult: An object containing the file content or an error message.
        """
        try:
            if not file_path.is_file():
                return FileReadResult(
                    file_path=str(file_path), error_message=te.FILE_NOT_FOUND
                )

            if file_path.suffix.lower() in cs.BINARY_EXTENSIONS:
                error_msg = te.BINARY_FILE.format(path=file_path)
                logger.warning(ls.TOOL_FILE_BINARY.format(message=error_msg))
                return FileReadResult(file_path=str(file_path), error_message=error_msg)

            try:
                content = file_path.read_text(encoding=cs.ENCODING_UTF8)
                logger.info(ls.TOOL_FILE_READ_SUCCESS.format(path=file_path))
                return FileReadResult(file_path=str(file_path), content=content)
            except UnicodeDecodeError:
                error_msg = te.UNICODE_DECODE.format(path=file_path)
                logger.warning(ls.TOOL_FILE_BINARY.format(message=error_msg))
                return FileReadResult(file_path=str(file_path), error_message=error_msg)

        except Exception as e:
            logger.error(ls.FILE_READER_ERR.format(path=file_path, error=e))
            return FileReadResult(
                file_path=str(file_path),
                error_message=ls.UNEXPECTED.format(error=e),
            )


def create_file_reader_tool(file_reader: FileReader) -> Tool:
    """
    Factory function to create a `pydantic-ai` Tool for reading files.

    Args:
        file_reader (FileReader): An instance of the FileReader class.

    Returns:
        Tool: An initialized `pydantic-ai` Tool.
    """

    async def read_file_content(file_path: str) -> str:
        """
        Reads and returns the full content of a specified text file.

        Args:
            file_path (str): The path to the file, relative to the project root.

        Returns:
            str: The content of the file, or an error message if it cannot be read.
        """
        result = await file_reader.read_file(file_path)
        if result.error_message:
            return te.ERROR_WRAPPER.format(message=result.error_message)
        return result.content or ""

    return Tool(
        function=read_file_content,
        name=td.AgenticToolName.READ_FILE,
        description=td.FILE_READER,
    )
