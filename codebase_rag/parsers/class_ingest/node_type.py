"""
This module is responsible for determining the specific `NodeType` of a
class-like AST node.

While the parser might identify a node with a generic "class" capture, this
module refines that into more specific types like `Interface`, `Enum`, `Struct`,
etc., based on the node's `tree-sitter` type. This allows for a more granular
and accurate representation of the codebase in the knowledge graph.
"""

from __future__ import annotations

from loguru import logger
from tree_sitter import Node

from codebase_rag.data_models.types_defs import NodeType

from ...core import constants as cs
from ...core import logs
from ..utils import safe_decode_with_fallback


def determine_node_type(
    class_node: Node,
    class_name: str | None,
    class_qn: str,
    language: cs.SupportedLanguage,
) -> NodeType:
    """
    Determines the specific NodeType for a class-like AST node.

    Args:
        class_node (Node): The AST node to analyze.
        class_name (str | None): The simple name of the class.
        class_qn (str): The fully qualified name of the class.
        language (cs.SupportedLanguage): The language of the source code.

    Returns:
        NodeType: The determined node type (e.g., CLASS, INTERFACE, ENUM).
    """
    match class_node.type:
        case cs.TS_INTERFACE_DECLARATION:
            logger.info(logs.CLASS_FOUND_INTERFACE.format(name=class_name, qn=class_qn))
            return NodeType.INTERFACE
        case cs.TS_ENUM_DECLARATION | cs.TS_ENUM_SPECIFIER | cs.TS_ENUM_CLASS_SPECIFIER:
            logger.info(logs.CLASS_FOUND_ENUM.format(name=class_name, qn=class_qn))
            return NodeType.ENUM
        case cs.TS_TYPE_ALIAS_DECLARATION:
            logger.info(logs.CLASS_FOUND_TYPE.format(name=class_name, qn=class_qn))
            return NodeType.TYPE
        case cs.TS_STRUCT_SPECIFIER:
            logger.info(logs.CLASS_FOUND_STRUCT.format(name=class_name, qn=class_qn))
            return NodeType.CLASS
        case cs.TS_UNION_SPECIFIER:
            logger.info(logs.CLASS_FOUND_UNION.format(name=class_name, qn=class_qn))
            return NodeType.UNION
        case cs.CppNodeType.TEMPLATE_DECLARATION:
            node_type = extract_template_class_type(class_node) or NodeType.CLASS
            logger.info(
                logs.CLASS_FOUND_TEMPLATE.format(
                    node_type=node_type, name=class_name, qn=class_qn
                )
            )
            return node_type
        case cs.CppNodeType.FUNCTION_DEFINITION if language == cs.SupportedLanguage.CPP:
            log_exported_class_type(class_node, class_name, class_qn)
            return NodeType.CLASS
        case _:
            logger.info(logs.CLASS_FOUND_CLASS.format(name=class_name, qn=class_qn))
            return NodeType.CLASS


def log_exported_class_type(
    class_node: Node, class_name: str | None, class_qn: str
) -> None:
    """
    Logs the specific type of an exported C++ class-like entity.

    Args:
        class_node (Node): The AST node of the exported entity.
        class_name (str | None): The simple name of the entity.
        class_qn (str): The fully qualified name of the entity.
    """
    node_text = safe_decode_with_fallback(class_node) if class_node.text else ""
    match _detect_export_type(node_text):
        case cs.CPP_EXPORT_STRUCT_PREFIX:
            logger.info(
                logs.CLASS_FOUND_EXPORTED_STRUCT.format(name=class_name, qn=class_qn)
            )
        case cs.CPP_EXPORT_UNION_PREFIX:
            logger.info(
                logs.CLASS_FOUND_EXPORTED_UNION.format(name=class_name, qn=class_qn)
            )
        case cs.CPP_EXPORT_TEMPLATE_PREFIX:
            logger.info(
                logs.CLASS_FOUND_EXPORTED_TEMPLATE.format(name=class_name, qn=class_qn)
            )
        case _:
            logger.info(
                logs.CLASS_FOUND_EXPORTED_CLASS.format(name=class_name, qn=class_qn)
            )


def _detect_export_type(node_text: str) -> str | None:
    """
    Detects the specific export type prefix in a C++ node's text.

    Args:
        node_text (str): The text content of the node.

    Returns:
        str | None: The detected prefix (e.g., 'export struct '), or None.
    """
    return next(
        (prefix for prefix in cs.CPP_EXPORT_PREFIXES if prefix in node_text),
        None,
    )


def extract_template_class_type(template_node: Node) -> NodeType | None:
    """
    Extracts the specific class-like type from within a C++ template declaration.

    Args:
        template_node (Node): The `template_declaration` AST node.

    Returns:
        NodeType | None: The specific NodeType (CLASS, ENUM, UNION), or None.
    """
    for child in template_node.children:
        match child.type:
            case cs.CppNodeType.CLASS_SPECIFIER | cs.TS_STRUCT_SPECIFIER:
                return NodeType.CLASS
            case cs.TS_ENUM_SPECIFIER:
                return NodeType.ENUM
            case cs.TS_UNION_SPECIFIER:
                return NodeType.UNION
    return None
