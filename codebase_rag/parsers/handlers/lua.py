"""
This module defines the `LuaHandler`, a language-specific handler for Lua.

It implements the `BaseLanguageHandler` protocol to provide Lua-specific logic
for tasks like extracting function names, which includes handling functions
assigned to variables or table fields.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core import constants as cs
from ..lua import utils as lua_utils
from .base import BaseLanguageHandler

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import ASTNode


class LuaHandler(BaseLanguageHandler):
    """Language handler for Lua."""

    def extract_function_name(self, node: ASTNode) -> str | None:
        """
        Extracts the name of a Lua function from its AST node.

        This method handles both named function declarations and functions
        assigned to variables or table fields (e.g., `my_func = function() ...`
        or `my_table.my_func = function() ...`).

        Args:
            node (ASTNode): The AST node of the function.

        Returns:
            str | None: The extracted name of the function, or None if it cannot be determined.
        """
        if (name_node := node.child_by_field_name(cs.TS_FIELD_NAME)) and name_node.text:
            from ..utils import safe_decode_text

            return safe_decode_text(name_node)

        if node.type == cs.TS_LUA_FUNCTION_DEFINITION:
            return lua_utils.extract_assigned_name(
                node, accepted_var_types=(cs.TS_DOT_INDEX_EXPRESSION, cs.TS_IDENTIFIER)
            )

        return None
