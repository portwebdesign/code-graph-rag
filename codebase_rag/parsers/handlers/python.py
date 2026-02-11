from __future__ import annotations

from typing import TYPE_CHECKING

from codebase_rag.core import constants as cs

from ..utils import safe_decode_text
from .base import BaseLanguageHandler

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import ASTNode


class PythonHandler(BaseLanguageHandler):
    """Handler for Python specific AST operations."""

    def extract_decorators(self, node: ASTNode) -> list[str]:
        """
        Extract decorators from a node.

        Args:
            node: The AST node.

        Returns:
            A list of decorator strings.
        """
        if not node.parent or node.parent.type != cs.TS_PY_DECORATED_DEFINITION:
            return []
        return [
            decorator_text
            for child in node.parent.children
            if child.type == cs.TS_PY_DECORATOR
            if (decorator_text := safe_decode_text(child))
        ]
