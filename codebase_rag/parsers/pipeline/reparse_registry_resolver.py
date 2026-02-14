"""
This module defines the `ReparseRegistryResolver`, a specialized pass for resolving
function calls using a pre-built function registry.

This resolver is intended to be run after the initial parsing and definition
ingestion phases. It operates on a simplified registry that maps simple function
names to their potential fully qualified definitions. It then re-parses the ASTs
to find call sites and uses this registry to link calls to their definitions,
providing an alternative or supplementary method to the main `CallResolver`. This
can be particularly useful for languages or codebases where the primary resolution
strategy might struggle with complex or dynamic call patterns.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

from tree_sitter import Node, QueryCursor

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import (
    FunctionRegistryTrieProtocol,
    LanguageQueries,
)
from codebase_rag.infrastructure.language_spec import LanguageSpec
from codebase_rag.parsers.core.utils import normalize_query_captures
from codebase_rag.services import IngestorProtocol


class ReparseRegistryResolver:
    """
    Resolves function calls by re-parsing ASTs and using a function registry.

    This class provides a secondary mechanism for call resolution. It builds a
    simple name-to-definition mapping from the main function registry and then
    iterates through the AST of each file to identify function calls. It uses
    this mapping to link calls to their definitions, which can help resolve
    calls missed by the primary `CallResolver`. This pass is controlled by the
    `CODEGRAPH_REPARSE_REGISTRY` environment variable.
    """

    def __init__(
        self,
        ingestor: IngestorProtocol,
        repo_path: Path,
        project_name: str,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        function_registry: FunctionRegistryTrieProtocol,
        module_qn_to_file_path: dict[str, Path],
    ) -> None:
        """
        Initializes the ReparseRegistryResolver.

        Args:
            ingestor (IngestorProtocol): The service for writing data to the graph.
            repo_path (Path): The root path of the repository.
            project_name (str): The name of the project.
            queries (dict): A dictionary of language-specific tree-sitter queries.
            function_registry (FunctionRegistryTrieProtocol): The main registry of all known functions.
            module_qn_to_file_path (dict[str, Path]): A mapping from module FQNs to file paths.
        """
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.queries = queries
        self.function_registry = function_registry
        self.module_qn_to_file_path = module_qn_to_file_path
        self._qn_to_path_cache: dict[str, str] = {}

        self.enabled = os.getenv("CODEGRAPH_REPARSE_REGISTRY", "").lower() not in {
            "0",
            "false",
            "no",
        }

    def process_ast_cache(
        self, ast_items: Iterable[tuple[Path, tuple[Node, cs.SupportedLanguage]]]
    ) -> None:
        """
        Processes cached AST items to resolve function calls using the registry.

        This is the main entry point for the pass. It builds a simplified registry
        and then iterates through each file's AST to resolve calls.

        Args:
            ast_items (Iterable): An iterable of (file_path, (root_node, language)) tuples.
        """
        if not self.enabled:
            return

        registry = self._build_registry()
        for file_path, (root_node, language) in ast_items:
            if language not in {
                cs.SupportedLanguage.PYTHON,
                cs.SupportedLanguage.GO,
                cs.SupportedLanguage.CSHARP,
                cs.SupportedLanguage.PHP,
            }:
                continue
            self._resolve_calls_for_file(file_path, root_node, language, registry)

    def _build_registry(self) -> dict[str, list[dict[str, str]]]:
        """
        Builds a simplified registry mapping simple names to full definition details.

        This creates a dictionary where keys are simple function names (e.g., "my_func")
        and values are a list of potential definitions, each including the full qualified
        name, node type, and file path.

        Returns:
            A dictionary mapping simple names to a list of potential definition matches.
        """
        registry: dict[str, list[dict[str, str]]] = {}
        for qn, node_type in self.function_registry.items():
            simple_name = qn.split(cs.SEPARATOR_DOT)[-1]
            file_path = self._find_file_path_for_qn(qn)
            entry = {
                "qn": qn,
                "type": node_type.value,
                "file_path": file_path or "",
            }
            registry.setdefault(simple_name, []).append(entry)
        return registry

    def _resolve_calls_for_file(
        self,
        file_path: Path,
        root_node: Node,
        language: cs.SupportedLanguage,
        registry: dict[str, list[dict[str, str]]],
    ) -> None:
        """
        Resolves all function calls within a single file using the provided registry.

        Args:
            file_path (Path): The path to the source file.
            root_node (Node): The root AST node of the file.
            language (cs.SupportedLanguage): The programming language of the file.
            registry (dict): The simplified function registry for resolution.
        """
        lang_queries = self.queries.get(language)
        if not lang_queries:
            return
        calls_query = lang_queries.get(cs.QUERY_CALLS)
        if not calls_query:
            return
        lang_config: LanguageSpec = lang_queries[cs.QUERY_CONFIG]

        module_qn = self._module_qn_for_path(file_path)
        cursor = QueryCursor(calls_query)
        captures = normalize_query_captures(cursor.captures(root_node))
        call_nodes = captures.get(cs.CAPTURE_CALL, [])

        for call_node in call_nodes:
            if not isinstance(call_node, Node):
                continue

            caller_qn = self._find_enclosing_caller_qn(
                call_node, module_qn, lang_config
            )
            if not caller_qn:
                continue

            caller_type = self.function_registry.get(caller_qn)
            if not caller_type:
                continue

            call_name = self._get_call_target_name(call_node)
            if not call_name:
                continue

            callee_qn, callee_type = self._resolve_from_registry(
                call_name, registry, str(file_path)
            )
            if not callee_qn or not callee_type:
                continue

            self.ingestor.ensure_relationship_batch(
                (caller_type.value, cs.KEY_QUALIFIED_NAME, caller_qn),
                cs.RelationshipType.CALLS,
                (callee_type, cs.KEY_QUALIFIED_NAME, callee_qn),
                {
                    cs.KEY_RELATION_TYPE: "reparse_registry",
                    "callsite_count": 1,
                    "line": int(call_node.start_point[0]) + 1,
                    "column": int(call_node.start_point[1]),
                    "is_dynamic": False,
                    "confidence": 0.7,
                    "source_parser": f"tree-sitter-{language.value}",
                },
            )

    def _find_enclosing_caller_qn(
        self, call_node: Node, module_qn: str, lang_config: LanguageSpec
    ) -> str | None:
        """
        Finds the qualified name of the function or method that contains the given call node.

        It traverses up the AST from the call node to find the enclosing function/method.

        Args:
            call_node (Node): The AST node representing the function call.
            module_qn (str): The qualified name of the module containing the call.
            lang_config (LanguageSpec): The language-specific configuration.

        Returns:
            The qualified name of the caller, or None if it cannot be determined.
        """
        current = call_node.parent
        function_name: str | None = None
        class_name: str | None = None

        while isinstance(current, Node):
            if (
                function_name is None
                and current.type in lang_config.function_node_types
            ):
                name_node = current.child_by_field_name(cs.FIELD_NAME)
                if name_node and name_node.text:
                    function_name = name_node.text.decode(cs.ENCODING_UTF8)

            if class_name is None and current.type in lang_config.class_node_types:
                name_node = current.child_by_field_name(cs.FIELD_NAME)
                if name_node and name_node.text:
                    class_name = name_node.text.decode(cs.ENCODING_UTF8)

            if function_name and class_name:
                break
            current = current.parent

        if not function_name:
            return None

        if class_name:
            return f"{module_qn}{cs.SEPARATOR_DOT}{class_name}{cs.SEPARATOR_DOT}{function_name}"

        return f"{module_qn}{cs.SEPARATOR_DOT}{function_name}"

    def _resolve_from_registry(
        self, call_name: str, registry: dict[str, list[dict[str, str]]], file_path: str
    ) -> tuple[str | None, str | None]:
        """
        Resolves a call name to a qualified name using the simplified registry.

        It prioritizes candidates defined in the same file to resolve ambiguity.

        Args:
            call_name (str): The simple name of the function being called.
            registry (dict): The function registry mapping simple names to definitions.
            file_path (str): The path of the file where the call occurs.

        Returns:
            A tuple of (qualified_name, node_type), or (None, None) if not resolved.
        """
        simple = re.split(r"[.:]|::", call_name)[-1]
        candidates = registry.get(simple, [])
        if not candidates:
            return None, None

        same_file = [c for c in candidates if c.get("file_path") == file_path]
        if len(same_file) == 1:
            return same_file[0]["qn"], same_file[0]["type"]

        if len(candidates) == 1:
            return candidates[0]["qn"], candidates[0]["type"]

        return None, None

    def _get_call_target_name(self, call_node: Node) -> str | None:
        """
        Extracts the name of the function being called from the call's AST node.

        Args:
            call_node (Node): The AST node representing the call expression.

        Returns:
            The name of the called function as a string, or None if it cannot be extracted.
        """
        if func_child := call_node.child_by_field_name(cs.TS_FIELD_FUNCTION):
            if func_child.text is not None:
                return func_child.text.decode(cs.ENCODING_UTF8)
        if name_node := call_node.child_by_field_name(cs.FIELD_NAME):
            if name_node.text is not None:
                return name_node.text.decode(cs.ENCODING_UTF8)
        return None

    def _find_file_path_for_qn(self, qn: str) -> str | None:
        """
        Finds the file path associated with a given qualified name using a cache.

        It works by progressively shortening the qualified name from the end until
        it finds a match in the `module_qn_to_file_path` mapping.

        Args:
            qn (str): The qualified name to look up.

        Returns:
            The file path as a string, or None if no associated module is found.
        """
        if qn in self._qn_to_path_cache:
            return self._qn_to_path_cache[qn]

        parts = qn.split(cs.SEPARATOR_DOT)
        for i in range(len(parts), 0, -1):
            candidate = cs.SEPARATOR_DOT.join(parts[:i])
            if candidate in self.module_qn_to_file_path:
                path = str(self.module_qn_to_file_path[candidate])
                self._qn_to_path_cache[qn] = path
                return path

        return None

    def _module_qn_for_path(self, file_path: Path) -> str:
        """
        Generates the module qualified name for a given file path.

        Args:
            file_path (Path): The path to the file.

        Returns:
            The fully qualified module name as a string.
        """
        relative_path = file_path.relative_to(self.repo_path)
        parts = list(relative_path.with_suffix("").parts)
        if file_path.name in (cs.INIT_PY, cs.MOD_RS):
            parts = list(relative_path.parent.parts)
        return cs.SEPARATOR_DOT.join([self.project_name, *parts])
