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
from codebase_rag.services import IngestorProtocol


class ReparseRegistryResolver:
    """
    Resolves function calls using a function registry.

    This class iterates through the AST of files, identifies function calls,
    and attempts to link them to their definitions using a provided function registry.
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
        Process cached AST items to resolve function calls.

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
        Build a simplified registry mapping simple names to full definitions.

        Returns:
            dict[str, list[dict[str, str]]]: A dictionary mapping simple function names
            to a list of potential matches (dictionaries with qn, type, file_path).
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
        Resolve function calls within a specific file.

        Args:
            file_path (Path): Path to the source file.
            root_node (Node): Root AST node of the file.
            language (cs.SupportedLanguage): The programming language of the file.
            registry (dict): The function registry for resolution.
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
        captures = cursor.captures(root_node)
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
                {cs.KEY_RELATION_TYPE: "reparse_registry"},
            )

    def _find_enclosing_caller_qn(
        self, call_node: Node, module_qn: str, lang_config: LanguageSpec
    ) -> str | None:
        """
        Find the qualified name of the function or method calling the target.

        Args:
            call_node (Node): The AST node representing the function call.
            module_qn (str): The qualified name of the module containing the call.
            lang_config (LanguageSpec): Language-specific configuration.

        Returns:
            str | None: The qualified name of the caller, or None if not found.
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
        Resolve a call name to a qualified name using the registry.

        Args:
            call_name (str): The name of the function being called.
            registry (dict): The function registry.
            file_path (str): The path of the file where the call occurs (for disambiguation).

        Returns:
            tuple[str | None, str | None]: A tuple containing the resolved Qualified Name
            and the Node Type (e.g., 'function', 'method'), or (None, None) if not resolved.
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
        Extract the name of the function being called from the AST node.

        Args:
            call_node (Node): The AST node representing the call.

        Returns:
            str | None: The name of the called function, or None if extraction fails.
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
        Find the file path associated with a given qualified name.

        Args:
            qn (str): The qualified name to look up.

        Returns:
            str | None: The string representation of the file path, or None if not found.
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
        Generate the module qualified name for a given file path.

        Args:
            file_path (Path): The path to the file.

        Returns:
            str: The fully qualified module name.
        """
        relative_path = file_path.relative_to(self.repo_path)
        parts = list(relative_path.with_suffix("").parts)
        if file_path.name in (cs.INIT_PY, cs.MOD_RS):
            parts = list(relative_path.parent.parts)
        return cs.SEPARATOR_DOT.join([self.project_name, *parts])
