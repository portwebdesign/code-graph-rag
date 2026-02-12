from __future__ import annotations

from typing import TYPE_CHECKING

from codebase_rag.core import constants as cs
from codebase_rag.infrastructure.language_spec import LANGUAGE_FQN_SPECS
from codebase_rag.parsers.core.utils import safe_decode_text
from codebase_rag.parsers.languages.rs import utils as rs_utils

from ...utils.fqn_resolver import resolve_fqn_from_ast
from .base import BaseLanguageHandler

if TYPE_CHECKING:
    from pathlib import Path

    from codebase_rag.data_models.types_defs import ASTNode
    from codebase_rag.infrastructure.language_spec import LanguageSpec


class RustHandler(BaseLanguageHandler):
    """Handler for Rust specific AST operations."""

    def extract_decorators(self, node: ASTNode) -> list[str]:
        """
        Extract attributes (decorators) from a node.

        Args:
            node: The AST node.

        Returns:
            A list of attribute strings.
        """
        outer_decorators: list[str] = []
        sibling = node.prev_named_sibling
        while sibling and sibling.type == cs.TS_RS_ATTRIBUTE_ITEM:
            if attr_text := safe_decode_text(sibling):
                outer_decorators.append(attr_text)
            sibling = sibling.prev_named_sibling

        decorators = list(reversed(outer_decorators))

        nodes_to_search = [node]
        if body_node := node.child_by_field_name(cs.FIELD_BODY):
            nodes_to_search.append(body_node)

        for search_node in nodes_to_search:
            decorators.extend(
                attr_text
                for child in search_node.children
                if child.type == cs.TS_RS_INNER_ATTRIBUTE_ITEM
                if (attr_text := safe_decode_text(child))
            )

        return decorators

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
            fqn_config := LANGUAGE_FQN_SPECS.get(cs.SupportedLanguage.RUST)
        ) and file_path:
            if func_qn := resolve_fqn_from_ast(
                node, file_path, repo_path, project_name, fqn_config
            ):
                return func_qn

        if path_parts := rs_utils.build_module_path(node):
            return f"{module_qn}{cs.SEPARATOR_DOT}{cs.SEPARATOR_DOT.join(path_parts)}{cs.SEPARATOR_DOT}{func_name}"
        return f"{module_qn}{cs.SEPARATOR_DOT}{func_name}"

    def should_process_as_impl_block(self, node: ASTNode) -> bool:
        """
        Check if the node should be processed as an impl block.

        Args:
            node: The AST node.

        Returns:
            True if it is an impl block, False otherwise.
        """
        return node.type == cs.TS_IMPL_ITEM

    def extract_impl_target(self, node: ASTNode) -> str | None:
        """
        Extract the target (type) of an impl block.

        Args:
            node: The impl block AST node.

        Returns:
            The target type name, or None if not found.
        """
        return rs_utils.extract_impl_target(node)
