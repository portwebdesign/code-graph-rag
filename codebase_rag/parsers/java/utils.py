"""
This module provides utility functions specifically for parsing Java source code.

It contains helpers for extracting detailed information from various Java-specific
AST nodes, such as classes, methods, fields, and annotations. These functions
are used by other Java-related parsers to deconstruct the AST into a structured
format.

Key functionalities:
-   Extracting full class and method information, including modifiers, return types,
    parameters, and inheritance.
-   Identifying special methods like the `main` method.
-   Resolving class and module contexts from fully qualified names.
-   Parsing package and import declarations.
-   Handling Java-specific path and module resolution logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from tree_sitter import Node

from codebase_rag.data_models.models import MethodModifiersAndAnnotations
from codebase_rag.data_models.types_defs import (
    ASTNode,
    JavaAnnotationInfo,
    JavaClassInfo,
    JavaFieldInfo,
    JavaMethodCallInfo,
    JavaMethodInfo,
)

from ...core import constants as cs
from ..utils import safe_decode_text

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import ASTCacheProtocol


class ClassContext(NamedTuple):
    """Holds the context for a class being processed."""

    module_qn: str
    target_class_name: str
    root_node: Node


def get_root_node_from_module_qn(
    module_qn: str,
    module_qn_to_file_path: dict[str, Path],
    ast_cache: ASTCacheProtocol,
    min_parts: int = 2,
) -> Node | None:
    """
    Retrieves the root AST node for a given module qualified name.

    Args:
        module_qn (str): The qualified name of the module.
        module_qn_to_file_path (dict): A map from module FQNs to file paths.
        ast_cache (ASTCacheProtocol): The cache of parsed ASTs.
        min_parts (int): The minimum number of parts the FQN must have.

    Returns:
        Node | None: The root AST node, or None if not found.
    """
    parts = module_qn.split(cs.SEPARATOR_DOT)
    if len(parts) < min_parts:
        return None

    file_path = module_qn_to_file_path.get(module_qn)
    if file_path is None or file_path not in ast_cache:
        return None

    root_node, _ = ast_cache[file_path]
    return root_node


def get_class_context_from_qn(
    class_qn: str,
    module_qn_to_file_path: dict[str, Path],
    ast_cache: ASTCacheProtocol,
) -> ClassContext | None:
    """
    Retrieves the full context (module FQN, class name, root node) for a class FQN.

    Args:
        class_qn (str): The fully qualified name of the class.
        module_qn_to_file_path (dict): A map from module FQNs to file paths.
        ast_cache (ASTCacheProtocol): The cache of parsed ASTs.

    Returns:
        ClassContext | None: The class context, or None if it cannot be resolved.
    """
    parts = class_qn.split(cs.SEPARATOR_DOT)
    if len(parts) < 2:
        return None

    module_qn = cs.SEPARATOR_DOT.join(parts[:-1])
    target_class_name = parts[-1]

    root_node = get_root_node_from_module_qn(
        module_qn, module_qn_to_file_path, ast_cache, min_parts=1
    )
    if root_node is None:
        return None

    return ClassContext(module_qn, target_class_name, root_node)


def extract_package_name(package_node: ASTNode) -> str | None:
    """
    Extracts the package name from a `package_declaration` node.

    Args:
        package_node (ASTNode): The package declaration node.

    Returns:
        str | None: The name of the package.
    """
    if package_node.type != cs.TS_PACKAGE_DECLARATION:
        return None

    return next(
        (
            safe_decode_text(child)
            for child in package_node.children
            if child.type in [cs.TS_SCOPED_IDENTIFIER, cs.TS_IDENTIFIER]
        ),
        None,
    )


def extract_import_path(import_node: ASTNode) -> dict[str, str]:
    """
    Extracts the local name and full path from an `import_declaration` node.

    Args:
        import_node (ASTNode): The import declaration node.

    Returns:
        dict[str, str]: A dictionary mapping the local name to the full import path.
    """
    if import_node.type != cs.TS_IMPORT_DECLARATION:
        return {}

    imports: dict[str, str] = {}
    imported_path = None
    is_wildcard = False

    for child in import_node.children:
        match child.type:
            case cs.TS_STATIC:
                pass
            case cs.TS_SCOPED_IDENTIFIER | cs.TS_IDENTIFIER:
                imported_path = safe_decode_text(child)
            case cs.TS_ASTERISK:
                is_wildcard = True

    if not imported_path:
        return imports

    if is_wildcard:
        wildcard_key = f"*{imported_path}"
        imports[wildcard_key] = imported_path
    elif parts := imported_path.split(cs.SEPARATOR_DOT):
        imported_name = parts[-1]
        imports[imported_name] = imported_path

    return imports


def _extract_superclass(class_node: ASTNode) -> str | None:
    """Extracts the superclass name from a class node."""
    superclass_node = class_node.child_by_field_name(cs.TS_FIELD_SUPERCLASS)
    if not superclass_node:
        return None

    match superclass_node.type:
        case cs.TS_TYPE_IDENTIFIER:
            return safe_decode_text(superclass_node)
        case cs.TS_GENERIC_TYPE:
            for child in superclass_node.children:
                if child.type == cs.TS_TYPE_IDENTIFIER:
                    return safe_decode_text(child)
    return None


def _extract_interface_name(type_child: ASTNode) -> str | None:
    """Extracts an interface name from a type node within an `implements` clause."""
    match type_child.type:
        case cs.TS_TYPE_IDENTIFIER:
            return safe_decode_text(type_child)
        case cs.TS_GENERIC_TYPE:
            for sub_child in type_child.children:
                if sub_child.type == cs.TS_TYPE_IDENTIFIER:
                    return safe_decode_text(sub_child)
    return None


def _extract_interfaces(class_node: ASTNode) -> list[str]:
    """Extracts all implemented interface names from a class node."""
    interfaces_node = class_node.child_by_field_name(cs.TS_FIELD_INTERFACES)
    if not interfaces_node:
        return []

    interfaces: list[str] = []
    for child in interfaces_node.children:
        if child.type == cs.TS_TYPE_LIST:
            for type_child in child.children:
                if interface_name := _extract_interface_name(type_child):
                    interfaces.append(interface_name)
    return interfaces


def _extract_type_parameters(class_node: ASTNode) -> list[str]:
    """Extracts generic type parameters from a class or method node."""
    type_params_node = class_node.child_by_field_name(cs.TS_FIELD_TYPE_PARAMETERS)
    if not type_params_node:
        return []

    type_parameters: list[str] = []
    for child in type_params_node.children:
        if child.type == cs.TS_TYPE_PARAMETER:
            if param_name := safe_decode_text(
                child.child_by_field_name(cs.TS_FIELD_NAME)
            ):
                type_parameters.append(param_name)
    return type_parameters


def extract_from_modifiers_node(
    node: ASTNode, allowed_modifiers: frozenset[str]
) -> MethodModifiersAndAnnotations:
    """
    Extracts modifiers and annotations from a `modifiers` node.

    Args:
        node (ASTNode): The parent node containing the `modifiers` child.
        allowed_modifiers (frozenset[str]): A set of valid modifier strings.

    Returns:
        MethodModifiersAndAnnotations: An object containing lists of modifiers and annotations.
    """
    result = MethodModifiersAndAnnotations()
    modifiers_node = next(
        (child for child in node.children if child.type == cs.TS_MODIFIERS), None
    )
    if not modifiers_node:
        return result
    for modifier_child in modifiers_node.children:
        match modifier_child.type:
            case _ if modifier_child.type in allowed_modifiers:
                if modifier := safe_decode_text(modifier_child):
                    result.modifiers.append(modifier)
            case cs.TS_ANNOTATION | cs.TS_MARKER_ANNOTATION:
                if annotation := safe_decode_text(modifier_child):
                    result.annotations.append(annotation)
    return result


def _extract_class_modifiers(class_node: ASTNode) -> list[str]:
    """Extracts the modifiers for a class node."""
    return extract_from_modifiers_node(class_node, cs.JAVA_CLASS_MODIFIERS).modifiers


def extract_class_info(class_node: ASTNode) -> JavaClassInfo:
    """
    Extracts comprehensive information about a class from its AST node.

    Args:
        class_node (ASTNode): The AST node of the class.

    Returns:
        JavaClassInfo: A TypedDict containing the class's name, type, inheritance, etc.
    """
    if class_node.type not in cs.JAVA_CLASS_NODE_TYPES:
        return JavaClassInfo(
            name=None,
            type="",
            superclass=None,
            interfaces=[],
            modifiers=[],
            type_parameters=[],
        )

    name: str | None = None
    if name_node := class_node.child_by_field_name(cs.TS_FIELD_NAME):
        name = safe_decode_text(name_node)

    return JavaClassInfo(
        name=name,
        type=class_node.type.replace(cs.JAVA_DECLARATION_SUFFIX, ""),
        superclass=_extract_superclass(class_node),
        interfaces=_extract_interfaces(class_node),
        modifiers=_extract_class_modifiers(class_node),
        type_parameters=_extract_type_parameters(class_node),
    )


def _get_method_type(method_node: ASTNode) -> str:
    """Determines if a node is a method or a constructor."""
    if method_node.type == cs.TS_CONSTRUCTOR_DECLARATION:
        return cs.JAVA_TYPE_CONSTRUCTOR
    return cs.JAVA_TYPE_METHOD


def _extract_method_return_type(method_node: ASTNode) -> str | None:
    """Extracts the return type of a method."""
    if method_node.type != cs.TS_METHOD_DECLARATION:
        return None
    if type_node := method_node.child_by_field_name(cs.TS_FIELD_TYPE):
        return safe_decode_text(type_node)
    return None


def _extract_formal_param_type(param_node: ASTNode) -> str | None:
    """Extracts the type from a `formal_parameter` node."""
    if param_type_node := param_node.child_by_field_name(cs.TS_FIELD_TYPE):
        return safe_decode_text(param_type_node)
    return None


def _extract_spread_param_type(spread_node: ASTNode) -> str | None:
    """Extracts the type from a `spread_parameter` (varargs) node."""
    for subchild in spread_node.children:
        if subchild.type == cs.TS_TYPE_IDENTIFIER:
            if param_type_text := safe_decode_text(subchild):
                return f"{param_type_text}..."
    return None


def _extract_method_parameters(method_node: ASTNode) -> list[str]:
    """Extracts a list of parameter types for a method."""
    params_node = method_node.child_by_field_name(cs.TS_FIELD_PARAMETERS)
    if not params_node:
        return []

    parameters: list[str] = []
    for child in params_node.children:
        param_type: str | None = None
        match child.type:
            case cs.TS_FORMAL_PARAMETER:
                param_type = _extract_formal_param_type(child)
            case cs.TS_SPREAD_PARAMETER:
                param_type = _extract_spread_param_type(child)
        if param_type:
            parameters.append(param_type)
    return parameters


def extract_method_info(method_node: ASTNode) -> JavaMethodInfo:
    """
    Extracts comprehensive information about a method from its AST node.

    Args:
        method_node (ASTNode): The AST node of the method.

    Returns:
        JavaMethodInfo: A TypedDict containing the method's name, types, modifiers, etc.
    """
    if method_node.type not in cs.JAVA_METHOD_NODE_TYPES:
        return JavaMethodInfo(
            name=None,
            type="",
            return_type=None,
            parameters=[],
            modifiers=[],
            type_parameters=[],
            annotations=[],
        )

    mods_and_annots = extract_from_modifiers_node(method_node, cs.JAVA_METHOD_MODIFIERS)

    return JavaMethodInfo(
        name=safe_decode_text(method_node.child_by_field_name(cs.TS_FIELD_NAME)),
        type=_get_method_type(method_node),
        return_type=_extract_method_return_type(method_node),
        parameters=_extract_method_parameters(method_node),
        modifiers=mods_and_annots.modifiers,
        type_parameters=[],
        annotations=mods_and_annots.annotations,
    )


def extract_field_info(field_node: ASTNode) -> JavaFieldInfo:
    """
    Extracts information about a class field from its AST node.

    Args:
        field_node (ASTNode): The `field_declaration` AST node.

    Returns:
        JavaFieldInfo: A TypedDict containing the field's name, type, and modifiers.
    """
    if field_node.type != cs.TS_FIELD_DECLARATION:
        return JavaFieldInfo(
            name=None,
            type=None,
            modifiers=[],
            annotations=[],
        )

    field_type: str | None = None
    if type_node := field_node.child_by_field_name(cs.TS_FIELD_TYPE):
        field_type = safe_decode_text(type_node)

    name: str | None = None
    declarator_node = field_node.child_by_field_name(cs.TS_FIELD_DECLARATOR)
    if declarator_node and declarator_node.type == cs.TS_VARIABLE_DECLARATOR:
        if name_node := declarator_node.child_by_field_name(cs.TS_FIELD_NAME):
            name = safe_decode_text(name_node)

    mods_and_annots = extract_from_modifiers_node(field_node, cs.JAVA_FIELD_MODIFIERS)

    return JavaFieldInfo(
        name=name,
        type=field_type,
        modifiers=mods_and_annots.modifiers,
        annotations=mods_and_annots.annotations,
    )


def extract_method_call_info(call_node: ASTNode) -> JavaMethodCallInfo | None:
    """
    Extracts information about a method call from its AST node.

    Args:
        call_node (ASTNode): The `method_invocation` AST node.

    Returns:
        JavaMethodCallInfo | None: A TypedDict with call info, or None if not a call node.
    """
    if call_node.type != cs.TS_METHOD_INVOCATION:
        return None

    name: str | None = None
    if name_node := call_node.child_by_field_name(cs.TS_FIELD_NAME):
        name = safe_decode_text(name_node)

    obj: str | None = None
    if object_node := call_node.child_by_field_name(cs.TS_FIELD_OBJECT):
        match object_node.type:
            case cs.TS_THIS:
                obj = cs.TS_THIS
            case cs.TS_SUPER:
                obj = cs.TS_SUPER
            case cs.TS_IDENTIFIER | cs.TS_FIELD_ACCESS:
                obj = safe_decode_text(object_node)

    arguments = 0
    if args_node := call_node.child_by_field_name(cs.TS_FIELD_ARGUMENTS):
        arguments = sum(
            1 for child in args_node.children if child.type not in cs.DELIMITER_TOKENS
        )

    return JavaMethodCallInfo(name=name, object=obj, arguments=arguments)


def _has_main_method_modifiers(method_node: ASTNode) -> bool:
    """Checks if a method has `public static` modifiers."""
    has_public = False
    has_static = False

    for child in method_node.children:
        if child.type == cs.TS_MODIFIERS:
            for modifier_child in child.children:
                match modifier_child.type:
                    case cs.JAVA_MODIFIER_PUBLIC:
                        has_public = True
                    case cs.JAVA_MODIFIER_STATIC:
                        has_static = True

    return has_public and has_static


def _is_valid_main_formal_param(param_node: ASTNode) -> bool:
    """Checks if a formal parameter is a valid `main` method parameter."""
    type_node = param_node.child_by_field_name(cs.TS_FIELD_TYPE)
    if not type_node:
        return False

    type_text = safe_decode_text(type_node)
    if not type_text:
        return False

    return (
        cs.JAVA_MAIN_PARAM_ARRAY in type_text
        or cs.JAVA_MAIN_PARAM_VARARGS in type_text
        or type_text.endswith(cs.JAVA_MAIN_PARAM_ARRAY)
        or type_text.endswith(cs.JAVA_MAIN_PARAM_VARARGS)
    )


def _is_valid_main_spread_param(spread_node: ASTNode) -> bool:
    """Checks if a spread parameter is a valid `main` method parameter."""
    for subchild in spread_node.children:
        if subchild.type == cs.TS_TYPE_IDENTIFIER:
            type_text = safe_decode_text(subchild)
            if type_text == cs.JAVA_MAIN_PARAM_TYPE:
                return True
    return False


def _has_valid_main_parameter(method_node: ASTNode) -> bool:
    """Checks if a method has the correct parameter signature for a `main` method."""
    parameters_node = method_node.child_by_field_name(cs.TS_FIELD_PARAMETERS)
    if not parameters_node:
        return False

    param_count = 0
    valid_param = False

    for child in parameters_node.children:
        match child.type:
            case cs.TS_FORMAL_PARAMETER:
                param_count += 1
                if _is_valid_main_formal_param(child):
                    valid_param = True
            case cs.TS_SPREAD_PARAMETER:
                param_count += 1
                if _is_valid_main_spread_param(child):
                    valid_param = True

    return param_count == 1 and valid_param


def is_main_method(method_node: ASTNode) -> bool:
    """
    Determines if a method node represents a standard Java `public static void main`.

    Args:
        method_node (ASTNode): The method declaration node.

    Returns:
        bool: True if it is a main method, False otherwise.
    """
    if method_node.type != cs.TS_METHOD_DECLARATION:
        return False

    name_node = method_node.child_by_field_name(cs.TS_FIELD_NAME)
    if not name_node or safe_decode_text(name_node) != cs.JAVA_MAIN_METHOD_NAME:
        return False

    type_node = method_node.child_by_field_name(cs.TS_FIELD_TYPE)
    if not type_node or type_node.type != cs.TS_VOID_TYPE:
        return False

    if not _has_main_method_modifiers(method_node):
        return False

    return _has_valid_main_parameter(method_node)


def get_java_visibility(node: ASTNode) -> str:
    """
    Determines the visibility (public, protected, private, package) of a node.

    Args:
        node (ASTNode): The node to check (e.g., class, method, field).

    Returns:
        str: The visibility level as a string.
    """
    for child in node.children:
        match child.type:
            case cs.JAVA_VISIBILITY_PUBLIC:
                return cs.JAVA_VISIBILITY_PUBLIC
            case cs.JAVA_VISIBILITY_PROTECTED:
                return cs.JAVA_VISIBILITY_PROTECTED
            case cs.JAVA_VISIBILITY_PRIVATE:
                return cs.JAVA_VISIBILITY_PRIVATE

    return cs.JAVA_VISIBILITY_PACKAGE


def build_qualified_name(
    node: ASTNode,
    include_classes: bool = True,
    include_methods: bool = False,
) -> list[str]:
    """
    Builds a list of path parts for a qualified name by traversing up the AST.

    Args:
        node (ASTNode): The starting node.
        include_classes (bool): Whether to include class names in the path.
        include_methods (bool): Whether to include method names in the path.

    Returns:
        list[str]: A list of names forming the path.
    """
    path_parts: list[str] = []
    current = node.parent

    while current and current.type != cs.TS_PROGRAM:
        if current.type in cs.JAVA_CLASS_NODE_TYPES and include_classes:
            if name_node := current.child_by_field_name(cs.TS_FIELD_NAME):
                if class_name := safe_decode_text(name_node):
                    path_parts.append(class_name)
        elif current.type in cs.JAVA_METHOD_NODE_TYPES and include_methods:
            if name_node := current.child_by_field_name(cs.TS_FIELD_NAME):
                if method_name := safe_decode_text(name_node):
                    path_parts.append(method_name)

        current = current.parent

    path_parts.reverse()
    return path_parts


def extract_annotation_info(annotation_node: ASTNode) -> JavaAnnotationInfo:
    """
    Extracts information from an annotation node.

    Args:
        annotation_node (ASTNode): The annotation AST node.

    Returns:
        JavaAnnotationInfo: A TypedDict with the annotation's name and arguments.
    """
    if annotation_node.type != cs.TS_ANNOTATION:
        return JavaAnnotationInfo(name=None, arguments=[])

    name: str | None = None
    if name_node := annotation_node.child_by_field_name(cs.TS_FIELD_NAME):
        name = safe_decode_text(name_node)

    arguments: list[str] = []
    if args_node := annotation_node.child_by_field_name(cs.TS_FIELD_ARGUMENTS):
        for child in args_node.children:
            if child.type not in cs.DELIMITER_TOKENS:
                if arg_value := safe_decode_text(child):
                    arguments.append(arg_value)

    return JavaAnnotationInfo(name=name, arguments=arguments)


def find_package_start_index(parts: list[str]) -> int | None:
    """
    Finds the starting index of the package path within a list of file path parts.

    This helps to correctly construct the FQN by identifying where the package
    name begins in a typical Java/JVM project structure (e.g., after 'src/main/java').

    Args:
        parts (list[str]): The parts of the file path.

    Returns:
        int | None: The starting index of the package path, or None.
    """
    for i, part in enumerate(parts):
        if part in cs.JAVA_JVM_LANGUAGES and i > 0:
            return i + 1

        if part == cs.JAVA_PATH_SRC and i + 1 < len(parts):
            next_part = parts[i + 1]

            if (
                next_part not in cs.JAVA_JVM_LANGUAGES
                and next_part not in cs.JAVA_SRC_FOLDERS
            ):
                return i + 1

            if _is_non_standard_java_src_layout(parts, i):
                return i + 1

    return None


def _is_non_standard_java_src_layout(parts: list[str], src_idx: int) -> bool:
    """Checks for a non-standard Java source layout (e.g., 'src/main/com/...')"""
    if src_idx + 2 >= len(parts):
        return False

    next_part = parts[src_idx + 1]
    part_after_next = parts[src_idx + 2]

    return (
        next_part in (cs.JAVA_PATH_MAIN, cs.JAVA_PATH_TEST)
        and part_after_next not in cs.JAVA_JVM_LANGUAGES
    )
