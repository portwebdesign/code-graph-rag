from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from tree_sitter import Node, Query, QueryCursor

from codebase_rag.core import constants as cs
from codebase_rag.parsers.core.cache_manager import CacheManager
from codebase_rag.parsers.core.utils import normalize_query_captures

_SCM_LANGUAGE_ALIAS: dict[str, str] = {
    cs.SupportedLanguage.CSHARP.value: "csharp",
}


@dataclass
class QueryInfo:
    """Information about a parsed query."""

    name: str
    query_string: str
    language: str
    compiled_query: Query | None = None


class QueryEngine:
    """
    Declarative tree-sitter query engine.

    Features:
    - Load .scm files with declarative query syntax
    - Compile and cache queries
    - Execute queries with named captures
    - Support multiple languages

    .scm Format:
        ; @query: query_name
        (node_type
          field: (child) @capture) @root
    """

    def __init__(
        self,
        queries_dir: Path | None = None,
        cache_max_entries: int = 256,
        cache_ttl_seconds: float | None = None,
    ):
        """
        Initialize QueryEngine with configurable caching.

        Creates a centralized query engine instance that loads and compiles
        Tree-sitter queries from .scm files. Supports lazy loading and caching
        for optimal performance.

        Args:
            queries_dir: Directory containing .scm files.
                        Defaults to codebase_rag/parsers/queries/
                        Each file should be named {language}.scm (e.g., python.scm)
            cache_max_entries: Maximum number of compiled queries to cache.
                              Default: 256 (sufficient for 18 languages × ~10 queries)
                              Override via CODEGRAPH_QUERY_CACHE_MAX_ENTRIES
            cache_ttl_seconds: Time-to-live for cache entries in seconds.
                              Default: None (infinite)
                              Override via CODEGRAPH_QUERY_CACHE_TTL

        Environment Variables:
            CODEGRAPH_QUERY_CACHE_MAX_ENTRIES: Max cache size
            CODEGRAPH_QUERY_CACHE_TTL: Cache TTL in seconds
            CODEGRAPH_QUERY_CACHE_CLEANUP_INTERVAL: Cleanup interval (default: 30s)

        Example:
            >>> engine = QueryEngine()
            >>> queries = engine.load_queries("python")
            >>> query = engine.get_query("python", "function_definition")
        """
        if queries_dir is None:
            queries_dir = Path(__file__).parent / "queries"

        self.queries_dir = Path(queries_dir)
        self.queries_dir.mkdir(parents=True, exist_ok=True)

        cache_max_env = os.getenv("CODEGRAPH_QUERY_CACHE_MAX_ENTRIES")
        cache_ttl_env = os.getenv("CODEGRAPH_QUERY_CACHE_TTL")
        cache_cleanup_env = os.getenv("CODEGRAPH_QUERY_CACHE_CLEANUP_INTERVAL")
        cache_max = int(cache_max_env) if cache_max_env else cache_max_entries
        cache_ttl = float(cache_ttl_env) if cache_ttl_env else cache_ttl_seconds
        cache_cleanup = float(cache_cleanup_env) if cache_cleanup_env else 30.0

        self._query_cache = CacheManager[Query](
            max_entries=cache_max,
            ttl_seconds=cache_ttl,
            cleanup_interval_seconds=cache_cleanup,
        )

        self._query_definitions: dict[str, dict[str, QueryInfo]] = {}

        self._cache_hits = 0
        self._cache_misses = 0

        logger.debug(f"QueryEngine initialized with queries_dir: {self.queries_dir}")

    def load_queries(self, language: str) -> dict[str, Query]:
        """
        Load and compile all queries for a language from .scm file.

        Parses the .scm file for the given language, extracts all @query: directives,
        compiles them into Tree-sitter Query objects, and caches the results.

        This is the primary entry point for loading language queries. Subsequent calls
        for the same language will return cached results.

        Args:
            language: Language name (e.g., 'python', 'javascript', 'csharp')
                     Must match a .scm filename in queries_dir
                     Supports aliases via _SCM_LANGUAGE_ALIAS

        Returns:
            Dictionary of query_name -> compiled Query
            Example: {
                'function_definition': <Query object>,
                'class_definition': <Query object>,
                'call_edge': <Query object>
            }

        Raises:
            FileNotFoundError: If .scm file doesn't exist (returns empty dict with warning)
            ValueError: If query format is invalid (logged and skipped)

        Performance:
            - First call: Reads file + compiles all queries (~50-200ms)
            - Cached calls: Returns immediately from memory (<1ms)

        Example:
            >>> engine = QueryEngine()
            >>> python_queries = engine.load_queries("python")
            >>> print(list(python_queries.keys()))
            ['function_definition', 'async_function_definition', 'method_definition', ...]
        """
        scm_language = _SCM_LANGUAGE_ALIAS.get(language, language)
        scm_file = self.queries_dir / f"{scm_language}.scm"

        if not scm_file.exists():
            logger.warning(f"No queries file for language: {language}")
            return {}

        if language in self._query_definitions:
            self._cache_hits += 1
            cached_queries = {
                name: info.compiled_query
                for name, info in self._query_definitions[language].items()
                if info.compiled_query is not None
            }
            return cached_queries

        self._cache_misses += 1

        query_infos = self._parse_scm_file(scm_file, language)

        compiled_queries = {}
        for query_name, query_info in query_infos.items():
            try:
                compiled = self._compile_query(
                    language, query_info.query_string, query_name
                )
                compiled_queries[query_name] = compiled
                query_info.compiled_query = compiled
            except Exception as e:
                logger.error(
                    f"Failed to compile query '{query_name}' for {language}: {e}"
                )
                continue

        self._query_definitions[language] = query_infos

        for query_name, compiled in compiled_queries.items():
            self._query_cache.set(f"{language}:{query_name}", compiled)

        logger.info(f"Loaded {len(compiled_queries)} queries for {language}")

        return compiled_queries

    def get_query(self, language: str, query_name: str) -> Query | None:
        """
        Get a single compiled query by name.

        Retrieves a specific query for a language. If the language hasn't been loaded yet,
        automatically triggers load_queries() first. Uses two-level caching for performance.

        Args:
            language: Language name (e.g., 'python', 'javascript')
            query_name: Name of the query as defined in .scm file
                       Example: 'function_definition', 'call_edge', 'import_statement'

        Returns:
            Compiled Query object or None if not found

        Cache Behavior:
            1. Check compiled query cache: "{language}:{query_name}"
            2. If miss, check query definitions cache
            3. If language not loaded, call load_queries()
            4. Return None if query doesn't exist

        Performance:
            - Cache hit: <1ms (memory lookup)
            - Cache miss: Triggers load_queries() (~50-200ms first time)

        Example:
            >>> engine = QueryEngine()
            >>> func_query = engine.get_query("python", "function_definition")
            >>> if func_query:
            ...     captures = func_query.captures(ast_root)
        """
        cached = self._query_cache.get(f"{language}:{query_name}")
        if cached:
            self._cache_hits += 1
            return cached

        if language not in self._query_definitions:
            self.load_queries(language)

        if language in self._query_definitions:
            query_info = self._query_definitions[language].get(query_name)
            if query_info and query_info.compiled_query:
                self._query_cache.set(
                    f"{language}:{query_name}", query_info.compiled_query
                )
                self._cache_hits += 1
                return query_info.compiled_query

        self._cache_misses += 1
        logger.debug(f"Query not found: {language}.{query_name}")
        return None

    def log_cache_stats(self) -> None:
        """
        Log detailed cache statistics for performance monitoring.

        Outputs cache hits, misses, evictions, expirations, and current size
        using loguru.logger.debug(). Useful for tuning cache parameters.

        Statistics Tracked:
            - hits: Number of successful cache lookups
            - misses: Number of cache misses (triggered loads)
            - evictions: Entries removed due to max_entries limit
            - expirations: Entries removed due to TTL expiry
            - size: Current number of cached queries

        Example Output:
            QueryEngine cache stats: hits=1250, misses=18, evictions=0,
            expirations=0, size=42
        """
        stats = self._query_cache.stats()
        logger.debug(
            "QueryEngine cache stats: hits={}, misses={}, evictions={}, expirations={}, size={}",
            stats.hits,
            stats.misses,
            stats.evictions,
            stats.expirations,
            self._query_cache.size(),
        )

    def execute_query(
        self, language: str, query_name: str, node: Node
    ) -> list[tuple[str, Node]]:
        """
        Execute a query and return captures.

        High-level convenience method that retrieves a query and executes it on an AST node.
        Automatically handles query loading and error cases.

        Args:
            language: Language name (e.g., 'python', 'javascript')
            query_name: Query name (e.g., 'function_definition')
            node: Root AST node from Tree-sitter parser
                 Typically the result of parser.parse(source_code).root_node

        Returns:
            List of (capture_name, node) tuples
            Example: [
                ('defined_function', <Node kind='function_definition'>),
                ('function_params', <Node kind='parameters'>)
            ]
            Empty list if query not found or execution fails

        Example:
            >>> engine = QueryEngine()
            >>> tree = python_parser.parse(b"def foo(): pass")
            >>> captures = engine.execute_query("python", "function_definition", tree.root_node)
            >>> for name, node in captures:
            ...     print(f"{name}: {node.text}")
        """
        query = self.get_query(language, query_name)

        if not query:
            logger.warning(f"Query not found: {language}.{query_name}")
            return []

        try:
            cursor = QueryCursor(query)
            captures = normalize_query_captures(cursor.captures(node))
            results: list[tuple[str, Node]] = []
            for capture_name, nodes in captures.items():
                results.extend((capture_name, capture_node) for capture_node in nodes)
            return results
        except Exception as e:
            logger.error(f"Error executing query {language}.{query_name}: {e}")
            return []

    def _parse_scm_file(self, scm_file: Path, language: str) -> dict[str, QueryInfo]:
        """
        Parse .scm file and extract queries.

        Internal method that parses a Scheme-formatted query file and extracts
        individual query definitions marked with @query: directives.

        Format:
            ; @query: query_name
            (query_content)
            ; @query: another_query
            (query_content2)

        Args:
            scm_file: Path to .scm file (e.g., queries/python.scm)
            language: Language name for logging

        Returns:
            Dictionary of query_name -> QueryInfo
            QueryInfo contains: name, query_string, language, compiled_query

        Implementation Details:
            - Uses regex to split file by @query: directives
            - Allows optional leading ';' for comment-style directives
            - Strips whitespace from query strings
            - Warns about empty queries but continues parsing
            - Returns empty dict if no queries found (with warning)

        Regex Pattern:
            r"(?:^|\n)\\s*;?\\s*@query:\\s*(\\w+)\\s*\n"
            r"((?:(?!\n\\s*;?\\s*@query:)[\\s\\S])*)"
        """
        content = scm_file.read_text(encoding="utf-8")
        queries = {}

        query_pattern = (
            r"(?:^|\n)\s*;?\s*@query:\s*(\w+)\s*\n"
            r"((?:(?!\n\s*;?\s*@query:)[\s\S])*)"
        )

        matches = re.finditer(query_pattern, content)

        for match in matches:
            query_name = match.group(1).strip()
            query_string = match.group(2).strip()

            if not query_string:
                logger.warning(f"Empty query: {query_name}")
                continue

            queries[query_name] = QueryInfo(
                name=query_name,
                query_string=query_string,
                language=language,
            )

            logger.debug(
                f"Parsed query {language}.{query_name} ({len(query_string)} chars)"
            )

        if not queries:
            logger.warning(f"No queries found in {scm_file}")

        return queries

    def _compile_query(
        self, language: str, query_string: str, query_name: str = "unnamed"
    ) -> Query:
        """
        Compile a tree-sitter query.

        Internal method that compiles a query string into a Tree-sitter Query object.
        Handles language loading and provides detailed error messages on failure.

        Args:
            language: Language name (e.g., 'python', 'javascript')
            query_string: S-expression query pattern
                         Example: "(function_definition name: (identifier) @func_name) @func"
            query_name: Query name for error logging (default: "unnamed")

        Returns:
            Compiled Query object ready for execution

        Raises:
            Exception: If compilation fails (syntax error, invalid node types, etc.)
                      Error includes first 100 chars of query_string for debugging

        Implementation:
            1. Calls get_parser_and_language() from parser_loader
            2. Validates language object is loaded
            3. Calls lang_obj.query() to compile
            4. Logs success/failure with query name

        Example Query String:
            (function_definition
              name: (identifier) @func_name
              parameters: (parameters)? @params
              body: (block)? @body) @function
        """
        from codebase_rag.infrastructure.parser_loader import get_parser_and_language

        try:
            _, lang_obj = get_parser_and_language(language)
            if not lang_obj:
                raise ValueError(f"Cannot load language: {language}")

            compiled = Query(lang_obj, query_string)

            logger.debug(f"Compiled query {language}.{query_name}")

            return compiled

        except Exception as e:
            logger.error(
                f"Failed to compile {language}.{query_name}: {e}\n"
                f"Query string: {query_string[:100]}..."
            )
            raise

    def stats(self) -> dict:
        """
        Get cache statistics.

        Returns detailed statistics about query engine performance and cache utilization.
        Useful for monitoring, debugging, and tuning cache parameters.

        Returns:
            Dictionary with cache stats:
            {
                'cache_hits': int,           # Successful cache lookups
                'cache_misses': int,         # Cache misses (triggered loads)
                'hit_rate': float,           # Hits / (hits + misses), 0.0-1.0
                'cached_queries': int,       # Current number of cached queries
                'loaded_languages': int,     # Number of languages loaded
                'total_queries_loaded': int  # Total queries across all languages
            }

        Example:
            >>> engine = QueryEngine()
            >>> engine.load_queries("python")
            >>> engine.load_queries("javascript")
            >>> stats = engine.stats()
            >>> print(f"Hit rate: {stats['hit_rate']:.2%}")
            Hit rate: 95.67%
        """
        total = self._cache_hits + self._cache_misses
        hit_rate = self._cache_hits / total if total > 0 else 0

        return {
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "hit_rate": hit_rate,
            "cached_queries": self._query_cache.size(),
            "loaded_languages": len(self._query_definitions),
            "total_queries_loaded": sum(
                len(queries) for queries in self._query_definitions.values()
            ),
        }

    def clear_cache(self):
        """
        Clear all cached queries.

        Removes all compiled Query objects from the cache but preserves
        query definitions. Next get_query() call will recompile from definitions.

        Use Cases:
            - Memory cleanup during long-running processes
            - Testing cache behavior
            - Forcing query recompilation

        Note: Does NOT reload .scm files from disk. Use reload_queries() for that.
        """
        self._query_cache.clear()
        logger.info("Query cache cleared")

    def reload_queries(self, language: str):
        """
        Reload queries for a language.

        Args:
            language: Language name
        """
        cache_keys = (
            self._query_cache.keys()
            if hasattr(self._query_cache, "keys")
            else list(getattr(self._query_cache, "_entries", {}).keys())
        )
        keys_to_remove = [key for key in cache_keys if key.startswith(f"{language}:")]
        for key in keys_to_remove:
            del self._query_cache[key]

        if language in self._query_definitions:
            del self._query_definitions[language]

        self.load_queries(language)
        logger.info(f"Reloaded queries for {language}")


_global_query_engine: QueryEngine | None = None


def get_query_engine(queries_dir: Path | None = None) -> QueryEngine:
    """
    Get or create global QueryEngine instance.

    Args:
        queries_dir: Optional custom queries directory

    Returns:
        QueryEngine instance
    """
    global _global_query_engine

    if _global_query_engine is None:
        _global_query_engine = QueryEngine(queries_dir)

    return _global_query_engine
