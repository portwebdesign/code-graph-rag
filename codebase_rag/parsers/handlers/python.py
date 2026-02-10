"""
This module defines the `PythonHandler`, a language-specific handler for Python.

It implements the `BaseLanguageHandler` protocol to provide Python-specific logic
for tasks like extracting decorator names from a decorated function or class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core import constants as cs
from ..utils import safe_decode_text
from .base import BaseLanguageHandler

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import ASTNode


class PythonHandler(BaseLanguageHandler):
    """Language handler for Python."""

    def extract_decorators(self, node: ASTNode) -> list[str]:
        """
        Extracts decorator names from a decorated Python function or class node.

        Args:
            node (ASTNode): The AST node of the function or class definition.

        Returns:
            list[str]: A list of decorator names as strings.
        """
        if not node.parent or node.parent.type != cs.TS_PY_DECORATED_DEFINITION:
            return []
        return [
            decorator_text
            for child in node.parent.children
            if child.type == cs.TS_PY_DECORATOR
            if (decorator_text := safe_decode_text(child))
        ]
