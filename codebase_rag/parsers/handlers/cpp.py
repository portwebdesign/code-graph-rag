from __future__ import annotations

from typing import TYPE_CHECKING

from codebase_rag.core import constants as cs
from codebase_rag.infrastructure.language_spec import LANGUAGE_FQN_SPECS

from ...utils.fqn_resolver import resolve_fqn_from_ast
from ..cpp import utils as cpp_utils
from ..utils import safe_decode_text
from .base import BaseLanguageHandler

if TYPE_CHECKING:
    from pathlib import Path

    from codebase_rag.data_models.types_defs import ASTNode
    from codebase_rag.infrastructure.language_spec import LanguageSpec


class CppHandler(BaseLanguageHandler):
    """Handler for C++ specific AST operations."""

    def extract_function_name(self, node: ASTNode) -> str | None:
        """
        Extract the name of a function from checking node.

        Args:
            node: The function AST node.

        Returns:
            The function name or a generated lambda name.
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
        Build the qualified name for a function.

        Args:
            node: The function node.
            module_qn: The module qualified name.
            func_name: The function name.
            lang_config: The language configuration.
            file_path: The file path (required for FQN resolution).
            repo_path: The repository root.
            project_name: The project name.

        Returns:
            The fully qualified function name.
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
        Check if a function is exported.

        Args:
            node: The function node.

        Returns:
            True if the function is exported, False otherwise.
        """
        return cpp_utils.is_exported(node)

    def extract_base_class_name(self, base_node: ASTNode) -> str | None:
        """
        Extract the name of a base class.

        Args:
            base_node: The base class AST node.

        Returns:
            The base class name.
        """
        if base_node.type == cs.TS_TEMPLATE_TYPE:
            if (
                name_node := base_node.child_by_field_name(cs.TS_FIELD_NAME)
            ) and name_node.text:
                return safe_decode_text(name_node)

        return safe_decode_text(base_node) if base_node.text else None
