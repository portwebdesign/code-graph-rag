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
)
from codebase_rag.infrastructure.language_spec import LanguageSpec

from ..services.protocols import IngestorProtocol
from .call_resolver import CallResolver
from .cpp import utils as cpp_utils
from .dynamic_call_resolver import DynamicCallResolver
from .import_processor import ImportProcessor
from .type_inference import TypeInferenceEngine
from .utils import (
    get_function_captures,
    is_method_node,
    normalize_query_captures,
    safe_decode_text,
)


class CallProcessor:
    """
    Process function and method calls within source code.

    This class handles the extraction of function calls from the syntax tree,
    resolves them to their definitions using CallResolver, and ingests the
    relationships into the graph database.

    Attributes:
        ingestor (IngestorProtocol): The data ingestor.
        repo_path (Path): Path to the repository root.
        project_name (str): Name of the project.
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
        Initialize the CallProcessor.

        Args:
            ingestor (IngestorProtocol): The data ingestor.
            repo_path (Path): Path to the repository root.
            project_name (str): Name of the project.
            function_registry (FunctionRegistryTrieProtocol): Function registry trie.
            import_processor (ImportProcessor): Import processor instance.
            class_inheritance (dict[str, list[str]]): Class inheritance map.
            type_inference (TypeInferenceEngine | None): Type inference engine.
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
        Extract the name from a node field.

        Args:
            node (Node): The tree-sitter node.
            field (str): The field name to look for. Defaults to cs.FIELD_NAME.

        Returns:
            str | None: The extracted name or None if not found.
        """
        name_node = node.child_by_field_name(field)
        if not name_node:
            return None
        return safe_decode_text(name_node)

    def process_calls_in_file(
        self,
        file_path: Path,
        root_node: Node,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """
        Process all calls within a file.

        Args:
            file_path (Path): Path to the source file.
            root_node (Node): The root node of the syntax tree.
            language (cs.SupportedLanguage): The language of the file.
            queries (dict[cs.SupportedLanguage, LanguageQueries]): Dictionary of queries.
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
        Process calls occurring inside function definitions.

        Args:
            root_node (Node): Root node of the file.
            module_qn (str): Qualified name of the module.
            language (cs.SupportedLanguage): Programming language.
            queries (dict[cs.SupportedLanguage, LanguageQueries]): Language queries.
            source_bytes (bytes | None): Source code content in bytes.
            source_text (str | None): Source code content as string.
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
        Extract class name from a Rust implementation block.

        Args:
            class_node (Node): The implementation node.

        Returns:
            str | None: The class name or None.
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
        Get the class name for a given class node.

        Args:
            class_node (Node): The class node.
            language (cs.SupportedLanguage): The language.

        Returns:
            str | None: The class name or None.
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
        Process calls occurring inside method definitions within a class.

        Args:
            body_node (Node): The body node of the class.
            class_qn (str): Qualified name of the class.
            module_qn (str): Qualified name of the module.
            language (cs.SupportedLanguage): Programming language.
            queries (dict[cs.SupportedLanguage, LanguageQueries]): Language queries.
            source_bytes (bytes | None): Source content bytes.
            source_text (str | None): Source content string.
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
        Iterate over classes and process calls within their methods.

        Args:
            root_node (Node): Root node of the file.
            module_qn (str): Qualified name of the module.
            language (cs.SupportedLanguage): Programming language.
            queries (dict[cs.SupportedLanguage, LanguageQueries]): Language queries.
            source_bytes (bytes | None): Source content bytes.
            source_text (str | None): Source content string.
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
        Process calls occurring at the module level (top-level scripts).

        Args:
            root_node (Node): Root node of the file.
            module_qn (str): Qualified name of the module.
            language (cs.SupportedLanguage): Programming language.
            queries (dict[cs.SupportedLanguage, LanguageQueries]): Language queries.
            source_bytes (bytes | None): Source content bytes.
            source_text (str | None): Source content string.
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
        Process and record file-level calls if enabled.

        Args:
            file_path (Path): Path to the file.
            root_node (Node): Root node.
            module_qn (str): Module qualified name.
            language (cs.SupportedLanguage): Language.
            queries (dict[cs.SupportedLanguage, LanguageQueries]): Queries.
            source_bytes (bytes | None): Source bytes.
            source_text (str | None): Source text.
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
            if callee_info:
                callee_type, callee_qn = callee_info
            elif dynamic_info := self._resolve_dynamic_call(
                call_node, module_qn, source_bytes, source_text
            ):
                callee_type, callee_qn = dynamic_info
            elif builtin_info := self._resolver.resolve_builtin_call(call_name):
                callee_type, callee_qn = builtin_info
            elif operator_info := self._resolver.resolve_cpp_operator_call(
                call_name, module_qn
            ):
                callee_type, callee_qn = operator_info
            elif self._placeholder_nodes_enabled:
                callee_type, callee_qn = self._ensure_placeholder_function(call_name)
            else:
                continue

            if callee_qn.startswith(f"{cs.BUILTIN_PREFIX}{cs.SEPARATOR_DOT}"):
                self._ensure_external_callee(callee_type, callee_qn)

            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.FILE, cs.KEY_PATH, relative_path),
                cs.RelationshipType.CALLS,
                (callee_type, cs.KEY_QUALIFIED_NAME, callee_qn),
                {
                    cs.KEY_FILE_LEVEL_CALL: True,
                    cs.KEY_IS_PLACEHOLDER: callee_qn.startswith(
                        f"{self.project_name}{cs.SEPARATOR_DOT}framework."
                    ),
                },
            )

    @staticmethod
    def _is_module_level_call(call_node: Node, lang_config: LanguageSpec) -> bool:
        """
        Check if a call node is at the module level (not inside a function or class).

        Args:
            call_node (Node): The call node.
            lang_config (LanguageSpec): Language configuration.

        Returns:
            bool: True if it is a module-level call, False otherwise.
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
        Ensure a placeholder function node exists for an unresolved call.

        Args:
            call_name (str): The name of the call.

        Returns:
            tuple[str, str]: The node label and qualified name of the placeholder.
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
        Extract the target name of a function call from the node.

        Args:
            call_node (Node): The call node.

        Returns:
            str | None: The name of the called function or None.
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
        Generate a name for an IIFE (Immediately Invoked Function Expression).

        Args:
            parenthesized_expr (Node): The parenthesized expression node.

        Returns:
            str | None: The generated IIFE name or None.
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
        Ingest all function calls found within a caller node (function/method/module).

        Args:
            caller_node (Node): The node containing calls.
            caller_qn (str): Qualified name of the caller.
            caller_type (str): Type of the caller (e.g., FUNCTION, METHOD).
            module_qn (str): Qualified name of the module.
            language (cs.SupportedLanguage): Programming language.
            queries (dict[cs.SupportedLanguage, LanguageQueries]): Language queries.
            class_context (str | None): Context if inside a class.
            source_bytes (bytes | None): Source bytes for dynamic analysis.
            source_text (str | None): Source text for dynamic analysis.
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
            if callee_info:
                callee_type, callee_qn = callee_info
            elif dynamic_info := self._resolve_dynamic_call(
                call_node, module_qn, source_bytes, source_text
            ):
                callee_type, callee_qn = dynamic_info
            elif builtin_info := self._resolver.resolve_builtin_call(call_name):
                callee_type, callee_qn = builtin_info
            elif operator_info := self._resolver.resolve_cpp_operator_call(
                call_name, module_qn
            ):
                callee_type, callee_qn = operator_info
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
            )

    def _build_nested_qualified_name(
        self,
        func_node: Node,
        module_qn: str,
        func_name: str,
        lang_config: LanguageSpec,
    ) -> str | None:
        """
        Build the qualified name for nested functions.

        Args:
            func_node (Node): The function node.
            module_qn (str): Module qualified name.
            func_name (str): Function name.
            lang_config (LanguageSpec): Language configuration.

        Returns:
            str | None: The qualified name or None.
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
        Check if a function node is a method.

        Args:
            func_node (Node): Function node.
            lang_config (LanguageSpec): Language config.

        Returns:
            bool: True if it is a method.
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
        Attempt to resolve a dynamic call using heuristics.

        Args:
            call_node (Node): The call node.
            module_qn (str): Module qualified name.
            source_bytes (bytes | None): Source bytes.
            source_text (str | None): Source text.

        Returns:
            tuple[str, str] | None: Tuple of type and qualified name, or None.
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
        Extract the code snippet corresponding to the call.

        Args:
            call_node (Node): Call node.
            source_bytes (bytes | None): Source bytes.
            source_text (str | None): Source text.

        Returns:
            str | None: Extracted snippet or None.
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
