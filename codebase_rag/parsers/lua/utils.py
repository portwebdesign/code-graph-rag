"""
This module provides utility functions specifically for parsing Lua source code.

It contains helpers for handling Lua's syntax, particularly for identifying
the names of functions that are defined anonymously and assigned to variables
or table fields.

Key functionalities:
-   `extract_assigned_name`: Traces up the AST from a function definition to find
    the variable it's being assigned to.
-   `extract_pcall_second_identifier`: A specific helper for handling the common
    `local ok, my_module = pcall(require, 'my_module')` pattern to extract the
    local variable name for the module.
"""

from tree_sitter import Node

from ...core import constants as cs
from ..utils import contains_node, safe_decode_text


def extract_assigned_name(
    target_node: Node, accepted_var_types: tuple[str, ...] = cs.LUA_DEFAULT_VAR_TYPES
) -> str | None:
    """
    Finds the name of a variable to which a target node (like a function definition)
    is being assigned.

    Args:
        target_node (Node): The node whose assigned name is to be found.
        accepted_var_types (tuple[str, ...]): A tuple of node types that are
                                              considered valid variable names.

    Returns:
        str | None: The name of the variable, or None if not found in an assignment.
    """
    current = target_node.parent
    while current and current.type != cs.TS_LUA_ASSIGNMENT_STATEMENT:
        current = current.parent

    if not current:
        return None

    expression_list = next(
        (
            child
            for child in current.children
            if child.type == cs.TS_LUA_EXPRESSION_LIST
        ),
        None,
    )
    if not expression_list:
        return None

    values = []
    values.extend(
        expression_list.child(i)
        for i in range(expression_list.child_count)
        if expression_list.field_name_for_child(i) == cs.FIELD_VALUE
    )
    target_index = next(
        (
            idx
            for idx, value in enumerate(values)
            if value == target_node or contains_node(value, target_node)
        ),
        -1,
    )
    if target_index == -1:
        return None

    variable_list = next(
        (child for child in current.children if child.type == cs.TS_LUA_VARIABLE_LIST),
        None,
    )
    if not variable_list:
        return None

    names = []
    names.extend(
        variable_list.child(i)
        for i in range(variable_list.child_count)
        if variable_list.field_name_for_child(i) == cs.FIELD_NAME
    )
    if target_index < len(names):
        var_child = names[target_index]
        if var_child.type in accepted_var_types:
            return safe_decode_text(var_child)

    return None


def find_ancestor_statement(node: Node) -> Node | None:
    """
    Traverses up from a node to find the first ancestor that is a statement.

    Args:
        node (Node): The starting node.

    Returns:
        Node | None: The ancestor statement node, or None if not found.
    """
    stmt = node.parent
    while stmt and not (
        stmt.type.endswith(cs.LUA_STATEMENT_SUFFIX)
        or stmt.type in {cs.TS_LUA_ASSIGNMENT_STATEMENT, cs.TS_LUA_LOCAL_STATEMENT}
    ):
        stmt = stmt.parent
    return stmt


def extract_pcall_second_identifier(call_node: Node) -> str | None:
    """
    Extracts the second identifier from the left-hand side of an assignment
    containing a `pcall`.

    This is used for the common Lua pattern: `local ok, my_module = pcall(...)`.

    Args:
        call_node (Node): The `pcall` function call node.

    Returns:
        str | None: The name of the second variable (e.g., 'my_module'), or None.
    """
    stmt = find_ancestor_statement(call_node)
    if not stmt:
        return None

    variable_list = next(
        (child for child in stmt.children if child.type == cs.TS_LUA_VARIABLE_LIST),
        None,
    )
    if not variable_list:
        return None

    names = []
    for i in range(variable_list.child_count):
        if variable_list.field_name_for_child(i) == cs.FIELD_NAME:
            name_node = variable_list.child(i)
            if name_node and name_node.type == cs.TS_LUA_IDENTIFIER:
                if decoded := safe_decode_text(name_node):
                    names.append(decoded)

    return names[1] if len(names) >= 2 else None
