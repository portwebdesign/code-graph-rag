"""
This module provides utility functions that are shared across different parsers
in the application.

These helpers perform common tasks related to AST traversal, node manipulation,
and data ingestion, reducing code duplication and centralizing common logic.

Key functionalities:
-   `get_function_captures`: Extracts function-related nodes from an AST using
    a tree-sitter query.
-   `safe_decode_text`: Safely decodes byte strings from tree-sitter nodes.
-   `ingest_method`: A generic function to ingest a method definition into the graph.
-   `is_method_node`: Determines if a function node is a method by checking its
    ancestors in the AST.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import TYPE_CHECKING, NamedTuple

from loguru import logger
from tree_sitter import Node, Query, QueryCursor

from codebase_rag.data_models.types_defs import (
    ASTNode,
    LanguageQueries,
    NodeType,
    PropertyDict,
    SimpleNameLookup,
    TreeSitterNodeProtocol,
)

from ..core import constants as cs
from ..core import logs

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import FunctionRegistryTrieProtocol
    from codebase_rag.infrastructure.language_spec import LanguageSpec

    from ..services import IngestorProtocol


class FunctionCapturesResult(NamedTuple):
    """The result of a function capture query."""

    lang_config: LanguageSpec
    captures: dict[str, list[ASTNode]]


def get_function_captures(
    root_node: ASTNode,
    language: cs.SupportedLanguage,
    queries: dict[cs.SupportedLanguage, LanguageQueries],
) -> FunctionCapturesResult | None:
    """
    Executes a tree-sitter query to capture all function nodes in an AST.

    Args:
        root_node (ASTNode): The root node of the AST to query.
        language (cs.SupportedLanguage): The language of the source code.
        queries (dict): A dictionary of pre-compiled queries.

    Returns:
        FunctionCapturesResult | None: A named tuple containing the language
            configuration and the captured nodes, or None if no query is available.
    """
    lang_queries = queries[language]
    lang_config = lang_queries[cs.QUERY_CONFIG]

    if not (query := lang_queries[cs.QUERY_FUNCTIONS]):
        return None

    cursor = QueryCursor(query)
    captures = cursor.captures(root_node)
    return FunctionCapturesResult(lang_config, captures)


@lru_cache(maxsize=10000)
def _cached_decode_bytes(text_bytes: bytes) -> str:
    """Caches the decoding of byte strings to avoid repeated work."""
    return text_bytes.decode(cs.ENCODING_UTF8)


def safe_decode_text(node: ASTNode | TreeSitterNodeProtocol | None) -> str | None:
    """
    Safely decodes the text content of a tree-sitter node.

    Args:
        node (ASTNode | TreeSitterNodeProtocol | None): The node to decode.

    Returns:
        str | None: The decoded text, or None if the node or its text is None.
    """
    if node is None or (text_bytes := node.text) is None:
        return None
    if isinstance(text_bytes, bytes):
        return _cached_decode_bytes(text_bytes)
    return str(text_bytes)


def get_query_cursor(query: Query) -> QueryCursor:
    """
    Creates a new `QueryCursor` for a given query.

    Args:
        query (Query): The tree-sitter Query object.

    Returns:
        QueryCursor: A new cursor instance.
    """
    return QueryCursor(query)


def safe_decode_with_fallback(node: ASTNode | None, fallback: str = "") -> str:
    """
    Safely decodes a node's text, returning a fallback string if decoding fails.

    Args:
        node (ASTNode | None): The node to decode.
        fallback (str): The string to return if decoding is not possible.

    Returns:
        str: The decoded text or the fallback string.
    """
    return result if (result := safe_decode_text(node)) is not None else fallback


def contains_node(parent: ASTNode, target: ASTNode) -> bool:
    """
    Recursively checks if a target node is a descendant of a parent node.

    Args:
        parent (ASTNode): The parent node.
        target (ASTNode): The target node to search for.

    Returns:
        bool: True if the target is found within the parent's descendants, False otherwise.
    """
    return parent == target or any(
        contains_node(child, target) for child in parent.children
    )


def ingest_method(
    method_node: ASTNode,
    container_qn: str,
    container_type: cs.NodeLabel,
    ingestor: IngestorProtocol,
    function_registry: FunctionRegistryTrieProtocol,
    simple_name_lookup: SimpleNameLookup,
    get_docstring_func: Callable[[ASTNode], str | None],
    language: cs.SupportedLanguage | None = None,
    extract_decorators_func: Callable[[ASTNode], list[str]] | None = None,
    method_qualified_name: str | None = None,
) -> None:
    """
    A generic helper function to ingest a method definition.

    Args:
        method_node (ASTNode): The AST node of the method.
        container_qn (str): The qualified name of the containing class or interface.
        container_type (cs.NodeLabel): The label of the container node.
        ingestor (IngestorProtocol): The data ingestion service.
        function_registry (FunctionRegistryTrieProtocol): The shared function registry.
        simple_name_lookup (SimpleNameLookup): The shared simple name lookup map.
        get_docstring_func (Callable): A function to extract the docstring.
        language (cs.SupportedLanguage | None): The language of the code.
        extract_decorators_func (Callable | None): A function to extract decorators.
        method_qualified_name (str | None): An optional override for the method's FQN.
    """
    if language == cs.SupportedLanguage.CPP:
        from .cpp import utils as cpp_utils

        method_name = cpp_utils.extract_function_name(method_node)
        if not method_name:
            return
    elif not (method_name_node := method_node.child_by_field_name(cs.FIELD_NAME)):
        return
    elif (text := method_name_node.text) is None:
        return
    else:
        method_name = text.decode(cs.ENCODING_UTF8)

    method_qn = method_qualified_name or f"{container_qn}.{method_name}"

    decorators = extract_decorators_func(method_node) if extract_decorators_func else []

    method_props: PropertyDict = {
        cs.KEY_QUALIFIED_NAME: method_qn,
        cs.KEY_NAME: method_name,
        cs.KEY_DECORATORS: decorators,
        cs.KEY_START_LINE: method_node.start_point[0] + 1,
        cs.KEY_END_LINE: method_node.end_point[0] + 1,
        cs.KEY_DOCSTRING: get_docstring_func(method_node),
    }

    logger.info(logs.METHOD_FOUND.format(name=method_name, qn=method_qn))
    ingestor.ensure_node_batch(cs.NodeLabel.METHOD, method_props)
    function_registry[method_qn] = NodeType.METHOD
    simple_name_lookup[method_name].add(method_qn)

    ingestor.ensure_relationship_batch(
        (container_type, cs.KEY_QUALIFIED_NAME, container_qn),
        cs.RelationshipType.DEFINES_METHOD,
        (cs.NodeLabel.METHOD, cs.KEY_QUALIFIED_NAME, method_qn),
    )


def ingest_exported_function(
    function_node: ASTNode,
    function_name: str,
    module_qn: str,
    export_type: str,
    ingestor: IngestorProtocol,
    function_registry: FunctionRegistryTrieProtocol,
    simple_name_lookup: SimpleNameLookup,
    get_docstring_func: Callable[[ASTNode], str | None],
    is_export_inside_function_func: Callable[[ASTNode], bool],
) -> None:
    """
    A helper function to ingest an exported function (e.g., in JavaScript).

    Args:
        function_node (ASTNode): The AST node of the function.
        function_name (str): The name of the function.
        module_qn (str): The qualified name of the module.
        export_type (str): The type of export (for logging).
        ingestor (IngestorProtocol): The data ingestion service.
        function_registry (FunctionRegistryTrieProtocol): The shared function registry.
        simple_name_lookup (SimpleNameLookup): The shared simple name lookup map.
        get_docstring_func (Callable): A function to extract the docstring.
        is_export_inside_function_func (Callable): A function to check if the export is nested.
    """
    if is_export_inside_function_func(function_node):
        return

    function_qn = f"{module_qn}.{function_name}"

    function_props = {
        cs.KEY_QUALIFIED_NAME: function_qn,
        cs.KEY_NAME: function_name,
        cs.KEY_START_LINE: function_node.start_point[0] + 1,
        cs.KEY_END_LINE: function_node.end_point[0] + 1,
        cs.KEY_DOCSTRING: get_docstring_func(function_node),
    }

    logger.info(
        logs.EXPORT_FOUND.format(
            export_type=export_type, name=function_name, qn=function_qn
        )
    )
    ingestor.ensure_node_batch(cs.NodeLabel.FUNCTION, function_props)
    function_registry[function_qn] = NodeType.FUNCTION
    simple_name_lookup[function_name].add(function_qn)


def is_method_node(func_node: ASTNode, lang_config: LanguageSpec) -> bool:
    """
    Determines if a function node is a method by checking if its ancestor is a class.

    Args:
        func_node (ASTNode): The function node to check.
        lang_config (LanguageSpec): The language specification.

    Returns:
        bool: True if the node is a method, False otherwise.
    """
    current = func_node.parent
    if not isinstance(current, Node):
        return False

    while current and current.type not in lang_config.module_node_types:
        if current.type in lang_config.class_node_types:
            return True
        current = current.parent
    return False
