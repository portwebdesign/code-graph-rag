from __future__ import annotations

import re
from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple

from loguru import logger
from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.core import logs as ls
from codebase_rag.data_models.types_defs import (
    ASTNode,
    FunctionRegistryTrieProtocol,
    NodeType,
    PropertyDict,
    SimpleNameLookup,
)
from codebase_rag.infrastructure.language_spec import LANGUAGE_FQN_SPECS, LanguageSpec

from ..utils.fqn_resolver import resolve_fqn_from_ast
from ..utils.path_utils import to_posix
from .cpp import utils as cpp_utils
from .lua import utils as lua_utils
from .rs import utils as rs_utils
from .utils import (
    build_lite_signature,
    extract_param_names,
    get_function_captures,
    ingest_method,
    is_method_node,
    safe_decode_text,
)

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import LanguageQueries
    from codebase_rag.services import IngestorProtocol

    from .handlers import LanguageHandler


class FunctionResolution(NamedTuple):
    """
    Result of resolving a function's identity.

    Attributes:
        qualified_name (str): Fully qualified name of the function.
        name (str): Simple name of the function.
        is_exported (bool): Whether the function is exported from its module.
    """

    qualified_name: str
    name: str
    is_exported: bool


class FunctionIngestMixin:
    """
    Mixin for processing and ingesting function definitions from AST.

    Provides methods to identifying, resolving, and registering functions across
    different languages (Python, JavaScript, TypeScript, C++, Rust, etc.).

    Attributes:
        ingestor (IngestorProtocol): Ingestor for creating nodes.
        repo_path (Path): Repository root path.
        project_name (str): Project name.
        function_registry (FunctionRegistryTrieProtocol): Registry for function storage.
        simple_name_lookup (SimpleNameLookup): Lookup table for simple names.
        module_qn_to_file_path (dict[str, Path]): Mapping from module QN to file path.
        _handler (LanguageHandler): Language-specific handler.
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
        """
        Extract docstring from a function node.

        Args:
            node (ASTNode): The function AST node.

        Returns:
            str | None: The docstring if found.
        """
        ...

    @abstractmethod
    def _extract_decorators(self, node: ASTNode) -> list[str]:
        """
        Extract decorators from a function node.

        Args:
            node (ASTNode): The function AST node.

        Returns:
            list[str]: list of decorator strings.
        """
        ...

    def _ingest_all_functions(
        self,
        root_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """
        Main entry point to discover and ingest all functions in a module.

        Args:
            root_node (Node): The root AST node of the file.
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The programming language.
            queries (dict): Dictionary containing Tree-sitter queries.
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
        Resolves the identity (name, qualified name) of a function.

        Args:
            func_node (Node): Function AST node.
            module_qn (str): Module qualified name.
            language (cs.SupportedLanguage): Language.
            lang_config (LanguageSpec): Language specifications.
            file_path (Path | None): Path to the source file.

        Returns:
            FunctionResolution | None: Resolved identity or None if failed.
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
        Attempts to resolve FQN using the unified FQN resolver strategy.

        Args:
            func_node (Node): Function node.
            language (cs.SupportedLanguage): Language.
            file_path (Path | None): File path.

        Returns:
            FunctionResolution | None: Resolution if successful.
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
        Fallback resolution strategy when unified FQN resolution fails.

        Args:
            func_node (Node): Function node.
            module_qn (str): Module qualified name.
            language (cs.SupportedLanguage): Language.
            lang_config (LanguageSpec): Language spec.

        Returns:
            FunctionResolution | None: Resolution result.
        """
        if language == cs.SupportedLanguage.CPP:
            return self._resolve_cpp_function(func_node, module_qn)
        return self._resolve_generic_function(
            func_node, module_qn, language, lang_config
        )

    def _handle_cpp_out_of_class_method(self, func_node: Node, module_qn: str) -> bool:
        """
        Handles C++ methods defined outside their class declaration.

        Args:
            func_node (Node): Function node.
            module_qn (str): Module qualified name.

        Returns:
            bool: True if handled as an out-of-class method.
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

        file_path = self.module_qn_to_file_path.get(module_qn)
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
            file_path=file_path,
            repo_path=self.repo_path,
        )

        return True

    def _resolve_cpp_function(
        self, func_node: Node, module_qn: str
    ) -> FunctionResolution | None:
        """
        Resolves a C++ function.

        Args:
            func_node (Node): Function node.
            module_qn (str): Module qualified name.

        Returns:
            FunctionResolution | None: Resolution result.
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
        Resolves a generic function (default strategy).

        Args:
            func_node (Node): Function node.
            module_qn (str): Module qualified name.
            language (cs.SupportedLanguage): Language.
            lang_config (LanguageSpec): Language spec.

        Returns:
            FunctionResolution: Resolution result.
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
        Builds the fully qualified name for a function.

        Args:
            func_node (Node): Function node.
            module_qn (str): Module qualified name.
            func_name (str): Simple function name.
            language (cs.SupportedLanguage): Language.
            lang_config (LanguageSpec): Language spec.

        Returns:
            str: Fully qualified name.
        """
        if language == cs.SupportedLanguage.RUST:
            return self._build_rust_function_qualified_name(
                func_node, module_qn, func_name
            )

        if language == cs.SupportedLanguage.KOTLIN:
            if receiver_type := self._extract_kotlin_receiver_type(func_node):
                return f"{module_qn}.{receiver_type}.{func_name}"

        nested_qn = self._build_nested_qualified_name(
            func_node, module_qn, func_name, lang_config
        )
        return nested_qn or f"{module_qn}.{func_name}"

    def _extract_kotlin_receiver_type(self, func_node: Node) -> str | None:
        """
        Extracts the receiver type for Kotlin extension functions.

        Args:
            func_node (Node): Function node.

        Returns:
            str | None: Receiver type name.
        """
        receiver_node = func_node.child_by_field_name("receiver_type")
        if not receiver_node or not receiver_node.text:
            return None
        receiver_raw = safe_decode_text(receiver_node) or ""
        if not receiver_raw:
            return None
        receiver_clean = re.sub(r"<.*?>", "", receiver_raw)
        receiver_clean = receiver_clean.replace(" ", "").replace("?", "")
        return receiver_clean

    def _register_function(
        self,
        func_node: Node,
        resolution: FunctionResolution,
        module_qn: str,
        language: cs.SupportedLanguage,
        lang_config: LanguageSpec,
    ) -> None:
        """
        Registers the function in the ingestor and registry.

        Args:
            func_node (Node): Function node.
            resolution (FunctionResolution): Resolution result.
            module_qn (str): Module qualified name.
            language (cs.SupportedLanguage): Language.
            lang_config (LanguageSpec): Language spec.
        """
        func_props = self._build_function_props(
            func_node, resolution, module_qn, language
        )
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

    def _detect_entry_point(
        self,
        func_node: Node,
        resolution: FunctionResolution,
        module_qn: str,
        language: cs.SupportedLanguage,
        decorators: list[str],
    ) -> bool:
        """
        Detects if a function is an entry point (e.g., main function, API handler).

        Args:
            func_node (Node): Function node.
            resolution (FunctionResolution): Resolution result.
            module_qn (str): Module qualified name.
            language (cs.SupportedLanguage): Language.
            decorators (list[str]): List of decorators.

        Returns:
            bool: True if it is an entry point.
        """
        name = (resolution.name or "").lower()
        module_lower = module_qn.lower()
        file_path = self.module_qn_to_file_path.get(module_qn)
        file_name = file_path.name.lower() if file_path else ""
        decorator_tokens = [token.lower() for token in decorators]

        if language == cs.SupportedLanguage.PYTHON:
            if name == "main":
                return True
            if file_name in {"__main__.py", "main.py", "app.py"} and name == "main":
                return True
            if any(
                token.startswith("@app.")
                or token.startswith("@router.")
                or token.startswith("@blueprint.")
                or token.startswith("@bp.")
                or token.startswith("@api.")
                for token in decorator_tokens
            ):
                return True
            return False

        if language in {cs.SupportedLanguage.JS, cs.SupportedLanguage.TS}:
            if name == "main":
                return True
            if any("export default" in token for token in decorator_tokens):
                return True
            if file_name in {
                "index.js",
                "index.ts",
                "main.js",
                "main.ts",
                "app.js",
                "app.ts",
            }:
                return True
            return False

        if language == cs.SupportedLanguage.JAVA:
            return name == "main"

        if language == cs.SupportedLanguage.GO:
            return name == "main"

        if language == cs.SupportedLanguage.RUBY:
            if file_path and any(part in {"bin", "script"} for part in file_path.parts):
                return True
            return "rake" in module_lower

        return False

    def _build_function_props(
        self,
        func_node: Node,
        resolution: FunctionResolution,
        module_qn: str | None = None,
        language: cs.SupportedLanguage | None = None,
    ) -> PropertyDict:
        """
        Builds the property dictionary for a function node.

        Args:
            func_node (Node): Function node.
            resolution (FunctionResolution): Resolution result.
            module_qn (str | None): Module qualified name.
            language (cs.SupportedLanguage | None): Language.

        Returns:
            PropertyDict: Function properties.
        """
        if module_qn is None:
            module_qn = cs.SEPARATOR_DOT.join(
                resolution.qualified_name.split(cs.SEPARATOR_DOT)[:-1]
            )
        if language is None:
            language = cs.SupportedLanguage.PYTHON
        decorators = self._extract_decorators(func_node)
        param_names = extract_param_names(func_node)
        signature_lite = build_lite_signature(
            resolution.name or "",
            param_names,
            None,
            language,
        )
        props: PropertyDict = {
            cs.KEY_QUALIFIED_NAME: resolution.qualified_name,
            cs.KEY_NAME: resolution.name,
            cs.KEY_DECORATORS: decorators,
            cs.KEY_START_LINE: func_node.start_point[0] + 1,
            cs.KEY_END_LINE: func_node.end_point[0] + 1,
            cs.KEY_DOCSTRING: self._get_docstring(func_node),
            cs.KEY_IS_EXPORTED: resolution.is_exported,
            cs.KEY_IS_ENTRY_POINT: self._detect_entry_point(
                func_node, resolution, module_qn, language, decorators
            ),
            cs.KEY_SIGNATURE_LITE: signature_lite,
        }
        file_path = self.module_qn_to_file_path.get(module_qn)
        if file_path:
            props[cs.KEY_PATH] = to_posix(file_path.relative_to(self.repo_path))
        return props

    def _create_function_relationships(
        self,
        func_node: Node,
        resolution: FunctionResolution,
        module_qn: str,
        language: cs.SupportedLanguage,
        lang_config: LanguageSpec,
    ) -> None:
        """
        Creates relationships for the function (DEFINES, EXPORTS).

        Args:
            func_node (Node): Function node.
            resolution (FunctionResolution): Resolution result.
            module_qn (str): Module qualified name.
            language (cs.SupportedLanguage): Language.
            lang_config (LanguageSpec): Language spec.
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
        Extracts function name from node using known patterns.

        Args:
            func_node (Node): Function node.

        Returns:
            str | None: Function name.
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
        Generates a name for an anonymous function.

        Args:
            func_node (Node): Function node.
            module_qn (str): Module qualified name.

        Returns:
            str: Generated unique name.
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
        Extracts Lua function name from assignment.

        Args:
            func_node (Node): Function node.

        Returns:
            str | None: Function name.
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
        Builds FQN for nested functions.

        Args:
            func_node (Node): Function node.
            module_qn (str): Module FQN.
            func_name (str): Function name.
            lang_config (LanguageSpec): Language config.
            skip_classes (bool): Whether to skip class ancestors.

        Returns:
            str | None: Nested FQN.
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
        Collects path parts from ancestors.

        Args:
            func_node (Node): Original function node.
            current (Node | None): Current ancestor.
            lang_config (LanguageSpec): Language config.
            skip_classes (bool): Skip classes.

        Returns:
            list[str] | None: Path parts or None.
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
        Process a single ancestor for patch extraction.

        Args:
            func_node (Node): Original node.
            current (Node): Ancestor node.
            lang_config (LanguageSpec): Language config.
            skip_classes (bool): Skip classes.

        Returns:
            str | None | Literal[False]: Name part, None (ignored), or False (abort).
        """
        if current.type in lang_config.function_node_types:
            return self._get_name_from_function_ancestor(current)

        if current.type in lang_config.class_node_types:
            return self._handle_class_ancestor(func_node, current, skip_classes)

        if current.type == cs.TS_METHOD_DEFINITION:
            return self._extract_node_name(current)

        return None

    def _get_name_from_function_ancestor(self, node: Node) -> str | None:
        """
        Get name from a function ancestor.

        Args:
            node (Node): Function node.

        Returns:
            str | None: Function name.
        """
        if name := self._extract_node_name(node):
            return name
        return self._extract_function_name(node)

    def _handle_class_ancestor(
        self, func_node: Node, class_node: Node, skip_classes: bool
    ) -> str | None | Literal[False]:
        """
        Handle class ancestor logic.

        Args:
            func_node (Node): Function node.
            class_node (Node): Class node.
            skip_classes (bool): Skip flag.

        Returns:
            str | None | Literal[False]: Class name, None, or False.
        """
        if skip_classes:
            return None
        if self._handler.is_inside_method_with_object_literals(func_node):
            return self._extract_node_name(class_node)
        return False

    def _extract_node_name(self, node: Node) -> str | None:
        """
        Extract name field from any node.

        Args:
            node (Node): Node to extract name from.

        Returns:
            str | None: Extracted name.
        """
        name_node = node.child_by_field_name(cs.FIELD_NAME)
        if name_node and name_node.text is not None:
            return safe_decode_text(name_node)
        return None

    def _format_nested_qn(
        self, module_qn: str, path_parts: list[str], func_name: str
    ) -> str:
        """
        Formats nested fully qualified name.

        Args:
            module_qn (str): Module qualified name.
            path_parts (list[str]): Path parts.
            func_name (str): Function name.

        Returns:
            str: Formatted FQN.
        """
        if path_parts:
            return f"{module_qn}.{cs.SEPARATOR_DOT.join(path_parts)}.{func_name}"
        return f"{module_qn}.{func_name}"

    def _build_rust_function_qualified_name(
        self, func_node: Node, module_qn: str, func_name: str
    ) -> str:
        """
        Builds Rust-specific qualified name.

        Args:
            func_node (Node): Function node.
            module_qn (str): Module qualified name.
            func_name (str): Function name.

        Returns:
            str: Rust FQN.
        """
        path_parts = rs_utils.build_module_path(func_node)
        if path_parts:
            return f"{module_qn}.{cs.SEPARATOR_DOT.join(path_parts)}.{func_name}"
        return f"{module_qn}.{func_name}"

    def _is_method(self, func_node: Node, lang_config: LanguageSpec) -> bool:
        """
        Checks if node is a method.

        Args:
            func_node (Node): Function node.
            lang_config (LanguageSpec): Language config.

        Returns:
            bool: True if method.
        """
        return is_method_node(func_node, lang_config)

    def _determine_function_parent(
        self, func_node: Node, module_qn: str, lang_config: LanguageSpec
    ) -> tuple[str, str]:
        """
        Determines the parent type and qualified name for a function.

        Args:
            func_node (Node): Function node.
            module_qn (str): Module qualified name.
            lang_config (LanguageSpec): Language config.

        Returns:
            tuple[str, str]: Parent label type, Parent qualified name.
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
