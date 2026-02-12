from __future__ import annotations

from loguru import logger
from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import NodeType
from codebase_rag.parsers.core.utils import safe_decode_with_fallback

from ... import logs


def determine_node_type(
    class_node: Node,
    class_name: str | None,
    class_qn: str,
    language: cs.SupportedLanguage,
) -> NodeType:
    """
    Determine the `NodeType` for a given class-like AST node.

    Args:
        class_node: The AST node.
        class_name: The simple name of the class (for logging).
        class_qn: The qualified name of the class (for logging).
        language: The programming language.

    Returns:
        The determined `NodeType` (CLASS, INTERFACE, ENUM, etc.).
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
    Log the specific type of C++ exported class/struct/union.

    Args:
        class_node: The AST node to check.
        class_name: The class name.
        class_qn: The class qualified name.
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
    return next(
        (prefix for prefix in cs.CPP_EXPORT_PREFIXES if prefix in node_text),
        None,
    )


def extract_template_class_type(template_node: Node) -> NodeType | None:
    """
    Determine the inner type of a C++ template declaration.

    Inspects the children of the template node to see if it wraps a class, struct,
    enum, or union.

    Args:
        template_node: The template declaration node.

    Returns:
        The inner `NodeType` or None if not found.
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
