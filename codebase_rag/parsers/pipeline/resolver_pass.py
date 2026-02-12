from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from tree_sitter import Node, Query, QueryCursor

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import (
    FunctionRegistryTrieProtocol,
    LanguageQueries,
    NodeType,
)
from codebase_rag.infrastructure.language_spec import get_language_spec_for_path
from codebase_rag.parsers.core.pre_scanner import PreScanIndex
from codebase_rag.parsers.core.utils import normalize_query_captures
from codebase_rag.services import IngestorProtocol

from .import_processor import ImportProcessor


class ResolverPass:
    """
    Executes the second pass of parsing to resolve relationships.

    This class handles:
    - Resolving imports to modules.
    - specialized relationships for JS/TS (JSX, error handlers, state mutations).
    - Linking imported symbols to their definitions.
    """

    def __init__(
        self,
        ingestor: IngestorProtocol,
        repo_path: Path,
        project_name: str,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        function_registry: FunctionRegistryTrieProtocol,
        import_processor: ImportProcessor,
        module_qn_to_file_path: dict[str, Path],
        pre_scan_index: PreScanIndex | None = None,
    ) -> None:
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.queries = queries
        self.function_registry = function_registry
        self.import_processor = import_processor
        self.module_qn_to_file_path = module_qn_to_file_path
        self.pre_scan_index = pre_scan_index
        self._compiled_queries: dict[tuple[str, str], Query] = {}

    def process_ast_cache(
        self, ast_items: Iterable[tuple[Path, tuple[object, cs.SupportedLanguage]]]
    ) -> None:
        """
        Process cached AST items to resolve imports and relationships.

        Args:
            ast_items (Iterable): Iterable of (file_path, (root_node, language)) tuples.
        """
        self._resolve_imports()
        self._resolve_import_symbols()
        for file_path, (root_node, language) in ast_items:
            self._process_js_ts_relations(file_path, root_node, language)

    def _resolve_imports(self) -> None:
        """
        Resolve imports to their source modules and create relationships.
        """
        for module_qn, mappings in self.import_processor.import_mapping.items():
            if not mappings:
                continue
            file_path = self.module_qn_to_file_path.get(module_qn)
            if not file_path:
                continue
            lang_spec = get_language_spec_for_path(file_path)
            if not lang_spec:
                continue
            language = lang_spec.language
            for full_name in mappings.values():
                module_path = self.import_processor._resolve_module_path(
                    cast(Any, full_name),
                    module_qn,
                    cast(cs.SupportedLanguage, language),
                )
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                    cs.RelationshipType.RESOLVES_IMPORT,
                    (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_path),
                )

            import_nodes = self.import_processor.import_nodes_by_module.get(
                module_qn, []
            )
            if import_nodes:
                for import_qn in import_nodes:
                    for local_name, full_name in mappings.items():
                        module_path = self.import_processor._resolve_module_path(
                            cast(Any, full_name),
                            module_qn,
                            cast(cs.SupportedLanguage, language),
                        )
                        self.ingestor.ensure_relationship_batch(
                            (cs.NodeLabel.IMPORT, cs.KEY_QUALIFIED_NAME, import_qn),
                            cs.RelationshipType.RESOLVES_IMPORT,
                            (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_path),
                            {
                                cs.KEY_IMPORTED_SYMBOL: local_name,
                                cs.KEY_LOCAL_NAME: local_name,
                            },
                        )

    def _process_js_ts_relations(
        self,
        file_path: Path,
        root_node: object,
        language: cs.SupportedLanguage,
    ) -> None:
        """
        Process specific relationships for JavaScript and TypeScript files.

        Args:
            file_path (Path): Path to the source file.
            root_node (object): Root AST node.
            language (cs.SupportedLanguage): Programming language.
        """
        if language not in {cs.SupportedLanguage.JS, cs.SupportedLanguage.TS}:
            return

        module_qn = self._module_qn_for_path(file_path)

        self._link_jsx_components(root_node, module_qn, language)
        self._link_error_handlers(root_node, module_qn, language)
        self._link_state_mutations(root_node, module_qn, language)

    def _resolve_import_symbols(self) -> None:
        """
        Link imported symbols to their definitions in other modules.
        """
        if not getattr(self.import_processor, "import_symbol_links", None):
            return

        for link in self.import_processor.import_symbol_links:
            import_qn = link.get("import_qn")
            full_name = link.get("full_name")
            local_name = link.get("local_name")
            is_default = bool(link.get("is_default"))
            is_namespace = bool(link.get("is_namespace"))

            if not import_qn or not full_name:
                continue
            if not full_name.startswith(f"{self.project_name}{cs.SEPARATOR_DOT}"):
                continue

            target = self._resolve_symbol_target(full_name)
            if not target:
                continue
            target_label, target_qn = target

            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.IMPORT, cs.KEY_QUALIFIED_NAME, import_qn),
                cs.RelationshipType.RESOLVES_IMPORT,
                (target_label, cs.KEY_QUALIFIED_NAME, target_qn),
                {
                    cs.KEY_IMPORTED_SYMBOL: local_name or "",
                    cs.KEY_IS_DEFAULT_IMPORT: is_default,
                    cs.KEY_IS_NAMESPACE_IMPORT: is_namespace,
                },
            )

    def _link_jsx_components(
        self, root_node: object, module_qn: str, language: cs.SupportedLanguage
    ) -> None:
        """
        Identify and link JSX components used in the module.

        Args:
            root_node (object): Root AST node.
            module_qn (str): Module qualified name.
            language (cs.SupportedLanguage): Programming language.
        """
        query = self._get_query(
            language,
            "(jsx_element (jsx_opening_element name: (_) @tag)) (jsx_self_closing_element name: (_) @tag)",
            "jsx_components",
        )
        if not query:
            return

        captures_fn = getattr(query, "captures", None)
        if captures_fn is None:
            return
        captures = normalize_query_captures(captures_fn(root_node))
        tags = captures.get("tag", [])
        if not tags:
            return

        for tag in tags:
            tag_name = tag.text.decode(cs.ENCODING_UTF8) if tag.text else ""
            if not tag_name or not tag_name[0].isupper():
                continue
            component_qn = self._ensure_component_node(module_qn, tag_name)
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                cs.RelationshipType.USES_COMPONENT,
                (cs.NodeLabel.COMPONENT, cs.KEY_QUALIFIED_NAME, component_qn),
                {cs.KEY_RELATION_TYPE: "jsx"},
            )

    def _link_error_handlers(
        self, root_node: object, module_qn: str, language: cs.SupportedLanguage
    ) -> None:
        """
        Identify and link error handling blocks (try/catch).

        Args:
            root_node (object): Root AST node.
            module_qn (str): Module qualified name.
            language (cs.SupportedLanguage): Programming language.
        """
        query = self._get_query(language, "(try_statement) @try", "handles_error")
        if not query:
            return

        captures_fn = getattr(query, "captures", None)
        if captures_fn is None:
            return
        captures = normalize_query_captures(captures_fn(root_node))
        if not captures:
            return

        target_label, target_qn = self._ensure_placeholder_function(
            "error_handler", "error_handler"
        )
        self.ingestor.ensure_relationship_batch(
            (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
            cs.RelationshipType.HANDLES_ERROR,
            (target_label, cs.KEY_QUALIFIED_NAME, target_qn),
            {cs.KEY_RELATION_TYPE: "try_catch"},
        )

    def _link_state_mutations(
        self, root_node: object, module_qn: str, language: cs.SupportedLanguage
    ) -> None:
        """
        Identify and link state mutations (assignments, updates).

        Args:
            root_node (object): Root AST node.
            module_qn (str): Module qualified name.
            language (cs.SupportedLanguage): Programming language.
        """
        query = self._get_query(
            language,
            "(assignment_expression) @assign (update_expression) @update",
            "mutates_state",
        )
        if not query:
            return

        cursor = QueryCursor(query)
        captures = normalize_query_captures(cursor.captures(cast(Node, root_node)))
        if not captures:
            return

        target_label, target_qn = self._ensure_placeholder_function(
            "state_mutation", "state_mutation"
        )
        self.ingestor.ensure_relationship_batch(
            (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
            cs.RelationshipType.MUTATES_STATE,
            (target_label, cs.KEY_QUALIFIED_NAME, target_qn),
            {cs.KEY_RELATION_TYPE: "assignment"},
        )

    def _find_component_target(self, name: str) -> tuple[str | None, str | None]:
        """
        Find the target qualified name and type for a component name.

        Args:
            name (str): The component name.

        Returns:
            tuple[str | None, str | None]: (qualified_name, type) or (None, None).
        """
        candidates = self.function_registry.find_ending_with(name)
        if not candidates:
            return None, None
        for qn in candidates:
            node_type = self.function_registry.get(qn)
            if node_type in {NodeType.CLASS, NodeType.FUNCTION, NodeType.METHOD}:
                return qn, node_type.value
        qn = candidates[0]
        node_type = self.function_registry.get(qn)
        return qn, node_type.value if node_type else None

    def _ensure_component_node(self, module_qn: str, tag_name: str) -> str:
        """
        Ensure a component node exists for a given HTML/JSX tag.

        Args:
            module_qn (str): Module qualified name.
            tag_name (str): The tag name.

        Returns:
            str: The qualified name of the component node.
        """
        component_qn = f"{module_qn}{cs.SEPARATOR_DOT}component.{tag_name}"
        namespace = (
            module_qn.rsplit(cs.SEPARATOR_DOT, 1)[0]
            if cs.SEPARATOR_DOT in module_qn
            else None
        )
        component_props = {
            cs.KEY_QUALIFIED_NAME: component_qn,
            cs.KEY_NAME: tag_name,
            cs.KEY_FRAMEWORK: "component",
            cs.KEY_MODULE_QN: module_qn,
            cs.KEY_SYMBOL_KIND: cs.NodeLabel.COMPONENT.value.lower(),
            cs.KEY_PARENT_QN: module_qn,
        }
        if namespace:
            component_props[cs.KEY_NAMESPACE] = namespace
            component_props[cs.KEY_PACKAGE] = namespace
        self.ingestor.ensure_node_batch(cs.NodeLabel.COMPONENT, component_props)
        return component_qn

    def _ensure_placeholder_function(
        self, name: str, framework_tag: str
    ) -> tuple[str, str]:
        """
        Ensure a placeholder function node exists for framework features.

        Args:
            name (str): Name of the placeholder.
            framework_tag (str): Framework metadata tag.

        Returns:
            tuple[str, str]: (Label, Qualified Name).
        """
        normalized = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")
        if not normalized:
            normalized = "unknown"
        placeholder_qn = f"{self.project_name}{cs.SEPARATOR_DOT}framework.{framework_tag}.{normalized}"
        namespace = (
            placeholder_qn.rsplit(cs.SEPARATOR_DOT, 1)[0]
            if cs.SEPARATOR_DOT in placeholder_qn
            else None
        )
        placeholder_props = {
            cs.KEY_QUALIFIED_NAME: placeholder_qn,
            cs.KEY_NAME: name,
            cs.KEY_DECORATORS: [],
            cs.KEY_IS_EXTERNAL: True,
            cs.KEY_IS_PLACEHOLDER: True,
            cs.KEY_FRAMEWORK: framework_tag,
            cs.KEY_FRAMEWORK_METADATA: json.dumps(
                {"origin": "placeholder", "reason": framework_tag},
                ensure_ascii=False,
            ),
            cs.KEY_SYMBOL_KIND: cs.NodeLabel.FUNCTION.value.lower(),
        }
        if namespace:
            placeholder_props[cs.KEY_MODULE_QN] = namespace
            placeholder_props[cs.KEY_PARENT_QN] = namespace
            placeholder_props[cs.KEY_NAMESPACE] = namespace
            placeholder_props[cs.KEY_PACKAGE] = namespace
        self.ingestor.ensure_node_batch(cs.NodeLabel.FUNCTION, placeholder_props)
        return cs.NodeLabel.FUNCTION, placeholder_qn

    def _get_query(
        self, language: cs.SupportedLanguage, query_text: str, key: str
    ) -> Query | None:
        """
        Compile or retrieve a cached Tree-sitter query.

        Args:
            language (cs.SupportedLanguage): Programming language.
            query_text (str): The S-expression query string.
            key (str): Unique key for caching.

        Returns:
            Query | None: Compiled query or None if compilation fails.
        """
        cache_key = (language.value, key)
        if cache_key in self._compiled_queries:
            return self._compiled_queries[cache_key]
        lang_queries = self.queries.get(language)
        if not lang_queries:
            return None
        language_obj = lang_queries.get("language")
        if language_obj is None:
            return None
        try:
            compiled = Query(language_obj, query_text)
        except Exception:
            return None
        self._compiled_queries[cache_key] = compiled
        return compiled

    def _resolve_symbol_target(self, full_name: str) -> tuple[str, str] | None:
        """
        Resolve a fully qualified symbol name to its target node label and QN.

        Args:
            full_name (str): The fully qualified name.

        Returns:
            tuple[str, str] | None: (Label, Qualified Name) or None if not found.
        """
        if full_name.endswith(cs.IMPORT_DEFAULT_SUFFIX):
            module_path = full_name[: -len(cs.IMPORT_DEFAULT_SUFFIX)]
            return cs.NodeLabel.MODULE, module_path

        candidates = self.function_registry.find_ending_with(
            full_name.split(cs.SEPARATOR_DOT)[-1]
        )
        for qn in candidates:
            if qn == full_name:
                node_type = self.function_registry.get(qn)
                if node_type:
                    return node_type.value, qn

        if self.pre_scan_index:
            symbol = full_name.split(cs.SEPARATOR_DOT)[-1]
            module_path = full_name.rsplit(cs.SEPARATOR_DOT, 1)[0]
            if symbol in self.pre_scan_index.module_to_symbols.get(module_path, set()):
                return cs.NodeLabel.MODULE, module_path
            modules = self.pre_scan_index.symbol_to_modules.get(symbol)
            if modules:
                return cs.NodeLabel.MODULE, sorted(modules)[0]
        return None

    def _module_qn_for_path(self, file_path: Path) -> str:
        """
        Generate module qualified name for a file path.

        Args:
            file_path (Path): Path to the file.

        Returns:
            str: Fully qualified module name.
        """
        relative_path = file_path.relative_to(self.repo_path)
        parts = list(relative_path.with_suffix("").parts)
        if file_path.name in (cs.INIT_PY, cs.MOD_RS):
            parts = list(relative_path.parent.parts)
        return cs.SEPARATOR_DOT.join([self.project_name, *parts])
