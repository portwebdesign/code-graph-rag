from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import ASTNode
from codebase_rag.infrastructure.language_spec import LANGUAGE_FQN_SPECS, LANGUAGE_SPECS
from codebase_rag.parsers.core.utils import (
    get_function_captures,
    is_method_node,
    safe_decode_text,
)
from codebase_rag.parsers.handlers.registry import get_handler
from codebase_rag.utils.fqn_resolver import resolve_fqn_from_ast


@dataclass(frozen=True)
class FunctionMetadata:
    """
    Metadata extracted from a function or method node.

    Args:
        qualified_name (str): The fully qualified name.
        label (cs.NodeLabel): The node label (FUNCTION or METHOD).
        module_qn (str): The qualified name of the containing module.
        name (str): The simple name of the function.
        decorators (list[str]): List of decorators applied.
        return_type (str | None): Return type annotation.
        parameter_types (list[tuple[str, str]]): List of (name, type) for parameters.
        thrown_exceptions (list[str]): List of exceptions explicitly thrown.
        caught_exceptions (list[str]): List of exceptions explicitly caught.
    """

    qualified_name: str
    label: cs.NodeLabel
    module_qn: str
    name: str
    decorators: list[str]
    return_type: str | None
    parameter_types: list[tuple[str, str]]
    thrown_exceptions: list[str]
    caught_exceptions: list[str]


class EnhancedFunctionExtractor:
    """
    Extracts detailed function metadata from AST nodes, including types and exceptions.

    Args:
        repo_path (Path): Path to the repository root.
        project_name (str): Name of the project.
    """

    def __init__(self, repo_path: Path, project_name: str) -> None:
        self.repo_path = repo_path
        self.project_name = project_name

    def extract_from_ast(
        self,
        file_path: Path,
        root_node: Node,
        language: cs.SupportedLanguage,
        queries: dict,
    ) -> list[FunctionMetadata]:
        """
        Extracts function metadata from the given AST root node.

        Args:
            file_path (Path): Path to the file being processed.
            root_node (Node): The root AST node.
            language (cs.SupportedLanguage): The language of the file.
            queries (dict): Queries dictionary for the language.

        Returns:
            list[FunctionMetadata]: A list of extracted function metadata objects.
        """
        result = get_function_captures(root_node, language, queries)
        if not result:
            return []

        lang_spec = LANGUAGE_SPECS.get(language)
        if not lang_spec:
            return []

        module_qn = self._module_qn_for_path(file_path)
        handler = get_handler(language)

        _, captures = result
        functions: list[FunctionMetadata] = []

        for func_node in captures.get(cs.CAPTURE_FUNCTION, []):
            if not isinstance(func_node, Node):
                continue

            is_method = is_method_node(func_node, lang_spec)
            name = self._extract_function_name(func_node)
            if not name:
                continue

            qualified_name = self._resolve_qualified_name(
                func_node,
                file_path,
                module_qn,
                name,
                language,
                lang_spec,
                is_method,
            )
            if not qualified_name:
                continue

            decorators = handler.extract_decorators(func_node)
            return_type = self._extract_return_type(func_node)
            parameter_types = self._extract_parameter_types(func_node)
            thrown, caught = self._extract_exception_types(func_node)

            functions.append(
                FunctionMetadata(
                    qualified_name=qualified_name,
                    label=cs.NodeLabel.METHOD if is_method else cs.NodeLabel.FUNCTION,
                    module_qn=module_qn,
                    name=name,
                    decorators=decorators,
                    return_type=return_type,
                    parameter_types=parameter_types,
                    thrown_exceptions=thrown,
                    caught_exceptions=caught,
                )
            )

        return functions

    def _resolve_qualified_name(
        self,
        func_node: ASTNode,
        file_path: Path,
        module_qn: str,
        name: str,
        language: cs.SupportedLanguage,
        lang_spec,
        is_method: bool,
    ) -> str | None:
        """
        Resolves the fully qualified name for a function or method.

        Args:
            func_node (ASTNode): The function node.
            file_path (Path): Path to the file.
            module_qn (str): Module qualified name.
            name (str): Simple function name.
            language (cs.SupportedLanguage): Language enum.
            lang_spec: Language specification object.
            is_method (bool): True if it is a method.

        Returns:
            str | None: The resolved qualified name.
        """
        fqn_spec = LANGUAGE_FQN_SPECS.get(language)
        if fqn_spec:
            resolved = resolve_fqn_from_ast(
                func_node,
                file_path,
                self.repo_path,
                self.project_name,
                fqn_spec,
            )
            if resolved:
                return resolved

        if is_method:
            class_name = self._find_enclosing_class_name(func_node, lang_spec)
            if class_name:
                return (
                    f"{module_qn}{cs.SEPARATOR_DOT}{class_name}{cs.SEPARATOR_DOT}{name}"
                )

        return f"{module_qn}{cs.SEPARATOR_DOT}{name}"

    def _find_enclosing_class_name(self, node: ASTNode, lang_spec) -> str | None:
        """
        Finds the name of the class enclosing the given node.

        Args:
            node (ASTNode): The starting node.
            lang_spec: Language specification.

        Returns:
            str | None: The class name or None.
        """
        current = node.parent
        while current and current.type not in lang_spec.module_node_types:
            if current.type in lang_spec.class_node_types:
                name_node = current.child_by_field_name(cs.FIELD_NAME)
                if name_node and name_node.text:
                    return safe_decode_text(name_node)
            current = current.parent
        return None

    def _extract_function_name(self, func_node: ASTNode) -> str | None:
        """
        Extracts the simple name of a function from its AST node.

        Args:
            func_node (ASTNode): The function node.

        Returns:
            str | None: The function name.
        """
        name_node = func_node.child_by_field_name(cs.FIELD_NAME)
        if name_node and name_node.text:
            return safe_decode_text(name_node)
        return None

    def _extract_return_type(self, func_node: ASTNode) -> str | None:
        """
        Extracts the return type annotation from a function node.

        Args:
            func_node (ASTNode): The function node.

        Returns:
            str | None: The return type name.
        """
        return_node = func_node.child_by_field_name("return_type")
        if return_node and return_node.text:
            return safe_decode_text(return_node)

        type_node = func_node.child_by_field_name(cs.TS_FIELD_TYPE)
        if type_node and type_node.text:
            return safe_decode_text(type_node)

        return None

    def _extract_parameter_types(self, func_node: ASTNode) -> list[tuple[str, str]]:
        """
        Extracts parameter names and types from a function node.

        Args:
            func_node (ASTNode): The function node.

        Returns:
            list[tuple[str, str]]: A list of (name, type) tuples.
        """
        params_node = func_node.child_by_field_name(cs.TS_FIELD_PARAMETERS)
        if not params_node:
            return []

        results: list[tuple[str, str]] = []
        for child in params_node.children:
            param_name = self._extract_parameter_name(child)
            param_type = self._extract_parameter_type(child)
            if param_name and param_type:
                results.append((param_name, param_type))
        return results

    def _extract_parameter_name(self, node: ASTNode) -> str | None:
        """
        Extracts the name from a parameter node.

        Args:
            node (ASTNode): The parameter node.

        Returns:
            str | None: The parameter name.
        """
        name_node = node.child_by_field_name(cs.FIELD_NAME)
        if name_node and name_node.text:
            return safe_decode_text(name_node)

        if node.type == cs.TS_IDENTIFIER and node.text:
            return safe_decode_text(node)

        for child in node.children:
            if child.type == cs.TS_IDENTIFIER and child.text:
                return safe_decode_text(child)
        return None

    def _extract_parameter_type(self, node: ASTNode) -> str | None:
        """
        Extracts the type annotation from a parameter node.

        Args:
            node (ASTNode): The parameter node.

        Returns:
            str | None: The type name.
        """
        type_node = node.child_by_field_name(cs.TS_FIELD_TYPE)
        if type_node and type_node.text:
            return safe_decode_text(type_node)

        annotation = node.child_by_field_name("type_annotation")
        if annotation and annotation.text:
            return safe_decode_text(annotation)

        return None

    def _extract_exception_types(
        self, func_node: ASTNode
    ) -> tuple[list[str], list[str]]:
        """
        Extracts thrown and caught exceptions from a function body.

        Args:
            func_node (ASTNode): The function node.

        Returns:
            tuple[list[str], list[str]]: Tuple of (thrown_list, caught_list).
        """
        thrown: list[str] = []
        caught: list[str] = []

        for node in self._walk_nodes(func_node):
            if node.type in {"raise_statement", "throw_statement"}:
                if exc_type := self._extract_exception_from_throw(node):
                    thrown.append(exc_type)
            if node.type in {"except_clause", "catch_clause"}:
                if exc_type := self._extract_exception_from_catch(node):
                    caught.append(exc_type)

        return thrown, caught

    def _extract_exception_from_throw(self, node: ASTNode) -> str | None:
        """
        Extracts the exception class name from a throw/raise statement.

        Args:
            node (ASTNode): The throw/raise node.

        Returns:
            str | None: The exception name.
        """
        expression = node.child_by_field_name("expression")
        if expression and expression.text:
            return self._normalize_exception_name(safe_decode_text(expression))

        for child in node.children:
            if child.is_named and child.text:
                return self._normalize_exception_name(safe_decode_text(child))
        return None

    def _extract_exception_from_catch(self, node: ASTNode) -> str | None:
        """
        Extracts the exception class name from a catch/except clause.

        Args:
            node (ASTNode): The catch/except node.

        Returns:
            str | None: The exception name.
        """
        type_node = node.child_by_field_name(cs.TS_FIELD_TYPE)
        if type_node and type_node.text:
            return self._normalize_exception_name(safe_decode_text(type_node))

        param_node = node.child_by_field_name(cs.FIELD_NAME)
        if param_node and param_node.text:
            return self._normalize_exception_name(safe_decode_text(param_node))
        return None

    def _normalize_exception_name(self, raw: str | None) -> str | None:
        """
        Normalizes an exception name (removes 'new', generics, parens).

        Args:
            raw (str | None): Raw exception string.

        Returns:
            str | None: Normalized name.
        """
        if not raw:
            return None
        stripped = raw.strip()
        if stripped.startswith("new "):
            stripped = stripped[4:]
        if "(" in stripped:
            stripped = stripped.split("(", 1)[0]
        if "{" in stripped:
            stripped = stripped.split("{", 1)[0]
        return stripped.strip() or None

    def _walk_nodes(self, node: ASTNode) -> Iterable[ASTNode]:
        """
        Iterates over all descendants of a node in depth-first order.

        Args:
            node (ASTNode): The starting node.

        Yields:
            ASTNode: Descendant nodes.
        """
        stack = [node]
        while stack:
            current = stack.pop()
            yield current
            stack.extend(reversed(current.children))

    def _module_qn_for_path(self, file_path: Path) -> str:
        """
        Generates the module qualified name from a file path.

        Args:
            file_path (Path): Path to the file.

        Returns:
            str: The module qualified name.
        """
        relative_path = file_path.relative_to(self.repo_path)
        parts = list(relative_path.with_suffix("").parts)
        if file_path.name in (cs.INIT_PY, cs.MOD_RS):
            parts = list(relative_path.parent.parts)
        return cs.SEPARATOR_DOT.join([self.project_name, *parts])
