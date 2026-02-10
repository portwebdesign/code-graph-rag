"""
This module provides utility functions specifically for parsing JavaScript and
TypeScript source code.

These helpers are used by other modules within the `js_ts` package to perform
common tasks related to AST traversal and information extraction for these
languages.

Key functionalities:
-   Extracting method call information from member expressions.
-   Finding specific method nodes within a class body or an entire AST.
-   Locating all `return` statements within a given scope.
-   Extracting constructor names from `new` expressions.
-   Analyzing return expressions to infer a method's return type.
"""

from typing import TYPE_CHECKING

from tree_sitter import Language, Node

from ...core import constants as cs
from ..utils import safe_decode_text

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import LanguageQueries


def get_js_ts_language_obj(
    language: cs.SupportedLanguage,
    queries: dict[cs.SupportedLanguage, "LanguageQueries"],
) -> Language | None:
    """
    Gets the tree-sitter Language object for JavaScript or TypeScript.

    Args:
        language (cs.SupportedLanguage): The language to get the object for.
        queries (dict): The dictionary of language queries.

    Returns:
        Language | None: The Language object, or None if the language is not JS/TS.
    """
    if language not in cs.JS_TS_LANGUAGES:
        return None

    lang_queries = queries[language]
    return lang_queries.get(cs.QUERY_LANGUAGE)


def _extract_class_qn(method_qn: str) -> str | None:
    """Extracts the class FQN from a method's FQN."""
    qn_parts = method_qn.split(cs.SEPARATOR_DOT)
    return cs.SEPARATOR_DOT.join(qn_parts[:-1]) if len(qn_parts) >= 2 else None


def extract_method_call(member_expr_node: Node) -> str | None:
    """
    Extracts a method call string (e.g., 'myObj.myMethod') from a `member_expression` node.

    Args:
        member_expr_node (Node): The `member_expression` AST node.

    Returns:
        str | None: The formatted method call string, or None.
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
    Finds a method by name within a class body node.

    Args:
        class_body_node (Node): The `class_body` AST node.
        method_name (str): The name of the method to find.

    Returns:
        Node | None: The AST node of the method, or None if not found.
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
    Finds a method by class and method name within a larger AST.

    Args:
        root_node (Node): The root node of the AST to search.
        class_name (str): The name of the containing class.
        method_name (str): The name of the method.

    Returns:
        Node | None: The AST node of the method, or None if not found.
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
    Recursively finds all `return_statement` nodes within a given node.

    Args:
        node (Node): The node to search within.
        return_nodes (list[Node]): A list to append the found return nodes to.
    """
    stack: list[Node] = [node]

    while stack:
        current = stack.pop()

        if current.type == cs.TS_RETURN_STATEMENT:
            return_nodes.append(current)

        stack.extend(reversed(current.children))


def extract_constructor_name(new_expr_node: Node) -> str | None:
    """
    Extracts the class name from a `new` expression.

    Args:
        new_expr_node (Node): The `new_expression` AST node.

    Returns:
        str | None: The name of the class being instantiated.
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
    Analyzes a return expression to infer the return type.

    Args:
        expr_node (Node): The expression node from a `return` statement.
        method_qn (str): The FQN of the method containing the return statement.

    Returns:
        str | None: The inferred return type FQN, or None.
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
