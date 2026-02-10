"""
This module defines the `LuaTypeInferenceEngine`, a class responsible for
inferring variable types in Lua source code.

Lua is a dynamically typed language, making type inference challenging. This
engine focuses on a common pattern: inferring the type of a variable based on
the return value of a method call, particularly for object construction patterns
like `local my_obj = MyClass:new()`.

Key functionalities:
-   Building a map of local variables to their inferred types within a given scope.
-   Processing variable declarations and assignments.
-   Inferring a variable's type by analyzing the method call on the right-hand
    side of an assignment.
-   Resolving class names using the import map and function registry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from codebase_rag.data_models.types_defs import (
    FunctionRegistryTrieProtocol,
    TreeSitterNodeProtocol,
)

from ...core import constants as cs
from ...core import logs as ls
from ..utils import safe_decode_text

if TYPE_CHECKING:
    from ..import_processor import ImportProcessor


class LuaTypeInferenceEngine:
    """
    A type inference engine specifically for Lua code.
    """

    def __init__(
        self,
        import_processor: ImportProcessor,
        function_registry: FunctionRegistryTrieProtocol,
        project_name: str,
    ):
        """
        Initializes the LuaTypeInferenceEngine.

        Args:
            import_processor (ImportProcessor): The shared import processor.
            function_registry (FunctionRegistryTrieProtocol): The shared function registry.
            project_name (str): The name of the project.
        """
        self.import_processor = import_processor
        self.function_registry = function_registry
        self.project_name = project_name

    def build_local_variable_type_map(
        self, caller_node: TreeSitterNodeProtocol, module_qn: str
    ) -> dict[str, str]:
        """
        Builds a map of local variable names to their inferred types for a given scope.

        Args:
            caller_node (TreeSitterNodeProtocol): The AST node representing the scope.
            module_qn (str): The qualified name of the module.

        Returns:
            dict[str, str]: A dictionary mapping variable names to their inferred type FQNs.
        """
        local_var_types: dict[str, str] = {}
        stack: list[TreeSitterNodeProtocol] = [caller_node]

        while stack:
            current = stack.pop()
            if current.type == cs.TS_LUA_VARIABLE_DECLARATION:
                self._process_variable_declaration(current, module_qn, local_var_types)
            stack.extend(reversed(current.children))

        logger.debug(ls.LUA_VAR_TYPE_MAP_BUILT.format(count=len(local_var_types)))
        return local_var_types

    def _process_variable_declaration(
        self,
        decl_node: TreeSitterNodeProtocol,
        module_qn: str,
        local_var_types: dict[str, str],
    ) -> None:
        """Processes a `variable_declaration` node to infer types."""
        assignment = next(
            (c for c in decl_node.children if c.type == cs.TS_LUA_ASSIGNMENT_STATEMENT),
            None,
        )
        if not assignment:
            return

        var_names = self._extract_var_names(assignment)
        func_calls = self._extract_function_calls(assignment)

        for i, var_name in enumerate(var_names):
            if i >= len(func_calls):
                break
            if var_type := self._infer_lua_variable_type_from_value(
                func_calls[i], module_qn
            ):
                local_var_types[var_name] = var_type
                logger.debug(
                    ls.LUA_VAR_INFERRED.format(var_name=var_name, var_type=var_type)
                )

    def _extract_var_names(self, assignment: TreeSitterNodeProtocol) -> list[str]:
        """Extracts variable names from the left-hand side of an assignment."""
        names: list[str] = []
        for child in assignment.children:
            if child.type != cs.TS_LUA_VARIABLE_LIST:
                continue
            for var_node in child.children:
                if var_node.type == cs.TS_LUA_IDENTIFIER:
                    if decoded := safe_decode_text(var_node):
                        names.append(decoded)
        return names

    def _extract_function_calls(
        self, assignment: TreeSitterNodeProtocol
    ) -> list[TreeSitterNodeProtocol]:
        """Extracts function call nodes from the right-hand side of an assignment."""
        calls: list[TreeSitterNodeProtocol] = []
        for child in assignment.children:
            if child.type != cs.TS_LUA_EXPRESSION_LIST:
                continue
            calls.extend(
                expr for expr in child.children if expr.type == cs.TS_LUA_FUNCTION_CALL
            )
        return calls

    def _infer_lua_variable_type_from_value(
        self, value_node: TreeSitterNodeProtocol, module_qn: str
    ) -> str | None:
        """
        Infers a variable's type from the value it's assigned, focusing on method calls.

        Args:
            value_node (TreeSitterNodeProtocol): The expression node on the right side.
            module_qn (str): The FQN of the current module.

        Returns:
            str | None: The inferred type FQN, or None.
        """
        if value_node.type == cs.TS_LUA_FUNCTION_CALL:
            for child in value_node.children:
                if child.type == cs.TS_LUA_METHOD_INDEX_EXPRESSION:
                    class_name = None
                    method_name = None

                    for grandchild in child.children:
                        if grandchild.type == cs.TS_LUA_IDENTIFIER:
                            if class_name is None:
                                class_name = safe_decode_text(grandchild)
                            else:
                                method_name = safe_decode_text(grandchild)

                    if class_name and method_name:
                        if class_qn := self._resolve_lua_class_name(
                            class_name, module_qn
                        ):
                            logger.debug(
                                ls.LUA_TYPE_INFERENCE_RETURN.format(
                                    class_name=class_name,
                                    method_name=method_name,
                                    class_qn=class_qn,
                                )
                            )
                            return class_qn

        return None

    def _resolve_lua_class_name(self, class_name: str, module_qn: str) -> str | None:
        """
        Resolves a Lua class name (often a table) to its likely FQN.

        Args:
            class_name (str): The simple name of the class/table.
            module_qn (str): The FQN of the current module.

        Returns:
            str | None: The resolved FQN, or None.
        """
        if module_qn in self.import_processor.import_mapping:
            import_map = self.import_processor.import_mapping[module_qn]
            if class_name in import_map:
                imported_qn = import_map[class_name]
                full_class_qn = f"{imported_qn}{cs.SEPARATOR_DOT}{class_name}"
                return full_class_qn

        local_class_qn = f"{module_qn}{cs.SEPARATOR_DOT}{class_name}"
        if local_class_qn in self.function_registry:
            return local_class_qn

        method_prefix = f"{local_class_qn}{cs.LUA_METHOD_SEPARATOR}"
        return next(
            (
                local_class_qn
                for qn, _ in self.function_registry.find_with_prefix(local_class_qn)
                if qn.startswith(method_prefix)
            ),
            None,
        )
