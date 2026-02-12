from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.infrastructure.language_spec import LANGUAGE_FQN_SPECS
from codebase_rag.parsers.core.utils import safe_decode_text
from codebase_rag.parsers.languages.rs import utils as rs_utils

from ...utils.fqn_resolver import resolve_fqn_from_ast
from ..languages.cpp import utils as cpp_utils

if TYPE_CHECKING:
    from codebase_rag.infrastructure.language_spec import LanguageSpec


def resolve_class_identity(
    class_node: Node,
    module_qn: str,
    language: cs.SupportedLanguage,
    lang_config: LanguageSpec,
    file_path: Path | None,
    repo_path: Path,
    project_name: str,
) -> tuple[str, str, bool] | None:
    """
    Resolve the identity (qualified name, simple name, export status) of a class node.

    Tries to resolve using C++ specific logic, then FQN configuration if available,
    and finally falls back to standard extraction logic.

    Args:
        class_node: The AST node for the class/struct/interface.
        module_qn: The qualified name of the module containing the class.
        language: The programming language.
        lang_config: The language specification configuration.
        file_path: The file path of the source file.
        repo_path: The repository root path.
        project_name: The project name.

    Returns:
        A tuple containing (qualified_name, class_name, is_exported) if successful,
        or None if identity could not be resolved.
    """
    if language == cs.SupportedLanguage.CPP and file_path:
        if file_path.suffix in cs.CPP_MODULE_EXTENSIONS or any(
            part in cs.CPP_MODULE_PATH_MARKERS for part in file_path.parts
        ):
            class_name = extract_cpp_class_name(class_node)
            if class_name:
                class_qn = cpp_utils.build_qualified_name(
                    class_node, module_qn, class_name
                )
                is_exported = class_node.type == cs.CppNodeType.FUNCTION_DEFINITION or (
                    cpp_utils.is_exported(class_node)
                )
                return class_qn, class_name, is_exported
    if (fqn_config := LANGUAGE_FQN_SPECS.get(language)) and file_path:
        if class_qn := resolve_fqn_from_ast(
            class_node,
            file_path,
            repo_path,
            project_name,
            fqn_config,
        ):
            class_name = class_qn.split(cs.SEPARATOR_DOT)[-1]
            is_exported = language == cs.SupportedLanguage.CPP and (
                class_node.type == cs.CppNodeType.FUNCTION_DEFINITION
                or cpp_utils.is_exported(class_node)
            )
            return class_qn, class_name, is_exported

    return resolve_class_identity_fallback(class_node, module_qn, language, lang_config)


def resolve_class_identity_fallback(
    class_node: Node,
    module_qn: str,
    language: cs.SupportedLanguage,
    lang_config: LanguageSpec,
) -> tuple[str, str, bool] | None:
    """
    Fallback checking for class identity when primary methods fail.

    Handles default name extraction and C++ specific fallback logic for exported classes.

    Args:
        class_node: The AST node.
        module_qn: The module qualified name.
        language: The language.
        lang_config: The language config.

    Returns:
        A tuple of (qualified_name, class_name, is_exported) or None.
    """
    if language == cs.SupportedLanguage.CPP:
        if class_node.type == cs.CppNodeType.FUNCTION_DEFINITION:
            class_name = cpp_utils.extract_exported_class_name(class_node)
            is_exported = True
        else:
            class_name = extract_cpp_class_name(class_node)
            is_exported = cpp_utils.is_exported(class_node)

        if not class_name:
            return None
        class_qn = cpp_utils.build_qualified_name(class_node, module_qn, class_name)
        return class_qn, class_name, is_exported

    class_name = extract_class_name(class_node)
    if not class_name:
        return None
    nested_qn = build_nested_qualified_name_for_class(
        class_node, module_qn, class_name, lang_config
    )
    return nested_qn or f"{module_qn}.{class_name}", class_name, False


def extract_cpp_class_name(class_node: Node) -> str | None:
    """
    Extract the name of a C++ class, struct, or template.

    Handles template lists and compound types to extract the correct identifier.

    Args:
        class_node: The AST node.

    Returns:
        The extracted class name or None.
    """
    if class_node.type == cs.CppNodeType.TEMPLATE_DECLARATION:
        for child in class_node.children:
            if child.type in cs.CPP_COMPOUND_TYPES:
                return extract_cpp_class_name(child)

    name_node = class_node.child_by_field_name(cs.KEY_NAME)
    if name_node and name_node.text:
        name_text = safe_decode_text(name_node)
        if name_node.type == cs.TS_TEMPLATE_TYPE:
            return name_text
        if template_args := _extract_cpp_template_args(name_node):
            return f"{name_text}{template_args}"
        if template_args := _extract_cpp_template_args_from_siblings(
            class_node, name_node
        ):
            return f"{name_text}{template_args}"
        if name_text and "<" not in name_text and class_node.text:
            if extracted := _extract_cpp_class_name_from_text(class_node):
                if "<" in extracted:
                    return extracted
        return name_text

    for child in class_node.children:
        if child.type == cs.TS_TEMPLATE_TYPE and child.text:
            return safe_decode_text(child)
        if child.type == cs.TS_TYPE_IDENTIFIER and child.text:
            name_text = safe_decode_text(child)
            if template_args := _extract_cpp_template_args_from_siblings(
                class_node, child
            ):
                return f"{name_text}{template_args}"
            if name_text and "<" not in name_text and class_node.text:
                if extracted := _extract_cpp_class_name_from_text(class_node):
                    if "<" in extracted:
                        return extracted
            return name_text

    return None


def _extract_cpp_template_args(node: Node) -> str | None:
    """
    Extract template arguments from a node's children.

    Args:
        node: The node to search.

    Returns:
        The template argument string (e.g., "<T>") or None.
    """
    for child in node.children:
        if child.type == cs.TS_TEMPLATE_ARGUMENT_LIST and child.text:
            return safe_decode_text(child)
    return None


def _extract_cpp_template_args_from_siblings(
    parent: Node, name_node: Node
) -> str | None:
    """
    Extract template arguments from siblings of the name node within the parent.

    Args:
        parent: The parent node.
        name_node: The node identifying the class name.

    Returns:
        The template argument string or None.
    """
    found_name = False
    for child in parent.children:
        if child == name_node:
            found_name = True
            continue
        if found_name and child.type == cs.TS_TEMPLATE_ARGUMENT_LIST and child.text:
            return safe_decode_text(child)
    return None


def _extract_cpp_class_name_from_text(class_node: Node) -> str | None:
    """
    Extract C++ class name by parsing the node's text content.

    Used as a fallback when tree-sitter structure is ambiguous or complex.

    Args:
        class_node: The AST node.

    Returns:
        The extracted class name or None.
    """
    if not class_node.text:
        return None
    text = safe_decode_text(class_node)
    if text is None:
        return None
    for keyword in ("class", "struct"):
        marker = f"{keyword} "
        idx = text.find(marker)
        if idx == -1:
            continue
        after = text[idx + len(marker) :].lstrip()
        if not after:
            continue
        end = len(after)
        for sep in ("{", ":", "\n"):
            pos = after.find(sep)
            if pos != -1:
                end = min(end, pos)
        candidate = after[:end].strip()
        if candidate:
            return candidate.split()[0]
    return None


def extract_class_name(class_node: Node) -> str | None:
    """
    Extract the name of a class from its AST node.

    Generic extraction that looks for a 'name' field or identifier child.

    Args:
        class_node: The AST node.

    Returns:
        The class name or None.
    """
    name_node = class_node.child_by_field_name(cs.KEY_NAME)
    if name_node and name_node.text:
        return safe_decode_text(name_node)

    current = class_node.parent
    while current:
        if current.type == cs.TS_VARIABLE_DECLARATOR:
            for child in current.children:
                if child.type == cs.TS_IDENTIFIER and child.text:
                    return safe_decode_text(child)
        current = current.parent

    return None


def build_nested_qualified_name_for_class(
    class_node: Node,
    module_qn: str,
    class_name: str,
    lang_config: LanguageSpec,
) -> str | None:
    """
    Build a qualified name for a nested class.

    Traverses up the AST to find parent classes/modules and constructs the dotted path.

    Args:
        class_node: The AST node of the nested class.
        module_qn: The module qualified name.
        class_name: The simple name of the nested class.
        lang_config: The language config.

    Returns:
        The nested qualified name or None if not nested.
    """
    if not isinstance(class_node.parent, Node):
        return None

    path_parts = rs_utils.build_module_path(
        class_node,
        include_classes=True,
        class_node_types=lang_config.class_node_types,
    )

    if path_parts:
        return f"{module_qn}.{cs.SEPARATOR_DOT.join(path_parts)}.{class_name}"
    return None
