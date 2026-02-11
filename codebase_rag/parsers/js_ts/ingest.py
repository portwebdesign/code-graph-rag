from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from tree_sitter import Query, QueryCursor

from codebase_rag.core import constants as cs
from codebase_rag.core import logs as lg
from codebase_rag.data_models.types_defs import (
    ASTNode,
    FunctionRegistryTrieProtocol,
    NodeType,
    PropertyDict,
    SimpleNameLookup,
)
from codebase_rag.infrastructure.language_spec import get_language_spec_for_path

from ...utils.path_utils import is_test_path
from ..utils import (
    build_lite_signature,
    extract_param_names,
    safe_decode_text,
    safe_decode_with_fallback,
)
from .module_system import JsTsModuleSystemMixin
from .utils import get_js_ts_language_obj

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import LanguageQueries
    from codebase_rag.infrastructure.language_spec import LanguageSpec
    from codebase_rag.services import IngestorProtocol

    from ..handlers import LanguageHandler
    from ..import_processor import ImportProcessor


class JsTsIngestMixin(JsTsModuleSystemMixin):
    """
    Mixin for ingesting JavaScript and TypeScript code structures.

    Handles prototype inheritance, object methods, assignment-based arrow functions,
    and integrates with the ingestion pipeline.
    """

    ingestor: IngestorProtocol
    repo_path: Path
    project_name: str
    function_registry: FunctionRegistryTrieProtocol
    simple_name_lookup: SimpleNameLookup
    module_qn_to_file_path: dict[str, Path]
    module_qn_to_file_hash: dict[str, str]
    import_processor: ImportProcessor
    class_inheritance: dict[str, list[str]]
    _handler: LanguageHandler

    @abstractmethod
    def _get_docstring(self, node: ASTNode) -> str | None: ...

    @abstractmethod
    def _build_nested_qualified_name(
        self,
        func_node: ASTNode,
        module_qn: str,
        func_name: str,
        lang_config: LanguageSpec,
        skip_classes: bool = False,
    ) -> str | None: ...

    def _build_js_ts_function_props(
        self,
        function_qn: str,
        function_name: str,
        function_node: ASTNode,
        module_qn: str,
    ) -> PropertyDict:
        namespace = (
            module_qn.rsplit(cs.SEPARATOR_DOT, 1)[0]
            if cs.SEPARATOR_DOT in module_qn
            else None
        )
        file_path = self.module_qn_to_file_path.get(module_qn)
        file_hash = self.module_qn_to_file_hash.get(module_qn)
        language_value = None
        language_key = None
        if file_path and (lang_spec := get_language_spec_for_path(file_path)):
            if isinstance(lang_spec.language, cs.SupportedLanguage):
                language_value = lang_spec.language
                language_key = lang_spec.language.value
            else:
                language_key = str(lang_spec.language)
        signature_lite = build_lite_signature(
            function_name,
            extract_param_names(function_node),
            None,
            language_value,
        )
        function_props: PropertyDict = {
            cs.KEY_QUALIFIED_NAME: function_qn,
            cs.KEY_NAME: function_name,
            cs.KEY_START_LINE: function_node.start_point[0] + 1,
            cs.KEY_END_LINE: function_node.end_point[0] + 1,
            cs.KEY_DOCSTRING: self._get_docstring(function_node),
            cs.KEY_SIGNATURE_LITE: signature_lite,
            cs.KEY_SIGNATURE: signature_lite,
            cs.KEY_DECORATORS: [],
            cs.KEY_DECORATORS_NORM: [],
            cs.KEY_MODULE_QN: module_qn,
            cs.KEY_SYMBOL_KIND: cs.NodeLabel.FUNCTION.value.lower(),
            cs.KEY_PARENT_QN: module_qn,
        }
        if namespace:
            function_props[cs.KEY_NAMESPACE] = namespace
            function_props[cs.KEY_PACKAGE] = namespace
        if language_key:
            function_props[cs.KEY_LANGUAGE] = language_key
        if file_path and self.repo_path:
            relative_path = file_path.relative_to(self.repo_path).as_posix()
            function_props[cs.KEY_PATH] = relative_path
            function_props[cs.KEY_REPO_REL_PATH] = relative_path
            function_props[cs.KEY_ABS_PATH] = file_path.resolve().as_posix()
            function_props[cs.KEY_IS_TEST] = is_test_path(
                file_path.relative_to(self.repo_path)
            )
        if file_hash:
            function_props[cs.KEY_FILE_HASH] = file_hash
        return function_props

    def _ingest_prototype_inheritance(
        self,
        root_node: ASTNode,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """
        Ingest prototype-based inheritance and method assignments.

        Args:
            root_node: The root AST node.
            module_qn: The module qualified name.
            language: The supported language.
            queries: Dictionary of language queries.
        """
        if language not in cs.JS_TS_LANGUAGES:
            return

        self._ingest_prototype_inheritance_links(
            root_node, module_qn, language, queries
        )

        self._ingest_prototype_method_assignments(
            root_node, module_qn, language, queries
        )

    def _ingest_prototype_inheritance_links(
        self,
        root_node: ASTNode,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """
        Ingest prototype inheritance links (Parent.prototype = Child.prototype).

        Args:
            root_node: The root AST node.
            module_qn: The module qualified name.
            language: The supported language.
            queries: Dictionary of language queries.
        """
        lang_queries = queries[language]

        language_obj = lang_queries.get(cs.QUERY_LANGUAGE)
        if not language_obj:
            return

        try:
            self._process_prototype_inheritance_captures(
                language_obj, root_node, module_qn
            )
        except Exception as e:
            logger.debug(lg.JS_PROTOTYPE_INHERITANCE_FAILED.format(error=e))

    def _process_prototype_inheritance_captures(
        self, language_obj, root_node, module_qn
    ):
        """
        Process captures for prototype inheritance queries.

        Args:
            language_obj: The tree-sitter language object.
            root_node: The root AST node.
            module_qn: The module qualified name.
        """
        query = Query(language_obj, cs.JS_PROTOTYPE_INHERITANCE_QUERY)
        cursor = QueryCursor(query)
        captures = cursor.captures(root_node)

        child_classes = captures.get(cs.CAPTURE_CHILD_CLASS, [])
        parent_classes = captures.get(cs.CAPTURE_PARENT_CLASS, [])

        if child_classes and parent_classes:
            for child_node, parent_node in zip(child_classes, parent_classes):
                if not child_node.text or not parent_node.text:
                    continue
                child_name = safe_decode_text(child_node)
                parent_name = safe_decode_text(parent_node)

                child_qn = f"{module_qn}{cs.SEPARATOR_DOT}{child_name}"
                parent_qn = f"{module_qn}{cs.SEPARATOR_DOT}{parent_name}"

                if child_qn not in self.class_inheritance:
                    self.class_inheritance[child_qn] = []
                if parent_qn not in self.class_inheritance[child_qn]:
                    self.class_inheritance[child_qn].append(parent_qn)

                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, child_qn),
                    cs.RelationshipType.INHERITS,
                    (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, parent_qn),
                )

                logger.debug(
                    lg.JS_PROTOTYPE_INHERITANCE.format(
                        child_qn=child_qn, parent_qn=parent_qn
                    )
                )

    def _ingest_prototype_method_assignments(
        self,
        root_node: ASTNode,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """
        Ingest methods assigned to prototypes (Class.prototype.method = function...).

        Args:
            root_node: The root AST node.
            module_qn: The module qualified name.
            language: The supported language.
            queries: Dictionary of language queries.
        """
        lang_queries = queries[language]

        language_obj = lang_queries.get(cs.QUERY_LANGUAGE)
        if not language_obj:
            return

        try:
            self._process_prototype_method_captures(language_obj, root_node, module_qn)
        except Exception as e:
            logger.debug(lg.JS_PROTOTYPE_METHODS_FAILED.format(error=e))

    def _process_prototype_method_captures(self, language_obj, root_node, module_qn):
        """
        Process captures for prototype method assignments.

        Args:
            language_obj: The tree-sitter language object.
            root_node: The root AST node.
            module_qn: The module qualified name.
        """
        method_query = Query(language_obj, cs.JS_PROTOTYPE_METHOD_QUERY)
        method_cursor = QueryCursor(method_query)
        method_captures = method_cursor.captures(root_node)

        constructor_names = method_captures.get(cs.CAPTURE_CONSTRUCTOR_NAME, [])
        method_names = method_captures.get(cs.CAPTURE_METHOD_NAME, [])
        method_functions = method_captures.get(cs.CAPTURE_METHOD_FUNCTION, [])

        for constructor_node, method_node, func_node in zip(
            constructor_names, method_names, method_functions
        ):
            constructor_name = (
                safe_decode_text(constructor_node) if constructor_node.text else None
            )
            method_name = safe_decode_text(method_node) if method_node.text else None

            if constructor_name and method_name:
                constructor_qn = f"{module_qn}{cs.SEPARATOR_DOT}{constructor_name}"
                method_qn = f"{constructor_qn}{cs.SEPARATOR_DOT}{method_name}"

                method_props = self._build_js_ts_function_props(
                    method_qn, method_name, func_node, module_qn
                )
                method_props[cs.KEY_PARENT_QN] = constructor_qn
                logger.info(
                    lg.JS_PROTOTYPE_METHOD_FOUND.format(
                        method_name=method_name, method_qn=method_qn
                    )
                )
                self.ingestor.ensure_node_batch(cs.NodeLabel.FUNCTION, method_props)

                self.function_registry[method_qn] = NodeType.FUNCTION
                self.simple_name_lookup[method_name].add(method_qn)

                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, constructor_qn),
                    cs.RelationshipType.DEFINES,
                    (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, method_qn),
                )

                logger.debug(
                    lg.JS_PROTOTYPE_METHOD_DEFINES.format(
                        constructor_qn=constructor_qn, method_qn=method_qn
                    )
                )

    def _ingest_object_literal_methods(
        self,
        root_node: ASTNode,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """
        Ingest methods defined within object literals.

        Args:
            root_node: The root AST node.
            module_qn: The module qualified name.
            language: The supported language.
            queries: Dictionary of language queries.
        """
        language_obj = get_js_ts_language_obj(language, queries)
        if not language_obj:
            return

        lang_config = queries[language].get(cs.QUERY_CONFIG)
        try:
            for query_text in [cs.JS_OBJECT_METHOD_QUERY, cs.JS_METHOD_DEF_QUERY]:
                self._process_object_method_query(
                    language_obj, query_text, root_node, module_qn, lang_config
                )
        except Exception as e:
            logger.debug(lg.JS_OBJECT_METHODS_DETECT_FAILED.format(error=e))

    def _process_object_method_query(
        self,
        language_obj,
        query_text: str,
        root_node: ASTNode,
        module_qn: str,
        lang_config,
    ) -> None:
        """
        Process a specific query for object literal methods.

        Args:
            language_obj: The tree-sitter language object.
            query_text: The query string to execute.
            root_node: The root AST node.
            module_qn: The module qualified name.
            lang_config: Language configuration.
        """
        try:
            query = Query(language_obj, query_text)
            cursor = QueryCursor(query)
            captures = cursor.captures(root_node)

            method_names = captures.get(cs.CAPTURE_METHOD_NAME, [])
            method_functions = captures.get(cs.CAPTURE_METHOD_FUNCTION, [])

            func_by_parent_pos: dict[tuple, ASTNode] = {
                (func.parent.start_point, func.parent.end_point): func
                for func in method_functions
                if func.parent
            }
            for method_name_node in method_names:
                if not method_name_node.parent:
                    continue
                pair_pos = (
                    method_name_node.parent.start_point,
                    method_name_node.parent.end_point,
                )
                method_func_node = func_by_parent_pos.get(pair_pos)
                if not method_func_node:
                    continue
                self._process_single_object_method(
                    method_name_node, method_func_node, module_qn, lang_config
                )
        except Exception as e:
            logger.debug(lg.JS_OBJECT_METHODS_PROCESS_FAILED.format(error=e))

    def _process_single_object_method(
        self,
        method_name_node: ASTNode,
        method_func_node: ASTNode,
        module_qn: str,
        lang_config,
    ) -> None:
        """
        Process a single object method found via query.

        Args:
            method_name_node: The node representing the method name.
            method_func_node: The node representing the method function/definition.
            module_qn: The module qualified name.
            lang_config: Language configuration.
        """
        if not method_name_node.text or not method_func_node:
            return

        method_name = safe_decode_text(method_name_node)
        if not method_name:
            return

        if self._handler.is_class_method(
            method_func_node
        ) and not self._handler.is_inside_method_with_object_literals(method_func_node):
            return

        method_qn = self._resolve_object_method_qn(
            method_name_node, method_func_node, module_qn, method_name, lang_config
        )

        self._register_object_method(
            method_name, method_qn, method_func_node, module_qn
        )

    def _resolve_object_method_qn(
        self,
        method_name_node: ASTNode,
        method_func_node: ASTNode,
        module_qn: str,
        method_name: str,
        lang_config,
    ) -> str:
        """
        Resolve the qualified name for an object method.

        Args:
            method_name_node: The method name node.
            method_func_node: The method function node.
            module_qn: The module qualified name.
            method_name: The extracted method name string.
            lang_config: Language configuration.

        Returns:
            The qualified name string.
        """
        if lang_config:
            method_qn = self._build_object_method_qualified_name(
                method_name_node, method_func_node, module_qn, method_name, lang_config
            )
            if method_qn is not None:
                return method_qn

        object_name = self._find_object_name_for_method(method_name_node)
        if object_name:
            return f"{module_qn}{cs.SEPARATOR_DOT}{object_name}{cs.SEPARATOR_DOT}{method_name}"
        return f"{module_qn}{cs.SEPARATOR_DOT}{method_name}"

    def _register_object_method(
        self,
        method_name: str,
        method_qn: str,
        method_func_node: ASTNode,
        module_qn: str,
    ) -> None:
        """
        Register an object method in the graph and registry.

        Args:
            method_name: The method name.
            method_qn: The method qualified name.
            method_func_node: The method AST node.
            module_qn: The module qualified name.
        """
        method_props = self._build_js_ts_function_props(
            method_qn, method_name, method_func_node, module_qn
        )
        logger.info(
            lg.JS_OBJECT_METHOD_FOUND.format(
                method_name=method_name, method_qn=method_qn
            )
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.FUNCTION, method_props)

        self.function_registry[method_qn] = NodeType.FUNCTION
        self.simple_name_lookup[method_name].add(method_qn)

        self.ingestor.ensure_relationship_batch(
            (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
            cs.RelationshipType.DEFINES,
            (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, method_qn),
        )

    def _ingest_assignment_arrow_functions(
        self,
        root_node: ASTNode,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """
        Ingest arrow functions assigned to variables or properties.

        Args:
            root_node: The root AST node.
            module_qn: The module qualified name.
            language: The supported language.
            queries: Dictionary of language queries.
        """
        if language not in cs.JS_TS_LANGUAGES:
            return

        try:
            lang_query = queries[language][cs.QUERY_LANGUAGE]
            lang_config = queries[language].get(cs.QUERY_CONFIG)

            for query_text in [
                cs.JS_OBJECT_ARROW_QUERY,
                cs.JS_ASSIGNMENT_ARROW_QUERY,
                cs.JS_ASSIGNMENT_FUNCTION_QUERY,
            ]:
                self._process_arrow_query(
                    lang_query, query_text, root_node, module_qn, lang_config
                )
        except Exception as e:
            logger.debug(lg.JS_ASSIGNMENT_ARROW_DETECT_FAILED.format(error=e))

    def _process_arrow_query(
        self,
        lang_query,
        query_text: str,
        root_node: ASTNode,
        module_qn: str,
        lang_config,
    ) -> None:
        """
        Process a specific query for arrow functions/assignments.

        Args:
            lang_query: The language query object.
            query_text: The query string.
            root_node: The root AST node.
            module_qn: The module qualified name.
            lang_config: Language configuration.
        """
        try:
            query = Query(lang_query, query_text)
            cursor = QueryCursor(query)
            captures = cursor.captures(root_node)

            method_names = captures.get(cs.CAPTURE_METHOD_NAME, [])
            member_exprs = captures.get(cs.CAPTURE_MEMBER_EXPR, [])
            arrow_functions = captures.get(cs.CAPTURE_ARROW_FUNCTION, [])
            function_exprs = captures.get(cs.CAPTURE_FUNCTION_EXPR, [])

            self._process_direct_arrow_functions(
                method_names, arrow_functions, module_qn, lang_config
            )
            self._process_member_expr_functions(
                member_exprs,
                arrow_functions,
                module_qn,
                lang_config,
                lg.JS_ASSIGNMENT_ARROW_FOUND,
            )
            self._process_member_expr_functions(
                member_exprs,
                function_exprs,
                module_qn,
                lang_config,
                lg.JS_ASSIGNMENT_FUNC_EXPR_FOUND,
            )
        except Exception as e:
            logger.debug(lg.JS_ASSIGNMENT_ARROW_QUERY_FAILED.format(error=e))

    def _process_direct_arrow_functions(
        self,
        method_names: list[ASTNode],
        arrow_functions: list[ASTNode],
        module_qn: str,
        lang_config,
    ) -> None:
        """
        Process directly assigned arrow functions (const x = () => ...).

        Args:
            method_names: List of name nodes.
            arrow_functions: List of arrow function nodes.
            module_qn: The module qualified name.
            lang_config: Language configuration.
        """
        for method_name, arrow_function in zip(method_names, arrow_functions):
            if not method_name.text or not arrow_function:
                continue

            function_name = safe_decode_text(method_name)
            if not function_name:
                continue

            function_qn = self._resolve_direct_arrow_qn(
                method_name, arrow_function, module_qn, function_name, lang_config
            )

            self._register_arrow_function(
                function_name, function_qn, arrow_function, lg.JS_OBJECT_ARROW_FOUND
            )

    def _resolve_direct_arrow_qn(
        self,
        method_name_node: ASTNode,
        _arrow_function: ASTNode,
        module_qn: str,
        function_name: str,
        lang_config,
    ) -> str:
        """
        Resolve qualified name for direct arrow function assignment.

        Args:
            method_name_node: The name node.
            _arrow_function: The arrow function node (unused).
            module_qn: The module qualified name.
            function_name: The function name.
            lang_config: Language configuration.

        Returns:
            The qualified name string.
        """
        if lang_config:
            function_qn = self._build_object_arrow_qualified_name(
                method_name_node, module_qn, function_name, lang_config
            )
            if function_qn is not None:
                return function_qn
        return f"{module_qn}{cs.SEPARATOR_DOT}{function_name}"

    def _build_object_arrow_qualified_name(
        self,
        method_name_node: ASTNode,
        module_qn: str,
        function_name: str,
        lang_config: LanguageSpec,
    ) -> str | None:
        """
        Build qualified name for an arrow function in an object/class.

        Args:
            method_name_node: The name node.
            module_qn: The module qualified name.
            function_name: The function name.
            lang_config: Language configuration.

        Returns:
            The qualified name, or None if not buildable.
        """
        skip_types = (
            cs.TS_OBJECT,
            cs.TS_VARIABLE_DECLARATOR,
            cs.TS_LEXICAL_DECLARATION,
            cs.TS_ASSIGNMENT_EXPRESSION,
            cs.TS_PAIR,
        )
        path_parts = self._js_collect_ancestor_path_parts(
            method_name_node.parent, lang_config, skip_types
        )
        return self._js_format_qualified_name(module_qn, path_parts, function_name)

    def _process_member_expr_functions(
        self,
        member_exprs: list[ASTNode],
        function_nodes: list[ASTNode],
        module_qn: str,
        lang_config,
        log_message: str,
    ) -> None:
        """
        Process functions assigned to member expressions (obj.prop = function...).

        Args:
            member_exprs: List of member expression nodes.
            function_nodes: List of function nodes.
            module_qn: The module qualified name.
            lang_config: Language configuration.
            log_message: Log message template for found functions.
        """
        for member_expr, function_node in zip(member_exprs, function_nodes):
            if not member_expr.text or not function_node:
                continue

            member_text = safe_decode_with_fallback(member_expr)
            if cs.SEPARATOR_DOT not in member_text:
                continue

            function_name = member_text.split(cs.SEPARATOR_DOT)[-1]
            function_qn = self._resolve_member_expr_qn(
                member_expr, function_node, module_qn, function_name, lang_config
            )

            self._register_arrow_function(
                function_name, function_qn, function_node, log_message
            )

    def _resolve_member_expr_qn(
        self,
        member_expr: ASTNode,
        function_node: ASTNode,
        module_qn: str,
        function_name: str,
        lang_config,
    ) -> str:
        """
        Resolve qualified name for a member expression assignment.

        Args:
            member_expr: The member expression node.
            function_node: The function node.
            module_qn: The module qualified name.
            function_name: The function name.
            lang_config: Language configuration.

        Returns:
            The qualified name string.
        """
        if lang_config:
            function_qn = self._build_assignment_arrow_function_qualified_name(
                member_expr, function_node, module_qn, function_name, lang_config
            )
            if function_qn is not None:
                return function_qn
        return f"{module_qn}{cs.SEPARATOR_DOT}{function_name}"

    def _register_arrow_function(
        self,
        function_name: str,
        function_qn: str,
        function_node: ASTNode,
        log_message: str,
    ) -> None:
        """
        Register an arrow function in the graph and registry.

        Args:
            function_name: The function name.
            function_qn: The function qualified name.
            function_node: The function AST node.
            log_message: Log message template to use.
        """
        module_qn = function_qn.rsplit(cs.SEPARATOR_DOT, 1)[0]
        function_props = self._build_js_ts_function_props(
            function_qn, function_name, function_node, module_qn
        )

        logger.debug(
            log_message.format(function_name=function_name, function_qn=function_qn)
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.FUNCTION, function_props)
        self.function_registry[function_qn] = NodeType.FUNCTION
        self.simple_name_lookup[function_name].add(function_qn)

    def _is_static_method_in_class(self, method_node: ASTNode) -> bool:
        if method_node.type == cs.TS_METHOD_DEFINITION:
            parent = method_node.parent
            if parent and parent.type == cs.TS_CLASS_BODY:
                for child in method_node.children:
                    if child.type == cs.TS_STATIC:
                        return True
        return False

    def _is_method_in_class(self, method_node: ASTNode) -> bool:
        current = method_node.parent
        while current:
            if current.type == cs.TS_CLASS_BODY:
                return True
            current = current.parent
        return False

    def _is_export_inside_function(self, node: ASTNode) -> bool:
        return self._handler.is_export_inside_function(node)

    def _find_object_name_for_method(self, method_name_node: ASTNode) -> str | None:
        current = method_name_node.parent
        while current:
            if current.type == cs.TS_VARIABLE_DECLARATOR:
                name_node = current.child_by_field_name(cs.FIELD_NAME)
                if name_node and name_node.type == cs.TS_IDENTIFIER and name_node.text:
                    return str(safe_decode_text(name_node))
            elif current.type == cs.TS_ASSIGNMENT_EXPRESSION:
                left_child = current.child_by_field_name(cs.FIELD_LEFT)
                if (
                    left_child
                    and left_child.type == cs.TS_IDENTIFIER
                    and left_child.text
                ):
                    return str(safe_decode_text(left_child))
            current = current.parent
        return None

    def _build_object_method_qualified_name(
        self,
        method_name_node: ASTNode,
        _method_func_node: ASTNode,
        module_qn: str,
        method_name: str,
        lang_config: LanguageSpec,
    ) -> str | None:
        """
        Build qualified name for a method in an object literal.

        Args:
            method_name_node: The name node.
            _method_func_node: The function node (unused).
            module_qn: The module qualified name.
            method_name: The method name.
            lang_config: Language configuration.

        Returns:
            The qualified name string, or None.
        """
        skip_types = (
            cs.TS_OBJECT,
            cs.TS_VARIABLE_DECLARATOR,
            cs.TS_LEXICAL_DECLARATION,
            cs.TS_ASSIGNMENT_EXPRESSION,
            cs.TS_PAIR,
        )
        path_parts = self._js_collect_ancestor_path_parts(
            method_name_node.parent, lang_config, skip_types
        )
        return self._js_format_qualified_name(module_qn, path_parts, method_name)

    def _build_assignment_arrow_function_qualified_name(
        self,
        member_expr: ASTNode,
        _arrow_function: ASTNode,
        module_qn: str,
        function_name: str,
        lang_config: LanguageSpec,
    ) -> str | None:
        """
        Build qualified name for an assignment arrow function.

        Args:
            member_expr: The member expression node.
            _arrow_function: The arrow function node (unused).
            module_qn: The module qualified name.
            function_name: The function name.
            lang_config: Language configuration.

        Returns:
            The qualified name string, or None.
        """
        current = member_expr.parent
        if current and current.type == cs.TS_ASSIGNMENT_EXPRESSION:
            current = current.parent

        skip_types = (cs.TS_EXPRESSION_STATEMENT, cs.TS_STATEMENT_BLOCK)
        path_parts = self._js_collect_ancestor_path_parts(
            current, lang_config, skip_types
        )
        return self._js_format_qualified_name(module_qn, path_parts, function_name)

    def _js_collect_ancestor_path_parts(
        self,
        start_node: ASTNode | None,
        lang_config: LanguageSpec,
        skip_types: tuple[str, ...],
    ) -> list[str]:
        """
        Collect path parts from ancestors to build a qualified name.

        Args:
            start_node: The starting AST node.
            lang_config: Language configuration.
            skip_types: Tuple of node types to skip.

        Returns:
            List of path parts (strings).
        """
        path_parts: list[str] = []
        current = start_node

        while current and current.type not in lang_config.module_node_types:
            if current.type in skip_types:
                current = current.parent
                continue

            if name := self._js_extract_ancestor_name(current, lang_config):
                path_parts.append(name)

            current = current.parent

        path_parts.reverse()
        return path_parts

    def _js_extract_ancestor_name(
        self, node: ASTNode, lang_config: LanguageSpec
    ) -> str | None:
        """
        Extract the name of an ancestor node for qualified name building.

        Args:
            node: The ancestor AST node.
            lang_config: Language configuration.

        Returns:
            The name string, or None.
        """
        naming_types = (
            *lang_config.function_node_types,
            *lang_config.class_node_types,
            cs.TS_METHOD_DEFINITION,
        )
        if node.type not in naming_types:
            return None

        name_node = node.child_by_field_name(cs.FIELD_NAME)
        return safe_decode_text(name_node) if name_node and name_node.text else None

    def _js_format_qualified_name(
        self, module_qn: str, path_parts: list[str], final_name: str
    ) -> str:
        if path_parts:
            return f"{module_qn}{cs.SEPARATOR_DOT}{cs.SEPARATOR_DOT.join(path_parts)}{cs.SEPARATOR_DOT}{final_name}"
        return f"{module_qn}{cs.SEPARATOR_DOT}{final_name}"
