"""
This module provides the `FunctionIngestMixin`, a component responsible for
identifying, resolving, and ingesting function definitions from a parsed AST.

As a mixin, it's designed to be used by a larger processor class (like
`DefinitionProcessor`). It handles the logic for both top-level functions and
nested functions, determining their fully qualified names (FQNs) and creating
the corresponding nodes and relationships in the graph.

Key functionalities:
-   Querying the AST for function and method nodes.
-   Resolving the FQN of each function, using either a unified, precise resolver
    or a fallback mechanism.
-   Handling language-specific function definitions (e.g., C++ out-of-class methods,
    Lua assignment functions).
-   Building a dictionary of properties for each function node.
-   Ingesting `Function` nodes and their `DEFINES` relationships to the graph.
-   Registering the function's FQN in the central function registry.
"""

from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple

from loguru import logger
from tree_sitter import Node

from codebase_rag.data_models.types_defs import (
    ASTNode,
    FunctionRegistryTrieProtocol,
    NodeType,
    PropertyDict,
    SimpleNameLookup,
)
from codebase_rag.infrastructure.language_spec import LANGUAGE_FQN_SPECS, LanguageSpec

from ..core import constants as cs
from ..core import logs as ls
from ..utils.fqn_resolver import resolve_fqn_from_ast
from .cpp import utils as cpp_utils
from .lua import utils as lua_utils
from .rs import utils as rs_utils
from .utils import (
    get_function_captures,
    ingest_method,
    is_method_node,
    safe_decode_text,
)

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import LanguageQueries

    from ..services import IngestorProtocol
    from .handlers import LanguageHandler


class FunctionResolution(NamedTuple):
    """Holds the resolved identity of a function."""

    qualified_name: str
    name: str
    is_exported: bool


class FunctionIngestMixin:
    """
    A mixin class providing functionality to ingest functions from an AST.
    """

    ingestor: IngestorProtocol
    repo_path: Path
    project_name: str
    function_registry: FunctionRegistryTrieProtocol
    simple_name_lookup: SimpleNameLookup
    module_qn_to_file_path: dict[str, Path]
    _handler: LanguageHandler

    @abstractmethod
    def _get_docstring(self, node: ASTNode) -> str | None:
        """Abstract method to extract a docstring from a node."""
        ...

    @abstractmethod
    def _extract_decorators(self, node: ASTNode) -> list[str]:
        """Abstract method to extract decorators from a node."""
        ...

    def _ingest_all_functions(
        self,
        root_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """
        Finds and ingests all top-level and nested functions in a given AST node.

        Args:
            root_node (Node): The root node of the AST to process.
            module_qn (str): The qualified name of the module being processed.
            language (cs.SupportedLanguage): The language of the source file.
            queries (dict): A dictionary of tree-sitter queries.
        """
        result = get_function_captures(root_node, language, queries)
        if not result:
            return

        lang_config, captures = result
        file_path = self.module_qn_to_file_path.get(module_qn)

        for func_node in captures.get(cs.CAPTURE_FUNCTION, []):
            if not isinstance(func_node, Node):
                logger.warning(
                    ls.FUNC_EXPECTED_NODE.format(
                        actual_type=type(func_node), value=func_node
                    )
                )
                continue
            if self._is_method(func_node, lang_config):
                continue

            if language == cs.SupportedLanguage.CPP:
                if self._handle_cpp_out_of_class_method(func_node, module_qn):
                    continue

            resolution = self._resolve_function_identity(
                func_node, module_qn, language, lang_config, file_path
            )
            if not resolution:
                continue

            self._register_function(
                func_node, resolution, module_qn, language, lang_config
            )

    def _resolve_function_identity(
        self,
        func_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        lang_config: LanguageSpec,
        file_path: Path | None,
    ) -> FunctionResolution | None:
        """
        Resolves the identity (name, FQN, export status) of a function node.

        Args:
            func_node (Node): The function's AST node.
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The language of the code.
            lang_config (LanguageSpec): The language specification.
            file_path (Path | None): The path to the source file.

        Returns:
            FunctionResolution | None: The resolved identity, or None if it fails.
        """
        resolution = self._try_unified_fqn_resolution(func_node, language, file_path)
        if resolution:
            return resolution

        return self._fallback_function_resolution(
            func_node, module_qn, language, lang_config
        )

    def _try_unified_fqn_resolution(
        self,
        func_node: Node,
        language: cs.SupportedLanguage,
        file_path: Path | None,
    ) -> FunctionResolution | None:
        """
        Tries to resolve a function's FQN using the unified, precise FQN resolver.

        Args:
            func_node (Node): The function's AST node.
            language (cs.SupportedLanguage): The language of the code.
            file_path (Path | None): The path to the source file.

        Returns:
            FunctionResolution | None: The resolved identity, or None if it fails.
        """
        fqn_config = LANGUAGE_FQN_SPECS.get(language)
        if not fqn_config or not file_path:
            return None

        func_qn = resolve_fqn_from_ast(
            func_node, file_path, self.repo_path, self.project_name, fqn_config
        )
        if not func_qn:
            return None

        func_name = func_qn.split(cs.SEPARATOR_DOT)[-1]
        is_exported = (
            cpp_utils.is_exported(func_node)
            if language == cs.SupportedLanguage.CPP
            else False
        )
        return FunctionResolution(func_qn, func_name, is_exported)

    def _fallback_function_resolution(
        self,
        func_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        lang_config: LanguageSpec,
    ) -> FunctionResolution | None:
        """
        Uses a fallback mechanism to resolve a function's identity.

        Args:
            func_node (Node): The function's AST node.
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The language of the code.
            lang_config (LanguageSpec): The language specification.

        Returns:
            FunctionResolution | None: The resolved identity.
        """
        if language == cs.SupportedLanguage.CPP:
            return self._resolve_cpp_function(func_node, module_qn)
        return self._resolve_generic_function(
            func_node, module_qn, language, lang_config
        )

    def _handle_cpp_out_of_class_method(self, func_node: Node, module_qn: str) -> bool:
        """
        Handles the special case of C++ methods defined outside their class body.

        Args:
            func_node (Node): The function definition node.
            module_qn (str): The qualified name of the module.

        Returns:
            bool: True if the node was handled as an out-of-class method, False otherwise.
        """
        if not cpp_utils.is_out_of_class_method_definition(func_node):
            return False

        class_name = cpp_utils.extract_class_name_from_out_of_class_method(func_node)
        if not class_name:
            return False

        class_name_normalized = class_name.replace(
            cs.SEPARATOR_DOUBLE_COLON, cs.SEPARATOR_DOT
        )
        class_qn = f"{module_qn}.{class_name_normalized}"

        ingest_method(
            method_node=func_node,
            container_qn=class_qn,
            container_type=cs.NodeLabel.CLASS,
            ingestor=self.ingestor,
            function_registry=self.function_registry,
            simple_name_lookup=self.simple_name_lookup,
            get_docstring_func=self._get_docstring,
            language=cs.SupportedLanguage.CPP,
            extract_decorators_func=self._extract_decorators,
        )

        return True

    def _resolve_cpp_function(
        self, func_node: Node, module_qn: str
    ) -> FunctionResolution | None:
        """
        Resolves the identity of a C++ function.

        Args:
            func_node (Node): The function's AST node.
            module_qn (str): The qualified name of the module.

        Returns:
            FunctionResolution | None: The resolved identity.
        """
        func_name = cpp_utils.extract_function_name(func_node)
        if not func_name:
            if func_node.type == cs.TS_CPP_LAMBDA_EXPRESSION:
                func_name = f"{cs.PREFIX_LAMBDA}{func_node.start_point[0]}_{func_node.start_point[1]}"
            else:
                return None

        func_qn = cpp_utils.build_qualified_name(func_node, module_qn, func_name)
        is_exported = cpp_utils.is_exported(func_node)
        return FunctionResolution(func_qn, func_name, is_exported)

    def _resolve_generic_function(
        self,
        func_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        lang_config: LanguageSpec,
    ) -> FunctionResolution:
        """
        A generic fallback for resolving a function's identity.

        Args:
            func_node (Node): The function's AST node.
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The language of the code.
            lang_config (LanguageSpec): The language specification.

        Returns:
            FunctionResolution: The resolved identity.
        """
        func_name = self._extract_function_name(func_node)

        if (
            not func_name
            and language == cs.SupportedLanguage.LUA
            and func_node.type == cs.TS_LUA_FUNCTION_DEFINITION
        ):
            func_name = self._extract_lua_assignment_function_name(func_node)

        if not func_name:
            func_name = self._generate_anonymous_function_name(func_node, module_qn)

        func_qn = self._build_function_qn(
            func_node, module_qn, func_name, language, lang_config
        )
        return FunctionResolution(func_qn, func_name, is_exported=False)

    def _build_function_qn(
        self,
        func_node: Node,
        module_qn: str,
        func_name: str,
        language: cs.SupportedLanguage,
        lang_config: LanguageSpec,
    ) -> str:
        """
        Builds the FQN for a function, handling nested cases.

        Args:
            func_node (Node): The function's AST node.
            module_qn (str): The qualified name of the module.
            func_name (str): The simple name of the function.
            language (cs.SupportedLanguage): The language of the code.
            lang_config (LanguageSpec): The language specification.

        Returns:
            str: The constructed FQN.
        """
        if language == cs.SupportedLanguage.RUST:
            return self._build_rust_function_qualified_name(
                func_node, module_qn, func_name
            )

        nested_qn = self._build_nested_qualified_name(
            func_node, module_qn, func_name, lang_config
        )
        return nested_qn or f"{module_qn}.{func_name}"

    def _register_function(
        self,
        func_node: Node,
        resolution: FunctionResolution,
        module_qn: str,
        language: cs.SupportedLanguage,
        lang_config: LanguageSpec,
    ) -> None:
        """
        Registers a function and its relationships in the graph.

        Args:
            func_node (Node): The function's AST node.
            resolution (FunctionResolution): The resolved identity of the function.
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The language of the code.
            lang_config (LanguageSpec): The language specification.
        """
        func_props = self._build_function_props(func_node, resolution)
        logger.info(
            ls.FUNC_FOUND.format(name=resolution.name, qn=resolution.qualified_name)
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.FUNCTION, func_props)

        self.function_registry[resolution.qualified_name] = NodeType.FUNCTION
        if resolution.name:
            self.simple_name_lookup[resolution.name].add(resolution.qualified_name)

        self._create_function_relationships(
            func_node, resolution, module_qn, language, lang_config
        )

    def _build_function_props(
        self, func_node: Node, resolution: FunctionResolution
    ) -> PropertyDict:
        """
        Builds a dictionary of properties for a function node.

        Args:
            func_node (Node): The function's AST node.
            resolution (FunctionResolution): The resolved identity of the function.

        Returns:
            PropertyDict: A dictionary of properties for the graph node.
        """
        return {
            cs.KEY_QUALIFIED_NAME: resolution.qualified_name,
            cs.KEY_NAME: resolution.name,
            cs.KEY_DECORATORS: self._extract_decorators(func_node),
            cs.KEY_START_LINE: func_node.start_point[0] + 1,
            cs.KEY_END_LINE: func_node.end_point[0] + 1,
            cs.KEY_DOCSTRING: self._get_docstring(func_node),
            cs.KEY_IS_EXPORTED: resolution.is_exported,
        }

    def _create_function_relationships(
        self,
        func_node: Node,
        resolution: FunctionResolution,
        module_qn: str,
        language: cs.SupportedLanguage,
        lang_config: LanguageSpec,
    ) -> None:
        """
        Creates the necessary relationships for a function node.

        Args:
            func_node (Node): The function's AST node.
            resolution (FunctionResolution): The resolved identity of the function.
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The language of the code.
            lang_config (LanguageSpec): The language specification.
        """
        parent_type, parent_qn = self._determine_function_parent(
            func_node, module_qn, lang_config
        )
        self.ingestor.ensure_relationship_batch(
            (parent_type, cs.KEY_QUALIFIED_NAME, parent_qn),
            cs.RelationshipType.DEFINES,
            (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, resolution.qualified_name),
        )

        if resolution.is_exported and language == cs.SupportedLanguage.CPP:
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                cs.RelationshipType.EXPORTS,
                (
                    cs.NodeLabel.FUNCTION,
                    cs.KEY_QUALIFIED_NAME,
                    resolution.qualified_name,
                ),
            )

    def _extract_function_name(self, func_node: Node) -> str | None:
        """
        Extracts the simple name of a function from its AST node.

        Args:
            func_node (Node): The function's AST node.

        Returns:
            str | None: The simple name of the function.
        """
        name_node = func_node.child_by_field_name(cs.FIELD_NAME)
        if name_node and name_node.text:
            return safe_decode_text(name_node)

        if func_node.type == cs.TS_ARROW_FUNCTION:
            current = func_node.parent
            while current:
                if current.type == cs.TS_VARIABLE_DECLARATOR:
                    for child in current.children:
                        if child.type == cs.TS_IDENTIFIER and child.text:
                            return safe_decode_text(child)
                current = current.parent

        return None

    def _generate_anonymous_function_name(self, func_node: Node, module_qn: str) -> str:
        """
        Generates a unique name for an anonymous function or lambda.

        Args:
            func_node (Node): The anonymous function's AST node.
            module_qn (str): The qualified name of the module.

        Returns:
            str: A generated unique name.
        """
        parent = func_node.parent
        if parent and parent.type == cs.TS_PARENTHESIZED_EXPRESSION:
            grandparent = parent.parent
            if (
                grandparent
                and grandparent.type == cs.TS_CALL_EXPRESSION
                and grandparent.child_by_field_name(cs.FIELD_FUNCTION) == parent
            ):
                func_type = (
                    cs.PREFIX_ARROW
                    if func_node.type == cs.TS_ARROW_FUNCTION
                    else cs.PREFIX_FUNC
                )
                return f"{cs.PREFIX_IIFE}{func_type}_{func_node.start_point[0]}_{func_node.start_point[1]}"

        if (
            parent
            and parent.type == cs.TS_CALL_EXPRESSION
            and parent.child_by_field_name(cs.FIELD_FUNCTION) == func_node
        ):
            return f"{cs.PREFIX_IIFE_DIRECT}{func_node.start_point[0]}_{func_node.start_point[1]}"

        return f"{cs.PREFIX_ANONYMOUS}{func_node.start_point[0]}_{func_node.start_point[1]}"

    def _extract_lua_assignment_function_name(self, func_node: Node) -> str | None:
        """
        Extracts the name of a Lua function defined via an assignment.

        Args:
            func_node (Node): The function definition node.

        Returns:
            str | None: The name of the function.
        """
        return lua_utils.extract_assigned_name(
            func_node,
            accepted_var_types=(cs.TS_DOT_INDEX_EXPRESSION, cs.TS_IDENTIFIER),
        )

    def _build_nested_qualified_name(
        self,
        func_node: Node,
        module_qn: str,
        func_name: str,
        lang_config: LanguageSpec,
        skip_classes: bool = False,
    ) -> str | None:
        """
        Builds the FQN for a nested function by traversing up the AST.

        Args:
            func_node (Node): The nested function's AST node.
            module_qn (str): The qualified name of the module.
            func_name (str): The simple name of the function.
            lang_config (LanguageSpec): The language specification.
            skip_classes (bool): Whether to skip class scopes in the path.

        Returns:
            str | None: The constructed FQN.
        """
        current = func_node.parent
        if not isinstance(current, Node):
            logger.warning(
                ls.CALL_UNEXPECTED_PARENT.format(
                    node=func_node, parent_type=type(current)
                )
            )
            return None

        path_parts = self._collect_ancestor_path_parts(
            func_node, current, lang_config, skip_classes
        )
        if path_parts is None:
            return None

        return self._format_nested_qn(module_qn, path_parts, func_name)

    def _collect_ancestor_path_parts(
        self,
        func_node: Node,
        current: Node | None,
        lang_config: LanguageSpec,
        skip_classes: bool,
    ) -> list[str] | None:
        """
        Collects the names of ancestor scopes to build a nested FQN.

        Args:
            func_node (Node): The starting function node.
            current (Node | None): The current ancestor node being processed.
            lang_config (LanguageSpec): The language specification.
            skip_classes (bool): Whether to skip class scopes.

        Returns:
            list[str] | None: A list of path parts, or None if resolution fails.
        """
        path_parts: list[str] = []

        while current and current.type not in lang_config.module_node_types:
            result = self._process_ancestor_for_path(
                func_node, current, lang_config, skip_classes
            )
            if result is False:
                return None
            if result is not None:
                path_parts.append(result)
            current = current.parent

        path_parts.reverse()
        return path_parts

    def _process_ancestor_for_path(
        self,
        func_node: Node,
        current: Node,
        lang_config: LanguageSpec,
        skip_classes: bool,
    ) -> str | None | Literal[False]:
        """
        Processes a single ancestor node to extract its name for the FQN path.

        Args:
            func_node (Node): The original function node.
            current (Node): The ancestor node to process.
            lang_config (LanguageSpec): The language specification.
            skip_classes (bool): Whether to skip class scopes.

        Returns:
            str | None | Literal[False]: The name part, None to skip, or False to fail.
        """
        if current.type in lang_config.function_node_types:
            return self._get_name_from_function_ancestor(current)

        if current.type in lang_config.class_node_types:
            return self._handle_class_ancestor(func_node, current, skip_classes)

        if current.type == cs.TS_METHOD_DEFINITION:
            return self._extract_node_name(current)

        return None

    def _get_name_from_function_ancestor(self, node: Node) -> str | None:
        """Extracts a name from an ancestor that is a function."""
        if name := self._extract_node_name(node):
            return name
        return self._extract_function_name(node)

    def _handle_class_ancestor(
        self, func_node: Node, class_node: Node, skip_classes: bool
    ) -> str | None | Literal[False]:
        """Handles an ancestor node that is a class."""
        if skip_classes:
            return None
        if self._handler.is_inside_method_with_object_literals(func_node):
            return self._extract_node_name(class_node)
        return False

    def _extract_node_name(self, node: Node) -> str | None:
        """Extracts a name from a node using the 'name' field."""
        name_node = node.child_by_field_name(cs.FIELD_NAME)
        if name_node and name_node.text is not None:
            return safe_decode_text(name_node)
        return None

    def _format_nested_qn(
        self, module_qn: str, path_parts: list[str], func_name: str
    ) -> str:
        """Formats the final nested FQN string."""
        if path_parts:
            return f"{module_qn}.{cs.SEPARATOR_DOT.join(path_parts)}.{func_name}"
        return f"{module_qn}.{func_name}"

    def _build_rust_function_qualified_name(
        self, func_node: Node, module_qn: str, func_name: str
    ) -> str:
        """
        Builds the FQN for a Rust function, considering its module path.

        Args:
            func_node (Node): The function's AST node.
            module_qn (str): The qualified name of the file/module.
            func_name (str): The simple name of the function.

        Returns:
            str: The constructed FQN.
        """
        path_parts = rs_utils.build_module_path(func_node)
        if path_parts:
            return f"{module_qn}.{cs.SEPARATOR_DOT.join(path_parts)}.{func_name}"
        return f"{module_qn}.{func_name}"

    def _is_method(self, func_node: Node, lang_config: LanguageSpec) -> bool:
        """
        Checks if a function node is a method (i.e., inside a class).

        Args:
            func_node (Node): The function node to check.
            lang_config (LanguageSpec): The language specification.

        Returns:
            bool: True if the node is a method, False otherwise.
        """
        return is_method_node(func_node, lang_config)

    def _determine_function_parent(
        self, func_node: Node, module_qn: str, lang_config: LanguageSpec
    ) -> tuple[str, str]:
        """
        Determines the parent of a function (either the module or another function).

        Args:
            func_node (Node): The function's AST node.
            module_qn (str): The qualified name of the module.
            lang_config (LanguageSpec): The language specification.

        Returns:
            tuple[str, str]: A tuple of the parent's label and qualified name.
        """
        current = func_node.parent
        if not isinstance(current, Node):
            return cs.NodeLabel.MODULE, module_qn

        while current and current.type not in lang_config.module_node_types:
            if current.type in lang_config.function_node_types:
                if name_node := current.child_by_field_name(cs.FIELD_NAME):
                    parent_text = name_node.text
                    if parent_text is None:
                        continue
                    if parent_func_name := safe_decode_text(name_node):
                        if parent_func_qn := self._build_nested_qualified_name(
                            current, module_qn, parent_func_name, lang_config
                        ):
                            return cs.NodeLabel.FUNCTION, parent_func_qn
                break

            current = current.parent

        return cs.NodeLabel.MODULE, module_qn
