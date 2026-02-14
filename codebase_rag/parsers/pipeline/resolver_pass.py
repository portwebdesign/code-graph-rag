"""
This module defines the ResolverPass, the second major phase of the parsing pipeline.

After the initial AST processing and definition extraction, this pass is responsible for
resolving connections and relationships between different code elements across files.
It handles resolving import statements to their corresponding modules, linking imported
symbols to their definitions, and identifying language-specific patterns like JSX
component usage in JavaScript/TypeScript.
"""

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
    Executes the second pass of parsing to resolve relationships between code entities.

    This pass builds upon the initial AST scan by connecting different parts of the
    codebase. It is responsible for:
    - Resolving module-level imports to their source files.
    - Linking specific imported symbols (functions, classes) to their definitions.
    - Identifying and creating relationships for language-specific constructs, such as
      JSX components, error handlers (try/catch), and state mutations in JS/TS.

    Attributes:
        ingestor (IngestorProtocol): The service for writing data to the graph.
        repo_path (Path): The root path of the repository being parsed.
        project_name (str): The name of the project.
        queries (dict[cs.SupportedLanguage, LanguageQueries]): Compiled tree-sitter queries.
        function_registry (FunctionRegistryTrieProtocol): A trie containing all found functions/classes.
        import_processor (ImportProcessor): The processor that handled import statements.
        module_qn_to_file_path (dict[str, Path]): Mapping from module QN to file path.
        pre_scan_index (PreScanIndex | None): Optional index from a pre-scan pass for faster lookups.
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
        """
        Initializes the ResolverPass.

        Args:
            ingestor (IngestorProtocol): The service for writing data to the graph.
            repo_path (Path): The root path of the repository.
            project_name (str): The name of the project.
            queries (dict[cs.SupportedLanguage, LanguageQueries]): Language-specific queries.
            function_registry (FunctionRegistryTrieProtocol): Registry of all functions and classes.
            import_processor (ImportProcessor): Processor containing import data from the first pass.
            module_qn_to_file_path (dict[str, Path]): Mapping from module qualified names to file paths.
            pre_scan_index (PreScanIndex | None): Optional pre-scan index for faster symbol resolution.
        """
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
        Process cached AST items to resolve imports and other cross-file relationships.

        This is the main entry point for the pass. It orchestrates the resolution of
        imports and then iterates through each file's AST to find and process
        language-specific relationships.

        Args:
            ast_items (Iterable): An iterable of (file_path, (root_node, language)) tuples
                                  from the AST cache.
        """
        self._resolve_imports()
        self._resolve_import_symbols()
        for file_path, (root_node, language) in ast_items:
            self._process_js_ts_relations(file_path, root_node, language)

    def _resolve_imports(self) -> None:
        """
        Resolve module-level imports to their source modules and create relationships in the graph.

        This method iterates through the import mappings collected by the `ImportProcessor`
        and creates `RESOLVES_IMPORT` relationships between modules and also between
        individual import nodes and the modules they resolve to.
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
            supported_language = cast(cs.SupportedLanguage, language)
            for full_name in mappings.values():
                module_path = self.import_processor._resolve_module_path(
                    cast(Any, full_name),
                    module_qn,
                    supported_language,
                )
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                    cs.RelationshipType.RESOLVES_IMPORT,
                    (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_path),
                    {
                        "import_kind": "static",
                        "confidence": 0.95,
                        "source_parser": f"tree-sitter-{supported_language.value}",
                    },
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
                            supported_language,
                        )
                        self.ingestor.ensure_relationship_batch(
                            (cs.NodeLabel.IMPORT, cs.KEY_QUALIFIED_NAME, import_qn),
                            cs.RelationshipType.RESOLVES_IMPORT,
                            (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_path),
                            {
                                cs.KEY_IMPORTED_SYMBOL: local_name,
                                cs.KEY_LOCAL_NAME: local_name,
                                "import_kind": "static",
                                "alias": local_name,
                                "confidence": 0.95,
                                "source_parser": f"tree-sitter-{supported_language.value}",
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

        This includes linking JSX components, error handlers, and state mutations.

        Args:
            file_path (Path): Path to the source file.
            root_node (object): Root AST node of the file.
            language (cs.SupportedLanguage): The programming language of the file.
        """
        if language not in {cs.SupportedLanguage.JS, cs.SupportedLanguage.TS}:
            return

        module_qn = self._module_qn_for_path(file_path)

        self._link_jsx_components(root_node, module_qn, language)
        self._link_error_handlers(root_node, module_qn, language)
        self._link_state_mutations(root_node, module_qn, language)

    def _resolve_import_symbols(self) -> None:
        """
        Link imported symbols (e.g., functions, classes) to their definitions in other modules.

        This method uses the `import_symbol_links` from the `ImportProcessor` to create
        `RESOLVES_IMPORT` relationships from an `Import` node to the specific `Function`
        or `Class` node it points to.
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
                    "import_kind": "static",
                    "alias": local_name or "",
                    "confidence": 0.9,
                    "source_parser": "resolver_pass",
                },
            )

    def _link_jsx_components(
        self, root_node: object, module_qn: str, language: cs.SupportedLanguage
    ) -> None:
        """
        Identify and link JSX components used within a module.

        It queries the AST for JSX tags (e.g., `<MyComponent />`) and creates
        `USES_COMPONENT` relationships from the module to the corresponding component nodes.

        Args:
            root_node (object): The root AST node for the file.
            module_qn (str): The qualified name of the module being processed.
            language (cs.SupportedLanguage): The programming language.
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
        Identify and link error handling blocks (e.g., try-catch) in the module.

        This creates a `HANDLES_ERROR` relationship from the module to a generic
        placeholder function representing error handling logic.

        Args:
            root_node (object): The root AST node for the file.
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The programming language.
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
        Identify and link state mutations (e.g., assignments, updates) in the module.

        This creates a `MUTATES_STATE` relationship from the module to a generic
        placeholder function representing state mutation logic.

        Args:
            root_node (object): The root AST node for the file.
            module_qn (str): The qualified name of the module.
            language (cs.SupportedLanguage): The programming language.
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
        Find the target qualified name and node type for a component name.

        It searches the function registry for a class, function, or method that
        matches the component name.

        Args:
            name (str): The component name (e.g., 'MyComponent').

        Returns:
            A tuple containing the qualified name and node type (e.g., 'function', 'class')
            of the component, or (None, None) if not found.
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
        Ensure a `Component` node exists for a given HTML/JSX tag and returns its QN.

        If the node doesn't exist, it will be created. This is used to represent
        the usage of a component within a module.

        Args:
            module_qn (str): The qualified name of the module where the component is used.
            tag_name (str): The tag name of the component (e.g., 'MyComponent').

        Returns:
            The qualified name of the component node.
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
        Ensure a placeholder `Function` node exists for abstract framework features.

        This is used for concepts like 'error_handler' or 'state_mutation' that don't
        correspond to a single, concrete function in the user's code but represent
        a capability.

        Args:
            name (str): A descriptive name for the placeholder (e.g., 'error_handler').
            framework_tag (str): A tag to categorize the framework feature.

        Returns:
            A tuple containing the node label (`Function`) and the qualified name of the
            placeholder node.
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
        Compile and cache, or retrieve a cached, Tree-sitter query.

        This avoids recompiling the same query multiple times.

        Args:
            language (cs.SupportedLanguage): The programming language for the query.
            query_text (str): The S-expression query string.
            key (str): A unique key to identify this query for caching purposes.

        Returns:
            A compiled `Query` object, or `None` if compilation fails.
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
        Resolve a fully qualified symbol name to its target node label and qualified name.

        It first checks the function registry for a direct match. If not found, it may
        consult the pre-scan index as a fallback to find the module containing the symbol.

        Args:
            full_name (str): The fully qualified name of the symbol to resolve.

        Returns:
            A tuple of (node_label, qualified_name) if found, otherwise `None`.
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
        Generate the module qualified name for a given file path.

        This converts a file system path into a language-agnostic qualified name
        (e.g., `project_name.folder.file`).

        Args:
            file_path (Path): The absolute path to the file.

        Returns:
            The fully qualified module name as a string.
        """
        relative_path = file_path.relative_to(self.repo_path)
        parts = list(relative_path.with_suffix("").parts)
        if file_path.name in (cs.INIT_PY, cs.MOD_RS):
            parts = list(relative_path.parent.parts)
        return cs.SEPARATOR_DOT.join([self.project_name, *parts])
