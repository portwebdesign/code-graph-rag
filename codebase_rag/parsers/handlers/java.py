"""
This module defines the `JavaHandler`, a language-specific handler for Java.

It implements the `BaseLanguageHandler` protocol to provide Java-specific logic
for tasks like extracting decorators (annotations) and building method qualified
names that include the parameter signature for overloading.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core import constants as cs
from ..java import utils as java_utils
from .base import BaseLanguageHandler

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import ASTNode


class JavaHandler(BaseLanguageHandler):
    """Language handler for Java."""

    def extract_decorators(self, node: ASTNode) -> list[str]:
        """
        Extracts annotations from a Java method or class node.

        Args:
            node (ASTNode): The AST node to extract annotations from.

        Returns:
            list[str]: A list of annotation names.
        """
        return java_utils.extract_from_modifiers_node(node, frozenset()).annotations

    def build_method_qualified_name(
        self,
        class_qn: str,
        method_name: str,
        method_node: ASTNode,
    ) -> str:
        """
        Builds the fully qualified name for a Java method, including its parameter signature
        to handle method overloading.

        Example: `com.mycompany.MyClass.myMethod(String, int)`

        Args:
            class_qn (str): The FQN of the containing class.
            method_name (str): The simple name of the method.
            method_node (ASTNode): The AST node of the method.

        Returns:
            str: The constructed FQN for the method.
        """
        if (method_info := java_utils.extract_method_info(method_node)) and method_info[
            cs.FIELD_PARAMETERS
        ]:
            param_sig = cs.SEPARATOR_COMMA_SPACE.join(method_info[cs.FIELD_PARAMETERS])
            return f"{class_qn}{cs.SEPARATOR_DOT}{method_name}({param_sig})"
        return f"{class_qn}{cs.SEPARATOR_DOT}{method_name}"
