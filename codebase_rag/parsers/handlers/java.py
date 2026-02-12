from __future__ import annotations

from typing import TYPE_CHECKING

from codebase_rag.core import constants as cs

from ..languages.java import utils as java_utils
from .base import BaseLanguageHandler

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import ASTNode


class JavaHandler(BaseLanguageHandler):
    """Handler for Java specific AST operations."""

    def extract_decorators(self, node: ASTNode) -> list[str]:
        """
        Extract method decorators (annotations).

        Args:
            node: The method node.

        Returns:
            A list of annotation strings.
        """
        return java_utils.extract_from_modifiers_node(node, frozenset()).annotations

    def build_method_qualified_name(
        self,
        class_qn: str,
        method_name: str,
        method_node: ASTNode,
    ) -> str:
        """
        Build the qualified name for a method (including parameters).

        Args:
            class_qn: The class qualified name.
            method_name: The method name.
            method_node: The method AST node.

        Returns:
            The unique qualified name for the method.
        """
        if (method_info := java_utils.extract_method_info(method_node)) and method_info[
            cs.FIELD_PARAMETERS
        ]:
            param_sig = cs.SEPARATOR_COMMA_SPACE.join(method_info[cs.FIELD_PARAMETERS])
            return f"{class_qn}{cs.SEPARATOR_DOT}{method_name}({param_sig})"
        return f"{class_qn}{cs.SEPARATOR_DOT}{method_name}"
