from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from loguru import logger
from tree_sitter import Node, Query, QueryCursor

from codebase_rag.core import constants as cs
from codebase_rag.core import logs
from codebase_rag.data_models.types_defs import (
    ASTNode,
    LanguageQueries,
    NodeType,
    PropertyDict,
    SimpleNameLookup,
    TreeSitterNodeProtocol,
)
from codebase_rag.utils.path_utils import is_test_path, to_posix

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import FunctionRegistryTrieProtocol
    from codebase_rag.infrastructure.language_spec import LanguageSpec
    from codebase_rag.services import IngestorProtocol


class FunctionCapturesResult(NamedTuple):
    """Result structure for function captures."""

    lang_config: LanguageSpec
    captures: dict[str, list[ASTNode]]


def iter_query_captures(
    raw_captures: Iterable[object] | dict[str, list[ASTNode]] | None,
) -> list[tuple[ASTNode, str]]:
    if raw_captures is None:
        return []
    if isinstance(raw_captures, dict):
        pairs: list[tuple[ASTNode, str]] = []
        for name, nodes in raw_captures.items():
            if not isinstance(name, str) or not isinstance(nodes, list):
                continue
            pairs.extend((node, name) for node in nodes if isinstance(node, Node))
        return pairs

    pairs = []
    for item in raw_captures:
        node = None
        name = None
        if hasattr(item, "node") and hasattr(item, "name"):
            node = getattr(item, "node", None)
            name = getattr(item, "name", None)
        elif hasattr(item, "node") and hasattr(item, "capture_name"):
            node = getattr(item, "node", None)
            name = getattr(item, "capture_name", None)
        elif isinstance(item, tuple | list) and len(item) >= 2:
            node = item[0]
            name = item[1]

        if isinstance(node, Node) and isinstance(name, str):
            pairs.append((node, name))

    return pairs


def normalize_query_captures(
    raw_captures: Iterable[object] | dict[str, list[ASTNode]] | None,
) -> dict[str, list[ASTNode]]:
    captures_dict: dict[str, list[ASTNode]] = {}
    for node, capture_name in iter_query_captures(raw_captures):
        captures_dict.setdefault(capture_name, []).append(node)
    return captures_dict


def get_function_captures(
    root_node: ASTNode,
    language: cs.SupportedLanguage,
    queries: dict[cs.SupportedLanguage, LanguageQueries],
) -> FunctionCapturesResult | None:
    """
    Executes the function capture query for the given language on the root node.

    Args:
        root_node (ASTNode): The root node of the AST.
        language (cs.SupportedLanguage): The language of the file.
        queries (dict[cs.SupportedLanguage, LanguageQueries]): The dictionary of queries.

    Returns:
        FunctionCapturesResult | None: The captures result or None if query missing.
    """
    lang_queries = queries[language]
    lang_config = lang_queries[cs.QUERY_CONFIG]

    if not (query := lang_queries[cs.QUERY_FUNCTIONS]):
        return None

    cursor = QueryCursor(query)
    captures = normalize_query_captures(cursor.captures(root_node))
    return FunctionCapturesResult(lang_config, captures)


@lru_cache(maxsize=10000)
def _cached_decode_bytes(text_bytes: bytes) -> str:
    return text_bytes.decode(cs.ENCODING_UTF8)


def safe_decode_text(node: ASTNode | TreeSitterNodeProtocol | None) -> str | None:
    """
    Safely decodes the text content of a Tree-sitter node.

    Args:
        node (ASTNode | TreeSitterNodeProtocol | None): The node to extract text from.

    Returns:
        str | None: The decoded string or None if node is None or has no text.
    """
    if node is None or (text_bytes := node.text) is None:
        return None
    if isinstance(text_bytes, bytes):
        return _cached_decode_bytes(text_bytes)
    return str(text_bytes)


def get_query_cursor(query: Query) -> QueryCursor:
    """Creates a new QueryCursor for the given query."""
    return QueryCursor(query)


def safe_decode_with_fallback(node: ASTNode | None, fallback: str = "") -> str:
    return result if (result := safe_decode_text(node)) is not None else fallback


def extract_param_names(func_node: ASTNode) -> list[str]:
    """Extract parameter names from a function node."""
    params_node = func_node.child_by_field_name("parameters")
    if params_node is None:
        params_node = func_node.child_by_field_name("params")
    if params_node is None:
        return []

    names: list[str] = []
    for child in params_node.children:
        name_node = (
            child.child_by_field_name("name")
            if hasattr(child, "child_by_field_name")
            else None
        )
        if name_node is None:
            name_node = (
                child.child_by_field_name("pattern")
                if hasattr(child, "child_by_field_name")
                else None
            )
        if name_node is None:
            name_node = (
                child.child_by_field_name("parameter")
                if hasattr(child, "child_by_field_name")
                else None
            )
        candidate = safe_decode_text(name_node) if name_node is not None else None
        if not candidate and child.type in {
            "identifier",
            "variable_name",
            "parameter",
            "required_parameter",
            "optional_parameter",
            "default_parameter",
            "typed_parameter",
            "rest_parameter",
            "formal_parameter",
        }:
            candidate = safe_decode_text(child)
        if candidate:
            names.append(candidate)
    return names


def build_lite_signature(
    name: str,
    params: list[str],
    return_type: str | None,
    language: cs.SupportedLanguage | None,
) -> str:
    """Format a minimal signature for a function or method."""
    params_text = ", ".join([param for param in params if param])
    signature = f"{name}({params_text})" if params_text else f"{name}{cs.EMPTY_PARENS}"
    if return_type:
        if language in {cs.SupportedLanguage.JS, cs.SupportedLanguage.TS}:
            return f"{signature}: {return_type}"
        if language == cs.SupportedLanguage.RUBY:
            return f"{signature} # => {return_type}"
        return f"{signature} -> {return_type}"
    return signature


def normalize_decorators(decorators: list[str]) -> list[str]:
    normalized: list[str] = []
    for decorator in decorators:
        cleaned = decorator.strip()
        if cleaned.startswith("@"):
            cleaned = cleaned[1:]
        cleaned = cleaned.strip()
        if cleaned:
            normalized.append(cleaned.lower())
    return normalized


def get_parent_qualified_name(qualified_name: str) -> str | None:
    if cs.SEPARATOR_DOT not in qualified_name:
        return None
    return qualified_name.rsplit(cs.SEPARATOR_DOT, 1)[0]


def infer_visibility(
    name: str | None, language: cs.SupportedLanguage | None
) -> str | None:
    if not name or not language:
        return None
    if language == cs.SupportedLanguage.PYTHON:
        if name.startswith("__") and not name.endswith("__"):
            return "private"
        if name.startswith("_"):
            return "protected"
        return "public"
    return None


def contains_node(parent: ASTNode, target: ASTNode) -> bool:
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
    module_qn: str | None = None,
    file_hash: str | None = None,
    file_path: Path | None = None,
    repo_path: Path | None = None,
) -> None:
    if language == cs.SupportedLanguage.CPP:
        from ..languages.cpp import utils as cpp_utils

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

    if not module_qn:
        module_qn = get_parent_qualified_name(container_qn)
    namespace = (
        module_qn.rsplit(cs.SEPARATOR_DOT, 1)[0]
        if module_qn and cs.SEPARATOR_DOT in module_qn
        else None
    )

    decorators = extract_decorators_func(method_node) if extract_decorators_func else []
    param_names = extract_param_names(method_node)
    signature_lite = build_lite_signature(
        method_name,
        param_names,
        None,
        language,
    )
    visibility = infer_visibility(method_name, language)
    if language == cs.SupportedLanguage.JAVA:
        from ..languages.java import utils as java_utils

        visibility = java_utils.get_java_visibility(method_node)

    method_props: PropertyDict = {
        cs.KEY_QUALIFIED_NAME: method_qn,
        cs.KEY_NAME: method_name,
        cs.KEY_DECORATORS: decorators,
        cs.KEY_DECORATORS_NORM: normalize_decorators(decorators),
        cs.KEY_START_LINE: method_node.start_point[0] + 1,
        cs.KEY_END_LINE: method_node.end_point[0] + 1,
        cs.KEY_DOCSTRING: get_docstring_func(method_node),
        cs.KEY_SIGNATURE_LITE: signature_lite,
        cs.KEY_SIGNATURE: signature_lite,
        cs.KEY_SYMBOL_KIND: cs.NodeLabel.METHOD.value.lower(),
        cs.KEY_PARENT_QN: container_qn,
    }
    if module_qn:
        method_props[cs.KEY_MODULE_QN] = module_qn
    if namespace:
        method_props[cs.KEY_NAMESPACE] = namespace
        method_props[cs.KEY_PACKAGE] = namespace
    if visibility:
        method_props[cs.KEY_VISIBILITY] = visibility
    if language:
        method_props[cs.KEY_LANGUAGE] = language.value
    if file_path and repo_path:
        relative_path = to_posix(file_path.relative_to(repo_path))
        method_props[cs.KEY_PATH] = relative_path
        method_props[cs.KEY_REPO_REL_PATH] = relative_path
        method_props[cs.KEY_ABS_PATH] = file_path.resolve().as_posix()
        method_props[cs.KEY_IS_TEST] = is_test_path(file_path.relative_to(repo_path))
    if file_hash:
        method_props[cs.KEY_FILE_HASH] = file_hash

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
    language: cs.SupportedLanguage | None = None,
    file_hash: str | None = None,
    file_path: Path | None = None,
    repo_path: Path | None = None,
) -> None:
    if is_export_inside_function_func(function_node):
        return

    function_qn = f"{module_qn}.{function_name}"
    namespace = (
        module_qn.rsplit(cs.SEPARATOR_DOT, 1)[0]
        if cs.SEPARATOR_DOT in module_qn
        else None
    )
    param_names = extract_param_names(function_node)
    signature_lite = build_lite_signature(
        function_name,
        param_names,
        None,
        language,
    )

    function_props = {
        cs.KEY_QUALIFIED_NAME: function_qn,
        cs.KEY_NAME: function_name,
        cs.KEY_START_LINE: function_node.start_point[0] + 1,
        cs.KEY_END_LINE: function_node.end_point[0] + 1,
        cs.KEY_DOCSTRING: get_docstring_func(function_node),
        cs.KEY_SIGNATURE_LITE: signature_lite,
        cs.KEY_SIGNATURE: signature_lite,
        cs.KEY_DECORATORS: [],
        cs.KEY_DECORATORS_NORM: [],
        cs.KEY_MODULE_QN: module_qn,
        cs.KEY_SYMBOL_KIND: cs.NodeLabel.FUNCTION.value.lower(),
        cs.KEY_PARENT_QN: module_qn,
    }
    if namespace:
        function_props[cs.KEY_NAMESPACE] = namespace
        function_props[cs.KEY_PACKAGE] = namespace
    if language:
        function_props[cs.KEY_LANGUAGE] = language.value
    if file_path and repo_path:
        relative_path = to_posix(file_path.relative_to(repo_path))
        function_props[cs.KEY_PATH] = relative_path
        function_props[cs.KEY_REPO_REL_PATH] = relative_path
        function_props[cs.KEY_ABS_PATH] = file_path.resolve().as_posix()
        function_props[cs.KEY_IS_TEST] = is_test_path(file_path.relative_to(repo_path))
    if file_hash:
        function_props[cs.KEY_FILE_HASH] = file_hash

    logger.info(
        logs.EXPORT_FOUND.format(
            export_type=export_type, name=function_name, qn=function_qn
        )
    )
    ingestor.ensure_node_batch(cs.NodeLabel.FUNCTION, function_props)
    function_registry[function_qn] = NodeType.FUNCTION
    simple_name_lookup[function_name].add(function_qn)


def is_method_node(func_node: ASTNode, lang_config: LanguageSpec) -> bool:
    current = func_node.parent
    if not isinstance(current, Node):
        return False

    while current and current.type not in lang_config.module_node_types:
        if current.type in lang_config.class_node_types:
            return True
        current = current.parent
    return False
