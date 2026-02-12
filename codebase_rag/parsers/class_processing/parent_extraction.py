from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger
from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.parsers.core.utils import safe_decode_text

from ... import logs
from ..languages.cpp import utils as cpp_utils
from .utils import find_child_by_type

if TYPE_CHECKING:
    from codebase_rag.parsers.pipeline.import_processor import ImportProcessor


def extract_parent_classes(
    class_node: Node,
    module_qn: str,
    import_processor: ImportProcessor,
    resolve_to_qn: Callable[[str, str], str],
) -> list[str]:
    """
    Extract a list of parent class qualified names for a given class node.

    Delegates to language-specific extraction logic based on the node type.

    Args:
        class_node: The AST node for the class.
        module_qn: The module qualified name.
        import_processor: The processor for resolving imports.
        resolve_to_qn: A callable to resolve simple names to qualified names.

    Returns:
        A list of qualified names of the parent classes.
    """
    if class_node.type in cs.CPP_CLASS_TYPES:
        return extract_cpp_parent_classes(class_node, module_qn)

    parent_classes: list[str] = []

    if class_node.type == cs.TS_CLASS_DECLARATION:
        parent_classes.extend(
            extract_java_superclass(class_node, module_qn, resolve_to_qn)
        )

    parent_classes.extend(
        extract_python_superclasses(
            class_node, module_qn, import_processor, resolve_to_qn
        )
    )

    if class_heritage_node := find_child_by_type(class_node, cs.TS_CLASS_HERITAGE):
        parent_classes.extend(
            extract_js_ts_heritage_parents(
                class_heritage_node, module_qn, import_processor, resolve_to_qn
            )
        )

    if class_node.type == cs.TS_INTERFACE_DECLARATION:
        parent_classes.extend(
            extract_interface_parents(
                class_node, module_qn, import_processor, resolve_to_qn
            )
        )

    return parent_classes


def extract_cpp_parent_classes(class_node: Node, module_qn: str) -> list[str]:
    parent_classes: list[str] = []
    for child in class_node.children:
        if child.type == cs.TS_BASE_CLASS_CLAUSE:
            parent_classes.extend(parse_cpp_base_classes(child, class_node, module_qn))
    return parent_classes


def parse_cpp_base_classes(
    base_clause_node: Node, class_node: Node, module_qn: str
) -> list[str]:
    """
    Parse the base class clause of a C++ class definition.

    Extracts base class names and resolves them to qualified names relative to the current module.

    Args:
        base_clause_node: The AST node containing the base class list.
        class_node: The class AST node.
        module_qn: The module qualified name.

    Returns:
        A list of resolved parent class qualified names.
    """
    parent_classes: list[str] = []
    base_type_nodes = (
        cs.TS_TYPE_IDENTIFIER,
        cs.CppNodeType.QUALIFIED_IDENTIFIER,
        cs.TS_TEMPLATE_TYPE,
    )

    for base_child in base_clause_node.children:
        if base_child.type in (
            cs.TS_ACCESS_SPECIFIER,
            cs.TS_VIRTUAL,
            cs.CHAR_COMMA,
            cs.CHAR_COLON,
        ):
            continue

        if base_child.type in base_type_nodes and base_child.text:
            if parent_name := safe_decode_text(base_child):
                base_name = extract_cpp_base_class_name(parent_name)
                parent_qn = cpp_utils.build_qualified_name(
                    class_node, module_qn, base_name
                )
                parent_classes.append(parent_qn)
                logger.debug(
                    logs.CLASS_CPP_INHERITANCE.format(
                        parent_name=parent_name, parent_qn=parent_qn
                    )
                )

    return parent_classes


def extract_cpp_base_class_name(parent_text: str) -> str:
    if cs.CHAR_ANGLE_OPEN in parent_text:
        parent_text = parent_text.split(cs.CHAR_ANGLE_OPEN)[0]

    if cs.SEPARATOR_DOUBLE_COLON in parent_text:
        parent_text = parent_text.split(cs.SEPARATOR_DOUBLE_COLON)[-1]

    return parent_text


def resolve_superclass_from_type_identifier(
    type_identifier_node: Node,
    module_qn: str,
    resolve_to_qn: Callable[[str, str], str],
) -> str | None:
    if type_identifier_node.text:
        if parent_name := safe_decode_text(type_identifier_node):
            return resolve_to_qn(parent_name, module_qn)
    return None


def extract_java_superclass(
    class_node: Node,
    module_qn: str,
    resolve_to_qn: Callable[[str, str], str],
) -> list[str]:
    """
    Extract the superclass from a Java class definition.

    Parses the `extends` clause and resolves the superclass name.
    """
    superclass_node = class_node.child_by_field_name(cs.FIELD_SUPERCLASS)
    if not superclass_node:
        return []

    if superclass_node.type == cs.TS_TYPE_IDENTIFIER:
        if resolved := resolve_superclass_from_type_identifier(
            superclass_node, module_qn, resolve_to_qn
        ):
            return [resolved]
        return []

    for child in superclass_node.children:
        if child.type == cs.TS_TYPE_IDENTIFIER:
            if resolved := resolve_superclass_from_type_identifier(
                child, module_qn, resolve_to_qn
            ):
                return [resolved]
    return []


def extract_python_superclasses(
    class_node: Node,
    module_qn: str,
    import_processor: ImportProcessor,
    resolve_to_qn: Callable[[str, str], str],
) -> list[str]:
    """
    Extract superclasses from a Python class definition.

    Parses the argument list of the class definition and resolves each base class
    using import mappings or local resolution.
    """
    superclasses_node = class_node.child_by_field_name(cs.FIELD_SUPERCLASSES)
    if not superclasses_node:
        return []

    parent_classes: list[str] = []
    import_map = import_processor.import_mapping.get(module_qn)

    for child in superclasses_node.children:
        if child.type != cs.TS_IDENTIFIER or not child.text:
            continue
        if not (parent_name := safe_decode_text(child)):
            continue

        if import_map and parent_name in import_map:
            parent_classes.append(import_map[parent_name])
        elif import_map:
            parent_classes.append(resolve_to_qn(parent_name, module_qn))
        else:
            parent_classes.append(f"{module_qn}.{parent_name}")

    return parent_classes


def extract_js_ts_heritage_parents(
    class_heritage_node: Node,
    module_qn: str,
    import_processor: ImportProcessor,
    resolve_to_qn: Callable[[str, str], str],
) -> list[str]:
    """
    Extract parent classes from JS/TS class heritage clauses.

    Handles `extends` clauses and mixin patterns (e.g. `extends Mixin(Base)`).
    """
    parent_classes: list[str] = []

    for child in class_heritage_node.children:
        if child.type == cs.TS_EXTENDS_CLAUSE:
            parent_classes.extend(
                extract_from_extends_clause(
                    child, module_qn, import_processor, resolve_to_qn
                )
            )
            break
        if child.type in cs.JS_TS_PARENT_REF_TYPES:
            if is_preceded_by_extends(child, class_heritage_node):
                if parent_name := safe_decode_text(child):
                    parent_classes.append(
                        resolve_js_ts_parent_class(
                            parent_name, module_qn, import_processor, resolve_to_qn
                        )
                    )
        elif child.type == cs.TS_CALL_EXPRESSION:
            if is_preceded_by_extends(child, class_heritage_node):
                parent_classes.extend(
                    extract_mixin_parent_classes(
                        child, module_qn, import_processor, resolve_to_qn
                    )
                )

    return parent_classes


def extract_from_extends_clause(
    extends_clause: Node,
    module_qn: str,
    import_processor: ImportProcessor,
    resolve_to_qn: Callable[[str, str], str],
) -> list[str]:
    for grandchild in extends_clause.children:
        if grandchild.type in cs.JS_TS_PARENT_REF_TYPES:
            if parent_name := safe_decode_text(grandchild):
                return [
                    resolve_js_ts_parent_class(
                        parent_name, module_qn, import_processor, resolve_to_qn
                    )
                ]
    return []


def is_preceded_by_extends(child: Node, parent_node: Node) -> bool:
    child_index = parent_node.children.index(child)
    return (
        child_index > 0 and parent_node.children[child_index - 1].type == cs.TS_EXTENDS
    )


def extract_interface_parents(
    class_node: Node,
    module_qn: str,
    import_processor: ImportProcessor,
    resolve_to_qn: Callable[[str, str], str],
) -> list[str]:
    """
    Extract parent interfaces for a TS interface declaration (extends clause).
    """
    extends_clause = find_child_by_type(class_node, cs.TS_EXTENDS_TYPE_CLAUSE)
    if not extends_clause:
        return []

    parent_classes: list[str] = []
    for child in extends_clause.children:
        if child.type == cs.TS_TYPE_IDENTIFIER and child.text:
            if parent_name := safe_decode_text(child):
                parent_classes.append(
                    resolve_js_ts_parent_class(
                        parent_name, module_qn, import_processor, resolve_to_qn
                    )
                )
    return parent_classes


def extract_mixin_parent_classes(
    call_expr_node: Node,
    module_qn: str,
    import_processor: ImportProcessor,
    resolve_to_qn: Callable[[str, str], str],
) -> list[str]:
    parent_classes: list[str] = []

    for child in call_expr_node.children:
        if child.type == cs.TS_ARGUMENTS:
            for arg_child in child.children:
                if arg_child.type == cs.TS_IDENTIFIER and arg_child.text:
                    if parent_name := safe_decode_text(arg_child):
                        parent_classes.append(
                            resolve_js_ts_parent_class(
                                parent_name, module_qn, import_processor, resolve_to_qn
                            )
                        )
                elif arg_child.type == cs.TS_CALL_EXPRESSION:
                    parent_classes.extend(
                        extract_mixin_parent_classes(
                            arg_child, module_qn, import_processor, resolve_to_qn
                        )
                    )
            break

    return parent_classes


def resolve_js_ts_parent_class(
    parent_name: str,
    module_qn: str,
    import_processor: ImportProcessor,
    resolve_to_qn: Callable[[str, str], str],
) -> str:
    if module_qn not in import_processor.import_mapping:
        return f"{module_qn}.{parent_name}"
    import_map = import_processor.import_mapping[module_qn]
    if parent_name in import_map:
        return import_map[parent_name]
    return resolve_to_qn(parent_name, module_qn)


def extract_implemented_interfaces(
    class_node: Node,
    module_qn: str,
    resolve_to_qn: Callable[[str, str], str],
) -> list[str]:
    """
    Extract interfaces implemented by a class (Java `implements` clause).

    Args:
        class_node: The class AST node.
        module_qn: The module qualified name.
        resolve_to_qn: Resolution callable.

    Returns:
        A list of qualified names of implemented interfaces.
    """
    implemented_interfaces: list[str] = []

    interfaces_node = class_node.child_by_field_name(cs.FIELD_INTERFACES)
    if interfaces_node:
        extract_java_interface_names(
            interfaces_node, implemented_interfaces, module_qn, resolve_to_qn
        )

    return implemented_interfaces


def extract_java_interface_names(
    interfaces_node: Node,
    interface_list: list[str],
    module_qn: str,
    resolve_to_qn: Callable[[str, str], str],
) -> None:
    for child in interfaces_node.children:
        if child.type == cs.TS_TYPE_LIST:
            for type_child in child.children:
                if type_child.type == cs.TS_TYPE_IDENTIFIER and type_child.text:
                    if interface_name := safe_decode_text(type_child):
                        interface_list.append(resolve_to_qn(interface_name, module_qn))
