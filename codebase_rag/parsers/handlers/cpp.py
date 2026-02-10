"""
This module defines the `CppHandler`, a language-specific handler for C++.

It implements the `BaseLanguageHandler` protocol to provide C++-specific logic
for tasks like extracting function names, building fully qualified names (FQNs),
and determining if a function is exported. This class encapsulates the unique
parsing and name resolution rules required for C++.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from codebase_rag.infrastructure.language_spec import LANGUAGE_FQN_SPECS

from ...core import constants as cs
from ...utils.fqn_resolver import resolve_fqn_from_ast
from ..cpp import utils as cpp_utils
from ..utils import safe_decode_text
from .base import BaseLanguageHandler

if TYPE_CHECKING:
    from pathlib import Path

    from codebase_rag.data_models.types_defs import ASTNode
    from codebase_rag.infrastructure.language_spec import LanguageSpec


class CppHandler(BaseLanguageHandler):
    """Language handler for C++."""

    def extract_function_name(self, node: ASTNode) -> str | None:
        """
        Extracts the name of a C++ function or lambda from its AST node.

        Args:
            node (ASTNode): The AST node of the function or lambda.

        Returns:
            str | None: The extracted name, or a generated name for lambdas, or None.
        """
        if func_name := cpp_utils.extract_function_name(node):
            return func_name

        if node.type == cs.TS_CPP_LAMBDA_EXPRESSION:
            return f"lambda_{node.start_point[0]}_{node.start_point[1]}"

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
        Builds the fully qualified name for a C++ function.

        It first attempts to use the precise FQN resolver and falls back to a
        heuristic-based builder if that fails.

        Args:
            node (ASTNode): The function's AST node.
            module_qn (str): The FQN of the containing module.
            func_name (str): The simple name of the function.
            lang_config (LanguageSpec | None): The language specification (unused for C++).
            file_path (Path | None): The path to the source file.
            repo_path (Path): The root path of the repository.
            project_name (str): The name of the project.

        Returns:
            str: The constructed fully qualified name.
        """
        if (
            fqn_config := LANGUAGE_FQN_SPECS.get(cs.SupportedLanguage.CPP)
        ) and file_path:
            if func_qn := resolve_fqn_from_ast(
                node, file_path, repo_path, project_name, fqn_config
            ):
                return func_qn

        return cpp_utils.build_qualified_name(node, module_qn, func_name)

    def is_function_exported(self, node: ASTNode) -> bool:
        """
        Checks if a C++ function is exported from a module.

        Args:
            node (ASTNode): The function's AST node.

        Returns:
            bool: True if the function is exported, False otherwise.
        """
        return cpp_utils.is_exported(node)

    def extract_base_class_name(self, base_node: ASTNode) -> str | None:
        """
        Extracts the name of a base class from its AST node in an inheritance clause.

        Args:
            base_node (ASTNode): The AST node representing the base class.

        Returns:
            str | None: The simple name of the base class.
        """
        if base_node.type == cs.TS_TEMPLATE_TYPE:
            if (
                name_node := base_node.child_by_field_name(cs.TS_FIELD_NAME)
            ) and name_node.text:
                return safe_decode_text(name_node)

        return safe_decode_text(base_node) if base_node.text else None
