from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import LanguageQueries

from .query_engine import QueryEngine
from .query_engine_adapter import _QUERY_NAME_MAP


@dataclass
class DeclarativeParseStats:
    """
    Statistics collected during the declarative parsing process.

    Args:
        language (str): The language being parsed (e.g., "python", "javascript").
        functions (int): Number of functions identifiers captured. Defaults to 0.
        classes (int): Number of classes identifiers captured. Defaults to 0.
        calls (int): Number of function calls identifiers captured. Defaults to 0.
        imports (int): Number of import identifiers captured. Defaults to 0.
    """

    language: str
    functions: int = 0
    classes: int = 0
    calls: int = 0
    imports: int = 0


class DeclarativeParser:
    """
    Parses source code using Tree-sitter queries purely for statistical/counting purposes.

    This parser does not construct the graph but counts elements like functions, classes,
    calls, and imports to provide usage statistics.

    Args:
        query_engine (QueryEngine): The engine used to execute Tree-sitter queries.
    """

    def __init__(self, query_engine: QueryEngine) -> None:
        self.query_engine = query_engine

    def parse(
        self,
        root_node: Node,
        language: cs.SupportedLanguage,
        queries: LanguageQueries,
    ) -> DeclarativeParseStats:
        """
        Parses a single file's AST root node and collects statistics.

        Args:
            root_node (Node): The root node of the Tree-sitter AST.
            language (cs.SupportedLanguage): The language of the file.
            queries (LanguageQueries): The collection of queries available for this language.

        Returns:
            DeclarativeParseStats: An object containing counts of found elements.
        """
        language_key = language.value
        query_map = _QUERY_NAME_MAP.get(language, {})

        stats = DeclarativeParseStats(language=language_key)
        if not query_map:
            return stats

        for key, attr in (
            ("functions", "functions"),
            ("classes", "classes"),
            ("calls", "calls"),
            ("imports", "imports"),
        ):
            query_name = query_map.get(key)
            if not query_name:
                continue
            if isinstance(query_name, list):
                count = 0
                for name in query_name:
                    captures = self.query_engine.execute_query(
                        language_key, name, root_node
                    )
                    count += len(captures)
            else:
                captures = self.query_engine.execute_query(
                    language_key, query_name, root_node
                )
                count = len(captures)
            setattr(stats, attr, count)

        return stats

    def process_ast_cache(
        self,
        ast_items: Iterable[tuple[Path, tuple[Node, cs.SupportedLanguage]]],
        queries_map: dict[cs.SupportedLanguage, LanguageQueries],
    ) -> list[DeclarativeParseStats]:
        """
        Processes a batch of pre-parsed ASTs to generate statistics for multiple files.

        Args:
            ast_items (Iterable[tuple[Path, tuple[Node, cs.SupportedLanguage]]]):
                An iterable of (file_path, (root_node, language)) tuples.
            queries_map (dict[cs.SupportedLanguage, LanguageQueries]):
                A mapping of supported languages to their query definitions.

        Returns:
            list[DeclarativeParseStats]: A list of statistics objects, one for each successfully processed file.
        """
        results: list[DeclarativeParseStats] = []
        for _, (root_node, language) in ast_items:
            language_queries = queries_map.get(language)
            if not language_queries:
                continue
            stats = self.parse(root_node, language, language_queries)
            results.append(stats)

        if results:
            total = len(results)
            logger.info(
                "Declarative parser processed {} files (languages: {})",
                total,
                {stat.language for stat in results},
            )
        return results
