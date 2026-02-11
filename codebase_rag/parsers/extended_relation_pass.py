from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from loguru import logger
from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import PropertyValue

from ..parsers.enhanced_function_extractor import EnhancedFunctionExtractor


class ExtendedRelationPass:
    """
    Pass for ingesting extended relationships like types, decorators, and exceptions using EnhancedFunctionExtractor.

    Args:
        ingestor: The ingestor instance.
        repo_path (Path): Path to the repository root.
        project_name (str): Name of the project.
        queries (dict): Language queries.
    """

    def __init__(
        self,
        ingestor,
        repo_path: Path,
        project_name: str,
        queries: dict,
    ) -> None:
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.queries = queries

    def process_ast_cache(
        self, ast_items: Iterable[tuple[Path, tuple[Node, cs.SupportedLanguage]]]
    ) -> None:
        """
        Processes AST items to extract and ingest extended relationships.

        Args:
            ast_items (Iterable[tuple[Path, tuple[Node, cs.SupportedLanguage]]]): Cached AST items.
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
        Ingests type relationships (return types, parameter types).

        Args:
            metadata: FunctionMetadata object.
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
        Ingests decorator relationships.

        Args:
            metadata: FunctionMetadata object.
            language (cs.SupportedLanguage): Language of the file.
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
        Ingests exception relationships (throws, caught_by).

        Args:
            metadata: FunctionMetadata object.
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
        Ensures a TYPE node exists.

        Args:
            type_name (str): Qualified name of the type.
        """
        props: dict[str, PropertyValue] = {
            cs.KEY_QUALIFIED_NAME: type_name,
            cs.KEY_NAME: type_name,
        }
        self.ingestor.ensure_node_batch(cs.NodeLabel.TYPE, props)

    def _ensure_function_node(self, qualified_name: str, name: str) -> None:
        """
        Ensures a FUNCTION node exists (often a placeholder for decorators).

        Args:
            qualified_name (str): Qualified name of the function.
            name (str): Simple name of the function.
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
