"""
This module defines the `CallProcessor`, which is responsible for identifying
and processing function and method calls within the parsed source code.

It works in conjunction with a `CallResolver` to determine the fully qualified
name (FQN) of the called function/method. Once a call is resolved, it creates
a `CALLS` relationship in the graph database between the caller and the callee.

Key functionalities:
-   Traversing the Abstract Syntax Tree (AST) to find function/method definitions.
-   Iterating through call expressions within each function, method, or at the
    module level.
-   Extracting the name of the called function.
-   Using the `CallResolver` to find the FQN of the callee.
-   Ingesting `CALLS` relationships into the database.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from tree_sitter import Node, QueryCursor

from codebase_rag.data_models.types_defs import (
    FunctionRegistryTrieProtocol,
    LanguageQueries,
)
from codebase_rag.infrastructure.language_spec import LanguageSpec

from ..core import constants as cs
from ..core import logs as ls
from ..services import IngestorProtocol
from .call_resolver import CallResolver
from .cpp import utils as cpp_utils
from .import_processor import ImportProcessor
from .type_inference import TypeInferenceEngine
from .utils import get_function_captures, is_method_node


class CallProcessor:
    """
    Processes and resolves function/method calls from an AST.
    """

    def __init__(
        self,
        ingestor: IngestorProtocol,
        repo_path: Path,
        project_name: str,
        function_registry: FunctionRegistryTrieProtocol,
        import_processor: ImportProcessor,
        type_inference: TypeInferenceEngine,
        class_inheritance: dict[str, list[str]],
    ) -> None:
        """
        Initializes the CallProcessor.

        Args:
            ingestor (IngestorProtocol): The data ingestion service.
            repo_path (Path): The root path of the repository.
            project_name (str): The name of the project.
            function_registry (FunctionRegistryTrieProtocol): The registry of all known functions.
            import_processor (ImportProcessor): The processor for handling imports.
            type_inference (TypeInferenceEngine): The engine for inferring variable types.
            class_inheritance (dict[str, list[str]]): A dictionary mapping classes to their parents.
        """
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name

        self._resolver = CallResolver(
            function_registry=function_registry,
            import_processor=import_processor,
            type_inference=type_inference,
            class_inheritance=class_inheritance,
        )

    def _get_node_name(self, node: Node, field: str = cs.FIELD_NAME) -> str | None:
        """
        Extracts the name from a tree-sitter node.

        Args:
            node (Node): The node to extract the name from.
            field (str): The field name where the name is stored.

        Returns:
            str | None: The extracted name, or None if not found.
        """
        name_node = node.child_by_field_name(field)
        if not name_node:
            return None
        text = name_node.text
        return None if text is None else text.decode(cs.ENCODING_UTF8)

    def process_calls_in_file(
        self,
        file_path: Path,
        root_node: Node,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """
        Processes all function and method calls in a given file.

        Args:
            file_path (Path): The path to the file being processed.
            root_node (Node): The root node of the file's AST.
            language (cs.SupportedLanguage): The language of the file.
            queries (dict): The collection of tree-sitter queries.
        """
        relative_path = file_path.relative_to(self.repo_path)
        logger.debug(ls.CALL_PROCESSING_FILE.format(path=relative_path))

        try:
            module_qn = cs.SEPARATOR_DOT.join(
                [self.project_name] + list(relative_path.with_suffix("").parts)
            )
            if file_path.name in (cs.INIT_PY, cs.MOD_RS):
                module_qn = cs.SEPARATOR_DOT.join(
                    [self.project_name] + list(relative_path.parent.parts)
                )

            self._process_calls_in_functions(root_node, module_qn, language, queries)
            self._process_calls_in_classes(root_node, module_qn, language, queries)
            self._process_module_level_calls(root_node, module_qn, language, queries)

        except Exception as e:
            logger.error(ls.CALL_PROCESSING_FAILED.format(path=file_path, error=e))

    def _process_calls_in_functions(
        self,
        root_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """
        Finds all top-level functions in a node and processes calls within them.

        Args:
            root_node (Node): The AST node to search within.
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The language of the code.
            queries (dict): The tree-sitter queries.
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
                )

    def _get_rust_impl_class_name(self, class_node: Node) -> str | None:
        """
        Gets the class name from a Rust `impl` block.

        Args:
            class_node (Node): The `impl_item` node.

        Returns:
            str | None: The name of the class being implemented, or None.
        """
        class_name = self._get_node_name(class_node, cs.FIELD_TYPE)
        if class_name:
            return class_name
        return next(
            (
                child.text.decode(cs.ENCODING_UTF8)
                for child in class_node.children
                if child.type == cs.TS_TYPE_IDENTIFIER and child.is_named and child.text
            ),
            None,
        )

    def _get_class_name_for_node(
        self, class_node: Node, language: cs.SupportedLanguage
    ) -> str | None:
        """
        Gets the name of a class node, with special handling for Rust `impl` blocks.

        Args:
            class_node (Node): The class-like node.
            language (cs.SupportedLanguage): The language of the node.

        Returns:
            str | None: The name of the class.
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
    ) -> None:
        """
        Processes all method calls within the body of a class.

        Args:
            body_node (Node): The body node of the class.
            class_qn (str): The qualified name of the class.
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The language of the code.
            queries (dict): The tree-sitter queries.
        """
        method_query = queries[language][cs.QUERY_FUNCTIONS]
        if not method_query:
            return
        method_cursor = QueryCursor(method_query)
        method_captures = method_cursor.captures(body_node)
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
            )

    def _process_calls_in_classes(
        self,
        root_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """
        Finds all classes in a node and processes method calls within them.

        Args:
            root_node (Node): The AST node to search within.
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The language of the code.
            queries (dict): The tree-sitter queries.
        """
        query = queries[language][cs.QUERY_CLASSES]
        if not query:
            return
        cursor = QueryCursor(query)
        captures = cursor.captures(root_node)
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
                    body_node, class_qn, module_qn, language, queries
                )

    def _process_module_level_calls(
        self,
        root_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """
        Processes calls made at the top level of a module.

        Args:
            root_node (Node): The root node of the module's AST.
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The language of the code.
            queries (dict): The tree-sitter queries.
        """
        self._ingest_function_calls(
            root_node, module_qn, cs.NodeLabel.MODULE, module_qn, language, queries
        )

    def _get_call_target_name(self, call_node: Node) -> str | None:
        """
        Extracts the target name of a function or method call from a call node.

        Args:
            call_node (Node): The call expression node.

        Returns:
            str | None: The name of the function being called.
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
                        return str(func_child.text.decode(cs.ENCODING_UTF8))
                case cs.TS_CPP_FIELD_EXPRESSION:
                    field_node = func_child.child_by_field_name(cs.FIELD_FIELD)
                    if field_node and field_node.text:
                        return str(field_node.text.decode(cs.ENCODING_UTF8))
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
                    operator_text = operator_node.text.decode(cs.ENCODING_UTF8)
                    return cpp_utils.convert_operator_symbol_to_name(operator_text)
            case cs.TS_METHOD_INVOCATION:
                object_node = call_node.child_by_field_name(cs.FIELD_OBJECT)
                name_node = call_node.child_by_field_name(cs.FIELD_NAME)
                if name_node and name_node.text:
                    method_name = str(name_node.text.decode(cs.ENCODING_UTF8))
                    if not object_node or not object_node.text:
                        return method_name
                    object_text = str(object_node.text.decode(cs.ENCODING_UTF8))
                    return f"{object_text}{cs.SEPARATOR_DOT}{method_name}"

        if name_node := call_node.child_by_field_name(cs.FIELD_NAME):
            if name_node.text is not None:
                return str(name_node.text.decode(cs.ENCODING_UTF8))

        return None

    def _get_iife_target_name(self, parenthesized_expr: Node) -> str | None:
        """
        Generates a unique name for an Immediately Invoked Function Expression (IIFE).

        Args:
            parenthesized_expr (Node): The parenthesized expression node containing the IIFE.

        Returns:
            str | None: A generated unique name for the IIFE.
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
    ) -> None:
        """
        Finds, resolves, and ingests all function calls within a given node.

        Args:
            caller_node (Node): The AST node of the calling function/method/module.
            caller_qn (str): The qualified name of the caller.
            caller_type (str): The label of the caller node (e.g., 'Function').
            module_qn (str): The qualified name of the containing module.
            language (cs.SupportedLanguage): The language of the code.
            queries (dict): The tree-sitter queries.
            class_context (str | None): The qualified name of the containing class, if any.
        """
        calls_query = queries[language].get(cs.QUERY_CALLS)
        if not calls_query:
            return

        local_var_types = self._resolver.type_inference.build_local_variable_type_map(
            caller_node, module_qn, language
        )

        cursor = QueryCursor(calls_query)
        captures = cursor.captures(caller_node)
        call_nodes = captures.get(cs.CAPTURE_CALL, [])

        logger.debug(
            ls.CALL_FOUND_NODES.format(
                count=len(call_nodes), language=language, caller=caller_qn
            )
        )

        for call_node in call_nodes:
            if not isinstance(call_node, Node):
                continue

            # (H) tree-sitter finds ALL call nodes including nested; no recursive processing needed

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
            elif builtin_info := self._resolver.resolve_builtin_call(call_name):
                callee_type, callee_qn = builtin_info
            elif operator_info := self._resolver.resolve_cpp_operator_call(
                call_name, module_qn
            ):
                callee_type, callee_qn = operator_info
            else:
                continue
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
        Builds the FQN for a nested function.

        Args:
            func_node (Node): The node of the nested function.
            module_qn (str): The qualified name of the containing module.
            func_name (str): The simple name of the nested function.
            lang_config (LanguageSpec): The language specification.

        Returns:
            str | None: The constructed FQN, or None if it cannot be built.
        """
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
                        path_parts.append(text.decode(cs.ENCODING_UTF8))
            elif current.type in lang_config.class_node_types:
                return None

            current = current.parent

        path_parts.reverse()
        if path_parts:
            return f"{module_qn}{cs.SEPARATOR_DOT}{cs.SEPARATOR_DOT.join(path_parts)}{cs.SEPARATOR_DOT}{func_name}"
        return f"{module_qn}{cs.SEPARATOR_DOT}{func_name}"

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
