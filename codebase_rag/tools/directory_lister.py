"""
This module defines the `DirectoryLister` class and a factory function for
creating a `pydantic-ai` tool that lists the contents of a directory.

The tool provides a safe way for the LLM agent to explore the file system
within the confines of the project root. It includes path validation to prevent
directory traversal attacks.

This is a fundamental tool for the agent to understand the layout of the
codebase and discover files to read or analyze.
"""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger
from pydantic_ai import Tool

from ..core import logs as ls
from ..infrastructure import exceptions as ex
from ..infrastructure import tool_errors as te
from . import tool_descriptions as td


class DirectoryLister:
    """
    A tool for safely listing the contents of a directory within the project root.
    """

    def __init__(self, project_root: str):
        """
        Initializes the DirectoryLister.

        Args:
            project_root (str): The absolute path to the root of the project.
        """
        self.project_root = Path(project_root).resolve()

    def list_directory_contents(self, directory_path: str) -> str:
        """
        Lists the contents of a specified directory.

        Args:
            directory_path (str): The path to the directory, relative to the project root.

        Returns:
            str: A newline-separated string of the directory's contents, or an
                 error message if the operation fails.
        """
        target_path = self._get_safe_path(directory_path)
        logger.info(ls.DIR_LISTING.format(path=target_path))

        try:
            if not target_path.is_dir():
                return te.DIRECTORY_INVALID.format(path=directory_path)

            if contents := os.listdir(target_path):
                return "\n".join(contents)
            return te.DIRECTORY_EMPTY.format(path=directory_path)

        except Exception as e:
            logger.error(ls.DIR_LIST_ERROR.format(path=directory_path, error=e))
            return te.DIRECTORY_LIST_FAILED.format(path=directory_path)

    def _get_safe_path(self, file_path: str) -> Path:
        """
        Resolves a file path and ensures it is within the project root.

        Args:
            file_path (str): The path to resolve.

        Returns:
            Path: The resolved, safe absolute path.

        Raises:
            PermissionError: If the path is outside the project root.
        """
        if Path(file_path).is_absolute():
            safe_path = Path(file_path).resolve()
        else:
            safe_path = (self.project_root / file_path).resolve()

        try:
            safe_path.relative_to(self.project_root.resolve())
        except ValueError as e:
            raise PermissionError(ex.ACCESS_DENIED) from e

        if not str(safe_path).startswith(str(self.project_root.resolve())):
            raise PermissionError(ex.ACCESS_DENIED)

        return safe_path


def create_directory_lister_tool(directory_lister: DirectoryLister) -> Tool:
    """
    Factory function to create a `pydantic-ai` Tool for listing directories.

    Args:
        directory_lister (DirectoryLister): An instance of the DirectoryLister class.

    Returns:
        Tool: An initialized `pydantic-ai` Tool.
    """
    return Tool(
        function=directory_lister.list_directory_contents,
        name=td.AgenticToolName.LIST_DIRECTORY,
        description=td.DIRECTORY_LISTER,
    )
