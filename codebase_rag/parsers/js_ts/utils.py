from typing import TYPE_CHECKING

from tree_sitter import Language, Node

from codebase_rag.core import constants as cs

from ..utils import safe_decode_text

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import LanguageQueries


def get_js_ts_language_obj(
    language: cs.SupportedLanguage,
    queries: dict[cs.SupportedLanguage, "LanguageQueries"],
) -> Language | None:
    """
    Retrieve the tree-sitter language object for JS or TS.

    Args:
        language: The specific language (JS or TS).
        queries: Dictionary of language queries and objects.

    Returns:
        The tree-sitter Language object, or None if not JS/TS.
    """
    if language not in cs.JS_TS_LANGUAGES:
        return None

    lang_queries = queries[language]
    return lang_queries.get(cs.QUERY_LANGUAGE)


def _extract_class_qn(method_qn: str) -> str | None:
    """
    Extract the class qualified name from a method qualified name.

    Args:
        method_qn: The method qualified name.

    Returns:
        The class qualified name, or None.
    """
    qn_parts = method_qn.split(cs.SEPARATOR_DOT)
    return cs.SEPARATOR_DOT.join(qn_parts[:-1]) if len(qn_parts) >= 2 else None


def extract_method_call(member_expr_node: Node) -> str | None:
    """
    Extract the method call string from a member expression node.

    Args:
        member_expr_node: The member expression AST node.

    Returns:
        The method call string (e.g., "obj.method"), or None.
    """
    object_node = member_expr_node.child_by_field_name(cs.FIELD_OBJECT)
    property_node = member_expr_node.child_by_field_name(cs.FIELD_PROPERTY)

    if object_node and property_node:
        object_text = object_node.text
        property_text = property_node.text

        if object_text and property_text:
            object_name = safe_decode_text(object_node)
            property_name = safe_decode_text(property_node)
            return f"{object_name}{cs.SEPARATOR_DOT}{property_name}"

    return None


def find_method_in_class_body(class_body_node: Node, method_name: str) -> Node | None:
    """
    Find a method definition within a class body by name.

    Args:
        class_body_node: The class body AST node.
        method_name: The name of the method to find.

    Returns:
        The method definition node, or None.
    """
    for child in class_body_node.children:
        if child.type == cs.TS_METHOD_DEFINITION:
            name_node = child.child_by_field_name(cs.FIELD_NAME)
            if name_node and name_node.text:
                found_name = safe_decode_text(name_node)
                if found_name == method_name:
                    return child

    return None


def find_method_in_ast(
    root_node: Node, class_name: str, method_name: str
) -> Node | None:
    """
    Find a method definition in the entire AST by class and method name.

    Args:
        root_node: The root AST node.
        class_name: The name of the class.
        method_name: The name of the method.

    Returns:
        The method definition node, or None.
    """
    stack: list[Node] = [root_node]

    while stack:
        current = stack.pop()

        if current.type == cs.TS_CLASS_DECLARATION:
            name_node = current.child_by_field_name(cs.FIELD_NAME)
            if name_node and name_node.text:
                found_class_name = safe_decode_text(name_node)
                if found_class_name == class_name:
                    if body_node := current.child_by_field_name(cs.FIELD_BODY):
                        return find_method_in_class_body(body_node, method_name)

        stack.extend(reversed(current.children))

    return None


def find_return_statements(node: Node, return_nodes: list[Node]) -> None:
    """
    Recursively find all return statements within a node (e.g., method body).

    Args:
        node: The AST node to search.
        return_nodes: List to append found return nodes to.
    """
    stack: list[Node] = [node]

    while stack:
        current = stack.pop()

        if current.type == cs.TS_RETURN_STATEMENT:
            return_nodes.append(current)

        stack.extend(reversed(current.children))


def extract_constructor_name(new_expr_node: Node) -> str | None:
    """
    Extract the constructor name from a 'new' expression.

    Args:
        new_expr_node: The new expression AST node.

    Returns:
        The constructor name, or None.
    """
    if new_expr_node.type != cs.TS_NEW_EXPRESSION:
        return None

    constructor_node = new_expr_node.child_by_field_name(cs.FIELD_CONSTRUCTOR)
    if constructor_node and constructor_node.type == cs.TS_IDENTIFIER:
        constructor_text = constructor_node.text
        if constructor_text:
            return safe_decode_text(constructor_node)

    return None


def analyze_return_expression(expr_node: Node, method_qn: str) -> str | None:
    """
    Analyze a return expression to infer the returned type.

    Args:
        expr_node: The expression node being returned.
        method_qn: The qualified name of the method (for 'this' resolution).

    Returns:
        The inferred type string, or None.
    """
    match expr_node.type:
        case cs.TS_NEW_EXPRESSION:
            if class_name := extract_constructor_name(expr_node):
                return _extract_class_qn(method_qn) or class_name
            return None

        case cs.TS_THIS:
            return _extract_class_qn(method_qn)

        case cs.TS_MEMBER_EXPRESSION:
            object_node = expr_node.child_by_field_name(cs.FIELD_OBJECT)
            if not object_node:
                return None

            match object_node.type:
                case cs.TS_THIS:
                    return _extract_class_qn(method_qn)
                case cs.TS_IDENTIFIER:
                    if object_node.text:
                        object_name = safe_decode_text(object_node)
                        qn_parts = method_qn.split(cs.SEPARATOR_DOT)
                        if len(qn_parts) >= 2 and object_name == qn_parts[-2]:
                            return cs.SEPARATOR_DOT.join(qn_parts[:-1])
            return None

        case _:
            return None
