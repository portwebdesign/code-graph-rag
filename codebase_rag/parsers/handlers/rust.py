"""
This module defines the `RustHandler`, a language-specific handler for Rust.

It implements the `BaseLanguageHandler` protocol to provide Rust-specific logic
for tasks like extracting attributes (which function as decorators), building
fully qualified names considering Rust's module system, and handling `impl`
blocks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from codebase_rag.infrastructure.language_spec import LANGUAGE_FQN_SPECS

from ...core import constants as cs
from ...utils.fqn_resolver import resolve_fqn_from_ast
from ..rs import utils as rs_utils
from ..utils import safe_decode_text
from .base import BaseLanguageHandler

if TYPE_CHECKING:
    from pathlib import Path

    from codebase_rag.data_models.types_defs import ASTNode
    from codebase_rag.infrastructure.language_spec import LanguageSpec


class RustHandler(BaseLanguageHandler):
    """Language handler for Rust."""

    def extract_decorators(self, node: ASTNode) -> list[str]:
        """
        Extracts attributes (`#[...]` and `#![...]`) from a Rust node.

        Args:
            node (ASTNode): The AST node to extract attributes from.

        Returns:
            list[str]: A list of attribute strings.
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
        Builds the fully qualified name for a Rust function.

        It first attempts to use the precise FQN resolver and falls back to a
        heuristic-based builder that considers `mod` blocks.

        Args:
            node (ASTNode): The function's AST node.
            module_qn (str): The FQN of the containing file-as-a-module.
            func_name (str): The simple name of the function.
            lang_config (LanguageSpec | None): The language specification.
            file_path (Path | None): The path to the source file.
            repo_path (Path): The root path of the repository.
            project_name (str): The name of the project.

        Returns:
            str: The constructed fully qualified name.
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
        Determines if a node should be treated as a Rust `impl` block.

        Args:
            node (ASTNode): The node to check.

        Returns:
            bool: True if the node is an `impl_item`.
        """
        return node.type == cs.TS_IMPL_ITEM

    def extract_impl_target(self, node: ASTNode) -> str | None:
        """
        Extracts the target struct or trait from a Rust `impl` block.

        Args:
            node (ASTNode): The `impl_item` AST node.

        Returns:
            str | None: The name of the struct or trait being implemented.
        """
        return rs_utils.extract_impl_target(node)
