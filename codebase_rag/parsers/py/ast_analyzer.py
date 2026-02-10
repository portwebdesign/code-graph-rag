"""
This module defines the `PythonAstAnalyzerMixin`, a component responsible for
analyzing a Python AST to infer types and resolve method calls.

As a mixin, it's designed to be used by the `PythonTypeInferenceEngine`. It
contains the core logic for traversing the AST, processing assignments, and
analyzing return statements to build a map of local variables to their inferred
types.

Key functionalities:
-   Traversing the AST to find assignments, comprehensions, and for-loops.
-   Processing simple and complex assignments to infer variable types.
-   Finding the AST node for a method given its fully qualified name.
-   Analyzing a method's `return` statements to infer its return type.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol

from loguru import logger
from tree_sitter import Node, QueryCursor

from codebase_rag.data_models.types_defs import LanguageQueries

from ...core import constants as cs
from ...core import logs as lg
from ..js_ts.utils import find_method_in_ast as find_js_method_in_ast
from ..utils import safe_decode_text

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ..factory import ASTCacheProtocol
    from ..js_ts.type_inference import JsTypeInferenceEngine

    class _AstAnalyzerDeps(Protocol):
        """Defines the dependencies required by the mixin for type hinting."""

        def build_local_variable_type_map(
            self, caller_node: Node, module_qn: str
        ) -> dict[str, str]: ...

        def _analyze_comprehension(
            self, node: Node, local_var_types: dict[str, str], module_qn: str
        ) -> None: ...

        def _analyze_for_loop(
            self, node: Node, local_var_types: dict[str, str], module_qn: str
        ) -> None: ...

        def _infer_instance_variable_types_from_assignments(
            self,
            assignments: list[Node],
            local_var_types: dict[str, str],
            module_qn: str,
        ) -> None: ...

    _AstBase: type = _AstAnalyzerDeps
else:
    _AstBase = object


class PythonAstAnalyzerMixin(_AstBase):
    """
    A mixin for analyzing Python ASTs to support type inference.
    """

    queries: dict[cs.SupportedLanguage, LanguageQueries]
    module_qn_to_file_path: dict[str, Path]
    ast_cache: ASTCacheProtocol

    _js_type_inference_getter: Callable[[], JsTypeInferenceEngine]

    @abstractmethod
    def _infer_type_from_expression(self, node: Node, module_qn: str) -> str | None:
        """Abstract method to infer type from any expression."""
        ...

    @abstractmethod
    def _infer_type_from_expression_simple(
        self, node: Node, module_qn: str
    ) -> str | None:
        """Abstract method to infer type from simple expressions."""
        ...

    @abstractmethod
    def _infer_type_from_expression_complex(
        self, node: Node, module_qn: str, local_var_types: dict[str, str]
    ) -> str | None:
        """Abstract method to infer type from complex expressions."""
        ...

    @abstractmethod
    def _infer_method_call_return_type(
        self, method_qn: str, module_qn: str, local_var_types: dict[str, str] | None
    ) -> str | None:
        """Abstract method to infer a method's return type."""
        ...

    @abstractmethod
    def _find_class_in_scope(self, class_name: str, module_qn: str) -> str | None:
        """Abstract method to find a class FQN from a simple name in scope."""
        ...

    def _traverse_single_pass(
        self, node: Node, local_var_types: dict[str, str], module_qn: str
    ) -> None:
        """
        Performs a single pass over the AST to collect and process nodes.

        Args:
            node (Node): The starting node for the traversal.
            local_var_types (dict[str, str]): The dictionary to populate with types.
            module_qn (str): The qualified name of the module.
        """
        assignments: list[Node] = []
        comprehensions: list[Node] = []
        for_statements: list[Node] = []

        stack: list[Node] = [node]
        while stack:
            current = stack.pop()
            node_type = current.type

            if node_type == cs.TS_PY_ASSIGNMENT:
                assignments.append(current)
            elif node_type == cs.TS_PY_LIST_COMPREHENSION:
                comprehensions.append(current)
            elif node_type == cs.TS_PY_FOR_STATEMENT:
                for_statements.append(current)

            stack.extend(reversed(current.children))

        for assignment in assignments:
            self._process_assignment_simple(assignment, local_var_types, module_qn)

        for assignment in assignments:
            self._process_assignment_complex(assignment, local_var_types, module_qn)

        for comp in comprehensions:
            self._analyze_comprehension(comp, local_var_types, module_qn)

        for for_stmt in for_statements:
            self._analyze_for_loop(for_stmt, local_var_types, module_qn)

        self._infer_instance_variable_types_from_assignments(
            assignments, local_var_types, module_qn
        )

    def _traverse_for_assignments(
        self,
        node: Node,
        local_var_types: dict[str, str],
        module_qn: str,
        processor: Callable[[Node, dict[str, str], str], None],
    ) -> None:
        """Recursively traverses the AST and applies a processor to assignment nodes."""
        stack: list[Node] = [node]
        while stack:
            current = stack.pop()
            if current.type == cs.TS_PY_ASSIGNMENT:
                processor(current, local_var_types, module_qn)
            stack.extend(reversed(current.children))

    def _process_assignment_simple(
        self, assignment_node: Node, local_var_types: dict[str, str], module_qn: str
    ) -> None:
        """Processes an assignment to infer types from simple literal values."""
        left_node = assignment_node.child_by_field_name(cs.TS_FIELD_LEFT)
        right_node = assignment_node.child_by_field_name(cs.TS_FIELD_RIGHT)

        if not left_node or not right_node:
            return

        var_name = self._extract_assignment_variable_name(left_node)
        if not var_name:
            return

        if inferred_type := self._infer_type_from_expression_simple(
            right_node, module_qn
        ):
            local_var_types[var_name] = inferred_type
            logger.debug(lg.PY_TYPE_SIMPLE.format(var=var_name, type=inferred_type))

    def _process_assignment_complex(
        self, assignment_node: Node, local_var_types: dict[str, str], module_qn: str
    ) -> None:
        """Processes an assignment to infer types from complex expressions like function calls."""
        left_node = assignment_node.child_by_field_name(cs.TS_FIELD_LEFT)
        right_node = assignment_node.child_by_field_name(cs.TS_FIELD_RIGHT)

        if not left_node or not right_node:
            return

        var_name = self._extract_assignment_variable_name(left_node)
        if not var_name:
            return

        if var_name in local_var_types:
            return

        if inferred_type := self._infer_type_from_expression_complex(
            right_node, module_qn, local_var_types
        ):
            local_var_types[var_name] = inferred_type
            logger.debug(lg.PY_TYPE_COMPLEX.format(var=var_name, type=inferred_type))

    def _extract_assignment_variable_name(self, node: Node) -> str | None:
        """Extracts the variable name from the left-hand side of an assignment."""
        if node.type != cs.TS_PY_IDENTIFIER or node.text is None:
            return None
        return safe_decode_text(node) or None

    def _find_method_ast_node(self, method_qn: str) -> Node | None:
        """
        Finds the AST node for a method given its fully qualified name.

        Args:
            method_qn (str): The FQN of the method.

        Returns:
            Node | None: The AST node of the method, or None if not found.
        """
        qn_parts = method_qn.split(cs.SEPARATOR_DOT)
        if len(qn_parts) < 3:
            return None

        class_name = qn_parts[-2]
        method_name = qn_parts[-1]

        expected_module = cs.SEPARATOR_DOT.join(qn_parts[:-2])
        file_path = self.module_qn_to_file_path.get(expected_module)
        if not file_path or file_path not in self.ast_cache:
            return None

        root_node, language = self.ast_cache[file_path]
        return self._find_method_in_ast(root_node, class_name, method_name, language)

    def _find_method_in_ast(
        self,
        root_node: Node,
        class_name: str,
        method_name: str,
        language: cs.SupportedLanguage,
    ) -> Node | None:
        """Dispatches to the correct language-specific method finder."""
        match language:
            case cs.SupportedLanguage.PYTHON:
                return self._find_python_method_in_ast(
                    root_node, class_name, method_name
                )
            case cs.SupportedLanguage.JS | cs.SupportedLanguage.TS:
                return find_js_method_in_ast(root_node, class_name, method_name)
            case _:
                return None

    def _find_python_method_in_ast(
        self, root_node: Node, class_name: str, method_name: str
    ) -> Node | None:
        """Finds a Python method node within a class in a given AST."""
        lang_queries = self.queries[cs.SupportedLanguage.PYTHON]
        class_query = lang_queries[cs.QUERY_KEY_CLASSES]
        if not class_query:
            return None
        cursor = QueryCursor(class_query)
        captures = cursor.captures(root_node)

        method_query = lang_queries[cs.QUERY_KEY_FUNCTIONS]
        if not method_query:
            return None

        for class_node in captures.get(cs.QUERY_CAPTURE_CLASS, []):
            if not isinstance(class_node, Node):
                continue

            name_node = class_node.child_by_field_name(cs.TS_FIELD_NAME)
            if not name_node or name_node.text is None:
                continue

            if safe_decode_text(name_node) != class_name:
                continue

            body_node = class_node.child_by_field_name(cs.TS_FIELD_BODY)
            if not body_node:
                continue

            method_cursor = QueryCursor(method_query)
            method_captures = method_cursor.captures(body_node)

            for method_node in method_captures.get(cs.QUERY_CAPTURE_FUNCTION, []):
                if not isinstance(method_node, Node):
                    continue

                method_name_node = method_node.child_by_field_name(cs.TS_FIELD_NAME)
                if not method_name_node or method_name_node.text is None:
                    continue

                if safe_decode_text(method_name_node) == method_name:
                    return method_node

        return None

    def _analyze_method_return_statements(
        self, method_node: Node, method_qn: str
    ) -> str | None:
        """
        Analyzes all return statements in a method to infer its return type.

        Args:
            method_node (Node): The AST node of the method.
            method_qn (str): The FQN of the method.

        Returns:
            str | None: The inferred return type, or None.
        """
        return_nodes: list[Node] = []
        self._find_return_statements(method_node, return_nodes)

        for return_node in return_nodes:
            return_value = next(
                (
                    child
                    for child in return_node.children
                    if child.type not in (cs.TS_PY_RETURN, cs.TS_PY_KEYWORD)
                ),
                None,
            )
            if return_value and (
                inferred_type := self._analyze_return_expression(
                    return_value, method_qn
                )
            ):
                return inferred_type

        return None

    def _find_return_statements(self, node: Node, return_nodes: list[Node]) -> None:
        """Recursively finds all `return_statement` nodes within a given node."""
        stack: list[Node] = [node]

        while stack:
            current = stack.pop()
            if current.type == cs.TS_PY_RETURN_STATEMENT:
                return_nodes.append(current)

            stack.extend(reversed(current.children))

    def _analyze_return_expression(self, expr_node: Node, method_qn: str) -> str | None:
        """Analyzes a return expression to infer the type."""
        match expr_node.type:
            case cs.TS_PY_CALL:
                return self._analyze_call_return(expr_node, method_qn)
            case cs.TS_PY_IDENTIFIER:
                return self._analyze_identifier_return(expr_node, method_qn)
            case cs.TS_PY_ATTRIBUTE:
                return self._analyze_attribute_return(expr_node, method_qn)
            case _:
                return None

    def _analyze_call_return(self, expr_node: Node, method_qn: str) -> str | None:
        """Analyzes a `call` expression in a return statement."""
        func_node = expr_node.child_by_field_name(cs.TS_FIELD_FUNCTION)
        if not func_node:
            return None

        if (
            func_node.type == cs.TS_PY_IDENTIFIER
            and func_node.text is not None
            and (class_name := safe_decode_text(func_node))
        ):
            return self._resolve_call_class_name(class_name, method_qn)

        if func_node.type == cs.TS_PY_ATTRIBUTE:
            if method_call_text := self._extract_method_call_from_attr(func_node):
                module_qn = cs.SEPARATOR_DOT.join(
                    method_qn.split(cs.SEPARATOR_DOT)[:-2]
                )
                return self._infer_method_call_return_type(
                    method_call_text, module_qn, None
                )

        return None

    def _resolve_call_class_name(self, class_name: str, method_qn: str) -> str | None:
        """Resolves the class name from a constructor call in a return statement."""
        qn_parts = method_qn.split(cs.SEPARATOR_DOT)
        if class_name == cs.PY_KEYWORD_CLS and len(qn_parts) >= 2:
            return qn_parts[-2]

        if class_name[0].isupper():
            module_qn = cs.SEPARATOR_DOT.join(qn_parts[:-2])
            resolved_class = self._find_class_in_scope(class_name, module_qn)
            return resolved_class or class_name

        return None

    def _analyze_identifier_return(self, expr_node: Node, method_qn: str) -> str | None:
        """Analyzes an `identifier` in a return statement."""
        if expr_node.text is None:
            return None

        identifier = safe_decode_text(expr_node)
        if not identifier:
            return None

        if identifier in (cs.PY_KEYWORD_SELF, cs.PY_KEYWORD_CLS):
            qn_parts = method_qn.split(cs.SEPARATOR_DOT)
            return qn_parts[-2] if len(qn_parts) >= 2 else None

        module_qn = cs.SEPARATOR_DOT.join(method_qn.split(cs.SEPARATOR_DOT)[:-2])
        if method_node := self._find_method_ast_node(method_qn):
            local_vars = self.build_local_variable_type_map(method_node, module_qn)
            if identifier in local_vars:
                logger.debug(
                    lg.PY_VAR_FROM_CONTEXT.format(
                        var=identifier, type=local_vars[identifier]
                    )
                )
                return local_vars[identifier]

        logger.debug(lg.PY_VAR_CANNOT_INFER.format(var=identifier))
        return None

    def _analyze_attribute_return(self, expr_node: Node, method_qn: str) -> str | None:
        """Analyzes an `attribute` access in a return statement."""
        object_node = expr_node.child_by_field_name(cs.TS_FIELD_OBJECT)
        if (
            object_node
            and object_node.type == cs.TS_PY_IDENTIFIER
            and object_node.text is not None
            and (object_name := safe_decode_text(object_node))
            and object_name in (cs.PY_KEYWORD_CLS, cs.PY_KEYWORD_SELF)
        ):
            qn_parts = method_qn.split(cs.SEPARATOR_DOT)
            return qn_parts[-2] if len(qn_parts) >= 2 else None

        return None

    def _extract_method_call_from_attr(self, attr_node: Node) -> str | None:
        """Extracts the full method call string from an `attribute` node."""
        return safe_decode_text(attr_node) or None if attr_node.text else None
