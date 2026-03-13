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
from codebase_rag.parsers.core.utils import (
    normalize_query_captures,
    safe_decode_with_fallback,
)
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
        self._components_by_module: dict[str, dict[str, str]] = {}
        self._default_component_by_module: dict[str, str] = {}
        self._component_source_nodes: dict[str, Node] = {}
        self._component_prop_aliases: dict[str, dict[str, str]] = {}
        self._component_prop_containers: dict[str, set[str]] = {}
        self._component_props: dict[str, list[dict[str, object]]] = {}
        self._component_hooks: dict[str, list[str]] = {}
        self._components_enriched: set[str] = set()

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
        ast_cache_items = list(ast_items)
        self._resolve_imports()
        self._resolve_import_symbols()
        for file_path, (root_node, language) in ast_cache_items:
            self._extract_js_ts_components(file_path, root_node, language)
        for file_path, (root_node, language) in ast_cache_items:
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
                for link in self.import_processor.import_symbol_links:
                    import_qn = str(link.get("import_qn") or "").strip()
                    link_module_qn = str(link.get("module_qn") or "").strip()
                    language_value = str(link.get("language") or "").strip()
                    full_name = str(link.get("full_name") or "").strip()
                    local_name = str(link.get("local_name") or "").strip()
                    if (
                        not import_qn
                        or import_qn not in import_nodes
                        or link_module_qn != module_qn
                        or not full_name
                    ):
                        continue
                    try:
                        import_language = cs.SupportedLanguage(language_value)
                    except ValueError:
                        import_language = supported_language

                    module_path = self.import_processor._resolve_module_path(
                        full_name,
                        module_qn,
                        import_language,
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
                            "source_parser": f"tree-sitter-{import_language.value}",
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

        self._enrich_component_graph(module_qn)
        self._link_jsx_components(root_node, module_qn, language)
        self._link_next_component_endpoints(file_path, module_qn)
        self._link_component_requests(file_path, module_qn)
        self._link_error_handlers(root_node, module_qn, language)
        self._link_state_mutations(root_node, module_qn, language)

    def _extract_js_ts_components(
        self,
        file_path: Path,
        root_node: object,
        language: cs.SupportedLanguage,
    ) -> None:
        if language not in {cs.SupportedLanguage.JS, cs.SupportedLanguage.TS}:
            return

        module_qn = self._module_qn_for_path(file_path)
        framework = "next" if self._next_special_kind(file_path) else "react"

        for node in self._walk_nodes(cast(Node, root_node)):
            if node.type == cs.TS_FUNCTION_DECLARATION:
                name_node = node.child_by_field_name(cs.FIELD_NAME)
                component_name = (
                    safe_decode_with_fallback(name_node) if name_node else ""
                )
                if self._is_component_function(component_name, node):
                    component_qn = self._ensure_component_node(
                        module_qn=module_qn,
                        component_name=component_name,
                        source_qn=self._resolve_local_component_symbol(
                            module_qn, component_name
                        ),
                        framework=framework,
                    )
                    self._component_source_nodes[component_qn] = node
            elif node.type == cs.TS_LEXICAL_DECLARATION:
                for declarator in self._lexical_component_candidates(node):
                    component_name = cast(str, declarator["name"])
                    component_qn = self._ensure_component_node(
                        module_qn=module_qn,
                        component_name=component_name,
                        source_qn=self._resolve_local_component_symbol(
                            module_qn, component_name
                        ),
                        framework=framework,
                    )
                    self._component_source_nodes[component_qn] = cast(
                        Node, declarator["node"]
                    )
            elif node.type in {
                cs.TS_CLASS_DECLARATION,
                cs.TS_ABSTRACT_CLASS_DECLARATION,
            }:
                name_node = node.child_by_field_name(cs.FIELD_NAME)
                component_name = (
                    safe_decode_with_fallback(name_node) if name_node else ""
                )
                if self._is_component_class(component_name, node):
                    component_qn = self._ensure_component_node(
                        module_qn=module_qn,
                        component_name=component_name,
                        source_qn=self._resolve_local_component_symbol(
                            module_qn, component_name
                        ),
                        framework=framework,
                    )
                    self._component_source_nodes[component_qn] = node

        default_export_name = self._extract_default_export_name(cast(Node, root_node))
        if default_export_name:
            default_component_qn = self._components_by_module.get(module_qn, {}).get(
                default_export_name
            )
            if default_component_qn:
                self._default_component_by_module[module_qn] = default_component_qn
        elif self._next_special_kind(file_path):
            components = list(self._components_by_module.get(module_qn, {}).values())
            if len(components) == 1:
                self._default_component_by_module[module_qn] = components[0]

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

            if not isinstance(import_qn, str) or not isinstance(full_name, str):
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
        del language

        for node in self._walk_nodes(cast(Node, root_node)):
            if node.type not in {"jsx_opening_element", "jsx_self_closing_element"}:
                continue
            name_node = node.child_by_field_name(cs.FIELD_NAME)
            if not name_node:
                continue
            tag_name = safe_decode_with_fallback(name_node)
            if not tag_name or not self._looks_like_component_reference(tag_name):
                continue
            component_qn = self._resolve_component_reference(module_qn, tag_name)
            if not component_qn:
                component_qn = self._ensure_component_node(
                    module_qn=module_qn,
                    component_name=tag_name.split(cs.SEPARATOR_DOT)[-1],
                    source_qn=None,
                    framework="react",
                )
            source_component_qn = self._find_enclosing_component_qn(
                name_node, module_qn
            )
            if source_component_qn and source_component_qn == component_qn:
                continue
            source_node = (
                (
                    cs.NodeLabel.COMPONENT,
                    cs.KEY_QUALIFIED_NAME,
                    source_component_qn,
                )
                if source_component_qn
                else (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn)
            )
            rel_props = {cs.KEY_RELATION_TYPE: "jsx"}
            rel_props.update(
                self._extract_jsx_usage_properties(
                    node,
                    source_component_qn,
                )
            )
            self.ingestor.ensure_relationship_batch(
                source_node,
                cs.RelationshipType.USES_COMPONENT,
                (cs.NodeLabel.COMPONENT, cs.KEY_QUALIFIED_NAME, component_qn),
                rel_props,
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

    def _ensure_component_node(
        self,
        module_qn: str,
        component_name: str,
        source_qn: str | None,
        framework: str,
    ) -> str:
        """
        Ensure a `Component` node exists for a given HTML/JSX tag and returns its QN.

        If the node doesn't exist, it will be created. This is used to represent
        the usage of a component within a module.

        Args:
            module_qn (str): The qualified name of the module where the component is used.
            component_name (str): The tag name of the component (e.g., 'MyComponent').
            source_qn (str | None): The underlying function/class qualified name.
            framework (str): Framework tag, typically `react` or `next`.

        Returns:
            The qualified name of the component node.
        """
        existing = self._components_by_module.get(module_qn, {}).get(component_name)
        if existing:
            return existing

        component_qn = (
            source_qn
            if source_qn
            else f"{module_qn}{cs.SEPARATOR_DOT}component.{component_name}"
        )
        namespace = (
            module_qn.rsplit(cs.SEPARATOR_DOT, 1)[0]
            if cs.SEPARATOR_DOT in module_qn
            else None
        )
        file_path = self.module_qn_to_file_path.get(module_qn)
        component_props = {
            cs.KEY_QUALIFIED_NAME: component_qn,
            cs.KEY_NAME: component_name,
            cs.KEY_FRAMEWORK: framework,
            cs.KEY_MODULE_QN: module_qn,
            cs.KEY_SYMBOL_KIND: cs.NodeLabel.COMPONENT.value.lower(),
            cs.KEY_PARENT_QN: module_qn,
        }
        if file_path:
            relative_path = file_path.relative_to(self.repo_path).as_posix()
            component_props[cs.KEY_PATH] = relative_path
            component_props[cs.KEY_REPO_REL_PATH] = relative_path
            component_props[cs.KEY_ABS_PATH] = file_path.resolve().as_posix()
        if namespace:
            component_props[cs.KEY_NAMESPACE] = namespace
            component_props[cs.KEY_PACKAGE] = namespace
        self.ingestor.ensure_node_batch(cs.NodeLabel.COMPONENT, component_props)
        self.ingestor.ensure_relationship_batch(
            (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
            cs.RelationshipType.DEFINES,
            (cs.NodeLabel.COMPONENT, cs.KEY_QUALIFIED_NAME, component_qn),
            {
                cs.KEY_RELATION_TYPE: framework,
            },
        )
        self._components_by_module.setdefault(module_qn, {})[component_name] = (
            component_qn
        )
        return component_qn

    def _walk_nodes(self, node: Node) -> Iterable[Node]:
        yield node
        for child in node.children:
            yield from self._walk_nodes(child)

    @staticmethod
    def _is_pascal_case(name: str) -> bool:
        return bool(name) and name[0].isupper() and "_" not in name

    def _looks_like_component_reference(self, tag_name: str) -> bool:
        first_segment = tag_name.split(cs.SEPARATOR_DOT, 1)[0]
        return self._is_pascal_case(first_segment)

    def _node_contains_jsx(self, node: Node) -> bool:
        return any(
            child.type in {"jsx_element", "jsx_self_closing_element", "jsx_fragment"}
            for child in self._walk_nodes(node)
        )

    def _is_component_function(self, name: str, node: Node) -> bool:
        return self._is_pascal_case(name) and self._node_contains_jsx(node)

    def _is_component_class(self, name: str, node: Node) -> bool:
        if not self._is_pascal_case(name):
            return False
        text = safe_decode_with_fallback(node)
        if re.search(r"extends\s+(?:React\.)?(?:PureComponent|Component)", text):
            return True
        for child in self._walk_nodes(node):
            if child.type == cs.TS_METHOD_DEFINITION:
                method_name = child.child_by_field_name(cs.FIELD_NAME)
                if (
                    method_name
                    and safe_decode_with_fallback(method_name) == "render"
                    and self._node_contains_jsx(child)
                ):
                    return True
        return False

    def _lexical_component_candidates(self, node: Node) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        for child in self._walk_nodes(node):
            if child.type != cs.TS_VARIABLE_DECLARATOR:
                continue
            name_node = child.child_by_field_name(cs.FIELD_NAME)
            value_node = child.child_by_field_name(cs.FIELD_VALUE)
            component_name = safe_decode_with_fallback(name_node) if name_node else ""
            if (
                not component_name
                or not value_node
                or not self._is_pascal_case(component_name)
            ):
                continue
            if value_node.type in {"arrow_function", "function_expression"} and (
                self._node_contains_jsx(value_node)
            ):
                candidates.append({"name": component_name, "node": child})
                continue
            if value_node.type == "call_expression":
                value_text = safe_decode_with_fallback(value_node)
                if re.search(
                    r"\b(?:React\.)?(?:memo|forwardRef)\s*\(", value_text
                ) and (self._node_contains_jsx(value_node)):
                    candidates.append({"name": component_name, "node": child})
        return candidates

    def _enrich_component_graph(self, module_qn: str) -> None:
        if module_qn in self._components_enriched:
            return
        for component_qn in self._components_by_module.get(module_qn, {}).values():
            source_node = self._component_source_nodes.get(component_qn)
            if not source_node:
                continue
            props, aliases, containers = self._extract_component_prop_model(source_node)
            self._component_prop_aliases[component_qn] = aliases
            self._component_prop_containers[component_qn] = containers
            self._component_props[component_qn] = props
            self._ingest_component_props(component_qn, props)
            hooks = self._link_component_hooks(component_qn, module_qn, source_node)
            self._component_hooks[component_qn] = hooks
            self.ingestor.ensure_node_batch(
                cs.NodeLabel.COMPONENT,
                {
                    cs.KEY_QUALIFIED_NAME: component_qn,
                    cs.KEY_PROPS: props,
                    "hooks_used": hooks,
                },
            )
        self._components_enriched.add(module_qn)

    def _extract_component_prop_model(
        self, component_node: Node
    ) -> tuple[list[dict[str, object]], dict[str, str], set[str]]:
        component_callable = self._component_callable_node(component_node)
        prop_defs: dict[str, dict[str, object]] = {}
        alias_map: dict[str, str] = {}
        container_names: set[str] = set()

        params_node = component_callable.child_by_field_name(cs.FIELD_PARAMETERS)
        if params_node:
            first_param = self._first_named_child(params_node)
            if first_param:
                self._collect_prop_bindings(
                    first_param,
                    prop_defs,
                    alias_map,
                    container_names,
                    prefix="",
                )

        body_node = (
            component_callable.child_by_field_name(cs.FIELD_BODY) or component_callable
        )
        self._collect_body_prop_destructuring(
            body_node,
            prop_defs,
            alias_map,
            container_names,
        )
        self._collect_prop_usage(
            body_node,
            prop_defs,
            alias_map,
            container_names,
        )

        props = [
            {
                "name": prop_name,
                "is_optional": bool(data.get("is_optional")),
                "is_used": bool(data.get("is_used")),
            }
            for prop_name, data in sorted(prop_defs.items())
        ]
        return props, alias_map, container_names

    def _collect_prop_bindings(
        self,
        node: Node,
        prop_defs: dict[str, dict[str, object]],
        alias_map: dict[str, str],
        container_names: set[str],
        prefix: str,
        is_optional: bool = False,
    ) -> None:
        if node.type in {
            "required_parameter",
            "optional_parameter",
            "formal_parameter",
        }:
            target = self._first_named_child(node)
            if target:
                self._collect_prop_bindings(
                    target,
                    prop_defs,
                    alias_map,
                    container_names,
                    prefix,
                    is_optional=is_optional,
                )
            return
        if node.type == "assignment_pattern":
            target = self._first_named_child(node)
            if target:
                self._collect_prop_bindings(
                    target,
                    prop_defs,
                    alias_map,
                    container_names,
                    prefix,
                    is_optional=True,
                )
            return
        if node.type == "object_assignment_pattern":
            target = self._first_named_child(node)
            if target:
                self._collect_prop_bindings(
                    target,
                    prop_defs,
                    alias_map,
                    container_names,
                    prefix,
                    is_optional=True,
                )
            return
        if node.type == "shorthand_property_identifier_pattern":
            prop_name = safe_decode_with_fallback(node)
            full_name = self._join_prop_path(prefix, prop_name)
            prop_defs.setdefault(full_name, {"is_optional": is_optional})
            alias_map[prop_name] = full_name
            return
        if node.type == cs.TS_IDENTIFIER:
            identifier = safe_decode_with_fallback(node)
            if prefix:
                prop_defs.setdefault(prefix, {"is_optional": is_optional})
                alias_map[identifier] = prefix
            elif identifier:
                container_names.add(identifier)
            return
        if node.type == "object_pattern":
            for child in node.children:
                if child.type == "shorthand_property_identifier_pattern":
                    prop_name = safe_decode_with_fallback(child)
                    full_name = self._join_prop_path(prefix, prop_name)
                    prop_defs.setdefault(full_name, {"is_optional": is_optional})
                    alias_map[prop_name] = full_name
                elif child.type == "pair_pattern":
                    key_node = child.children[0] if child.children else None
                    value_node = child.children[-1] if child.children else None
                    if not key_node or not value_node:
                        continue
                    key_name = safe_decode_with_fallback(key_node)
                    next_prefix = self._join_prop_path(prefix, key_name)
                    prop_defs.setdefault(next_prefix, {"is_optional": is_optional})
                    self._collect_prop_bindings(
                        value_node,
                        prop_defs,
                        alias_map,
                        container_names,
                        next_prefix,
                        is_optional=is_optional,
                    )
                elif child.type == "assignment_pattern":
                    self._collect_prop_bindings(
                        child,
                        prop_defs,
                        alias_map,
                        container_names,
                        prefix,
                        is_optional=True,
                    )
                elif child.type == "object_assignment_pattern":
                    self._collect_prop_bindings(
                        child,
                        prop_defs,
                        alias_map,
                        container_names,
                        prefix,
                        is_optional=True,
                    )

    def _collect_body_prop_destructuring(
        self,
        node: Node,
        prop_defs: dict[str, dict[str, object]],
        alias_map: dict[str, str],
        container_names: set[str],
    ) -> None:
        for child in self._walk_nodes(node):
            if child.type != cs.TS_VARIABLE_DECLARATOR:
                continue
            name_node = child.child_by_field_name(cs.FIELD_NAME)
            value_node = child.child_by_field_name(cs.FIELD_VALUE)
            if not name_node or not value_node:
                continue
            base_path = self._resolve_prop_base_path(
                value_node, alias_map, container_names
            )
            if base_path is None:
                continue
            self._collect_prop_bindings(
                name_node,
                prop_defs,
                alias_map,
                container_names,
                base_path,
            )

    def _collect_prop_usage(
        self,
        body_node: Node,
        prop_defs: dict[str, dict[str, object]],
        alias_map: dict[str, str],
        container_names: set[str],
    ) -> None:
        for child in self._walk_nodes(body_node):
            if child.type == "member_expression":
                member_path = self._member_expression_path(child)
                if not member_path:
                    continue
                base_name, segments = member_path
                if base_name in container_names and segments:
                    prop_name = ".".join(segments)
                    entry = prop_defs.setdefault(prop_name, {})
                    entry["is_used"] = True
                elif base_name in alias_map and segments:
                    prop_name = self._join_prop_path(
                        alias_map[base_name], ".".join(segments)
                    )
                    entry = prop_defs.setdefault(prop_name, {})
                    entry["is_used"] = True
            elif child.type == cs.TS_IDENTIFIER:
                identifier = safe_decode_with_fallback(child)
                if identifier in alias_map and not self._is_pattern_identifier(child):
                    entry = prop_defs.setdefault(alias_map[identifier], {})
                    entry["is_used"] = True

    def _ingest_component_props(
        self, component_qn: str, props: list[dict[str, object]]
    ) -> None:
        if not props:
            return
        component_name = component_qn.rsplit(cs.SEPARATOR_DOT, 1)[-1]
        for index, prop in enumerate(props):
            prop_name = str(prop.get("name") or f"prop_{index}")
            sanitized = re.sub(r"[^A-Za-z0-9_]+", "_", prop_name).strip("_") or "prop"
            param_qn = f"{component_qn}{cs.SEPARATOR_DOT}prop.{index}.{sanitized}"
            self.ingestor.ensure_node_batch(
                cs.NodeLabel.PARAMETER,
                {
                    cs.KEY_QUALIFIED_NAME: param_qn,
                    cs.KEY_NAME: prop_name.split(cs.SEPARATOR_DOT)[-1],
                    cs.KEY_PATH: self._component_path(component_qn),
                    "parameter_index": index,
                    "component_name": component_name,
                    "component_qn": component_qn,
                    "parameter_type": "prop",
                    "prop_path": prop_name,
                    "is_optional": bool(prop.get("is_optional")),
                    "is_used": bool(prop.get("is_used")),
                },
            )
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.COMPONENT, cs.KEY_QUALIFIED_NAME, component_qn),
                cs.RelationshipType.HAS_PARAMETER,
                (cs.NodeLabel.PARAMETER, cs.KEY_QUALIFIED_NAME, param_qn),
                {
                    "parameter_index": index,
                    "parameter_name": prop_name,
                    cs.KEY_RELATION_TYPE: "component_prop",
                },
            )

    def _link_component_hooks(
        self,
        component_qn: str,
        module_qn: str,
        component_node: Node,
    ) -> list[str]:
        hook_names: list[str] = []
        component_callable = self._component_callable_node(component_node)
        body_node = (
            component_callable.child_by_field_name(cs.FIELD_BODY) or component_callable
        )
        for node in self._walk_nodes(body_node):
            if node.type != "call_expression":
                continue
            hook_target = self._resolve_hook_target(module_qn, node)
            if not hook_target:
                continue
            hook_qn, hook_name = hook_target
            hook_names.append(hook_name)
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.COMPONENT, cs.KEY_QUALIFIED_NAME, component_qn),
                cs.RelationshipType.CALLS,
                (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, hook_qn),
                {
                    cs.KEY_RELATION_TYPE: "react_hook",
                    cs.KEY_HOOK_NAME: hook_name,
                },
            )
        return sorted(set(hook_names))

    def _resolve_hook_target(
        self, module_qn: str, call_node: Node
    ) -> tuple[str, str] | None:
        function_node = call_node.child_by_field_name(cs.FIELD_FUNCTION)
        if not function_node:
            return None
        import_mapping = self.import_processor.import_mapping.get(module_qn, {})

        if function_node.type == cs.TS_IDENTIFIER:
            hook_name = safe_decode_with_fallback(function_node)
            if not re.match(r"^use[A-Z]", hook_name):
                return None
            imported = import_mapping.get(hook_name)
            if imported:
                resolved_qn = self._resolve_hook_import(imported, hook_name)
                if resolved_qn:
                    return resolved_qn, hook_name
            local_qn = self._find_component_symbol_qn(
                hook_name, preferred_module=module_qn
            )
            if local_qn:
                return local_qn, hook_name
            return self._ensure_external_hook_node(hook_name, hook_name), hook_name

        member_path = self._member_expression_path(function_node)
        if not member_path:
            return None
        base_name, segments = member_path
        if not segments:
            return None
        hook_name = segments[-1]
        if not re.match(r"^use[A-Z]", hook_name):
            return None
        imported = import_mapping.get(base_name)
        if imported:
            qualified = f"{imported}{cs.SEPARATOR_DOT}{hook_name}"
            resolved_qn = self._resolve_hook_import(qualified, hook_name)
            if resolved_qn:
                return resolved_qn, hook_name
        return self._ensure_external_hook_node(
            hook_name, f"{base_name}.{hook_name}"
        ), hook_name

    def _resolve_hook_import(
        self, imported_full_name: str, hook_name: str
    ) -> str | None:
        if imported_full_name.startswith(f"{self.project_name}{cs.SEPARATOR_DOT}"):
            target = self._resolve_symbol_target(imported_full_name)
            if target and target[0] == cs.NodeLabel.FUNCTION:
                return target[1]
            symbol_qn = self._find_component_symbol_qn(hook_name)
            if symbol_qn:
                return symbol_qn
        return self._ensure_external_hook_node(hook_name, imported_full_name)

    def _ensure_external_hook_node(self, hook_name: str, source_name: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9_]+", "_", source_name).strip("_") or hook_name
        hook_qn = (
            f"{self.project_name}{cs.SEPARATOR_DOT}framework.react_hook.{normalized}"
        )
        self.ingestor.ensure_node_batch(
            cs.NodeLabel.FUNCTION,
            {
                cs.KEY_QUALIFIED_NAME: hook_qn,
                cs.KEY_NAME: hook_name,
                cs.KEY_IS_EXTERNAL: True,
                cs.KEY_IS_PLACEHOLDER: True,
                cs.KEY_FRAMEWORK: "react_hook",
                cs.KEY_MODULE_QN: f"{self.project_name}{cs.SEPARATOR_DOT}framework.react_hook",
                cs.KEY_PARENT_QN: f"{self.project_name}{cs.SEPARATOR_DOT}framework.react_hook",
                cs.KEY_SYMBOL_KIND: cs.NodeLabel.FUNCTION.value.lower(),
            },
        )
        return hook_qn

    def _extract_jsx_usage_properties(
        self,
        jsx_node: Node,
        source_component_qn: str | None,
    ) -> dict[str, object]:
        if not source_component_qn:
            return {}
        attr_names: list[str] = []
        prop_bindings: list[str] = []
        alias_map = self._component_prop_aliases.get(source_component_qn, {})
        containers = self._component_prop_containers.get(source_component_qn, set())
        for child in jsx_node.children:
            if child.type != "jsx_attribute" or not child.children:
                continue
            attr_name = safe_decode_with_fallback(child.children[0])
            if not attr_name:
                continue
            attr_names.append(attr_name)
            expression_node = next(
                (
                    grandchild
                    for grandchild in child.children
                    if grandchild.type == "jsx_expression"
                ),
                None,
            )
            if not expression_node:
                continue
            source_props = self._extract_prop_sources_from_expression(
                expression_node,
                alias_map,
                containers,
            )
            for source_prop in source_props:
                prop_bindings.append(f"{attr_name}:{source_prop}")
        rel_props: dict[str, object] = {}
        if attr_names:
            rel_props["props_passed"] = sorted(set(attr_names))
        if prop_bindings:
            rel_props["prop_bindings"] = sorted(set(prop_bindings))
        return rel_props

    def _extract_prop_sources_from_expression(
        self,
        node: Node,
        alias_map: dict[str, str],
        containers: set[str],
    ) -> list[str]:
        prop_sources: set[str] = set()
        for child in self._walk_nodes(node):
            if child.type == cs.TS_IDENTIFIER:
                identifier = safe_decode_with_fallback(child)
                if identifier in alias_map and not self._is_pattern_identifier(child):
                    prop_sources.add(alias_map[identifier])
            elif child.type == "member_expression":
                member_path = self._member_expression_path(child)
                if not member_path:
                    continue
                base_name, segments = member_path
                if base_name in containers and segments:
                    prop_sources.add(".".join(segments))
                elif base_name in alias_map and segments:
                    prop_sources.add(
                        self._join_prop_path(alias_map[base_name], ".".join(segments))
                    )
        return sorted(prop_sources)

    def _resolve_prop_base_path(
        self,
        value_node: Node,
        alias_map: dict[str, str],
        container_names: set[str],
    ) -> str | None:
        if value_node.type == cs.TS_IDENTIFIER:
            identifier = safe_decode_with_fallback(value_node)
            if identifier in container_names:
                return ""
            return alias_map.get(identifier)
        member_path = self._member_expression_path(value_node)
        if not member_path:
            return None
        base_name, segments = member_path
        if base_name in container_names:
            return ".".join(segments)
        if base_name in alias_map:
            return self._join_prop_path(alias_map[base_name], ".".join(segments))
        return None

    def _member_expression_path(self, node: Node) -> tuple[str, list[str]] | None:
        parts: list[str] = []
        current = node
        while current and current.type == "member_expression":
            property_node = current.child_by_field_name("property")
            object_node = current.child_by_field_name("object")
            if not object_node and len(current.children) >= 3:
                object_node = current.children[0]
                property_node = current.children[-1]
            if not property_node or not object_node:
                return None
            parts.insert(0, safe_decode_with_fallback(property_node))
            if object_node.type == cs.TS_IDENTIFIER:
                return safe_decode_with_fallback(object_node), parts
            current = object_node
        return None

    def _is_pattern_identifier(self, node: Node) -> bool:
        current = node.parent
        while current:
            if current.type in {
                "required_parameter",
                "optional_parameter",
                "formal_parameters",
                "object_pattern",
                "pair_pattern",
                "assignment_pattern",
                cs.TS_VARIABLE_DECLARATOR,
            }:
                return True
            if current.type in {
                "statement_block",
                "jsx_expression",
                "call_expression",
                "return_statement",
            }:
                return False
            current = current.parent
        return False

    def _component_path(self, component_qn: str) -> str:
        module_qn = component_qn.rsplit(cs.SEPARATOR_DOT, 1)[0]
        file_path = self.module_qn_to_file_path.get(module_qn)
        return file_path.relative_to(self.repo_path).as_posix() if file_path else ""

    def _component_callable_node(self, component_node: Node) -> Node:
        if component_node.type == cs.TS_VARIABLE_DECLARATOR:
            value_node = component_node.child_by_field_name(cs.FIELD_VALUE)
            if value_node is not None:
                if value_node.type in {"arrow_function", "function_expression"}:
                    return value_node
                if value_node.type == "call_expression":
                    for child in self._walk_nodes(value_node):
                        if child.type in {"arrow_function", "function_expression"}:
                            return child
        return component_node

    @staticmethod
    def _first_named_child(node: Node) -> Node | None:
        for child in node.children:
            if child.type not in {"(", ")", ",", ":"}:
                return child
        return None

    @staticmethod
    def _join_prop_path(prefix: str, suffix: str) -> str:
        if not prefix:
            return suffix
        if not suffix:
            return prefix
        return f"{prefix}.{suffix}"

    def _resolve_local_component_symbol(
        self, module_qn: str, component_name: str
    ) -> str | None:
        return self._find_component_symbol_qn(
            component_name, preferred_module=module_qn
        )

    def _find_component_symbol_qn(
        self, component_name: str, preferred_module: str | None = None
    ) -> str | None:
        candidates = self.function_registry.find_ending_with(component_name)
        preferred_prefix = (
            f"{preferred_module}{cs.SEPARATOR_DOT}" if preferred_module else ""
        )
        for qn in candidates:
            node_type = self.function_registry.get(qn)
            if node_type not in {NodeType.CLASS, NodeType.FUNCTION, NodeType.METHOD}:
                continue
            if preferred_prefix and qn.startswith(preferred_prefix):
                return qn
        for qn in candidates:
            node_type = self.function_registry.get(qn)
            if node_type in {NodeType.CLASS, NodeType.FUNCTION, NodeType.METHOD}:
                return qn
        return None

    def _extract_default_export_name(self, root_node: Node) -> str | None:
        for node in self._walk_nodes(root_node):
            if node.type != "export_statement":
                continue
            export_text = safe_decode_with_fallback(node).strip()
            for pattern in (
                r"^export\s+default\s+function\s+([A-Za-z_$][\w$]*)",
                r"^export\s+default\s+class\s+([A-Za-z_$][\w$]*)",
                r"^export\s+default\s+(?:React\.)?(?:memo|forwardRef)\(\s*([A-Za-z_$][\w$]*)",
                r"^export\s+default\s+([A-Za-z_$][\w$]*)",
            ):
                match = re.search(pattern, export_text)
                if match:
                    return match.group(1)
        return None

    def _find_enclosing_component_qn(self, node: Node, module_qn: str) -> str | None:
        current = node.parent
        while current:
            if current.type == cs.TS_FUNCTION_DECLARATION:
                name_node = current.child_by_field_name(cs.FIELD_NAME)
                name = safe_decode_with_fallback(name_node) if name_node else ""
                component_qn = self._components_by_module.get(module_qn, {}).get(name)
                if component_qn:
                    return component_qn
            elif current.type == cs.TS_VARIABLE_DECLARATOR:
                name_node = current.child_by_field_name(cs.FIELD_NAME)
                name = safe_decode_with_fallback(name_node) if name_node else ""
                component_qn = self._components_by_module.get(module_qn, {}).get(name)
                if component_qn:
                    return component_qn
            elif current.type in {
                cs.TS_CLASS_DECLARATION,
                cs.TS_ABSTRACT_CLASS_DECLARATION,
            }:
                name_node = current.child_by_field_name(cs.FIELD_NAME)
                name = safe_decode_with_fallback(name_node) if name_node else ""
                component_qn = self._components_by_module.get(module_qn, {}).get(name)
                if component_qn:
                    return component_qn
            current = current.parent
        return None

    def _resolve_component_reference(self, module_qn: str, tag_name: str) -> str | None:
        local_name = tag_name.split(cs.SEPARATOR_DOT)[-1]
        local_component = self._components_by_module.get(module_qn, {}).get(local_name)
        if cs.SEPARATOR_DOT not in tag_name and local_component:
            return local_component

        import_mapping = self.import_processor.import_mapping.get(module_qn, {})
        if cs.SEPARATOR_DOT in tag_name:
            namespace_alias, component_name = tag_name.split(cs.SEPARATOR_DOT, 1)
            imported_full_name = import_mapping.get(namespace_alias)
            if imported_full_name:
                resolved_component = self._resolve_imported_component(
                    imported_full_name, component_name
                )
                if resolved_component:
                    return resolved_component
            symbol_qn = self._find_component_symbol_qn(component_name)
            if not symbol_qn:
                return None
            target_module = symbol_qn.rsplit(cs.SEPARATOR_DOT, 1)[0]
            return self._ensure_component_node(
                module_qn=target_module,
                component_name=component_name.split(cs.SEPARATOR_DOT)[-1],
                source_qn=symbol_qn,
                framework="react",
            )

        imported_full_name = import_mapping.get(tag_name)
        if imported_full_name:
            resolved_component = self._resolve_imported_component(
                imported_full_name, tag_name
            )
            if resolved_component:
                return resolved_component

        if local_component:
            return local_component

        symbol_qn = self._find_component_symbol_qn(tag_name, preferred_module=module_qn)
        if not symbol_qn:
            return None
        target_module = symbol_qn.rsplit(cs.SEPARATOR_DOT, 1)[0]
        return self._ensure_component_node(
            module_qn=target_module,
            component_name=tag_name,
            source_qn=symbol_qn,
            framework="react",
        )

    def _resolve_imported_component(
        self, imported_full_name: str, component_name: str
    ) -> str | None:
        if not imported_full_name.startswith(f"{self.project_name}{cs.SEPARATOR_DOT}"):
            return None

        if imported_full_name.endswith(cs.IMPORT_DEFAULT_SUFFIX):
            module_qn = imported_full_name[: -len(cs.IMPORT_DEFAULT_SUFFIX)]
            default_component = self._default_component_by_module.get(module_qn)
            if default_component:
                return default_component
            components = self._components_by_module.get(module_qn, {})
            if len(components) == 1:
                return next(iter(components.values()))
            return None

        if (
            imported_full_name in self.module_qn_to_file_path
            or imported_full_name in self._components_by_module
        ):
            module_qn = imported_full_name
            target_symbol = component_name.split(cs.SEPARATOR_DOT)[-1]
        else:
            target_symbol = imported_full_name.rsplit(cs.SEPARATOR_DOT, 1)[-1]
            module_qn = imported_full_name.rsplit(cs.SEPARATOR_DOT, 1)[0]
        component_qn = self._components_by_module.get(module_qn, {}).get(target_symbol)
        if component_qn:
            return component_qn

        symbol_qn = self._find_component_symbol_qn(
            target_symbol, preferred_module=module_qn
        )
        if not symbol_qn:
            return None
        return self._ensure_component_node(
            module_qn=module_qn,
            component_name=component_name.split(cs.SEPARATOR_DOT)[-1],
            source_qn=symbol_qn,
            framework="react",
        )

    def _next_special_kind(self, file_path: Path) -> str | None:
        relative_path = file_path.relative_to(self.repo_path).as_posix()
        if relative_path.endswith("/page.js") or relative_path.endswith("/page.jsx"):
            return "page"
        if relative_path.endswith("/page.ts") or relative_path.endswith("/page.tsx"):
            return "page"
        if relative_path.endswith("/layout.js") or relative_path.endswith(
            "/layout.jsx"
        ):
            return "layout"
        if relative_path.endswith("/layout.ts") or relative_path.endswith(
            "/layout.tsx"
        ):
            return "layout"
        return None

    def _link_next_component_endpoints(self, file_path: Path, module_qn: str) -> None:
        special_kind = self._next_special_kind(file_path)
        if not special_kind:
            return
        component_qn = self._default_component_by_module.get(module_qn)
        if not component_qn:
            components = self._components_by_module.get(module_qn, {})
            if len(components) == 1:
                component_qn = next(iter(components.values()))
        if not component_qn:
            return
        route_path = self._route_path_for_next_app_entry(file_path)
        if not route_path:
            return
        endpoint_qn = (
            f"{self.project_name}{cs.SEPARATOR_DOT}endpoint.next.GET:{route_path}#"
            f"{special_kind}:{file_path.relative_to(self.repo_path).as_posix()}"
        )
        self.ingestor.ensure_relationship_batch(
            (cs.NodeLabel.COMPONENT, cs.KEY_QUALIFIED_NAME, component_qn),
            cs.RelationshipType.HAS_ENDPOINT,
            (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn),
            {
                cs.KEY_RELATION_TYPE: f"next_{special_kind}",
                "framework": "next",
                "http_method": "GET",
                "route_path": route_path,
                "source_parser": "resolver_pass",
            },
        )

    def _link_component_requests(self, file_path: Path, module_qn: str) -> None:
        component_map = self._components_by_module.get(module_qn, {})
        if not component_map:
            return

        relative_path = file_path.relative_to(self.repo_path).as_posix()
        for component_qn in component_map.values():
            source_node = self._component_source_nodes.get(component_qn)
            if source_node is None:
                continue
            component_source = safe_decode_with_fallback(source_node)
            for request in self._extract_component_request_endpoints(component_source):
                endpoint_qn = self._ensure_request_endpoint_node(
                    relative_path,
                    cast(str, request["framework"]),
                    cast(str, request["method"]),
                    cast(str, request["path"]),
                    cast(str, request["raw_path"]),
                )
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.COMPONENT, cs.KEY_QUALIFIED_NAME, component_qn),
                    cs.RelationshipType.REQUESTS_ENDPOINT,
                    (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn),
                    {
                        cs.KEY_RELATION_TYPE: "http_request",
                        cs.KEY_FRAMEWORK: cast(str, request["framework"]),
                        cs.KEY_HTTP_METHOD: cast(str, request["method"]),
                        cs.KEY_ROUTE_PATH: cast(str, request["path"]),
                        cs.KEY_RAW_PATH: cast(str, request["raw_path"]),
                        "source_parser": "resolver_pass",
                    },
                )

    def _extract_component_request_endpoints(self, source: str) -> list[dict[str, str]]:
        requests: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()

        def _append(framework: str, method: str, raw_path: str) -> None:
            normalized_path = self._normalize_request_path(raw_path)
            if not normalized_path:
                return
            key = (framework, method.upper(), normalized_path)
            if key in seen:
                return
            seen.add(key)
            requests.append(
                {
                    "framework": framework,
                    "method": method.upper(),
                    "path": normalized_path,
                    "raw_path": raw_path,
                }
            )

        method_pattern = re.compile(
            r"method\s*:\s*['\"](GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)['\"]",
            re.IGNORECASE,
        )
        fetch_pattern = re.compile(
            r"fetch\s*\(\s*['\"]([^'\"]+)['\"](\s*,\s*\{([^}]*)\})?",
            re.IGNORECASE,
        )
        fetch_template_pattern = re.compile(
            r"fetch\s*\(\s*`([^`]+)`(\s*,\s*\{([^}]*)\})?",
            re.IGNORECASE,
        )
        axios_pattern = re.compile(
            r"axios\.(get|post|put|delete|patch)\s*\(\s*(['\"`])(?P<path>[^'\"`]+)\2",
            re.IGNORECASE,
        )

        for match in fetch_pattern.finditer(source):
            options = match.group(3) or ""
            method_match = method_pattern.search(options)
            _append(
                "http", method_match.group(1) if method_match else "GET", match.group(1)
            )
        for match in fetch_template_pattern.finditer(source):
            options = match.group(3) or ""
            method_match = method_pattern.search(options)
            _append(
                "http", method_match.group(1) if method_match else "GET", match.group(1)
            )
        for match in axios_pattern.finditer(source):
            _append("http", match.group(1), match.group("path"))

        return requests

    def _ensure_request_endpoint_node(
        self,
        relative_path: str,
        framework: str,
        method: str,
        route_path: str,
        raw_path: str,
    ) -> str:
        endpoint_qn = f"{self.project_name}{cs.SEPARATOR_DOT}endpoint.{framework}.{method}:{route_path}"
        self.ingestor.ensure_node_batch(
            cs.NodeLabel.ENDPOINT,
            {
                cs.KEY_QUALIFIED_NAME: endpoint_qn,
                cs.KEY_NAME: f"{method} {route_path}",
                cs.KEY_PATH: relative_path,
                cs.KEY_FRAMEWORK: framework,
                cs.KEY_HTTP_METHOD: method,
                cs.KEY_ROUTE_PATH: route_path,
                cs.KEY_RAW_PATH: raw_path,
            },
        )
        return endpoint_qn

    @staticmethod
    def _normalize_request_path(raw_path: str) -> str:
        path = raw_path.strip().strip("'\"`")
        if not path:
            return ""
        http_match = re.match(r"^(https?://[^/]+)(/.*)?$", path)
        if http_match:
            path = http_match.group(2) or "/"
        path = path.replace("\\", "/")
        path = re.sub(r"\$\{[^}]+\}", "{param}", path)
        path = re.sub(r"\[[^/]+\]", "{param}", path)
        path = re.sub(r":[A-Za-z_][A-Za-z0-9_]*", "{param}", path)
        path = re.sub(r"//+", "/", path)
        if path and not path.startswith("/"):
            path = f"/{path}"
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        return path

    def _route_path_for_next_app_entry(self, file_path: Path) -> str | None:
        relative_parts = file_path.relative_to(self.repo_path).parts
        if "app" not in relative_parts:
            return None
        app_index = relative_parts.index("app")
        route_segments: list[str] = []
        for segment in relative_parts[app_index + 1 : -1]:
            if segment.startswith("@"):
                continue
            if segment.startswith("(") and segment.endswith(")"):
                continue
            route_segments.append(self._normalize_next_route_segment(segment))
        if not route_segments:
            return "/"
        return "/" + "/".join(route_segments)

    @staticmethod
    def _normalize_next_route_segment(segment: str) -> str:
        if segment.startswith("[[...") and segment.endswith("]]"):
            return "{param}"
        if segment.startswith("[...") and segment.endswith("]"):
            return "{param}"
        if segment.startswith("[") and segment.endswith("]"):
            return "{param}"
        return segment

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
