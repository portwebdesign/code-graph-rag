from __future__ import annotations

from tree_sitter import Node

from codebase_rag.parsers.core.utils import safe_decode_with_fallback


def decode_node_stripped(node: Node) -> str:
    """
    Safely decode a node's text and strip whitespace.

    Args:
        node: The AST node.

    Returns:
        The stripped text content, or an empty string if decoding fails.
    """
    return safe_decode_with_fallback(node).strip() if node.text else ""


def find_child_by_type(node: Node, node_type: str) -> Node | None:
    """
    Find the first child node of a specific type.

    Args:
        node: The parent AST node.
        node_type: The type string to search for (e.g. 'class_body').

    Returns:
        The first matching child node, or None if not found.
    """
    return next((c for c in node.children if c.type == node_type), None)
