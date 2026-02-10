"""
This module defines the `BaseLanguageHandler`, an abstract base class that
outlines the protocol for language-specific parsing logic.

It provides default implementations for common tasks, which can be overridden by
concrete handler classes for specific languages. This allows the main parsing
processors to work with a consistent interface while delegating language-specific
details to the appropriate handler.

The handler protocol covers tasks like:
-   Extracting names from function and class nodes.
-   Building fully qualified names (FQNs).
-   Detecting exported functions.
-   Extracting decorators and base class names.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core import constants as cs
from ..utils import safe_decode_text

if TYPE_CHECKING:
    from pathlib import Path

    from codebase_rag.data_models.types_defs import ASTNode
    from codebase_rag.infrastructure.language_spec import LanguageSpec


class BaseLanguageHandler:
    """
    Abstract base class for language-specific parsing handlers.
    Provides default implementations for common parsing tasks.
    """

    def is_inside_method_with_object_literals(self, node: ASTNode) -> bool:
        """
        Checks if a node is inside a method that uses object literals (JS/TS specific).

        Args:
            node (ASTNode): The node to check.

        Returns:
            bool: False by default.
        """
        return False

    def is_class_method(self, node: ASTNode) -> bool:
        """
        Checks if a function node is a class method.

        Args:
            node (ASTNode): The function node.

        Returns:
            bool: False by default.
        """
        return False

    def is_export_inside_function(self, node: ASTNode) -> bool:
        """
        Checks if an export statement is nested inside a function (JS/TS specific).

        Args:
            node (ASTNode): The export node.

        Returns:
            bool: False by default.
        """
        return False

    def extract_function_name(self, node: ASTNode) -> str | None:
        """
        Extracts the name of a function from its AST node.

        Args:
            node (ASTNode): The function's AST node.

        Returns:
            str | None: The extracted name, or None if not found.
        """
        if (name_node := node.child_by_field_name(cs.TS_FIELD_NAME)) and name_node.text:
            return safe_decode_text(name_node)
        return None

    def build_function_qualified_name(
        self,
        node: ASTNode,
        module_qn: str,
        func_name: str,
        lang_config: LanguageSpec | None,
        file_path: Path | None,
        repo_path: Path,
        project_name: str,
    ) -> str:
        """
        Builds the fully qualified name for a function.

        Args:
            node (ASTNode): The function's AST node.
            module_qn (str): The FQN of the containing module.
            func_name (str): The simple name of the function.
            lang_config (LanguageSpec | None): The language specification.
            file_path (Path | None): The path to the source file.
            repo_path (Path): The root path of the repository.
            project_name (str): The name of the project.

        Returns:
            str: The constructed fully qualified name.
        """
        return f"{module_qn}{cs.SEPARATOR_DOT}{func_name}"

    def is_function_exported(self, node: ASTNode) -> bool:
        """
        Checks if a function is exported from its module.

        Args:
            node (ASTNode): The function's AST node.

        Returns:
            bool: False by default.
        """
        return False

    def should_process_as_impl_block(self, node: ASTNode) -> bool:
        """
        Determines if a node should be treated as a Rust `impl` block.

        Args:
            node (ASTNode): The node to check.

        Returns:
            bool: False by default.
        """
        return False

    def extract_impl_target(self, node: ASTNode) -> str | None:
        """
        Extracts the target type from a Rust `impl` block.

        Args:
            node (ASTNode): The `impl` block node.

        Returns:
            str | None: None by default.
        """
        return None

    def build_method_qualified_name(
        self,
        class_qn: str,
        method_name: str,
        method_node: ASTNode,
    ) -> str:
        """
        Builds the fully qualified name for a method.

        Args:
            class_qn (str): The FQN of the containing class.
            method_name (str): The simple name of the method.
            method_node (ASTNode): The method's AST node.

        Returns:
            str: The constructed FQN for the method.
        """
        return f"{class_qn}{cs.SEPARATOR_DOT}{method_name}"

    def extract_base_class_name(self, base_node: ASTNode) -> str | None:
        """
        Extracts the name of a base class from an inheritance clause.

        Args:
            base_node (ASTNode): The node representing the base class.

        Returns:
            str | None: The simple name of the base class.
        """
        return safe_decode_text(base_node) if base_node.text else None

    def extract_decorators(self, node: ASTNode) -> list[str]:
        """
        Extracts a list of decorators from a decorated node.

        Args:
            node (ASTNode): The node to extract decorators from.

        Returns:
            list[str]: An empty list by default.
        """
        return []

    def build_nested_function_qn(
        self,
        func_node: ASTNode,
        module_qn: str,
        func_name: str,
        lang_config: LanguageSpec,
    ) -> str | None:
        """
        Builds the FQN for a nested function.

        Args:
            func_node (ASTNode): The nested function's AST node.
            module_qn (str): The FQN of the containing module.
            func_name (str): The simple name of the function.
            lang_config (LanguageSpec): The language specification.

        Returns:
            str | None: The constructed FQN, or None if it's not nested.
        """
        if (
            path_parts := self._collect_ancestor_path_parts(func_node, lang_config)
        ) is None:
            return None
        return self._format_nested_qn(module_qn, path_parts, func_name)

    def _collect_ancestor_path_parts(
        self,
        func_node: ASTNode,
        lang_config: LanguageSpec,
    ) -> list[str] | None:
        """Collects the names of ancestor scopes to build a nested FQN path."""
        path_parts: list[str] = []
        current = func_node.parent

        while current and current.type not in lang_config.module_node_types:
            if current.type in lang_config.function_node_types:
                if name := self._extract_node_name(current):
                    path_parts.append(name)
            elif current.type in lang_config.class_node_types:
                return None
            current = current.parent

        path_parts.reverse()
        return path_parts

    def _extract_node_name(self, node: ASTNode) -> str | None:
        """Extracts a name from a node using the 'name' field."""
        if (name_node := node.child_by_field_name(cs.TS_FIELD_NAME)) and name_node.text:
            return safe_decode_text(name_node)
        return None

    def _format_nested_qn(
        self, module_qn: str, path_parts: list[str], func_name: str
    ) -> str:
        """Formats the final nested FQN string."""
        if path_parts:
            return f"{module_qn}{cs.SEPARATOR_DOT}{cs.SEPARATOR_DOT.join(path_parts)}{cs.SEPARATOR_DOT}{func_name}"
        return f"{module_qn}{cs.SEPARATOR_DOT}{func_name}"
