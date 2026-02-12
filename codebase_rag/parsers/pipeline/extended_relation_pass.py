"""
This module defines the `ExtendedRelationPass`, a parsing phase that focuses on
extracting and ingesting more detailed, language-specific relationships from the
source code.

While other passes handle core structures like definitions and calls, this pass
digs deeper to find relationships such as:
- Type hints for parameters and return values (`RETURNS_TYPE`, `PARAMETER_TYPE`).
- Decorator or annotation usage (`DECORATES`, `ANNOTATES`).
- Exception handling (`THROWS`, `CAUGHT_BY`).

It uses the `EnhancedFunctionExtractor` to gather this detailed information from
the AST and then creates the corresponding nodes and relationships in the graph,
adding a richer layer of semantic detail to the code representation.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from loguru import logger
from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import PropertyValue
from codebase_rag.parsers.type_inference.enhanced_function_extractor import (
    EnhancedFunctionExtractor,
)


class ExtendedRelationPass:
    """
    A parsing pass for ingesting extended relationships like types, decorators, and exceptions.

    This pass utilizes the `EnhancedFunctionExtractor` to analyze the AST and extract
    detailed metadata about functions and methods, which is then used to create
    rich semantic relationships in the graph.
    """

    def __init__(
        self,
        ingestor,
        repo_path: Path,
        project_name: str,
        queries: dict,
    ) -> None:
        """
        Initializes the ExtendedRelationPass.

        Args:
            ingestor: The ingestor instance for writing to the graph.
            repo_path (Path): The path to the repository root.
            project_name (str): The name of the project.
            queries (dict): A dictionary of language-specific tree-sitter queries.
        """
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.queries = queries

    def process_ast_cache(
        self, ast_items: Iterable[tuple[Path, tuple[Node, cs.SupportedLanguage]]]
    ) -> None:
        """
        Processes cached AST items to extract and ingest extended relationships.

        This is the main entry point for the pass. It iterates through all parsed
        files, uses the `EnhancedFunctionExtractor` to get detailed metadata, and
        then calls ingestion methods for each type of relationship.

        Args:
            ast_items (Iterable): An iterable of (file_path, (root_node, language)) tuples.
        """
        extractor = EnhancedFunctionExtractor(
            repo_path=self.repo_path,
            project_name=self.project_name,
        )

        for file_path, (root_node, language) in ast_items:
            try:
                functions = extractor.extract_from_ast(
                    file_path=file_path,
                    root_node=root_node,
                    language=language,
                    queries=self.queries,
                )
            except Exception as exc:
                logger.warning("Extended relation extraction failed: {}", exc)
                continue

            for metadata in functions:
                self._ingest_type_relations(metadata)
                self._ingest_decorator_relations(metadata, language)
                self._ingest_exception_relations(metadata)

    def _ingest_type_relations(self, metadata) -> None:
        """
        Ingests type-related relationships, such as return types and parameter types.

        Args:
            metadata: A `FunctionMetadata` object containing the extracted type info.
        """
        if metadata.return_type:
            self._ensure_type_node(metadata.return_type)
            self.ingestor.ensure_relationship_batch(
                (metadata.label, cs.KEY_QUALIFIED_NAME, metadata.qualified_name),
                cs.RelationshipType.RETURNS_TYPE,
                (cs.NodeLabel.TYPE, cs.KEY_QUALIFIED_NAME, metadata.return_type),
            )

        for param_name, param_type in metadata.parameter_types:
            self._ensure_type_node(param_type)
            self.ingestor.ensure_relationship_batch(
                (metadata.label, cs.KEY_QUALIFIED_NAME, metadata.qualified_name),
                cs.RelationshipType.PARAMETER_TYPE,
                (cs.NodeLabel.TYPE, cs.KEY_QUALIFIED_NAME, param_type),
                {cs.KEY_NAME: param_name},
            )

    def _ingest_decorator_relations(
        self, metadata, language: cs.SupportedLanguage
    ) -> None:
        """
        Ingests relationships for decorators (Python) or annotations (Java).

        Args:
            metadata: A `FunctionMetadata` object containing decorator info.
            language (cs.SupportedLanguage): The programming language of the file.
        """
        for decorator in metadata.decorators:
            decorator_name = decorator.lstrip("@").strip()
            if not decorator_name:
                continue
            normalized = decorator_name.split("(", 1)[0].strip()
            if not normalized:
                continue

            if cs.SEPARATOR_DOT in normalized:
                decorator_qn = normalized
                decorator_name_simple = normalized.split(cs.SEPARATOR_DOT)[-1]
            else:
                decorator_qn = f"{metadata.module_qn}{cs.SEPARATOR_DOT}{normalized}"
                decorator_name_simple = normalized

            self._ensure_function_node(decorator_qn, decorator_name_simple)

            rel_type = (
                cs.RelationshipType.ANNOTATES
                if language == cs.SupportedLanguage.JAVA
                else cs.RelationshipType.DECORATES
            )

            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, decorator_qn),
                rel_type,
                (metadata.label, cs.KEY_QUALIFIED_NAME, metadata.qualified_name),
            )

    def _ingest_exception_relations(self, metadata) -> None:
        """
        Ingests relationships for exceptions that are thrown or caught.

        Args:
            metadata: A `FunctionMetadata` object containing exception info.
        """
        for exception_type in metadata.thrown_exceptions:
            self._ensure_type_node(exception_type)
            self.ingestor.ensure_relationship_batch(
                (metadata.label, cs.KEY_QUALIFIED_NAME, metadata.qualified_name),
                cs.RelationshipType.THROWS,
                (cs.NodeLabel.TYPE, cs.KEY_QUALIFIED_NAME, exception_type),
            )

        for exception_type in metadata.caught_exceptions:
            self._ensure_type_node(exception_type)
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.TYPE, cs.KEY_QUALIFIED_NAME, exception_type),
                cs.RelationshipType.CAUGHT_BY,
                (metadata.label, cs.KEY_QUALIFIED_NAME, metadata.qualified_name),
            )

    def _ensure_type_node(self, type_name: str) -> None:
        """
        Ensures that a `Type` node exists in the graph for the given type name.

        If the node doesn't exist, it is created.

        Args:
            type_name (str): The qualified name of the type.
        """
        props: dict[str, PropertyValue] = {
            cs.KEY_QUALIFIED_NAME: type_name,
            cs.KEY_NAME: type_name,
        }
        self.ingestor.ensure_node_batch(cs.NodeLabel.TYPE, props)

    def _ensure_function_node(self, qualified_name: str, name: str) -> None:
        """
        Ensures that a `Function` node exists, often as a placeholder for a decorator.

        If the decorator function itself is not found in the codebase, this creates
        a placeholder node to represent it, allowing the relationship to be formed.

        Args:
            qualified_name (str): The fully qualified name of the function.
            name (str): The simple name of the function.
        """
        props: dict[str, PropertyValue] = {
            cs.KEY_QUALIFIED_NAME: qualified_name,
            cs.KEY_NAME: name,
            cs.KEY_DECORATORS: [],
            cs.KEY_DECORATORS_NORM: [],
            cs.KEY_IS_PLACEHOLDER: True,
            cs.KEY_SYMBOL_KIND: cs.NodeLabel.FUNCTION.value.lower(),
        }
        self.ingestor.ensure_node_batch(cs.NodeLabel.FUNCTION, props)
