"""
This module defines the `LanguageHandler` protocol, which specifies the interface
that all language-specific handlers must implement.

By using a `Protocol`, the application can leverage structural typing, ensuring
that any class providing the required methods can be used as a language handler,
without needing to inherit from a specific base class. This promotes loose
coupling and makes it easier to add support for new languages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from codebase_rag.data_models.types_defs import ASTNode
    from codebase_rag.infrastructure.language_spec import LanguageSpec


class LanguageHandler(Protocol):
    """
    A protocol defining the interface for language-specific parsing logic.
    """

    def is_inside_method_with_object_literals(self, node: ASTNode) -> bool:
        """
        Checks if a node is inside a method defined within an object literal (JS/TS specific).

        Args:
            node (ASTNode): The node to check.

        Returns:
            bool: True if the condition is met, False otherwise.
        """
        ...

    def is_class_method(self, node: ASTNode) -> bool:
        """
        Checks if a function node is a method of a class.

        Args:
            node (ASTNode): The function node.

        Returns:
            bool: True if it is a class method, False otherwise.
        """
        ...

    def is_export_inside_function(self, node: ASTNode) -> bool:
        """
        Checks if an export statement is nested inside a function (JS/TS specific).

        Args:
            node (ASTNode): The export node.

        Returns:
            bool: True if the export is nested, False otherwise.
        """
        ...

    def extract_function_name(self, node: ASTNode) -> str | None:
        """
        Extracts the name of a function from its AST node.

        Args:
            node (ASTNode): The function's AST node.

        Returns:
            str | None: The extracted name, or None if not found.
        """
        ...

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
        ...

    def is_function_exported(self, node: ASTNode) -> bool:
        """
        Checks if a function is exported from its module.

        Args:
            node (ASTNode): The function's AST node.

        Returns:
            bool: True if the function is exported, False otherwise.
        """
        ...

    def should_process_as_impl_block(self, node: ASTNode) -> bool:
        """
        Determines if a node should be treated as a Rust `impl` block.

        Args:
            node (ASTNode): The node to check.

        Returns:
            bool: True if it should be processed as an `impl` block.
        """
        ...

    def extract_impl_target(self, node: ASTNode) -> str | None:
        """
        Extracts the target type from a Rust `impl` block.

        Args:
            node (ASTNode): The `impl` block node.

        Returns:
            str | None: The name of the type being implemented.
        """
        ...

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
        ...

    def extract_base_class_name(self, base_node: ASTNode) -> str | None:
        """
        Extracts the name of a base class from an inheritance clause.

        Args:
            base_node (ASTNode): The node representing the base class.

        Returns:
            str | None: The simple name of the base class.
        """
        ...

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
            str | None: The constructed FQN, or None if not applicable.
        """
        ...

    def extract_decorators(self, node: ASTNode) -> list[str]:
        """
        Extracts a list of decorators from a decorated node.

        Args:
            node (ASTNode): The node to extract decorators from.

        Returns:
            list[str]: A list of decorator names.
        """
        ...
