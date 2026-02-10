"""
This module is responsible for resolving the identity of a class-like AST node.

"Identity" here refers to its fully qualified name (FQN), its simple name, and
whether it is exported (in languages that support explicit exports like C++).

It provides a main resolution function that first attempts to use the precise,
unified FQN resolver. If that fails or is not applicable for the language, it
falls back to language-specific heuristics to construct the name.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tree_sitter import Node

from codebase_rag.infrastructure.language_spec import LANGUAGE_FQN_SPECS

from ...core import constants as cs
from ...utils.fqn_resolver import resolve_fqn_from_ast
from ..cpp import utils as cpp_utils
from ..rs import utils as rs_utils
from ..utils import safe_decode_text

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
    Resolves the identity (FQN, simple name, export status) of a class node.

    Args:
        class_node (Node): The AST node for the class.
        module_qn (str): The qualified name of the containing module.
        language (cs.SupportedLanguage): The language of the code.
        lang_config (LanguageSpec): The language specification.
        file_path (Path | None): The path to the source file.
        repo_path (Path): The root path of the repository.
        project_name (str): The name of the project.

    Returns:
        tuple[str, str, bool] | None: A tuple of (qualified_name, simple_name, is_exported),
                                     or None if resolution fails.
    """
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
    A fallback mechanism to resolve class identity using language-specific heuristics.

    Args:
        class_node (Node): The AST node for the class.
        module_qn (str): The qualified name of the containing module.
        language (cs.SupportedLanguage): The language of the code.
        lang_config (LanguageSpec): The language specification.

    Returns:
        tuple[str, str, bool] | None: A tuple of (qualified_name, simple_name, is_exported),
                                     or None if resolution fails.
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
    Extracts the name of a C++ class, struct, or union.

    Args:
        class_node (Node): The AST node for the C++ class-like entity.

    Returns:
        str | None: The extracted name, or None if not found.
    """
    if class_node.type == cs.CppNodeType.TEMPLATE_DECLARATION:
        for child in class_node.children:
            if child.type in cs.CPP_COMPOUND_TYPES:
                return extract_cpp_class_name(child)

    for child in class_node.children:
        if child.type == cs.TS_TYPE_IDENTIFIER and child.text:
            return safe_decode_text(child)

    name_node = class_node.child_by_field_name(cs.KEY_NAME)
    return safe_decode_text(name_node) if name_node and name_node.text else None


def extract_class_name(class_node: Node) -> str | None:
    """
    Extracts the name of a class from its AST node.

    Args:
        class_node (Node): The AST node for the class.

    Returns:
        str | None: The extracted name, or None if not found.
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
    Builds the FQN for a nested class by traversing up the AST.

    Args:
        class_node (Node): The AST node of the nested class.
        module_qn (str): The qualified name of the containing module.
        class_name (str): The simple name of the class.
        lang_config (LanguageSpec): The language specification.

    Returns:
        str | None: The constructed FQN, or None if it's a top-level class.
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
