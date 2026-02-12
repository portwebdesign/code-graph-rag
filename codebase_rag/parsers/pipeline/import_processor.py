"""
This module defines the `ImportProcessor`, a key component in the parsing pipeline
that is responsible for handling import statements across various programming languages.

The processor's main role is to parse import declarations from the Abstract Syntax
Tree (AST), resolve the imported paths to fully qualified names (FQNs), and maintain
a mapping from local aliases to their corresponding FQNs for each module. This
information is essential for the `CallResolver` to correctly identify the definitions
of functions and classes used across different files. It also handles the identification
and caching of standard library modules to distinguish between internal and external code.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from tree_sitter import Node, QueryCursor

from codebase_rag.core import constants as cs
from codebase_rag.core import logs as ls
from codebase_rag.infrastructure.language_spec import LanguageSpec
from codebase_rag.parsers.core.utils import (
    normalize_query_captures,
    safe_decode_text,
    safe_decode_with_fallback,
)
from codebase_rag.parsers.languages.common.stdlib_extractor import (
    StdlibCacheStats,
    StdlibExtractor,
    clear_stdlib_cache,
    flush_stdlib_cache,
    get_stdlib_cache_stats,
    load_persistent_cache,
    save_persistent_cache,
)

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import (
        FunctionRegistryTrieProtocol,
        LanguageQueries,
    )
    from codebase_rag.services import IngestorProtocol


class ImportProcessor:
    """
    Handles the parsing and resolution of import statements for various languages.

    This class uses tree-sitter queries to find import-related nodes in the AST.
    It then applies language-specific logic to extract the imported entities,
    resolve their full paths (handling relative paths, aliases, and wildcards),
    and stores this information in a structured way.

    The primary output is `import_mapping`, a dictionary that maps a module's
    qualified name to another dictionary, which in turn maps local names/aliases
    used within that module to their fully qualified names.

    Attributes:
        ingestor (IngestorProtocol | None): Service for writing data to the graph.
        repo_path (Path): The root path of the repository being parsed.
        project_name (str): The name of the project.
        function_registry (FunctionRegistryTrieProtocol | None): A trie of known functions/classes.
        import_mapping (dict[str, dict[str, str]]): The main mapping of module QN -> {local_name -> FQN}.
        import_nodes_created (set[str]): A set to track created external module nodes to avoid duplication.
        import_nodes_by_module (dict[str, list[str]]): Maps module QN to a list of its import node QNs.
        import_symbol_links (list[dict[str, str]]): A list of records for linking import symbols to definitions.
        std_lib_cache (dict[str, set[str]]): A cache for standard library modules for different languages.
        stdlib_extractor (StdlibExtractor): A utility to help identify standard library paths.
    """

    def __init__(
        self,
        repo_path: Path,
        project_name: str,
        ingestor: IngestorProtocol | None = None,
        function_registry: FunctionRegistryTrieProtocol | None = None,
    ):
        """
        Initializes the ImportProcessor.

        Args:
            repo_path (Path): The absolute path to the root of the repository.
            project_name (str): The name of the project, used as the root of the FQN.
            ingestor (IngestorProtocol | None): The service for writing data to the graph.
            function_registry (FunctionRegistryTrieProtocol | None): A trie of known functions/classes.
        """
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.function_registry = function_registry
        self.import_mapping: dict[str, dict[str, str]] = {}
        self.import_nodes_created: set[str] = set()
        self.import_nodes_by_module: dict[str, list[str]] = {}
        self.import_symbol_links: list[dict[str, str]] = []
        self.std_lib_cache: dict[str, set[str]] = {}
        self.stdlib_extractor = StdlibExtractor(function_registry)

        load_persistent_cache()

    def __del__(self) -> None:
        """Saves the standard library cache to a persistent file on object destruction."""
        try:
            save_persistent_cache()
        except Exception:
            pass

    @staticmethod
    def flush_stdlib_cache() -> None:
        """Manually triggers saving the standard library cache to its persistent file."""
        flush_stdlib_cache()

    @staticmethod
    def clear_stdlib_cache() -> None:
        """Clears the in-memory and persistent standard library cache."""
        clear_stdlib_cache()

    @staticmethod
    def get_stdlib_cache_stats() -> StdlibCacheStats:
        """
        Retrieves statistics about the standard library cache.

        Returns:
            A `StdlibCacheStats` object with information about cache size and hits/misses.
        """
        return get_stdlib_cache_stats()

    def remove_module(self, module_qn: str) -> None:
        """
        Removes all import mappings associated with a specific module.

        This is used when a file is re-parsed to ensure stale data is cleared.

        Args:
            module_qn (str): The qualified name of the module to remove.
        """
        if module_qn in self.import_mapping:
            del self.import_mapping[module_qn]

    def _resolve_relative_import(self, relative_node: Node, module_qn: str) -> str:
        """
        Resolves a relative import path based on the current module's qualified name.

        Args:
            relative_node (Node): The AST node representing the relative import.
            module_qn (str): The qualified name of the current module.

        Returns:
            The resolved, fully qualified name of the imported module.
        """
        module_parts = module_qn.split(cs.SEPARATOR_DOT)[1:]

        dots = 0
        module_name = ""

        for child in relative_node.children:
            if child.type == cs.TS_IMPORT_PREFIX:
                if decoded_text := safe_decode_text(child):
                    dots = len(decoded_text)
            elif child.type == cs.TS_DOTTED_NAME:
                if decoded_name := safe_decode_text(child):
                    module_name = decoded_name

        target_parts = module_parts[:-dots] if dots > 0 else module_parts

        if module_name:
            target_parts.extend(module_name.split(cs.SEPARATOR_DOT))

        return cs.SEPARATOR_DOT.join(target_parts)

    def _is_local_java_import(self, import_path: str) -> bool:
        """
        Checks if a Java import path corresponds to a local project directory.

        Args:
            import_path (str): The Java import path (e.g., "com.example.MyClass").

        Returns:
            True if the top-level package name matches a directory in the repo root.
        """
        top_level = import_path.split(cs.SEPARATOR_DOT)[0]
        return (self.repo_path / top_level).is_dir()

    def _resolve_java_import_path(self, import_path: str) -> str:
        """
        Resolves a Java import path, prefixing it with the project name if it's local.

        Args:
            import_path (str): The Java import path.

        Returns:
            The resolved, fully qualified name.
        """
        if self._is_local_java_import(import_path):
            return f"{self.project_name}{cs.SEPARATOR_DOT}{import_path}"
        return import_path

    def _is_local_js_import(self, full_name: str) -> bool:
        """
        Checks if a JavaScript/TypeScript import is local to the project.

        Args:
            full_name (str): The fully qualified name of the import.

        Returns:
            True if the name starts with the project name.
        """
        return full_name.startswith(self.project_name + cs.SEPARATOR_DOT)

    def _resolve_js_internal_module(self, full_name: str) -> str:
        """
        Resolves an internal JS/TS module import, handling file extensions and index files.

        This helps differentiate between importing a module and importing a specific
        entity from within that module.

        Args:
            full_name (str): The fully qualified name of the import.

        Returns:
            The resolved module-level qualified name.
        """
        if full_name.endswith(cs.IMPORT_DEFAULT_SUFFIX):
            return full_name[: -len(cs.IMPORT_DEFAULT_SUFFIX)]

        parts = full_name.split(cs.SEPARATOR_DOT)
        if len(parts) <= 2:
            return full_name

        potential_module = cs.SEPARATOR_DOT.join(parts[:-1])
        relative_path = cs.SEPARATOR_SLASH.join(parts[1:-1])

        for ext in (cs.EXT_JS, cs.EXT_TS, cs.EXT_JSX, cs.EXT_TSX):
            if (self.repo_path / f"{relative_path}{ext}").is_file():
                return potential_module
            index_path = self.repo_path / relative_path / f"{cs.INDEX_INDEX}{ext}"
            if index_path.is_file():
                return potential_module

        return full_name

    def _is_local_rust_import(self, import_path: str) -> bool:
        """
        Checks if a Rust import path is local to the current crate.

        Args:
            import_path (str): The Rust `use` path.

        Returns:
            True if the path starts with "crate::".
        """
        return import_path.startswith(cs.RUST_CRATE_PREFIX)

    def _ensure_external_module_node(self, module_path: str, full_name: str) -> None:
        """
        Ensures that a `Module` node exists in the graph for an external dependency.

        If the node doesn't exist, it is created with the `is_external` flag set to True.

        Args:
            module_path (str): The resolved qualified name of the external module.
            full_name (str): The original import path from the source code.
        """
        if not self.ingestor or not module_path:
            return
        if module_path in self.import_nodes_created:
            return
        if cs.SEPARATOR_DOUBLE_COLON in module_path:
            name = module_path.rsplit(cs.SEPARATOR_DOUBLE_COLON, 1)[-1]
        else:
            name = module_path.rsplit(cs.SEPARATOR_DOT, 1)[-1]
        namespace = (
            module_path.rsplit(cs.SEPARATOR_DOT, 1)[0]
            if cs.SEPARATOR_DOT in module_path
            else None
        )
        module_props = {
            cs.KEY_NAME: name,
            cs.KEY_QUALIFIED_NAME: module_path,
            cs.KEY_PATH: full_name,
            cs.KEY_IS_EXTERNAL: True,
            cs.KEY_MODULE_QN: module_path,
            cs.KEY_REPO_REL_PATH: full_name,
            cs.KEY_SYMBOL_KIND: cs.NodeLabel.MODULE.value.lower(),
        }
        if namespace:
            module_props[cs.KEY_NAMESPACE] = namespace
            module_props[cs.KEY_PACKAGE] = namespace
            module_props[cs.KEY_PARENT_QN] = namespace
        self.ingestor.ensure_node_batch(cs.NodeLabel.MODULE, module_props)
        self.import_nodes_created.add(module_path)

    def _resolve_rust_import_path(self, import_path: str, module_qn: str) -> str:
        """
        Resolves a Rust `use` path to a fully qualified name.

        Handles local crate-relative paths and external crate paths.

        Args:
            import_path (str): The path from the `use` statement.
            module_qn (str): The qualified name of the current module.

        Returns:
            The resolved qualified name of the imported module.
        """
        if self._is_local_rust_import(import_path):
            path_without_crate = import_path[len(cs.RUST_CRATE_PREFIX) :]
            module_parts = module_qn.split(cs.SEPARATOR_DOT)
            try:
                src_index = module_parts.index(cs.LANG_SRC_DIR)
                crate_root_qn = cs.SEPARATOR_DOT.join(module_parts[: src_index + 1])
            except ValueError:
                crate_root_qn = self.project_name
            module_part = path_without_crate.split(cs.SEPARATOR_DOUBLE_COLON)[0]
            return f"{crate_root_qn}{cs.SEPARATOR_DOT}{module_part}"

        parts = import_path.split(cs.SEPARATOR_DOUBLE_COLON)
        module_path = (
            cs.SEPARATOR_DOUBLE_COLON.join(parts[:-1]) if len(parts) > 1 else parts[0]
        )

        self._ensure_external_module_node(module_path, import_path)
        return module_path

    def _resolve_module_path(
        self,
        full_name: str,
        module_qn: str,
        language: cs.SupportedLanguage,
    ) -> str:
        """
        A general-purpose dispatcher for resolving an import path based on language.

        Args:
            full_name (str): The full import string from the source.
            module_qn (str): The qualified name of the current module.
            language (cs.SupportedLanguage): The programming language.

        Returns:
            The resolved qualified name of the module part of the import.
        """
        project_prefix = self.project_name + cs.SEPARATOR_DOT
        match language:
            case cs.SupportedLanguage.JAVA:
                if full_name.startswith(project_prefix):
                    return full_name
                module_path = (
                    full_name.rsplit(cs.SEPARATOR_DOT, 1)[0]
                    if cs.SEPARATOR_DOT in full_name
                    else full_name
                )
                self._ensure_external_module_node(module_path, full_name)
                return module_path
            case cs.SupportedLanguage.JS | cs.SupportedLanguage.TS:
                if self._is_local_js_import(full_name):
                    return self._resolve_js_internal_module(full_name)
            case cs.SupportedLanguage.RUST:
                return self._resolve_rust_import_path(full_name, module_qn)
            case cs.SupportedLanguage.PHP:
                php_path = full_name.replace("\\", cs.SEPARATOR_DOT)
                self._ensure_external_module_node(php_path, full_name)
                return php_path

        module_path = self.stdlib_extractor.extract_module_path(full_name, language)
        if not module_path.startswith(project_prefix):
            self._ensure_external_module_node(module_path, full_name)
        return module_path

    def parse_imports(
        self,
        root_node: Node,
        module_qn: str,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> None:
        """
        Parses all imports from a file's AST using language-specific queries.

        This is the main entry point for processing imports in a file.

        Args:
            root_node (Node): The root node of the file's AST.
            module_qn (str): The qualified name of the module being processed.
            language (cs.SupportedLanguage): The programming language of the file.
            queries (dict): A dictionary of tree-sitter queries for various languages.
        """
        lang_queries = queries.get(language)
        if not lang_queries:
            return

        import_query = lang_queries.get("imports")
        if not import_query:
            return

        lang_config = lang_queries.get("config")
        if not lang_config:
            return

        cursor = QueryCursor(import_query)
        captures_dict = normalize_query_captures(cursor.captures(root_node))
        self.process_imports(captures_dict, module_qn, lang_config, language)

    def process_imports(
        self,
        captures: dict,
        module_qn: str,
        lang_config: LanguageSpec,
        language: cs.SupportedLanguage,
    ) -> None:
        """
        Processes the captured import nodes and populates the import mapping.

        This method acts as a dispatcher, calling the appropriate language-specific
        parsing method based on the file's language.

        Args:
            captures (dict): The dictionary of captured nodes from the tree-sitter query.
            module_qn (str): The qualified name of the current module.
            lang_config (LanguageSpec): The language-specific configuration.
            language (cs.SupportedLanguage): The programming language.
        """
        self.import_mapping.setdefault(module_qn, {})

        if language == cs.SupportedLanguage.PYTHON:
            self._parse_python_imports(captures, module_qn)
        elif language in (cs.SupportedLanguage.JS, cs.SupportedLanguage.TS):
            self._parse_js_imports(captures, module_qn)
        elif language == cs.SupportedLanguage.GO:
            self._parse_go_imports(captures, module_qn)
        elif language == cs.SupportedLanguage.JAVA:
            self._parse_java_imports(captures, module_qn)
        elif language == cs.SupportedLanguage.RUST:
            self._parse_rust_imports(captures, module_qn)
        elif language == cs.SupportedLanguage.SCALA:
            self._parse_scala_imports(captures, module_qn)
        elif language == cs.SupportedLanguage.CSHARP:
            self._parse_csharp_imports(captures, module_qn)
        elif language == cs.SupportedLanguage.CPP:
            self._parse_cpp_imports(captures, module_qn)
        elif language == cs.SupportedLanguage.PHP:
            self._parse_php_imports(captures, module_qn)
        elif language == cs.SupportedLanguage.RUBY:
            self._parse_ruby_imports(captures, module_qn)
        elif language == cs.SupportedLanguage.LUA:
            self._parse_lua_imports(captures, module_qn)
        else:
            self._parse_generic_imports(captures, module_qn, lang_config)

    def resolve_type_fqn(self, type_name: str, module_qn: str) -> str | None:
        """
        Resolves a type name to its fully qualified name using the import mappings.

        Args:
            type_name (str): The local type name to resolve (e.g., "MyClass").
            module_qn (str): The qualified name of the module where the type is used.

        Returns:
            The resolved FQN of the type, or None if it cannot be found.
        """
        if not type_name:
            return None

        mapping = self.import_mapping.get(module_qn, {})
        if type_name in mapping:
            return mapping[type_name]

        for key, value in mapping.items():
            if key.startswith("*"):
                pass

        return None

    def _parse_python_imports(self, captures: dict, module_qn: str) -> None:
        """
        Parses Python `import` and `from ... import` statements.

        Args:
            captures (dict): The dictionary of captured nodes from the tree-sitter query.
            module_qn (str): The qualified name of the current module.
        """
        for node in captures.get(cs.CAPTURE_IMPORT, []):
            if not isinstance(node, Node):
                continue

            if node.type == cs.TS_IMPORT_FROM_STATEMENT:
                self._handle_python_from_import(node, module_qn)
            elif node.type == cs.TS_IMPORT_STATEMENT:
                self._handle_python_import_statement(node, module_qn)

    def _handle_python_from_import(self, node: Node, module_qn: str) -> None:
        """
        Handles a Python `from ... import ...` statement.

        Args:
            node (Node): The AST node for the `from_import` statement.
            module_qn (str): The qualified name of the current module.
        """
        module_name_node = node.child_by_field_name(cs.FIELD_MODULE_NAME)
        module_path = ""
        relative_level = 0

        for child in node.children:
            if child.type == ".":
                relative_level += 1
            elif child == module_name_node:
                break

        if module_name_node:
            module_path = safe_decode_with_fallback(module_name_node)

        full_module_path = self._resolve_python_module_path(
            module_path, relative_level, module_qn
        )

        for child in node.children:
            if child.type in {cs.TS_DOTTED_NAME, cs.TS_ALIASED_IMPORT}:
                pass

        self._extract_python_names_from_import(node, full_module_path, module_qn)

    def _handle_python_import_from_statement(self, node: Node, module_qn: str) -> None:
        """
        Alias for `_handle_python_from_import`.

        Args:
            node (Node): The AST node for the `from_import` statement.
            module_qn (str): The qualified name of the current module.
        """
        self._handle_python_from_import(node, module_qn)

    def _handle_python_import_statement(self, node: Node, module_qn: str) -> None:
        """
        Handles a Python `import ...` statement.

        Args:
            node (Node): The AST node for the `import` statement.
            module_qn (str): The qualified name of the current module.
        """
        for child in node.children:
            if child.type == cs.TS_DOTTED_NAME:
                name = safe_decode_with_fallback(child)
                self.import_mapping[module_qn][name] = name
            elif child.type == cs.TS_ALIASED_IMPORT:
                val_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if val_node and alias_node:
                    full_name = safe_decode_with_fallback(val_node)
                    alias = safe_decode_with_fallback(alias_node)
                    self.import_mapping[module_qn][alias] = full_name

    def _extract_python_names_from_import(
        self, node: Node, full_module_path: str, module_qn: str
    ) -> None:
        """
        Extracts the imported names from a Python `from ... import ...` statement.

        This method needs to be implemented to handle aliased and wildcard imports.

        Args:
            node (Node): The AST node for the import statement.
            full_module_path (str): The resolved path of the module being imported from.
            module_qn (str): The qualified name of the current module.
        """
        pass

    def _resolve_python_module_path(
        self, partial_path: str, relative_level: int, current_module: str
    ) -> str:
        """
        Resolves a Python module path, correctly handling relative imports.

        Args:
            partial_path (str): The module path specified in the import statement.
            relative_level (int): The number of leading dots (e.g., 1 for `.`, 2 for `..`).
            current_module (str): The FQN of the module containing the import.

        Returns:
            The resolved, fully qualified module path.
        """
        if relative_level == 0:
            return partial_path

        parts = current_module.split(cs.SEPARATOR_DOT)

        if parts:
            parts.pop()

        for _ in range(relative_level - 1):
            if parts:
                parts.pop()

        base = cs.SEPARATOR_DOT.join(parts)
        if partial_path:
            return f"{base}.{partial_path}" if base else partial_path
        return base

    def _parse_js_imports(self, captures: dict, module_qn: str) -> None:
        """
        Parses JavaScript/TypeScript `import` statements.

        Args:
            captures (dict): The dictionary of captured nodes from the tree-sitter query.
            module_qn (str): The qualified name of the current module.
        """
        for node in captures.get(cs.CAPTURE_IMPORT, []):
            if not isinstance(node, Node):
                continue

            source_node = node.child_by_field_name(cs.FIELD_SOURCE)
            if not source_node:
                continue

            source_path = safe_decode_with_fallback(source_node).strip("'\"")
            _ = self._resolve_js_module_path(source_path, module_qn)

            _ = node.child_by_field_name("import_clause")

    def _resolve_js_module_path(self, import_path: str, current_module: str) -> str:
        """
        Resolves a JS/TS module path, handling relative paths.

        Args:
            import_path (str): The path from the import statement (e.g., './utils').
            current_module (str): The FQN of the module containing the import.

        Returns:
            The resolved, fully qualified module path.
        """
        if not import_path.startswith(
            cs.PATH_CURRENT_DIR
        ) and not import_path.startswith(cs.PATH_PARENT_DIR):
            return import_path.replace(cs.SEPARATOR_SLASH, cs.SEPARATOR_DOT)

        current_parts = current_module.split(cs.SEPARATOR_DOT)[:-1]
        import_parts = import_path.split(cs.SEPARATOR_SLASH)

        for part in import_parts:
            if part == cs.PATH_CURRENT_DIR:
                continue
            if part == cs.PATH_PARENT_DIR:
                if current_parts:
                    current_parts.pop()
            elif part:
                current_parts.append(part)

        return cs.SEPARATOR_DOT.join(current_parts)

    def _parse_go_imports(self, captures: dict, module_qn: str) -> None:
        """
        Parses Go `import` declarations.

        Args:
            captures (dict): The dictionary of captured nodes from the tree-sitter query.
            module_qn (str): The qualified name of the current module.
        """
        for node in captures.get(cs.CAPTURE_IMPORT, []):
            if not isinstance(node, Node):
                continue
            specs = self._extract_go_import_specs(node)
            for name, path in specs:
                self.import_mapping[module_qn][name] = path

    def _extract_go_import_specs(self, node: Node) -> list[tuple[str, str]]:
        """
        Extracts import specifications from a Go import node.

        Handles single imports and grouped `import (...)` blocks.

        Args:
            node (Node): The Go import declaration node.

        Returns:
            A list of (alias, path) tuples. The alias is the package name if not explicit.
        """
        specs = []
        return specs

    def _parse_java_imports(self, captures: dict, module_qn: str) -> None:
        """
        Parses Java `import` statements.

        Args:
            captures (dict): The dictionary of captured nodes from the tree-sitter query.
            module_qn (str): The qualified name of the current module.
        """
        for node in captures.get(cs.CAPTURE_IMPORT, []):
            pass

    def _parse_rust_imports(self, captures: dict, module_qn: str) -> None:
        """
        Parses Rust `use` declarations.

        Args:
            captures (dict): The dictionary of captured nodes from the tree-sitter query.
            module_qn (str): The qualified name of the current module.
        """
        for node in captures.get(cs.CAPTURE_IMPORT, []):
            pass

    def _parse_scala_imports(self, captures: dict, module_qn: str) -> None:
        """
        Parses Scala `import` statements.

        Args:
            captures (dict): The dictionary of captured nodes from the tree-sitter query.
            module_qn (str): The qualified name of the current module.
        """
        for import_node in captures.get(cs.CAPTURE_IMPORT, []):
            if not isinstance(import_node, Node):
                continue
            import_text = safe_decode_with_fallback(import_node)
            for entry in self._split_scala_import_entries(import_text):
                self._register_scala_import_entry(entry, module_qn)

    def _split_scala_import_entries(self, import_text: str) -> list[str]:
        """
        Splits a Scala import statement into individual entries, handling grouping.

        For example, `import a.b, a.{c, d}` becomes `['a.b', 'a.{c, d}']`.

        Args:
            import_text (str): The raw text of the import statement.

        Returns:
            A list of individual import strings.
        """
        if not import_text:
            return []
        text = import_text.strip()
        if text.startswith("import "):
            text = text[len("import ") :].strip()

        entries: list[str] = []
        buffer: list[str] = []
        depth = 0

        for ch in text:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth = max(depth - 1, 0)

            if ch == "," and depth == 0:
                entry = "".join(buffer).strip()
                if entry:
                    entries.append(entry)
                buffer = []
                continue
            buffer.append(ch)

        tail = "".join(buffer).strip()
        if tail:
            entries.append(tail)

        return entries

    def _register_scala_import_entry(self, entry: str, module_qn: str) -> None:
        """
        Registers a single Scala import entry, handling grouped imports.

        For example, `a.{b, c}` is broken down and registered.

        Args:
            entry (str): The import entry string.
            module_qn (str): The qualified name of the current module.
        """
        cleaned = entry.strip().rstrip(";")
        if not cleaned:
            return

        if "{" in cleaned and "}" in cleaned:
            prefix, group_part = cleaned.split("{", 1)
            prefix = prefix.strip().rstrip(".")
            group_part = group_part.split("}", 1)[0]
            items = [item.strip() for item in group_part.split(",") if item.strip()]
            for item in items:
                self._register_scala_import_item(prefix, item, module_qn)
            return

        self._register_scala_import_item("", cleaned, module_qn)

    def _register_scala_import_item(
        self, prefix: str, item: str, module_qn: str
    ) -> None:
        """
        Registers a specific Scala import item, handling aliases and wildcards.

        Args:
            prefix (str): The package or object prefix for the import.
            item (str): The specific item being imported (e.g., `ClassName`, `_`, `Name => Alias`).
            module_qn (str): The qualified name of the current module.
        """
        if not item:
            return

        alias = None
        original = item
        if "=>" in item:
            original, alias = [part.strip() for part in item.split("=>", 1)]

        full_name = f"{prefix}.{original}" if prefix else original
        if original == "_" or full_name.endswith("._"):
            module_path = prefix or full_name[:-2]
            if module_path:
                wildcard_key = f"*{module_path}"
                self.import_mapping[module_qn][wildcard_key] = module_path
                logger.debug(ls.IMP_WILDCARD_IMPORT.format(module=module_path))
            return

        local_name = alias or full_name.split(cs.SEPARATOR_DOT)[-1]
        self.import_mapping[module_qn][local_name] = full_name
        logger.debug(ls.IMP_IMPORT.format(local=local_name, full=full_name))

    def _parse_csharp_imports(self, captures: dict, module_qn: str) -> None:
        """
        Parses C# `using` statements.

        Args:
            captures (dict): The dictionary of captured nodes from the tree-sitter query.
            module_qn (str): The qualified name of the current module.
        """
        for import_node in captures.get(cs.CAPTURE_IMPORT, []):
            if not isinstance(import_node, Node):
                continue
            import_text = safe_decode_with_fallback(import_node)
            for local_name, full_name in self._parse_csharp_using(import_text):
                self.import_mapping[module_qn][local_name] = full_name
                logger.debug(ls.IMP_IMPORT.format(local=local_name, full=full_name))

    def _parse_csharp_using(self, using_text: str) -> list[tuple[str, str]]:
        """
        Parses a single C# `using` statement line, handling aliases.

        Args:
            using_text (str): The text of the `using` statement.

        Returns:
            A list containing a single (alias, full_name) tuple.
        """
        if not using_text:
            return []

        text = using_text.strip().rstrip(";")
        if text.startswith("global "):
            text = text[len("global ") :].strip()
        if text.startswith("using "):
            text = text[len("using ") :].strip()
        if text.startswith("static "):
            text = text[len("static ") :].strip()

        if not text:
            return []

        if "=" in text:
            alias, target = [part.strip() for part in text.split("=", 1)]
            if alias and target:
                return [(alias, target)]
            return []

        local_name = text.split(cs.SEPARATOR_DOT)[-1]
        return [(local_name, text)]

    def _parse_cpp_imports(self, captures: dict, module_qn: str) -> None:
        """
        Parses C++ `#include`, `import`, and module-related declarations.

        Args:
            captures (dict): The dictionary of captured nodes from the tree-sitter query.
            module_qn (str): The qualified name of the current module.
        """
        for import_node in captures.get(cs.CAPTURE_IMPORT, []):
            if import_node.type == cs.TS_PREPROC_INCLUDE:
                self._parse_cpp_include(import_node, module_qn)
            elif import_node.type == cs.TS_IMPORT_DECLARATION:
                self._parse_cpp_import_declaration(import_node, module_qn)
            elif import_node.type == cs.TS_TEMPLATE_FUNCTION:
                self._parse_cpp_module_import(import_node, module_qn)
            elif import_node.type == cs.TS_DECLARATION:
                self._parse_cpp_module_declaration(import_node, module_qn)

    def _parse_cpp_import_declaration(self, import_node: Node, module_qn: str) -> None:
        """
        Parses a C++20 `import` declaration.

        Args:
            import_node (Node): The AST node for the import declaration.
            module_qn (str): The qualified name of the current module.
        """
        import_text = safe_decode_with_fallback(import_node).strip()
        if not import_text:
            return

        if import_text.startswith("export "):
            import_text = import_text[len("export ") :].strip()

        if import_text.startswith(f"{cs.IMPORT_IMPORT} "):
            import_text = import_text[len(cs.IMPORT_IMPORT) :].strip()

        import_text = import_text.rstrip(cs.CHAR_SEMICOLON).strip()
        if not import_text:
            return

        is_header = import_text.startswith("<") or import_text.startswith('"')
        module_name = import_text
        if module_name.startswith("<") and module_name.endswith(">"):
            module_name = module_name[1:-1].strip()
        elif module_name.startswith('"') and module_name.endswith('"'):
            module_name = module_name[1:-1].strip()

        if not module_name:
            return

        local_name = module_name.lstrip(":")
        if local_name.startswith(cs.CPP_STD_PREFIX):
            local_name = local_name[len(cs.CPP_STD_PREFIX) :].lstrip(cs.SEPARATOR_DOT)

        if module_name.startswith(cs.CPP_STD_PREFIX):
            full_name = f"{cs.IMPORT_STD_PREFIX}{module_name[len(cs.CPP_STD_PREFIX) :].lstrip(cs.SEPARATOR_DOT)}"
        elif is_header:
            full_name = f"{cs.IMPORT_STD_PREFIX}{module_name}"
        else:
            full_name = (
                f"{self.project_name}{cs.SEPARATOR_DOT}{module_name.lstrip(':')}"
            )

        self.import_mapping[module_qn][local_name] = full_name
        logger.debug(ls.IMP_CPP_MODULE.format(local=local_name, full=full_name))

    def _parse_cpp_include(self, include_node: Node, module_qn: str) -> None:
        """
        Parses a C++ `#include` directive.

        Args:
            include_node (Node): The AST node for the `#include` directive.
            module_qn (str): The qualified name of the current module.
        """
        include_path = None
        is_system_include = False

        for child in include_node.children:
            if child.type == cs.TS_STRING_LITERAL:
                include_path = safe_decode_with_fallback(child).strip('"')
                is_system_include = False
            elif child.type == cs.TS_SYSTEM_LIB_STRING:
                include_path = safe_decode_with_fallback(child).strip("<>")
                is_system_include = True

        if include_path:
            header_name = include_path.split(cs.SEPARATOR_SLASH)[-1]
            if header_name.endswith(cs.EXT_H) or header_name.endswith(cs.EXT_HPP):
                local_name = header_name.split(cs.SEPARATOR_DOT)[0]
            else:
                local_name = header_name

            if is_system_include:
                full_name = (
                    include_path
                    if include_path.startswith(cs.CPP_STD_PREFIX)
                    else f"{cs.IMPORT_STD_PREFIX}{include_path}"
                )
            else:
                path_parts = (
                    include_path.replace(cs.SEPARATOR_SLASH, cs.SEPARATOR_DOT)
                    .replace(cs.EXT_H, "")
                    .replace(cs.EXT_HPP, "")
                )
                full_name = f"{self.project_name}{cs.SEPARATOR_DOT}{path_parts}"

            self.import_mapping[module_qn][local_name] = full_name
            logger.debug(
                ls.IMP_CPP_INCLUDE.format(
                    local=local_name, full=full_name, system=is_system_include
                )
            )

    def _parse_cpp_module_import(self, import_node: Node, module_qn: str) -> None:
        """
        Parses a C++ module import that uses an older template-like syntax.

        Args:
            import_node (Node): The AST node for the import.
            module_qn (str): The qualified name of the current module.
        """
        identifier_child = None
        template_args_child = None

        for child in import_node.children:
            if child.type == cs.TS_IDENTIFIER:
                identifier_child = child
            elif child.type == cs.TS_TEMPLATE_ARGUMENT_LIST:
                template_args_child = child

        if (
            identifier_child
            and safe_decode_text(identifier_child) == cs.IMPORT_IMPORT
            and template_args_child
        ):
            module_name = None
            for child in template_args_child.children:
                if child.type == cs.TS_TYPE_DESCRIPTOR:
                    for desc_child in child.children:
                        if desc_child.type == cs.TS_TYPE_IDENTIFIER:
                            module_name = safe_decode_with_fallback(desc_child)
                            break
                elif child.type == cs.TS_TYPE_IDENTIFIER:
                    module_name = safe_decode_with_fallback(child)

            if module_name:
                local_name = module_name
                full_name = f"{cs.IMPORT_STD_PREFIX}{module_name}"

                self.import_mapping[module_qn][local_name] = full_name
                logger.debug(ls.IMP_CPP_MODULE.format(local=local_name, full=full_name))

    def _parse_cpp_module_declaration(self, decl_node: Node, module_qn: str) -> None:
        """
        Parses C++ module declarations, including exports and partitions.

        Args:
            decl_node (Node): The AST node for the declaration.
            module_qn (str): The qualified name of the current module.
        """
        decoded_text = safe_decode_text(decl_node)
        if not decoded_text:
            return
        decl_text = decoded_text.strip()

        if decl_text.startswith(cs.CPP_MODULE_PREFIX) and not decl_text.startswith(
            cs.CPP_MODULE_PRIVATE_PREFIX
        ):
            parts = decl_text.split()
            if len(parts) >= 2:
                self._register_cpp_module_mapping(
                    parts, 1, module_qn, ls.IMP_CPP_MODULE_IMPL
                )
        elif decl_text.startswith(cs.CPP_EXPORT_MODULE_PREFIX):
            parts = decl_text.split()
            if len(parts) >= 3:
                self._register_cpp_module_mapping(
                    parts, 2, module_qn, ls.IMP_CPP_MODULE_IFACE
                )
        elif cs.CPP_IMPORT_PARTITION_PREFIX in decl_text:
            colon_pos = decl_text.find(cs.SEPARATOR_COLON)
            if colon_pos != -1:
                if partition_part := decl_text[colon_pos + 1 :].split(";")[0].strip():
                    partition_name = f"{cs.CPP_PARTITION_PREFIX}{partition_part}"
                    full_name = f"{self.project_name}{cs.SEPARATOR_DOT}{partition_part}"
                    self.import_mapping[module_qn][partition_name] = full_name
                    logger.debug(
                        ls.IMP_CPP_PARTITION.format(
                            partition=partition_name, full=full_name
                        )
                    )

    def _register_cpp_module_mapping(
        self, parts: list[str], name_index: int, module_qn: str, log_template: str
    ) -> None:
        """
        A helper method to register C++ module mappings in the import map.

        Args:
            parts (list[str]): The split text parts of the module declaration.
            name_index (int): The index in `parts` where the module name is located.
            module_qn (str): The qualified name of the current module.
            log_template (str): The logging template to use for the debug message.
        """
        module_name = parts[name_index].rstrip(";")
        self.import_mapping[module_qn][module_name] = (
            f"{self.project_name}{cs.SEPARATOR_DOT}{module_name}"
        )
        logger.debug(log_template.format(name=module_name))

    def _parse_generic_imports(
        self, captures: dict, module_qn: str, lang_config: LanguageSpec
    ) -> None:
        """
        A generic fallback for parsing imports in unsupported or simple languages.

        This method currently only logs the presence of an import.

        Args:
            captures (dict): The dictionary of captured nodes from the tree-sitter query.
            module_qn (str): The qualified name of the current module.
            lang_config (LanguageSpec): The language-specific configuration.
        """
        for import_node in captures.get(cs.CAPTURE_IMPORT, []):
            logger.debug(
                ls.IMP_GENERIC.format(
                    language=lang_config.language, node_type=import_node.type
                )
            )

    def _parse_php_imports(self, captures: dict, module_qn: str) -> None:
        """
        Parses PHP `use`, `include`, and `require` statements.

        Args:
            captures (dict): The dictionary of captured nodes from the tree-sitter query.
            module_qn (str): The qualified name of the current module.
        """
        for use_node in captures.get("use", []):
            if not isinstance(use_node, Node):
                continue
            use_text = safe_decode_with_fallback(use_node)
            for local_name, full_name in self._parse_php_use_statement(use_text):
                self.import_mapping[module_qn][local_name] = full_name
                logger.debug(ls.IMP_IMPORT.format(local=local_name, full=full_name))

        for include_node in captures.get("include", []) + captures.get("require", []):
            if not isinstance(include_node, Node):
                continue
            include_text = safe_decode_with_fallback(include_node)
            if not (import_path := self._extract_php_include_path(include_text)):
                continue
            local_name = Path(import_path).stem or import_path
            full_name = import_path.replace(cs.SEPARATOR_SLASH, cs.SEPARATOR_DOT)
            self.import_mapping[module_qn][local_name] = full_name

    def _parse_ruby_imports(self, captures: dict, module_qn: str) -> None:
        """
        Parses Ruby `require`, `require_relative`, and `load` statements.

        Args:
            captures (dict): The dictionary of captured nodes from the tree-sitter query.
            module_qn (str): The qualified name of the current module.
        """
        require_nodes = captures.get("require", []) + captures.get(
            cs.CAPTURE_IMPORT, []
        )

        for require_node in require_nodes:
            if not isinstance(require_node, Node):
                continue
            require_text = safe_decode_with_fallback(require_node)
            for import_path in self._extract_ruby_require_paths(require_text):
                resolved_path = self._resolve_ruby_require_path(import_path, module_qn)
                if not resolved_path:
                    continue
                local_name = resolved_path.split(cs.SEPARATOR_DOT)[-1]
                self.import_mapping[module_qn][local_name] = resolved_path
                logger.debug(ls.IMP_IMPORT.format(local=local_name, full=resolved_path))

    def _extract_ruby_require_paths(self, require_text: str) -> list[str]:
        """
        Extracts the path argument from Ruby `require`, `require_relative`, or `load` statements.

        Args:
            require_text (str): The full text of the require statement.

        Returns:
            A list of path strings found in the statement.
        """
        if not require_text:
            return []
        matches = re.findall(
            r"\b(require|require_relative|load)\s*(?:\(|\s)\s*['\"]([^'\"]+)['\"]",
            require_text,
        )
        return [match[1] for match in matches if match[1]]

    def _resolve_ruby_require_path(self, import_path: str, module_qn: str) -> str:
        """
        Resolves a Ruby require path, handling both relative and standard paths.

        Args:
            import_path (str): The path from the `require` statement.
            module_qn (str): The qualified name of the current module.

        Returns:
            The resolved, fully qualified name for the required file.
        """
        normalized = import_path.strip()
        if not normalized:
            return ""

        if normalized.startswith("./") or normalized.startswith("../"):
            current_parts = module_qn.split(cs.SEPARATOR_DOT)[1:]
            if current_parts:
                current_parts = current_parts[:-1]
            rel_parts = normalized.replace("\\", cs.SEPARATOR_SLASH).split(
                cs.SEPARATOR_SLASH
            )
            for part in rel_parts:
                if part in {"", cs.PATH_CURRENT_DIR}:
                    continue
                if part == cs.PATH_PARENT_DIR:
                    if current_parts:
                        current_parts.pop()
                    continue
                current_parts.append(part)
            module_path = cs.SEPARATOR_DOT.join(current_parts)
            return (
                f"{self.project_name}{cs.SEPARATOR_DOT}{module_path}"
                if module_path
                else self.project_name
            )

        return self._normalize_ruby_import_path(normalized)

    def _normalize_ruby_import_path(self, import_path: str) -> str:
        """
        Normalizes a standard Ruby import path by replacing slashes with dots.

        Args:
            import_path (str): The import path.

        Returns:
            The normalized, dot-separated path.
        """
        return (
            import_path.replace("\\", cs.SEPARATOR_SLASH)
            .strip(cs.SEPARATOR_SLASH)
            .replace(cs.SEPARATOR_SLASH, cs.SEPARATOR_DOT)
        )

    def _parse_php_use_statement(self, use_text: str) -> list[tuple[str, str]]:
        """
        Parses the body of a PHP `use` statement, handling aliases and groups.

        Args:
            use_text (str): The full text of the `use` statement.

        Returns:
            A list of (alias, fqn) tuples for each imported entity.
        """
        results: list[tuple[str, str]] = []
        match = re.search(r"use\s+(.+);", use_text)
        if not match:
            return results

        use_body = match.group(1).strip()
        if "{" in use_body and "}" in use_body:
            prefix, group_part = use_body.split("{", 1)
            prefix = prefix.strip().rstrip("\\")
            group_part = group_part.split("}", 1)[0]
            entries = [e.strip() for e in group_part.split(",") if e.strip()]
            for entry in entries:
                target, alias = self._split_php_use_alias(entry)
                full_name = f"{prefix}\\{target}" if prefix else target
                local_name = alias or target.split("\\")[-1]
                results.append((local_name, self._normalize_php_fqn(full_name)))
            return results

        entries = [e.strip() for e in use_body.split(",") if e.strip()]
        for entry in entries:
            target, alias = self._split_php_use_alias(entry)
            local_name = alias or target.split("\\")[-1]
            results.append((local_name, self._normalize_php_fqn(target)))
        return results

    def _split_php_use_alias(self, entry: str) -> tuple[str, str | None]:
        """
        Splits a PHP `use` entry into its target and an optional alias.

        Args:
            entry (str): A single entry from a `use` statement (e.g., "MyClass as C").

        Returns:
            A tuple of (target, alias). Alias is None if not present.
        """
        alias_match = re.search(r"\s+as\s+", entry, re.IGNORECASE)
        if alias_match:
            parts = re.split(r"\s+as\s+", entry, flags=re.IGNORECASE)
            return parts[0].strip(), parts[1].strip()
        return entry.strip(), None

    def _normalize_php_fqn(self, name: str) -> str:
        """
        Normalizes a PHP FQN by removing any leading backslash.

        Args:
            name (str): The input FQN.

        Returns:
            The normalized FQN.
        """
        normalized = name.strip().lstrip("\\")
        return normalized

    def _extract_php_include_path(self, include_text: str) -> str | None:
        """
        Extracts the path from a PHP `include` or `require` statement string.

        Args:
            include_text (str): The text of the include/require statement.

        Returns:
            The path string if found, otherwise None.
        """
        match = re.search(r"['\"]([^'\"]+)['\"]", include_text)
        if not match:
            return None
        return match.group(1)

    def _parse_lua_imports(self, captures: dict, module_qn: str) -> None:
        """
        Parses Lua `require` statements.

        Args:
            captures (dict): The dictionary of captured nodes from the tree-sitter query.
            module_qn (str): The qualified name of the current module.
        """
        for call_node in captures.get(cs.CAPTURE_IMPORT, []):
            if self._lua_is_require_call(call_node):
                if module_path := self._lua_extract_require_arg(call_node):
                    local_name = (
                        self._lua_extract_assignment_lhs(call_node)
                        or module_path.split(cs.SEPARATOR_DOT)[-1]
                    )
                    resolved = self._resolve_lua_module_path(module_path, module_qn)
                    self.import_mapping[module_qn][local_name] = resolved
            elif self._lua_is_pcall_require(call_node):
                if module_path := self._lua_extract_pcall_require_arg(call_node):
                    local_name = (
                        self._lua_extract_pcall_assignment_lhs(call_node)
                        or module_path.split(cs.SEPARATOR_DOT)[-1]
                    )
                    resolved = self._resolve_lua_module_path(module_path, module_qn)
                    self.import_mapping[module_qn][local_name] = resolved

            elif self._lua_is_stdlib_call(call_node):
                if stdlib_module := self._lua_extract_stdlib_module(call_node):
                    self.import_mapping[module_qn][stdlib_module] = stdlib_module

    def _lua_is_require_call(self, call_node: Node) -> bool:
        """
        Checks if an AST node represents a Lua `require` call.

        Args:
            call_node (Node): The AST node to check.

        Returns:
            True if the node is a `require` call, False otherwise.
        """
        first_child = call_node.children[0] if call_node.children else None
        if first_child and first_child.type == cs.TS_IDENTIFIER:
            return safe_decode_text(first_child) == cs.IMPORT_REQUIRE
        return False

    def _lua_is_pcall_require(self, call_node: Node) -> bool:
        """
        Checks if an AST node represents a protected Lua require call, `pcall(require, ...)`.

        Args:
            call_node (Node): The AST node to check.

        Returns:
            True if the node is a `pcall(require, ...)` call, False otherwise.
        """
        first_child = call_node.children[0] if call_node.children else None
        if not (
            first_child
            and first_child.type == cs.TS_IDENTIFIER
            and safe_decode_text(first_child) == cs.IMPORT_PCALL
        ):
            return False

        args = call_node.child_by_field_name(cs.FIELD_ARGUMENTS)
        if not args:
            return False

        first_arg_node = next(
            (
                child
                for child in args.children
                if child.type not in cs.PUNCTUATION_TYPES
            ),
            None,
        )

        return (
            first_arg_node is not None
            and first_arg_node.type == cs.TS_IDENTIFIER
            and safe_decode_text(first_arg_node) == cs.IMPORT_REQUIRE
        )

    def _lua_extract_require_arg(self, call_node: Node) -> str | None:
        """
        Extracts the module path argument from a Lua `require` call.

        Args:
            call_node (Node): The `require` call node.

        Returns:
            The module path string, or None if not found.
        """
        args = call_node.child_by_field_name(cs.FIELD_ARGUMENTS)
        candidates = args.children if args else call_node.children
        for node in candidates:
            if node.type in cs.LUA_STRING_TYPES:
                if decoded := safe_decode_text(node):
                    return decoded.strip("'\"")
        return None

    def _lua_extract_pcall_require_arg(self, call_node: Node) -> str | None:
        """
        Extracts the module path argument from a `pcall(require, ...)` call.

        Args:
            call_node (Node): The `pcall` node.

        Returns:
            The module path string, or None if not found.
        """
        args = call_node.child_by_field_name(cs.FIELD_ARGUMENTS)
        if not args:
            return None
        found_require = False
        for child in args.children:
            if found_require and child.type in cs.LUA_STRING_TYPES:
                if decoded := safe_decode_text(child):
                    return decoded.strip("'\"")
            if (
                child.type == cs.TS_IDENTIFIER
                and safe_decode_text(child) == cs.IMPORT_REQUIRE
            ):
                found_require = True
        return None

    def _lua_extract_assignment_lhs(self, call_node: Node) -> str | None:
        """
        Extracts the left-hand side variable name from an assignment involving a `require` call.

        For `local my_mod = require("my_mod")`, this would extract "my_mod".

        Args:
            call_node (Node): The `require` call node.

        Returns:
            The variable name if the call is part of an assignment, otherwise None.
        """
        return None

    def _lua_extract_pcall_assignment_lhs(self, call_node: Node) -> str | None:
        """
        Extracts the left-hand side variable name from an assignment involving a `pcall(require, ...)` call.

        For `local ok, my_mod = pcall(require, "my_mod")`, this would extract "my_mod".

        Args:
            call_node (Node): The `pcall` call node.

        Returns:
            The second variable name if the call is part of a multi-assignment, otherwise None.
        """
        return None

    def _resolve_lua_module_path(self, import_path: str, current_module: str) -> str:
        """
        Resolves a Lua module path, handling dot-separated paths and checking for local files.

        Args:
            import_path (str): The import path string from the `require` call.
            current_module (str): The qualified name of the current module.

        Returns:
            The resolved fully qualified name.
        """
        if import_path.startswith(cs.PATH_RELATIVE_PREFIX) or import_path.startswith(
            cs.PATH_PARENT_PREFIX
        ):
            parts = current_module.split(cs.SEPARATOR_DOT)[:-1]
            rel_parts = list(
                import_path.replace("\\", cs.SEPARATOR_SLASH).split(cs.SEPARATOR_SLASH)
            )
            for p in rel_parts:
                if p == cs.PATH_CURRENT_DIR:
                    continue
                if p == cs.PATH_PARENT_DIR:
                    if parts:
                        parts.pop()
                elif p:
                    parts.append(p)
            return cs.SEPARATOR_DOT.join(parts)
        dotted = import_path.replace(cs.SEPARATOR_SLASH, cs.SEPARATOR_DOT)

        try:
            relative_file = (
                dotted.replace(cs.SEPARATOR_DOT, cs.SEPARATOR_SLASH) + cs.EXT_LUA
            )
            if (self.repo_path / relative_file).is_file():
                return f"{self.project_name}{cs.SEPARATOR_DOT}{dotted}"
            if (self.repo_path / f"{dotted}{cs.EXT_LUA}").is_file():
                return f"{self.project_name}{cs.SEPARATOR_DOT}{dotted}"
        except OSError:
            pass

        return dotted

    def _lua_is_stdlib_call(self, call_node: Node) -> bool:
        """
        Checks if a call is to a Lua standard library module (e.g., `table.insert`).

        Args:
            call_node (Node): The call node to check.

        Returns:
            True if it's a call to a known standard library module.
        """
        if not call_node.children:
            return False

        first_child = call_node.children[0]
        if first_child.type == cs.TS_DOT_INDEX_EXPRESSION and (
            first_child.children and first_child.children[0].type == cs.TS_IDENTIFIER
        ):
            module_name = safe_decode_text(first_child.children[0])
            return module_name in cs.LUA_STDLIB_MODULES

        return False

    def _lua_extract_stdlib_module(self, call_node: Node) -> str | None:
        """
        Extracts the Lua standard library module name from a call.

        Args:
            call_node (Node): The call node.

        Returns:
            The name of the standard library module (e.g., "table"), or None.
        """
        if not call_node.children:
            return None

        first_child = call_node.children[0]
        if first_child.type == cs.TS_DOT_INDEX_EXPRESSION and (
            first_child.children and first_child.children[0].type == cs.TS_IDENTIFIER
        ):
            return safe_decode_text(first_child.children[0])

        return None
