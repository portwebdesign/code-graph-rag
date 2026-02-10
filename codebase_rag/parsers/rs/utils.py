"""
This module provides utility functions specifically for parsing Rust source code.

It contains helpers for handling Rust's complex module and import (`use`) system,
as well as its trait implementation (`impl`) blocks.

Key functionalities:
-   `extract_use_imports`: Recursively parses a `use` declaration to extract all
    imported items, including aliases, wildcards, and nested groups.
-   `extract_impl_target`: Finds the name of the trait or struct that an `impl`
    block is for.
-   `build_module_path`: Traverses up the AST from a given node to construct its
    module path based on parent `mod` items.
"""

from collections.abc import Sequence

from tree_sitter import Node

from ...core import constants as cs
from ..utils import safe_decode_text


def _collect_path_parts(node: Node, parts: list[str]) -> None:
    """Recursively collects parts of a qualified path."""
    match node.type:
        case cs.TS_IDENTIFIER | cs.TS_TYPE_IDENTIFIER:
            if part := safe_decode_text(node):
                parts.append(part)
        case cs.TS_SCOPED_IDENTIFIER | cs.TS_RS_SCOPED_TYPE_IDENTIFIER:
            for child in node.children:
                if child.type != cs.SEPARATOR_DOUBLE_COLON:
                    _collect_path_parts(child, parts)
        case cs.TS_RS_CRATE | cs.KEYWORD_SUPER | cs.KEYWORD_SELF:
            if part := safe_decode_text(node):
                parts.append(part)


def _extract_path_from_node(node: Node) -> str:
    """Extracts a full path string (e.g., `std::collections::HashMap`) from a node."""
    match node.type:
        case cs.TS_IDENTIFIER | cs.TS_TYPE_IDENTIFIER:
            return safe_decode_text(node) or ""
        case cs.TS_SCOPED_IDENTIFIER | cs.TS_RS_SCOPED_TYPE_IDENTIFIER:
            parts: list[str] = []
            _collect_path_parts(node, parts)
            return cs.SEPARATOR_DOUBLE_COLON.join(parts)
        case cs.TS_RS_CRATE | cs.KEYWORD_SUPER | cs.KEYWORD_SELF:
            return safe_decode_text(node) or ""
        case _:
            return ""


def _process_use_tree(node: Node, base_path: str, imports: dict[str, str]) -> None:
    """Recursively processes a `use` tree to extract all imports."""
    match node.type:
        case cs.TS_IDENTIFIER | cs.TS_TYPE_IDENTIFIER:
            if name := safe_decode_text(node):
                full_path = (
                    f"{base_path}{cs.SEPARATOR_DOUBLE_COLON}{name}"
                    if base_path
                    else name
                )
                imports[name] = full_path

        case cs.TS_SCOPED_IDENTIFIER | cs.TS_RS_SCOPED_TYPE_IDENTIFIER:
            if (full_path := _extract_path_from_node(node)) and (
                parts := full_path.split(cs.SEPARATOR_DOUBLE_COLON)
            ):
                imported_name = parts[-1]
                imports[imported_name] = full_path

        case cs.TS_RS_USE_AS_CLAUSE:
            _process_use_as_clause(node, base_path, imports)

        case cs.TS_RS_USE_WILDCARD:
            _process_use_wildcard(node, base_path, imports)

        case cs.TS_RS_USE_LIST:
            for child in node.children:
                if child.type not in cs.RS_USE_LIST_DELIMITERS:
                    _process_use_tree(child, base_path, imports)

        case cs.TS_RS_SCOPED_USE_LIST:
            _process_scoped_use_list(node, base_path, imports)

        case cs.KEYWORD_SELF:
            imports[cs.KEYWORD_SELF] = base_path or cs.KEYWORD_SELF

        case _:
            for child in node.children:
                _process_use_tree(child, base_path, imports)


def _process_use_as_clause(node: Node, base_path: str, imports: dict[str, str]) -> None:
    """Processes a `use ... as ...` alias clause."""
    original_path = ""
    alias_name = ""

    children = [c for c in node.children if c.type != cs.TS_RS_KEYWORD_AS]
    if len(children) == 2:
        path_node, alias_node = children

        if path_node.type == cs.KEYWORD_SELF:
            original_path = base_path or cs.KEYWORD_SELF
        else:
            original_path = _extract_path_from_node(path_node)
            if base_path and original_path:
                original_path = f"{base_path}{cs.SEPARATOR_DOUBLE_COLON}{original_path}"
            elif base_path:
                original_path = base_path

        alias_name = safe_decode_text(alias_node) or ""

    if alias_name and original_path:
        imports[alias_name] = original_path


def _process_use_wildcard(node: Node, base_path: str, imports: dict[str, str]) -> None:
    """Processes a wildcard `*` import."""
    if wildcard_base := next(
        (
            _extract_path_from_node(child)
            for child in node.children
            if child.type != cs.RS_WILDCARD_PREFIX
        ),
        "",
    ):
        wildcard_key = f"{cs.RS_WILDCARD_PREFIX}{wildcard_base}"
        imports[wildcard_key] = wildcard_base
    elif base_path:
        wildcard_key = f"{cs.RS_WILDCARD_PREFIX}{base_path}"
        imports[wildcard_key] = base_path


def _process_scoped_use_list(
    node: Node, base_path: str, imports: dict[str, str]
) -> None:
    """Processes a scoped use list, e.g., `std::{collections, io}`."""
    new_base_path = ""

    for child in node.children:
        match child.type:
            case (
                cs.TS_IDENTIFIER
                | cs.TS_SCOPED_IDENTIFIER
                | cs.TS_RS_CRATE
                | cs.KEYWORD_SUPER
                | cs.KEYWORD_SELF
            ):
                new_base_path = _extract_path_from_node(child)
            case cs.TS_RS_USE_LIST:
                final_base = (
                    f"{base_path}{cs.SEPARATOR_DOUBLE_COLON}{new_base_path}"
                    if base_path
                    else new_base_path
                )
                _process_use_tree(child, final_base, imports)


def extract_impl_target(impl_node: Node) -> str | None:
    """
    Extracts the target struct or trait from a Rust `impl` block.

    Args:
        impl_node (Node): The `impl_item` AST node.

    Returns:
        str | None: The name of the struct or trait being implemented.
    """
    if impl_node.type != cs.TS_IMPL_ITEM:
        return None

    for i in range(impl_node.child_count):
        if impl_node.field_name_for_child(i) == cs.FIELD_TYPE:
            type_node = impl_node.child(i)
            if type_node is None:
                continue
            match type_node.type:
                case cs.TS_GENERIC_TYPE:
                    for child in type_node.children:
                        if child.type == cs.TS_TYPE_IDENTIFIER:
                            return safe_decode_text(child)
                case cs.TS_TYPE_IDENTIFIER:
                    return safe_decode_text(type_node)
                case cs.TS_RS_SCOPED_TYPE_IDENTIFIER:
                    for child in type_node.children:
                        if child.type == cs.TS_TYPE_IDENTIFIER:
                            if name := safe_decode_text(child):
                                return name

    return None


def extract_use_imports(use_node: Node) -> dict[str, str]:
    """
    Extracts all imports from a Rust `use` declaration.

    Args:
        use_node (Node): The `use_declaration` AST node.

    Returns:
        dict[str, str]: A dictionary mapping local names to their full import paths.
    """
    if use_node.type != cs.TS_USE_DECLARATION:
        return {}

    imports: dict[str, str] = {}

    argument_node = use_node.child_by_field_name(cs.RS_FIELD_ARGUMENT)
    if argument_node:
        _process_use_tree(argument_node, "", imports)

    return imports


def build_module_path(
    node: Node,
    include_impl_targets: bool = False,
    include_classes: bool = False,
    class_node_types: Sequence[str] | None = None,
) -> list[str]:
    """
    Builds a module path for a node by traversing up its ancestor `mod` items.

    Args:
        node (Node): The starting node.
        include_impl_targets (bool): Whether to include `impl` targets in the path.
        include_classes (bool): Whether to include class-like structures in the path.
        class_node_types (Sequence[str] | None): The node types to consider as classes.

    Returns:
        list[str]: A list of path parts representing the module hierarchy.
    """
    path_parts: list[str] = []
    current = node.parent

    while current and current.type != cs.TS_RS_SOURCE_FILE:
        match current.type:
            case cs.TS_RS_MOD_ITEM:
                if name_node := current.child_by_field_name(cs.FIELD_NAME):
                    text = name_node.text
                    if text is not None:
                        path_parts.append(text.decode(cs.RS_ENCODING_UTF8))
            case cs.TS_IMPL_ITEM if include_impl_targets:
                if impl_target := extract_impl_target(current):
                    path_parts.append(impl_target)
            case _ if (
                include_classes
                and class_node_types
                and current.type in class_node_types
            ):
                if current.type != cs.TS_IMPL_ITEM:
                    if name_node := current.child_by_field_name(cs.FIELD_NAME):
                        text = name_node.text
                        if text is not None:
                            path_parts.append(text.decode(cs.RS_ENCODING_UTF8))

        current = current.parent

    path_parts.reverse()
    return path_parts
