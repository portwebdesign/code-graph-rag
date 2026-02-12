"""
This module provides the `FunctionIngestMixin`, a component responsible for the
discovery, processing, and ingestion of function and method definitions from a file's
Abstract Syntax Tree (AST).

It works as a mixin to be used by language-specific handlers. It contains the generic
logic to traverse an AST, identify function nodes using tree-sitter queries, resolve
their fully qualified names (FQNs), extract relevant properties (like docstrings,
decorators, and signatures), and ingest them into the graph database as `Function`
or `Method` nodes. It also handles establishing `DEFINES` relationships from the
parent scope (e.g., Module, Class, or another Function) to the newly created node.
"""

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
from codebase_rag.parsers.core.utils import (
    build_lite_signature,
    extract_param_names,
    get_function_captures,
    infer_visibility,
    ingest_method,
    is_method_node,
    normalize_decorators,
    safe_decode_text,
)
from codebase_rag.parsers.languages.cpp import utils as cpp_utils
from codebase_rag.parsers.languages.lua import utils as lua_utils
from codebase_rag.parsers.languages.rs import utils as rs_utils
from codebase_rag.utils.fqn_resolver import resolve_fqn_from_ast
from codebase_rag.utils.path_utils import is_test_path, to_posix

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import LanguageQueries
    from codebase_rag.parsers.handlers import LanguageHandler
    from codebase_rag.services import IngestorProtocol


class FunctionResolution(NamedTuple):
    """
    Represents the resolved identity of a function or method.

    This structure holds the essential identifiers for a function after it has been
    processed, including its unique name within the codebase and its simple name.

    Attributes:
        qualified_name (str): The fully qualified name (FQN) of the function,
                              providing a unique identifier across the project.
        name (str): The simple, short name of the function as it appears in the code.
        is_exported (bool): A flag indicating whether the function is exported from
                            its module, making it accessible to other modules.
    """

    qualified_name: str
    name: str
    is_exported: bool


class FunctionIngestMixin:
    """
    A mixin for processing and ingesting function definitions from an AST.

    This class provides the core logic for identifying, resolving, and registering
    functions and methods from source code. It is designed to be mixed into a
    language-specific handler that provides concrete implementations for
    language-dependent details like docstring extraction.

    The mixin handles:
    - Querying the AST for function/method nodes.
    - Resolving the fully qualified name (FQN) for each function.
    - Handling language-specific cases (e.g., C++ out-of-class definitions, Rust modules).
    - Extracting properties like decorators, signatures, and visibility.
    - Ingesting the final `Function` node into the database.
    - Registering the function in a central registry for cross-file resolution.
    """

    ingestor: IngestorProtocol
    repo_path: Path
    project_name: str
    function_registry: FunctionRegistryTrieProtocol
    simple_name_lookup: SimpleNameLookup
    module_qn_to_file_path: dict[str, Path]
    module_qn_to_file_hash: dict[str, str]
    _handler: LanguageHandler

    @abstractmethod
    def _get_docstring(self, node: ASTNode) -> str | None:
        """
        Abstract method to extract a docstring from a function/method node.

        Each language handler must implement this to provide language-specific
        docstring parsing logic.

        Args:
            node (ASTNode): The function or method's AST node.

        Returns:
            The extracted docstring as a string, or None if not found.
        """
        ...

    @abstractmethod
    def _extract_decorators(self, node: ASTNode) -> list[str]:
        """
        Abstract method to extract decorators from a function/method node.

        Each language handler must implement this to handle its specific syntax
        for decorators, annotations, or attributes.

        Args:
            node (ASTNode): The function or method's AST node.

        Returns:
            A list of decorator names as strings.
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
        Finds and ingests all functions within a given module's AST.

        This is the main entry point for the mixin. It uses a tree-sitter query
        to find all function captures, filters out methods (which are handled
        separately), and processes each function.

        Args:
            root_node (Node): The root AST node of the file.
            module_qn (str): The qualified name of the module being processed.
            language (cs.SupportedLanguage): The programming language of the file.
            queries (dict): A dictionary containing the tree-sitter queries for the language.
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

        It first attempts to use a unified, language-agnostic FQN resolution
        strategy. If that fails, it falls back to a language-specific strategy.

        Args:
            func_node (Node): The AST node of the function.
            module_qn (str): The qualified name of the containing module.
            language (cs.SupportedLanguage): The programming language.
            lang_config (LanguageSpec): The language-specific configuration.
            file_path (Path | None): The path to the source file.

        Returns:
            A `FunctionResolution` object if successful, otherwise None.
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
        Attempts to resolve the FQN using the unified `resolve_fqn_from_ast` utility.

        This is the preferred method as it centralizes FQN logic.

        Args:
            func_node (Node): The function's AST node.
            language (cs.SupportedLanguage): The programming language.
            file_path (Path | None): The path to the source file.

        Returns:
            A `FunctionResolution` object if the FQN was resolved, otherwise None.
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
        Provides a fallback FQN resolution strategy for languages or cases not
        covered by the unified resolver.

        Args:
            func_node (Node): The function's AST node.
            module_qn (str): The qualified name of the containing module.
            language (cs.SupportedLanguage): The programming language.
            lang_config (LanguageSpec): The language-specific configuration.

        Returns:
            A `FunctionResolution` object or None.
        """
        if language == cs.SupportedLanguage.CPP:
            return self._resolve_cpp_function(func_node, module_qn)
        return self._resolve_generic_function(
            func_node, module_qn, language, lang_config
        )

    def _handle_cpp_out_of_class_method(self, func_node: Node, module_qn: str) -> bool:
        """
        Handles the special case of C++ methods defined outside their class declaration.

        It identifies such methods, determines their class, and ingests them as
        `Method` nodes attached to the correct `Class`.

        Args:
            func_node (Node): The function definition AST node.
            module_qn (str): The qualified name of the containing module.

        Returns:
            True if the node was handled as an out-of-class method, False otherwise.
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
        file_hash = self.module_qn_to_file_hash.get(module_qn)
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
            module_qn=module_qn,
            file_hash=file_hash,
            file_path=file_path,
            repo_path=self.repo_path,
        )

        return True

    def _resolve_cpp_function(
        self, func_node: Node, module_qn: str
    ) -> FunctionResolution | None:
        """
        Resolves the identity of a C++ function, handling namespaces and export status.

        Args:
            func_node (Node): The function's AST node.
            module_qn (str): The qualified name of the containing module.

        Returns:
            A `FunctionResolution` object, or None if the name cannot be determined.
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
        A generic function resolution strategy for most languages.

        It extracts the function name, generates one for anonymous functions, and
        builds the FQN based on the module and any nested structure.

        Args:
            func_node (Node): The function's AST node.
            module_qn (str): The qualified name of the containing module.
            language (cs.SupportedLanguage): The programming language.
            lang_config (LanguageSpec): The language-specific configuration.

        Returns:
            A `FunctionResolution` object.
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
        Builds the fully qualified name (FQN) for a function.

        This method accounts for nested functions, Rust module paths, and Kotlin
        extension functions to construct an accurate FQN.

        Args:
            func_node (Node): The function's AST node.
            module_qn (str): The qualified name of the containing module.
            func_name (str): The simple name of the function.
            language (cs.SupportedLanguage): The programming language.
            lang_config (LanguageSpec): The language-specific configuration.

        Returns:
            The constructed fully qualified name as a string.
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
        Extracts the receiver type for a Kotlin extension function.

        For example, in `fun String.myExtension()`, this would extract "String".

        Args:
            func_node (Node): The function's AST node.

        Returns:
            The receiver type as a string, or None if it's not an extension function.
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
        Registers the function with the ingestor and internal registries.

        This method finalizes the function processing by:
        1. Building the property dictionary for the function node.
        2. Ingesting the node into the graph database.
        3. Adding the function to the `function_registry` for call resolution.
        4. Creating necessary relationships like `DEFINES` and `EXPORTS`.

        Args:
            func_node (Node): The function's AST node.
            resolution (FunctionResolution): The resolved identity of the function.
            module_qn (str): The qualified name of the containing module.
            language (cs.SupportedLanguage): The programming language.
            lang_config (LanguageSpec): The language-specific configuration.
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
        Detects if a function is a likely entry point for execution.

        This uses heuristics based on function name (e.g., `main`), file name
        (e.g., `main.py`), and decorators (e.g., `@app.route`).

        Args:
            func_node (Node): The function's AST node.
            resolution (FunctionResolution): The resolved identity of the function.
            module_qn (str): The qualified name of the containing module.
            language (cs.SupportedLanguage): The programming language.
            decorators (list[str]): A list of decorators on the function.

        Returns:
            True if the function is determined to be an entry point, False otherwise.
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
        Builds the dictionary of properties for a function node to be stored in the graph.

        Args:
            func_node (Node): The function's AST node.
            resolution (FunctionResolution): The resolved identity of the function.
            module_qn (str | None): The qualified name of the containing module.
            language (cs.SupportedLanguage | None): The programming language.

        Returns:
            A dictionary of properties for the function node.
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
        namespace = (
            module_qn.rsplit(cs.SEPARATOR_DOT, 1)[0]
            if cs.SEPARATOR_DOT in module_qn
            else None
        )
        props: PropertyDict = {
            cs.KEY_QUALIFIED_NAME: resolution.qualified_name,
            cs.KEY_NAME: resolution.name,
            cs.KEY_DECORATORS: decorators,
            cs.KEY_DECORATORS_NORM: normalize_decorators(decorators),
            cs.KEY_START_LINE: func_node.start_point[0] + 1,
            cs.KEY_END_LINE: func_node.end_point[0] + 1,
            cs.KEY_DOCSTRING: self._get_docstring(func_node),
            cs.KEY_IS_EXPORTED: resolution.is_exported,
            cs.KEY_IS_ENTRY_POINT: self._detect_entry_point(
                func_node, resolution, module_qn, language, decorators
            ),
            cs.KEY_SIGNATURE_LITE: signature_lite,
            cs.KEY_SIGNATURE: signature_lite,
            cs.KEY_LANGUAGE: language.value,
            cs.KEY_MODULE_QN: module_qn,
            cs.KEY_SYMBOL_KIND: cs.NodeLabel.FUNCTION.value.lower(),
            cs.KEY_PARENT_QN: module_qn,
        }
        if namespace:
            props[cs.KEY_NAMESPACE] = namespace
            props[cs.KEY_PACKAGE] = namespace
        if visibility := infer_visibility(resolution.name, language):
            props[cs.KEY_VISIBILITY] = visibility
        file_path = self.module_qn_to_file_path.get(module_qn)
        if file_path:
            relative_path = to_posix(file_path.relative_to(self.repo_path))
            props[cs.KEY_PATH] = relative_path
            props[cs.KEY_REPO_REL_PATH] = relative_path
            props[cs.KEY_ABS_PATH] = file_path.resolve().as_posix()
            props[cs.KEY_IS_TEST] = is_test_path(file_path.relative_to(self.repo_path))
            if file_hash := self.module_qn_to_file_hash.get(module_qn):
                props[cs.KEY_FILE_HASH] = file_hash
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
        Creates relationships for the function, such as `DEFINES` and `EXPORTS`.

        Args:
            func_node (Node): The function's AST node.
            resolution (FunctionResolution): The resolved identity of the function.
            module_qn (str): The qualified name of the containing module.
            language (cs.SupportedLanguage): The programming language.
            lang_config (LanguageSpec): The language-specific configuration.
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

        Handles common patterns like named functions and arrow functions assigned to variables.

        Args:
            func_node (Node): The function's AST node.

        Returns:
            The extracted function name, or None if it's anonymous or can't be found.
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
        Generates a unique, deterministic name for an anonymous function.

        The name is based on the function's start position in the file to ensure
        it's unique and consistent across runs. It also identifies IIFEs.

        Args:
            func_node (Node): The anonymous function's AST node.
            module_qn (str): The qualified name of the containing module.

        Returns:
            A generated unique name for the function.
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
        Extracts a Lua function's name when it's defined as part of a table assignment.

        For example, `my_table.my_func = function() ... end`.

        Args:
            func_node (Node): The function definition node.

        Returns:
            The assigned name of the function (e.g., "my_table.my_func"), or None.
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
        Builds the FQN for a function that is nested inside another function.

        It traverses up the AST to find the names of all enclosing functions.

        Args:
            func_node (Node): The nested function's AST node.
            module_qn (str): The qualified name of the containing module.
            func_name (str): The simple name of the nested function.
            lang_config (LanguageSpec): The language configuration.
            skip_classes (bool): Whether to skip class ancestors in the path.

        Returns:
            The constructed nested FQN, or None if it's not a valid nested function.
        """
        if lang_config.language in {
            cs.SupportedLanguage.JSON,
            cs.SupportedLanguage.YAML,
        }:
            return None
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
        Recursively collects the names of ancestor functions to build a nested path.

        Args:
            func_node (Node): The original function node.
            current (Node | None): The current ancestor node being inspected.
            lang_config (LanguageSpec): The language configuration.
            skip_classes (bool): If True, ancestors that are classes are ignored.

        Returns:
            A list of name parts from the ancestors, or None to abort.
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
        Processes a single ancestor node to extract its name for a nested FQN.

        Args:
            func_node (Node): The original function node.
            current (Node): The ancestor node to process.
            lang_config (LanguageSpec): The language configuration.
            skip_classes (bool): If True, class ancestors are ignored.

        Returns:
            - A string name part if the ancestor is a valid part of the path.
            - None if the ancestor should be ignored.
            - False if the nesting is invalid (e.g., nested in a class) and should be aborted.
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
        Gets the name from an ancestor that is a function.

        Args:
            node (Node): The ancestor function node.

        Returns:
            The name of the function, or None.
        """
        if name := self._extract_node_name(node):
            return name
        return self._extract_function_name(node)

    def _handle_class_ancestor(
        self, func_node: Node, class_node: Node, skip_classes: bool
    ) -> str | None | Literal[False]:
        """
        Handles the logic when an ancestor is a class.

        Args:
            func_node (Node): The original function node.
            class_node (Node): The class ancestor node.
            skip_classes (bool): If True, class ancestors are ignored.

        Returns:
            - The class name if it's part of a valid structure.
            - None if the class should be skipped.
            - False to abort FQN construction.
        """
        if skip_classes:
            return None
        if self._handler.is_inside_method_with_object_literals(func_node):
            return self._extract_node_name(class_node)
        return False

    def _extract_node_name(self, node: Node) -> str | None:
        """
        A simple utility to extract the text from a node's "name" field.

        Args:
            node (Node): The AST node.

        Returns:
            The decoded name, or None.
        """
        name_node = node.child_by_field_name(cs.FIELD_NAME)
        if name_node and name_node.text is not None:
            return safe_decode_text(name_node)
        return None

    def _format_nested_qn(
        self, module_qn: str, path_parts: list[str], func_name: str
    ) -> str:
        """
        Formats the final nested FQN from its constituent parts.

        Args:
            module_qn (str): The base module FQN.
            path_parts (list[str]): The list of ancestor names.
            func_name (str): The simple name of the function.

        Returns:
            The complete, dot-separated FQN.
        """
        if path_parts:
            return f"{module_qn}.{cs.SEPARATOR_DOT.join(path_parts)}.{func_name}"
        return f"{module_qn}.{func_name}"

    def _build_rust_function_qualified_name(
        self, func_node: Node, module_qn: str, func_name: str
    ) -> str:
        """
        Builds a Rust-specific FQN, accounting for `mod` blocks.

        Args:
            func_node (Node): The function's AST node.
            module_qn (str): The qualified name of the file-level module.
            func_name (str): The simple name of the function.

        Returns:
            The complete Rust FQN.
        """
        path_parts = rs_utils.build_module_path(func_node)
        if path_parts:
            return f"{module_qn}.{cs.SEPARATOR_DOT.join(path_parts)}.{func_name}"
        return f"{module_qn}.{func_name}"

    def _is_method(self, func_node: Node, lang_config: LanguageSpec) -> bool:
        """
        Checks if a function node is a method (i.e., defined within a class context).

        Args:
            func_node (Node): The function node to check.
            lang_config (LanguageSpec): The language configuration.

        Returns:
            True if the node is a method, False otherwise.
        """
        return is_method_node(func_node, lang_config)

    def _determine_function_parent(
        self, func_node: Node, module_qn: str, lang_config: LanguageSpec
    ) -> tuple[str, str]:
        """
        Determines the direct parent of a function for creating the `DEFINES` relationship.

        The parent can be a Module, or another Function if it's a nested function.

        Args:
            func_node (Node): The function's AST node.
            module_qn (str): The qualified name of the module.
            lang_config (LanguageSpec): The language configuration.

        Returns:
            A tuple containing the parent's label (e.g., 'Module') and its qualified name.
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
