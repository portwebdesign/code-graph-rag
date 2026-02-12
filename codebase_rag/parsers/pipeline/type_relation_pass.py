"""
This module defines the `TypeRelationPass`, a parsing phase dedicated to identifying
and creating relationships between types, such as class inheritance, interface
implementation, and struct embedding.

After the initial definition pass has identified all the classes, interfaces, and
other types, this pass runs to connect them. It uses regular expressions to parse
inheritance and implementation clauses from the source code of supported languages
(currently C#, Go, and PHP). It then creates `INHERITS`, `IMPLEMENTS`, or `EMBEDS`
relationships in the graph database, building out the type hierarchy of the codebase.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from pathlib import Path

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import (
    FunctionRegistryTrieProtocol,
    LanguageQueries,
)
from codebase_rag.services import IngestorProtocol


class TypeRelationPass:
    """
    Resolves and creates type relationships like inheritance and implementation.

    This pass processes the AST cache, focusing on languages with explicit type
    hierarchies like C#, Go, and PHP. It uses regular expressions to find
    class/interface definitions and their parent types, then creates the
    corresponding `INHERITS`, `IMPLEMENTS`, or `EMBEDS` relationships in the graph.
    This pass is controlled by the `CODEGRAPH_TYPE_RELATIONS` environment variable.
    """

    def __init__(
        self,
        ingestor: IngestorProtocol,
        repo_path: Path,
        project_name: str,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        function_registry: FunctionRegistryTrieProtocol,
    ) -> None:
        """
        Initializes the TypeRelationPass.

        Args:
            ingestor (IngestorProtocol): The service for writing data to the graph.
            repo_path (Path): The root path of the repository.
            project_name (str): The name of the project.
            queries (dict): A dictionary of language-specific tree-sitter queries.
            function_registry (FunctionRegistryTrieProtocol): A trie of all known functions/types.
        """
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.queries = queries
        self.function_registry = function_registry

        self.enabled = os.getenv("CODEGRAPH_TYPE_RELATIONS", "").lower() not in {
            "0",
            "false",
            "no",
        }

    def process_ast_cache(
        self, ast_items: Iterable[tuple[Path, tuple[object, cs.SupportedLanguage]]]
    ) -> None:
        """
        Processes the cached ASTs to find and create type relationships.

        This is the main entry point for the pass. It iterates through all parsed
        files and dispatches to language-specific handlers.

        Args:
            ast_items (Iterable): An iterable of (file_path, (root_node, language)) tuples.
        """
        if not self.enabled:
            return

        for file_path, (_, language) in ast_items:
            if language not in {
                cs.SupportedLanguage.CSHARP,
                cs.SupportedLanguage.GO,
                cs.SupportedLanguage.PHP,
            }:
                continue
            try:
                source = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            module_qn = self._module_qn_for_path(file_path)

            if language == cs.SupportedLanguage.CSHARP:
                self._process_csharp_types(source, module_qn)
            elif language == cs.SupportedLanguage.GO:
                self._process_go_types(source, module_qn)
            elif language == cs.SupportedLanguage.PHP:
                self._process_php_types(source, module_qn)

    def _process_csharp_types(self, source: str, module_qn: str) -> None:
        """
        Extracts and links C# types (classes, interfaces) and their relationships.

        Args:
            source (str): The source code content of the C# file.
            module_qn (str): The qualified name of the module.
        """
        class_pattern = re.compile(
            r"\b(class|struct)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<generics><[^>{}]+>)?\s*(?:\:\s*(?P<parents>[^\{]+))?",
            re.IGNORECASE,
        )
        interface_pattern = re.compile(
            r"\binterface\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<generics><[^>{}]+>)?\s*(?:\:\s*(?P<parents>[^\{]+))?",
            re.IGNORECASE,
        )

        for match in class_pattern.finditer(source):
            class_name = match.group("name")
            class_qn, class_label = self._resolve_type_qn(
                class_name, module_qn, default_label=cs.NodeLabel.CLASS
            )
            if not class_qn:
                continue

            self._ingest_type_parameters(class_qn, class_label, match.group("generics"))

            parents = self._split_parents(match.group("parents"))
            self._link_csharp_parents(class_qn, class_label, parents, module_qn)

        for match in interface_pattern.finditer(source):
            interface_name = match.group("name")
            interface_qn, _ = self._resolve_type_qn(
                interface_name, module_qn, default_label=cs.NodeLabel.INTERFACE
            )
            if not interface_qn:
                continue

            self._ingest_type_parameters(
                interface_qn, cs.NodeLabel.INTERFACE, match.group("generics")
            )

            parents = self._split_parents(match.group("parents"))
            for parent in parents:
                parent_qn, _ = self._resolve_type_qn(
                    parent, module_qn, default_label=cs.NodeLabel.INTERFACE
                )
                if not parent_qn:
                    continue
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.INTERFACE, cs.KEY_QUALIFIED_NAME, interface_qn),
                    cs.RelationshipType.INHERITS,
                    (cs.NodeLabel.INTERFACE, cs.KEY_QUALIFIED_NAME, parent_qn),
                )

    def _process_go_types(self, source: str, module_qn: str) -> None:
        """
        Extracts and links Go types (structs, interfaces) and their embeddings.

        Args:
            source (str): The source code content of the Go file.
            module_qn (str): The qualified name of the module.
        """
        type_pattern = re.compile(
            r"\btype\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<generics>\[[^\]]+\])?\s*(?P<kind>struct|interface)\s*\{(?P<body>[\s\S]*?)\}",
            re.IGNORECASE,
        )

        for match in type_pattern.finditer(source):
            name = match.group("name")
            kind = match.group("kind").lower()
            body = match.group("body") or ""

            label = cs.NodeLabel.CLASS if kind == "struct" else cs.NodeLabel.INTERFACE
            type_qn, type_label = self._resolve_type_qn(name, module_qn, label)
            if not type_qn:
                continue

            self._ingest_type_parameters(type_qn, type_label, match.group("generics"))

            if kind == "struct":
                embedded = self._extract_go_embedded_types(body)
                for embed in embedded:
                    target_qn, target_label = self._resolve_type_qn(
                        embed, module_qn, default_label=cs.NodeLabel.CLASS
                    )
                    if not target_qn:
                        continue
                    self.ingestor.ensure_relationship_batch(
                        (type_label, cs.KEY_QUALIFIED_NAME, type_qn),
                        cs.RelationshipType.EMBEDS,
                        (target_label, cs.KEY_QUALIFIED_NAME, target_qn),
                    )
            else:
                embedded = self._extract_go_embedded_types(body)
                for embed in embedded:
                    target_qn, _ = self._resolve_type_qn(
                        embed, module_qn, default_label=cs.NodeLabel.INTERFACE
                    )
                    if not target_qn:
                        continue
                    self.ingestor.ensure_relationship_batch(
                        (cs.NodeLabel.INTERFACE, cs.KEY_QUALIFIED_NAME, type_qn),
                        cs.RelationshipType.INHERITS,
                        (cs.NodeLabel.INTERFACE, cs.KEY_QUALIFIED_NAME, target_qn),
                    )

    def _process_php_types(self, source: str, module_qn: str) -> None:
        """
        Extracts and links PHP types (classes, interfaces) and their inheritance.

        Args:
            source (str): The source code content of the PHP file.
            module_qn (str): The qualified name of the module.
        """
        class_pattern = re.compile(
            r"\bclass\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:extends\s+(?P<extends>[A-Za-z_][A-Za-z0-9_\\]+))?\s*(?:implements\s+(?P<impls>[^\{]+))?",
            re.IGNORECASE,
        )
        interface_pattern = re.compile(
            r"\binterface\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:extends\s+(?P<extends>[^\{]+))?",
            re.IGNORECASE,
        )

        for match in class_pattern.finditer(source):
            class_name = match.group("name")
            class_qn, class_label = self._resolve_type_qn(
                class_name, module_qn, default_label=cs.NodeLabel.CLASS
            )
            if not class_qn:
                continue

            if extends := match.group("extends"):
                parent_qn, parent_label = self._resolve_type_qn(
                    extends, module_qn, default_label=cs.NodeLabel.CLASS
                )
                if parent_qn:
                    self.ingestor.ensure_relationship_batch(
                        (class_label, cs.KEY_QUALIFIED_NAME, class_qn),
                        cs.RelationshipType.INHERITS,
                        (parent_label, cs.KEY_QUALIFIED_NAME, parent_qn),
                    )

            impls = self._split_parents(match.group("impls"))
            for impl in impls:
                interface_qn, _ = self._resolve_type_qn(
                    impl, module_qn, default_label=cs.NodeLabel.INTERFACE
                )
                if not interface_qn:
                    continue
                self.ingestor.ensure_relationship_batch(
                    (class_label, cs.KEY_QUALIFIED_NAME, class_qn),
                    cs.RelationshipType.IMPLEMENTS,
                    (cs.NodeLabel.INTERFACE, cs.KEY_QUALIFIED_NAME, interface_qn),
                )

        for match in interface_pattern.finditer(source):
            name = match.group("name")
            interface_qn, _ = self._resolve_type_qn(
                name, module_qn, default_label=cs.NodeLabel.INTERFACE
            )
            if not interface_qn:
                continue

            parents = self._split_parents(match.group("extends"))
            for parent in parents:
                parent_qn, _ = self._resolve_type_qn(
                    parent, module_qn, default_label=cs.NodeLabel.INTERFACE
                )
                if not parent_qn:
                    continue
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.INTERFACE, cs.KEY_QUALIFIED_NAME, interface_qn),
                    cs.RelationshipType.INHERITS,
                    (cs.NodeLabel.INTERFACE, cs.KEY_QUALIFIED_NAME, parent_qn),
                )

    def _link_csharp_parents(
        self,
        class_qn: str,
        class_label: str,
        parents: list[str],
        module_qn: str,
    ) -> None:
        """
        Links a C# class to its parent class and implemented interfaces.

        In C#, the first parent is the base class (if it's not an interface),
        and the rest are interfaces.

        Args:
            class_qn (str): The qualified name of the child class.
            class_label (str): The node label of the child (e.g., 'Class').
            parents (list[str]): A list of parent type names.
            module_qn (str): The qualified name of the current module.
        """
        if not parents:
            return

        parent_qns: list[tuple[str, str]] = []
        for parent in parents:
            default_label = cs.NodeLabel.CLASS
            if re.match(r"^I[A-Z]", parent):
                default_label = cs.NodeLabel.INTERFACE
            parent_qn, parent_label = self._resolve_type_qn(
                parent, module_qn=module_qn, default_label=default_label
            )
            if parent_qn:
                parent_qns.append((parent_qn, parent_label))

        if not parent_qns:
            return

        base_set = False
        for parent_qn, parent_label in parent_qns:
            if not base_set and parent_label != cs.NodeLabel.INTERFACE:
                self.ingestor.ensure_relationship_batch(
                    (class_label, cs.KEY_QUALIFIED_NAME, class_qn),
                    cs.RelationshipType.INHERITS,
                    (parent_label, cs.KEY_QUALIFIED_NAME, parent_qn),
                )
                base_set = True
                continue

            self.ingestor.ensure_relationship_batch(
                (class_label, cs.KEY_QUALIFIED_NAME, class_qn),
                cs.RelationshipType.IMPLEMENTS,
                (cs.NodeLabel.INTERFACE, cs.KEY_QUALIFIED_NAME, parent_qn),
            )

        if not base_set and parent_qns:
            parent_qn, parent_label = parent_qns[0]
            self.ingestor.ensure_relationship_batch(
                (class_label, cs.KEY_QUALIFIED_NAME, class_qn),
                cs.RelationshipType.INHERITS,
                (parent_label, cs.KEY_QUALIFIED_NAME, parent_qn),
            )

    def _split_parents(self, raw: str | None) -> list[str]:
        """
        Parses a string of parent types (e.g., "Base, IInterface<T>") into a list.

        This helper cleans the input string and splits it by commas, removing
        generic type arguments for simplicity.

        Args:
            raw (str | None): The raw string from the class/interface definition.

        Returns:
            A list of clean parent type names.
        """
        if not raw:
            return []
        cleaned = raw.split("{")[0]
        parts = [p.strip() for p in cleaned.split(",") if p.strip()]
        return [p.split("<", 1)[0].strip() for p in parts]

    def _ingest_type_parameters(
        self, type_qn: str, type_label: str, generics_raw: str | None
    ) -> None:
        """
        Ingests generic type parameters for a given type.

        This creates `Type` nodes for each parameter (e.g., 'T', 'U') and links
        them to the parent type with a `HAS_TYPE_PARAMETER` relationship.

        Args:
            type_qn (str): The qualified name of the generic type.
            type_label (str): The node label of the generic type.
            generics_raw (str | None): The raw string of generic parameters (e.g., "<T, U>").
        """
        if not generics_raw:
            return
        params = self._parse_type_parameters(generics_raw)
        if not params:
            return

        self.ingestor.ensure_node_batch(
            type_label,
            {
                cs.KEY_QUALIFIED_NAME: type_qn,
                cs.KEY_TYPE_PARAMETERS: json.dumps(params, ensure_ascii=False),
            },
        )

        for param in params:
            param_qn = f"{type_qn}{cs.SEPARATOR_DOT}typeparam.{param}"
            self.ingestor.ensure_node_batch(
                cs.NodeLabel.TYPE,
                {
                    cs.KEY_QUALIFIED_NAME: param_qn,
                    cs.KEY_NAME: param,
                    cs.KEY_IS_EXTERNAL: False,
                },
            )
            self.ingestor.ensure_relationship_batch(
                (type_label, cs.KEY_QUALIFIED_NAME, type_qn),
                cs.RelationshipType.HAS_TYPE_PARAMETER,
                (cs.NodeLabel.TYPE, cs.KEY_QUALIFIED_NAME, param_qn),
            )

    @staticmethod
    def _parse_type_parameters(generics_raw: str) -> list[str]:
        """
        Parses a generics string (e.g., "<T, U where T: Clone>") into a list of names.

        Args:
            generics_raw (str): The raw string from the type definition.

        Returns:
            A list of the type parameter names (e.g., ['T', 'U']).
        """
        raw = generics_raw.strip().strip("<>").strip("[]")
        if not raw:
            return []
        parts = []
        for item in raw.split(","):
            name = item.strip().split(" ", 1)[0].strip()
            if name:
                parts.append(name)
        return parts

    def _extract_go_embedded_types(self, body: str) -> list[str]:
        """
        Extracts embedded types from the body of a Go struct or interface.

        In Go, embedding is done by listing a type name on its own line within
        the struct or interface body.

        Args:
            body (str): The source code of the struct or interface body.

        Returns:
            A list of the names of the embedded types.
        """
        embedded: list[str] = []
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            if line.startswith("/"):
                continue

            line = line.split("//", 1)[0].strip()
            token = line.split("`")[0].strip()
            if not token:
                continue
            parts = token.split()
            if len(parts) == 1:
                embedded.append(parts[0].lstrip("*"))
        return embedded

    def _resolve_type_qn(
        self,
        name: str,
        module_qn: str | None,
        default_label: str,
    ) -> tuple[str | None, str]:
        """
        Resolves a type name to its qualified name and node label.

        It first searches the `function_registry` for a known type. If not found,
        it creates a placeholder node for the type, assuming it's external or
        defined elsewhere.

        Args:
            name (str): The simple or partially qualified name of the type.
            module_qn (str | None): The qualified name of the current module for context.
            default_label (str): The node label to use if a placeholder is created.

        Returns:
            A tuple of (qualified_name, node_label).
        """
        candidates = self.function_registry.find_ending_with(name)
        for qn in candidates:
            node_type = self.function_registry.get(qn)
            if node_type:
                return qn, node_type.value

        if module_qn:
            fallback_qn = f"{module_qn}{cs.SEPARATOR_DOT}{name}"
        else:
            fallback_qn = f"{self.project_name}{cs.SEPARATOR_DOT}{name}"

        self.ingestor.ensure_node_batch(
            default_label,
            {
                cs.KEY_QUALIFIED_NAME: fallback_qn,
                cs.KEY_NAME: name,
                cs.KEY_IS_EXTERNAL: True,
                cs.KEY_IS_PLACEHOLDER: True,
            },
        )
        return fallback_qn, default_label

    def _module_qn_for_path(self, file_path: Path) -> str:
        """
        Generates the module qualified name for a given file path.

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
