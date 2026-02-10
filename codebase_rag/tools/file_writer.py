"""
This module defines the `FileWriter` class and a factory function for creating
a `pydantic-ai` tool that allows an LLM agent to create new files.

The tool is designed with security in mind, using a decorator to validate that
any requested file path is within the project's root directory, preventing
directory traversal attacks. It ensures that parent directories are created if
they don't exist.

This tool is essential for the agent to create new source files, documentation,
or any other text-based file as part of its tasks.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from pydantic_ai import Tool

from codebase_rag.data_models.schemas import FileCreationResult
from codebase_rag.infrastructure.decorators import validate_project_path

from ..core import constants as cs
from ..core import logs as ls
from ..infrastructure import tool_errors as te
from . import tool_descriptions as td


class FileWriter:
    """
    A tool for safely creating new files within the project root.
    """

    def __init__(self, project_root: str = "."):
        """
        Initializes the FileWriter.

        Args:
            project_root (str): The absolute path to the root of the project.
        """
        self.project_root = Path(project_root).resolve()
        logger.info(ls.FILE_WRITER_INIT.format(root=self.project_root))

    async def create_file(self, file_path: str, content: str) -> FileCreationResult:
        """
        Creates a new file with the given content. This is the entry point before path validation.

        Args:
            file_path (str): The path where the new file will be created, relative to the project root.
            content (str): The content to write to the new file.

        Returns:
            FileCreationResult: An object indicating the success or failure of the operation.
        """
        logger.info(ls.FILE_WRITER_CREATE.format(path=file_path))
        return await self._create_validated(file_path, content)

    @validate_project_path(FileCreationResult, path_arg_name="file_path")
    async def _create_validated(
        self, file_path: Path, content: str
    ) -> FileCreationResult:
        """
        Creates the file after its path has been validated.

        Args:
            file_path (Path): The validated, absolute path for the new file.
            content (str): The content to write.

        Returns:
            FileCreationResult: An object indicating the success or failure.
        """
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding=cs.ENCODING_UTF8)
            logger.info(
                ls.FILE_WRITER_SUCCESS.format(chars=len(content), path=file_path)
            )
            return FileCreationResult(file_path=str(file_path))
        except Exception as e:
            err_msg = te.FILE_WRITER_CREATE.format(path=file_path, error=e)
            logger.error(err_msg)
            return FileCreationResult(file_path=str(file_path), error_message=err_msg)


def create_file_writer_tool(file_writer: FileWriter) -> Tool:
    """
    Factory function to create a `pydantic-ai` Tool for writing files.

    Args:
        file_writer (FileWriter): An instance of the FileWriter class.

    Returns:
        Tool: An initialized `pydantic-ai` Tool.
    """

    async def create_new_file(file_path: str, content: str) -> FileCreationResult:
        """
        Creates a new file with the specified content.

        This tool requires user approval before execution.

        Args:
            file_path (str): The path for the new file, relative to the project root.
            content (str): The content to be written to the file.

        Returns:
            FileCreationResult: An object indicating the result of the file creation.
        """
        return await file_writer.create_file(file_path, content)

    return Tool(
        function=create_new_file,
        name=td.AgenticToolName.CREATE_FILE,
        description=td.FILE_WRITER,
        requires_approval=True,
    )
