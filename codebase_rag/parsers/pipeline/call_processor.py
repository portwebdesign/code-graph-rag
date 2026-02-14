"""
This module defines the CallProcessor, a critical component in the parsing pipeline
responsible for identifying, resolving, and ingesting function and method calls.

The CallProcessor traverses the Abstract Syntax Tree (AST) of each source file,
finds call expressions, and uses the CallResolver to determine the fully qualified
name (QN) of the callee. It handles calls within functions, methods, and at the
module level, creating `CALLS` relationships in the graph database to build the
code's call graph.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import cast

from loguru import logger
from tree_sitter import Node, QueryCursor

from codebase_rag.core import constants as cs
from codebase_rag.core import logs as ls
from codebase_rag.data_models.types_defs import (
    FunctionRegistryTrieProtocol,
    LanguageQueries,
    PropertyDict,
)
from codebase_rag.infrastructure.language_spec import LanguageSpec
from codebase_rag.parsers.core.utils import (
    get_function_captures,
    is_method_node,
    normalize_query_captures,
    safe_decode_text,
)
from codebase_rag.parsers.type_inference import TypeInferenceEngine
from codebase_rag.services.protocols import IngestorProtocol

from ..languages.cpp import utils as cpp_utils
from .call_resolver import CallResolver
from .dynamic_call_resolver import DynamicCallResolver
from .import_processor import ImportProcessor


class CallProcessor:
    """
    Processes function and method calls within source code to build the call graph.

    This class orchestrates the discovery and resolution of call expressions. It uses
    tree-sitter queries to find call sites in the AST, extracts the call name, and
    then employs a `CallResolver` to determine the target function or method's fully
    qualified name. It handles various contexts, including regular functions, class
    methods, and module-level calls. Resolved calls are then ingested into the graph
    database as `CALLS` relationships.

    Attributes:
        ingestor (IngestorProtocol): The service for writing data to the graph.
        repo_path (Path): The root path of the repository being parsed.
        project_name (str): The name of the project.
    """

    def __init__(
        self,
        ingestor: IngestorProtocol,
        repo_path: Path,
        project_name: str,
        function_registry: FunctionRegistryTrieProtocol,
        import_processor: ImportProcessor,
        class_inheritance: dict[str, list[str]],
        type_inference: TypeInferenceEngine | None = None,
    ) -> None:
        """
        Initializes the CallProcessor.

        Args:
            ingestor (IngestorProtocol): The service for writing data to the graph.
            repo_path (Path): The root path of the repository.
            project_name (str): The name of the project.
            function_registry (FunctionRegistryTrieProtocol): A trie containing all found functions/classes.
            import_processor (ImportProcessor): The processor that handled import statements.
            class_inheritance (dict[str, list[str]]): A map of class inheritance relationships.
            type_inference (TypeInferenceEngine | None): The engine for inferring variable types.
        """
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name

        self._resolver = CallResolver(
            function_registry=function_registry,
            import_processor=import_processor,
            class_inheritance=class_inheritance,
            type_inference=type_inference,
        )
        self._dynamic_resolver = DynamicCallResolver(function_registry)
        env_heuristic = os.getenv("CODEGRAPH_HEURISTIC_CALLS", "").lower()
        self._heuristic_calls_enabled = env_heuristic not in {"0", "false", "no"}
        framework_meta_enabled = os.getenv(
            "CODEGRAPH_FRAMEWORK_METADATA", ""
        ).lower() in {
            "1",
            "true",
            "yes",
        }
        self._file_level_calls_enabled = (
            os.getenv("CODEGRAPH_FILE_LEVEL_CALLS", "").lower() in {"1", "true", "yes"}
            or framework_meta_enabled
        )
        self._placeholder_nodes_enabled = (
            os.getenv("CODEGRAPH_PLACEHOLDER_NODES", "").lower() in {"1", "true", "yes"}
            or framework_meta_enabled
        )

    def _get_node_name(self, node: Node, field: str = cs.FIELD_NAME) -> str | None:
        """
        Extracts the text of a named child field from a tree-sitter node.

        Args:
            node (Node): The parent tree-sitter node.
            field (str): The name of the field to extract (e.g., "name").

        Returns:
            The decoded text content of the child node, or None if not found.
        """
        name_node = node.child_by_field_name(field)
        if not name_node:
            return None
        return safe_decode_text(name_node)

    @staticmethod
    def _build_call_relationship_props(
        language: cs.SupportedLanguage,
        call_node: Node,
        *,
        is_dynamic: bool,
        confidence: float,
        relation_type: str,
        extra: PropertyDict | None = None,
    ) -> PropertyDict:
        props: PropertyDict = {
            "callsite_count": 1,
            "line": int(call_node.start_point[0]) + 1,
            "column": int(call_node.start_point[1]),
            "is_dynamic": bool(is_dynamic),
            "confidence": float(confidence),
            "source_parser": f"tree-sitter-{language.value}",
            "relation_type": relation_type,
        }
        run_id = os.getenv("CODEGRAPH_ANALYSIS_RUN_ID", "").strip()
        if run_id:
            props["analysis_run_id"] = run_id
            props["last_seen_run"] = run_id
        if extra:
            props.update(extra)
        return props

    def process_calls_in_file(
        self,
        file_path: Path,
        root_node: Node,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """
        Processes all function and method calls within a single source file.

        This is the main entry point for processing a file. It orchestrates the
        traversal of the AST to find calls within functions, classes, and at the
        module level.

        Args:
            file_path (Path): The absolute path to the source file.
            root_node (Node): The root node of the file's AST.
            language (cs.SupportedLanguage): The programming language of the file.
            queries (dict[cs.SupportedLanguage, LanguageQueries]): Language-specific queries.
        """
        if language in {cs.SupportedLanguage.JSON, cs.SupportedLanguage.YAML}:
            return
        relative_path = file_path.relative_to(self.repo_path)
        logger.debug(ls.CALL_PROCESSING_FILE.format(path=relative_path))

        try:
            source_bytes = file_path.read_bytes()
            source_text = source_bytes.decode(cs.ENCODING_UTF8, errors="ignore")
            module_qn = cs.SEPARATOR_DOT.join(
                [self.project_name] + list(relative_path.with_suffix("").parts)
            )
            if file_path.name in (cs.INIT_PY, cs.MOD_RS):
                module_qn = cs.SEPARATOR_DOT.join(
                    [self.project_name] + list(relative_path.parent.parts)
                )

            self._process_calls_in_functions(
                root_node,
                module_qn,
                language,
                queries,
                source_bytes,
                source_text,
            )
            self._process_calls_in_classes(
                root_node,
                module_qn,
                language,
                queries,
                source_bytes,
                source_text,
            )
            self._process_module_level_calls(
                root_node,
                module_qn,
                language,
                queries,
                source_bytes,
                source_text,
            )
            self._process_file_level_calls(
                file_path,
                root_node,
                module_qn,
                language,
                queries,
                source_bytes,
                source_text,
            )

        except Exception as e:
            logger.error(ls.CALL_PROCESSING_FAILED.format(path=file_path, error=e))

    def _process_calls_in_functions(
        self,
        root_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        source_bytes: bytes | None,
        source_text: str | None,
    ) -> None:
        """
        Finds all top-level function definitions and processes the calls within them.

        Args:
            root_node (Node): The root AST node of the file.
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The programming language.
            queries (dict[cs.SupportedLanguage, LanguageQueries]): Language-specific queries.
            source_bytes (bytes | None): The raw byte content of the source file.
            source_text (str | None): The decoded string content of the source file.
        """
        result = get_function_captures(root_node, language, queries)
        if not result:
            return

        lang_config, captures = result
        func_nodes = captures.get(cs.CAPTURE_FUNCTION, [])
        for func_node in func_nodes:
            if not isinstance(func_node, Node):
                continue
            if self._is_method(func_node, lang_config):
                continue

            if language == cs.SupportedLanguage.CPP:
                func_name = cpp_utils.extract_function_name(func_node)
            else:
                func_name = self._get_node_name(func_node)
            if not func_name:
                continue
            if func_qn := self._build_nested_qualified_name(
                func_node, module_qn, func_name, lang_config
            ):
                self._ingest_function_calls(
                    func_node,
                    func_qn,
                    cs.NodeLabel.FUNCTION,
                    module_qn,
                    language,
                    queries,
                    source_bytes=source_bytes,
                    source_text=source_text,
                )

    def _get_rust_impl_class_name(self, class_node: Node) -> str | None:
        """
        Extracts the class name from a Rust `impl` block.

        Args:
            class_node (Node): The `impl` item node.

        Returns:
            The name of the class being implemented, or None.
        """
        class_name = self._get_node_name(class_node, cs.FIELD_TYPE)
        if class_name:
            return class_name
        return next(
            (
                safe_decode_text(child)
                for child in class_node.children
                if child.type == cs.TS_TYPE_IDENTIFIER and child.is_named and child.text
            ),
            None,
        )

    def _get_class_name_for_node(
        self, class_node: Node, language: cs.SupportedLanguage
    ) -> str | None:
        """
        Gets the name of a class from its corresponding AST node.

        Handles special cases like Rust `impl` blocks.

        Args:
            class_node (Node): The class or impl node.
            language (cs.SupportedLanguage): The programming language.

        Returns:
            The name of the class, or None if it cannot be determined.
        """
        if language == cs.SupportedLanguage.RUST and class_node.type == cs.TS_IMPL_ITEM:
            return self._get_rust_impl_class_name(class_node)
        return self._get_node_name(class_node)

    def _process_methods_in_class(
        self,
        body_node: Node,
        class_qn: str,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        source_bytes: bytes | None,
        source_text: str | None,
    ) -> None:
        """
        Processes calls occurring inside method definitions within a class body.

        Args:
            body_node (Node): The AST node representing the class body.
            class_qn (str): The qualified name of the containing class.
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The programming language.
            queries (dict[cs.SupportedLanguage, LanguageQueries]): Language-specific queries.
            source_bytes (bytes | None): The raw byte content of the source file.
            source_text (str | None): The decoded string content of the source file.
        """
        method_query = queries[language][cs.QUERY_FUNCTIONS]
        if not method_query:
            return
        method_cursor = QueryCursor(method_query)
        method_captures = normalize_query_captures(method_cursor.captures(body_node))
        method_nodes = method_captures.get(cs.CAPTURE_FUNCTION, [])
        for method_node in method_nodes:
            if not isinstance(method_node, Node):
                continue
            method_name = self._get_node_name(method_node)
            if not method_name:
                continue
            method_qn = f"{class_qn}{cs.SEPARATOR_DOT}{method_name}"
            self._ingest_function_calls(
                method_node,
                method_qn,
                cs.NodeLabel.METHOD,
                module_qn,
                language,
                queries,
                class_qn,
                source_bytes,
                source_text,
            )

    def _process_calls_in_classes(
        self,
        root_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        source_bytes: bytes | None,
        source_text: str | None,
    ) -> None:
        """
        Finds all class definitions in the file and processes calls within their methods.

        Args:
            root_node (Node): The root AST node of the file.
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The programming language.
            queries (dict[cs.SupportedLanguage, LanguageQueries]): Language-specific queries.
            source_bytes (bytes | None): The raw byte content of the source file.
            source_text (str | None): The decoded string content of the source file.
        """
        query = queries[language][cs.QUERY_CLASSES]
        if not query:
            return
        cursor = QueryCursor(query)
        captures = normalize_query_captures(cursor.captures(root_node))
        class_nodes = captures.get(cs.CAPTURE_CLASS, [])

        for class_node in class_nodes:
            if not isinstance(class_node, Node):
                continue
            class_name = self._get_class_name_for_node(class_node, language)
            if not class_name:
                continue
            class_qn = f"{module_qn}{cs.SEPARATOR_DOT}{class_name}"
            if body_node := class_node.child_by_field_name(cs.FIELD_BODY):
                self._process_methods_in_class(
                    body_node,
                    class_qn,
                    module_qn,
                    language,
                    queries,
                    source_bytes,
                    source_text,
                )

    def _process_module_level_calls(
        self,
        root_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        source_bytes: bytes | None,
        source_text: str | None,
    ) -> None:
        """
        Processes calls that occur at the top level of a module (outside any function or class).

        These calls are attributed to the module itself.

        Args:
            root_node (Node): The root AST node of the file.
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The programming language.
            queries (dict[cs.SupportedLanguage, LanguageQueries]): Language-specific queries.
            source_bytes (bytes | None): The raw byte content of the source file.
            source_text (str | None): The decoded string content of the source file.
        """
        self._ingest_function_calls(
            root_node,
            module_qn,
            cs.NodeLabel.MODULE,
            module_qn,
            language,
            queries,
            source_bytes=source_bytes,
            source_text=source_text,
        )

    def _process_file_level_calls(
        self,
        file_path: Path,
        root_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        source_bytes: bytes | None,
        source_text: str | None,
    ) -> None:
        """
        Processes and records file-level calls, attributing them to the `File` node.

        This is an optional feature, enabled by configuration, that creates `CALLS`
        relationships directly from `File` nodes to the functions they call at the
        top level.

        Args:
            file_path (Path): The path to the file being processed.
            root_node (Node): The root AST node of the file.
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The programming language.
            queries (dict[cs.SupportedLanguage, LanguageQueries]): Language-specific queries.
            source_bytes (bytes | None): The raw byte content of the source file.
            source_text (str | None): The decoded string content of the source file.
        """
        if not self._file_level_calls_enabled:
            return

        calls_query = queries[language].get(cs.QUERY_CALLS)
        if not calls_query:
            return

        lang_config = queries[language][cs.QUERY_CONFIG]
        relative_path = file_path.relative_to(self.repo_path).as_posix()

        if self._resolver.type_inference:
            local_var_types = cast(
                TypeInferenceEngine, self._resolver.type_inference
            ).build_local_variable_type_map(  # ty: ignore[possibly-missing-attribute]
                root_node, module_qn, language
            )
        else:
            local_var_types = {}

        cursor = QueryCursor(calls_query)
        captures = normalize_query_captures(cursor.captures(root_node))
        call_nodes = captures.get(cs.CAPTURE_CALL, [])

        for call_node in call_nodes:
            if not isinstance(call_node, Node):
                continue
            if not self._is_module_level_call(call_node, lang_config):
                continue

            call_name = self._get_call_target_name(call_node)
            if not call_name:
                continue

            callee_info = self._resolver.resolve_function_call(
                call_name, module_qn, local_var_types
            )
            is_dynamic = False
            confidence = 1.0
            if callee_info:
                callee_type, callee_qn = callee_info
            elif dynamic_info := self._resolve_dynamic_call(
                call_node, module_qn, source_bytes, source_text
            ):
                callee_type, callee_qn = dynamic_info
                is_dynamic = True
                confidence = 0.65
            elif builtin_info := self._resolver.resolve_builtin_call(call_name):
                callee_type, callee_qn = builtin_info
                confidence = 0.9
            elif operator_info := self._resolver.resolve_cpp_operator_call(
                call_name, module_qn
            ):
                callee_type, callee_qn = operator_info
                confidence = 0.85
            elif self._placeholder_nodes_enabled:
                callee_type, callee_qn = self._ensure_placeholder_function(call_name)
                confidence = 0.5
            else:
                continue

            if callee_qn.startswith(f"{cs.BUILTIN_PREFIX}{cs.SEPARATOR_DOT}"):
                self._ensure_external_callee(callee_type, callee_qn)

            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.FILE, cs.KEY_PATH, relative_path),
                cs.RelationshipType.CALLS,
                (callee_type, cs.KEY_QUALIFIED_NAME, callee_qn),
                self._build_call_relationship_props(
                    language,
                    call_node,
                    is_dynamic=is_dynamic,
                    confidence=confidence,
                    relation_type="file_level_call",
                    extra={
                        cs.KEY_FILE_LEVEL_CALL: True,
                        cs.KEY_IS_PLACEHOLDER: callee_qn.startswith(
                            f"{self.project_name}{cs.SEPARATOR_DOT}framework."
                        ),
                    },
                ),
            )

    @staticmethod
    def _is_module_level_call(call_node: Node, lang_config: LanguageSpec) -> bool:
        """
        Checks if a call node is at the module level (i.e., not inside a function or class).

        Args:
            call_node (Node): The call node to check.
            lang_config (LanguageSpec): The language configuration specifying function/class node types.

        Returns:
            True if the call is at the module level, False otherwise.
        """
        current = call_node.parent
        while isinstance(current, Node):
            if current.type in lang_config.function_node_types:
                return False
            if current.type in lang_config.class_node_types:
                return False
            current = current.parent
        return True

    def _ensure_placeholder_function(self, call_name: str) -> tuple[str, str]:
        """
        Ensures a placeholder function node exists for an unresolved call.

        This is used when a call cannot be resolved to a concrete function, creating a
        generic node to represent the call target. This is useful for framework-specific
        or dynamic calls.

        Args:
            call_name (str): The name of the unresolved call.

        Returns:
            A tuple containing the node label (`Function`) and the qualified name of the
            created or existing placeholder node.
        """
        normalized = re.sub(r"[^A-Za-z0-9_]+", "_", call_name).strip("_")
        if not normalized:
            normalized = "unknown_call"
        placeholder_qn = f"{self.project_name}{cs.SEPARATOR_DOT}framework.{normalized}"
        self.ingestor.ensure_node_batch(
            cs.NodeLabel.FUNCTION,
            {
                cs.KEY_QUALIFIED_NAME: placeholder_qn,
                cs.KEY_NAME: call_name,
                cs.KEY_DECORATORS: [],
                cs.KEY_IS_EXTERNAL: True,
                cs.KEY_IS_PLACEHOLDER: True,
                cs.KEY_FRAMEWORK: "framework_placeholder",
                cs.KEY_FRAMEWORK_METADATA: json.dumps(
                    {"origin": "placeholder", "reason": "unresolved_call"},
                    ensure_ascii=False,
                ),
            },
        )
        return cs.NodeLabel.FUNCTION, placeholder_qn

    def _ensure_external_callee(self, callee_type: str, callee_qn: str) -> None:
        """
        Ensures that a node for an external or built-in callee exists in the graph.

        Args:
            callee_type (str): The node label of the callee (e.g., 'Function').
            callee_qn (str): The fully qualified name of the callee.
        """
        callee_name = callee_qn.rsplit(cs.SEPARATOR_DOT, 1)[-1]
        self.ingestor.ensure_node_batch(
            callee_type,
            {
                cs.KEY_QUALIFIED_NAME: callee_qn,
                cs.KEY_NAME: callee_name,
                cs.KEY_IS_EXTERNAL: True,
            },
        )

    def _get_call_target_name(self, call_node: Node) -> str | None:
        """
        Extracts the target name of a function call from its AST node.

        This method handles various AST node types across different languages to
        reliably extract the name or expression being called.

        Args:
            call_node (Node): The AST node representing the call expression.

        Returns:
            The string representation of the call name (e.g., "my_func", "obj.method"),
            or None if it cannot be determined.
        """
        if func_child := call_node.child_by_field_name(cs.TS_FIELD_FUNCTION):
            match func_child.type:
                case (
                    cs.TS_IDENTIFIER
                    | cs.TS_ATTRIBUTE
                    | cs.TS_MEMBER_EXPRESSION
                    | cs.CppNodeType.QUALIFIED_IDENTIFIER
                    | cs.TS_SCOPED_IDENTIFIER
                ):
                    if func_child.text is not None:
                        return safe_decode_text(func_child)
                case cs.TS_CPP_FIELD_EXPRESSION:
                    field_node = func_child.child_by_field_name(cs.FIELD_FIELD)
                    if field_node and field_node.text:
                        return safe_decode_text(field_node)
                case cs.TS_PARENTHESIZED_EXPRESSION:
                    return self._get_iife_target_name(func_child)

        match call_node.type:
            case (
                cs.TS_CPP_BINARY_EXPRESSION
                | cs.TS_CPP_UNARY_EXPRESSION
                | cs.TS_CPP_UPDATE_EXPRESSION
            ):
                operator_node = call_node.child_by_field_name(cs.FIELD_OPERATOR)
                if operator_node and operator_node.text:
                    operator_text = safe_decode_text(operator_node)
                    if operator_text is None:
                        return None
                    return cpp_utils.convert_operator_symbol_to_name(operator_text)
            case cs.TS_METHOD_INVOCATION:
                object_node = call_node.child_by_field_name(cs.FIELD_OBJECT)
                name_node = call_node.child_by_field_name(cs.FIELD_NAME)
                if name_node and name_node.text:
                    method_name = safe_decode_text(name_node)
                    if method_name is None:
                        return None
                    if not object_node or not object_node.text:
                        return method_name
                    object_text = safe_decode_text(object_node)
                    if object_text is None:
                        return method_name
                    return f"{object_text}{cs.SEPARATOR_DOT}{method_name}"

        if name_node := call_node.child_by_field_name(cs.FIELD_NAME):
            if name_node.text is not None:
                return safe_decode_text(name_node)

        return None

    def _get_iife_target_name(self, parenthesized_expr: Node) -> str | None:
        """
        Generates a unique name for an IIFE (Immediately Invoked Function Expression).

        Since IIFEs are anonymous, a synthetic name is created based on their
        location in the source file to serve as a unique identifier.

        Args:
            parenthesized_expr (Node): The parenthesized expression node containing the function.

        Returns:
            A generated name for the IIFE (e.g., "__iife_func_10_5"), or None.
        """
        for child in parenthesized_expr.children:
            match child.type:
                case cs.TS_FUNCTION_EXPRESSION:
                    return f"{cs.IIFE_FUNC_PREFIX}{child.start_point[0]}_{child.start_point[1]}"
                case cs.TS_ARROW_FUNCTION:
                    return f"{cs.IIFE_ARROW_PREFIX}{child.start_point[0]}_{child.start_point[1]}"
        return None

    def _ingest_function_calls(
        self,
        caller_node: Node,
        caller_qn: str,
        caller_type: str,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        class_context: str | None = None,
        source_bytes: bytes | None = None,
        source_text: str | None = None,
    ) -> None:
        """
        Finds, resolves, and ingests all calls within a given scope (function, method, or module).

        This is the core logic loop that iterates through call nodes found by a query,
        resolves them, and creates the `CALLS` relationship in the graph.

        Args:
            caller_node (Node): The AST node for the scope containing the calls (e.g., function body).
            caller_qn (str): The qualified name of the calling entity.
            caller_type (str): The node label of the caller ('Function', 'Method', 'Module').
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The programming language.
            queries (dict[cs.SupportedLanguage, LanguageQueries]): Language-specific queries.
            class_context (str | None): The qualified name of the containing class, if any.
            source_bytes (bytes | None): The raw byte content for dynamic analysis.
            source_text (str | None): The decoded string content for dynamic analysis.
        """
        calls_query = queries[language].get(cs.QUERY_CALLS)
        if not calls_query:
            return

        if self._resolver.type_inference:
            local_var_types = cast(
                TypeInferenceEngine, self._resolver.type_inference
            ).build_local_variable_type_map(  # ty: ignore[possibly-missing-attribute]
                caller_node, module_qn, language
            )
        else:
            local_var_types = {}

        cursor = QueryCursor(calls_query)
        captures = normalize_query_captures(cursor.captures(caller_node))
        call_nodes = captures.get(cs.CAPTURE_CALL, [])

        logger.debug(
            ls.CALL_FOUND_NODES.format(
                count=len(call_nodes), language=language, caller=caller_qn
            )
        )

        for call_node in call_nodes:
            if not isinstance(call_node, Node):
                continue

            call_name = self._get_call_target_name(call_node)
            if not call_name:
                continue

            if (
                language == cs.SupportedLanguage.JAVA
                and call_node.type == cs.TS_METHOD_INVOCATION
            ):
                callee_info = self._resolver.resolve_java_method_call(
                    call_node, module_qn, local_var_types
                )
            else:
                callee_info = self._resolver.resolve_function_call(
                    call_name, module_qn, local_var_types, class_context
                )
            is_dynamic = False
            confidence = 1.0
            if callee_info:
                callee_type, callee_qn = callee_info
            elif dynamic_info := self._resolve_dynamic_call(
                call_node, module_qn, source_bytes, source_text
            ):
                callee_type, callee_qn = dynamic_info
                is_dynamic = True
                confidence = 0.65
            elif builtin_info := self._resolver.resolve_builtin_call(call_name):
                callee_type, callee_qn = builtin_info
                confidence = 0.9
            elif operator_info := self._resolver.resolve_cpp_operator_call(
                call_name, module_qn
            ):
                callee_type, callee_qn = operator_info
                confidence = 0.85
            else:
                continue

            if callee_qn.startswith(f"{cs.BUILTIN_PREFIX}{cs.SEPARATOR_DOT}"):
                self._ensure_external_callee(callee_type, callee_qn)
            logger.debug(
                ls.CALL_FOUND.format(
                    caller=caller_qn,
                    call_name=call_name,
                    callee_type=callee_type,
                    callee_qn=callee_qn,
                )
            )

            self.ingestor.ensure_relationship_batch(
                (caller_type, cs.KEY_QUALIFIED_NAME, caller_qn),
                cs.RelationshipType.CALLS,
                (callee_type, cs.KEY_QUALIFIED_NAME, callee_qn),
                self._build_call_relationship_props(
                    language,
                    call_node,
                    is_dynamic=is_dynamic,
                    confidence=confidence,
                    relation_type="call",
                ),
            )

    def _build_nested_qualified_name(
        self,
        func_node: Node,
        module_qn: str,
        func_name: str,
        lang_config: LanguageSpec,
    ) -> str | None:
        """
        Builds the fully qualified name for a nested function.

        It traverses up the AST from the function node to construct the full path,
        including the names of any enclosing functions.

        Args:
            func_node (Node): The AST node of the nested function.
            module_qn (str): The qualified name of the containing module.
            func_name (str): The simple name of the nested function.
            lang_config (LanguageSpec): The language configuration.

        Returns:
            The fully qualified name for the nested function, or None if it's inside a class.
        """
        if lang_config.language in {
            cs.SupportedLanguage.JSON,
            cs.SupportedLanguage.YAML,
        }:
            return None
        path_parts: list[str] = []
        current = func_node.parent

        if not isinstance(current, Node):
            logger.warning(
                ls.CALL_UNEXPECTED_PARENT.format(
                    node=func_node, parent_type=type(current)
                )
            )
            return None

        while current and current.type not in lang_config.module_node_types:
            if current.type in lang_config.function_node_types:
                if name_node := current.child_by_field_name(cs.FIELD_NAME):
                    text = name_node.text
                    if text is not None:
                        decoded = safe_decode_text(name_node)
                        if decoded is not None:
                            path_parts.append(decoded)
            elif current.type in lang_config.class_node_types:
                return None

            current = current.parent

        path_parts.reverse()
        if path_parts:
            return f"{module_qn}{cs.SEPARATOR_DOT}{cs.SEPARATOR_DOT.join(path_parts)}{cs.SEPARATOR_DOT}{func_name}"
        return f"{module_qn}{cs.SEPARATOR_DOT}{func_name}"

    def _is_method(self, func_node: Node, lang_config: LanguageSpec) -> bool:
        """
        Determines if a function node represents a class method.

        Args:
            func_node (Node): The function node to check.
            lang_config (LanguageSpec): The language configuration.

        Returns:
            True if the function is a method, False otherwise.
        """
        return is_method_node(func_node, lang_config)

    def _resolve_dynamic_call(
        self,
        call_node: Node,
        module_qn: str,
        source_bytes: bytes | None,
        source_text: str | None,
    ) -> tuple[str, str] | None:
        """
        Attempts to resolve a dynamic call using heuristic-based analysis.

        This is a fallback for when static resolution fails. It extracts a snippet
        of code around the call and uses a `DynamicCallResolver` to guess the target.

        Args:
            call_node (Node): The AST node of the call.
            module_qn (str): The qualified name of the module.
            source_bytes (bytes | None): The raw byte content of the file.
            source_text (str | None): The decoded string content of the file.

        Returns:
            A tuple of (node_label, qualified_name) if resolved, otherwise None.
        """
        if not self._heuristic_calls_enabled:
            return None
        snippet = self._extract_call_snippet(call_node, source_bytes, source_text)
        if not snippet:
            return None
        return self._dynamic_resolver.resolve_from_snippet(snippet, module_qn)

    @staticmethod
    def _extract_call_snippet(
        call_node: Node, source_bytes: bytes | None, source_text: str | None
    ) -> str | None:
        """
        Extracts the source code snippet corresponding to a call node.

        Args:
            call_node (Node): The call's AST node.
            source_bytes (bytes | None): The raw byte content of the file.
            source_text (str | None): The decoded string content of the file.

        Returns:
            The source text of the call expression, or None.
        """
        if isinstance(source_bytes, bytes | bytearray) and hasattr(
            call_node, "start_byte"
        ):
            start = getattr(call_node, "start_byte", None)
            end = getattr(call_node, "end_byte", None)
            if isinstance(start, int) and isinstance(end, int) and end > start:
                snippet_bytes = source_bytes[start:end]
                return snippet_bytes.decode(cs.ENCODING_UTF8, errors="ignore")
        if call_node.text:
            if isinstance(call_node.text, bytes):
                return call_node.text.decode(cs.ENCODING_UTF8, errors="ignore")
            return str(call_node.text)
        return source_text
