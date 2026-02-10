"""
This module provides utility functions specifically for the class ingestion process.

These helpers are used by other modules within the `class_ingest` package to
perform common, small-scale tasks related to AST node manipulation.
"""

from __future__ import annotations

from tree_sitter import Node

from ..utils import safe_decode_with_fallback


def decode_node_stripped(node: Node) -> str:
    """
    Safely decodes a tree-sitter node's text and strips leading/trailing whitespace.

    Args:
        node (Node): The node to decode.

    Returns:
        str: The decoded and stripped text, or an empty string if the node has no text.
    """
    return safe_decode_with_fallback(node).strip() if node.text else ""


def find_child_by_type(node: Node, node_type: str) -> Node | None:
    """
    Finds the first direct child of a node that matches a given type.

    Args:
        node (Node): The parent node to search within.
        node_type (str): The tree-sitter node type to find.

    Returns:
        Node | None: The first matching child node, or None if not found.
    """
    return next((c for c in node.children if c.type == node_type), None)
