"""
This module is responsible for updating the code graph by parsing the codebase,
identifying structural elements, functions, and their calls, and storing this
information in a graph database.

It uses tree-sitter for parsing source code files into Abstract Syntax Trees (ASTs),
and then processes these ASTs to extract relevant information. The process is
divided into several passes:
1.  **Structure Identification**: Identifies modules, packages, and other structural
    elements of the project.
2.  **File Processing**: Processes individual files to identify function/method
    definitions and other code elements.
3.  **Function Call Processing**: Scans for function and method calls to establish
    relationships between code elements.
4.  **Semantic Embedding Generation**: Generates vector embeddings for code snippets
    to enable semantic search capabilities.

Key components imported and used in this module include:
-   `tree_sitter.Parser`: For parsing source code.
-   `loguru.logger`: For logging progress and issues.
-   `ProcessorFactory`: A factory to create various processors for handling
    different aspects of graph creation (structure, definitions, calls).
-   `IngestorProtocol`, `QueryProtocol`: Protocols for interacting with the graph
    database.
-   `FunctionRegistryTrie`: A trie-based data structure for efficient storage and
    retrieval of function qualified names.
-   `BoundedASTCache`: A cache to store parsed ASTs, optimizing performance by
    avoiding re-parsing of files.
-   Configuration and constants from `.config`, `.constants`, and language-specific
    details from `.language_spec`.
"""

import sys
from collections import OrderedDict, defaultdict
from collections.abc import Callable, ItemsView, KeysView
from pathlib import Path

from loguru import logger
from tree_sitter import Node, Parser

from codebase_rag.core import constants as cs
from codebase_rag.core import logs as ls
from codebase_rag.core.config import settings
from codebase_rag.data_models.types_defs import (
    EmbeddingQueryResult,
    FunctionRegistry,
    LanguageQueries,
    NodeType,
    QualifiedName,
    ResultRow,
    SimpleNameLookup,
    TrieNode,
)
from codebase_rag.infrastructure.language_spec import (
    LANGUAGE_FQN_SPECS,
    get_language_spec,
)
from codebase_rag.parsers.factory import ProcessorFactory
from codebase_rag.services import IngestorProtocol, QueryProtocol
from codebase_rag.utils.dependencies import has_semantic_dependencies
from codebase_rag.utils.fqn_resolver import find_function_source_by_fqn
from codebase_rag.utils.path_utils import should_skip_path
from codebase_rag.utils.source_extraction import extract_source_with_fallback


class FunctionRegistryTrie:
    """A Trie-based registry for storing and querying fully qualified names (FQNs) of functions.

    This class provides an efficient way to store function FQNs and their types,
    allowing for quick lookups, prefix-based searches, and deletions. It uses a
    dictionary-based trie structure for the FQNs and a separate dictionary for
    direct lookups.

    Attributes:
        root (TrieNode): The root of the trie.
        _entries (FunctionRegistry): A dictionary for direct FQN lookups.
        _simple_name_lookup (SimpleNameLookup | None): An optional index for fast
            lookups by simple function name.
    """

    def __init__(self, simple_name_lookup: SimpleNameLookup | None = None) -> None:
        """Initializes the FunctionRegistryTrie.

        Args:
            simple_name_lookup (SimpleNameLookup | None, optional): An index to map
                simple names to a set of FQNs. Defaults to None.
        """
        self.root: TrieNode = {}
        self._entries: FunctionRegistry = {}
        self._simple_name_lookup = simple_name_lookup

    def insert(self, qualified_name: QualifiedName, func_type: NodeType) -> None:
        """Inserts a qualified name and its type into the trie.

        Args:
            qualified_name (QualifiedName): The fully qualified name of the function.
            func_type (NodeType): The type of the function node (e.g., 'function', 'method').
        """
        self._entries[qualified_name] = func_type

        parts = qualified_name.split(cs.SEPARATOR_DOT)
        current: TrieNode = self.root

        for part in parts:
            if part not in current:
                current[part] = {}
            child = current[part]
            assert isinstance(child, dict)
            current = child

        current[cs.TRIE_TYPE_KEY] = func_type
        current[cs.TRIE_QN_KEY] = qualified_name

    def get(
        self, qualified_name: QualifiedName, default: NodeType | None = None
    ) -> NodeType | None:
        """Retrieves the type of a function by its qualified name.

        Args:
            qualified_name (QualifiedName): The FQN of the function.
            default (NodeType | None, optional): The default value to return if the
                name is not found. Defaults to None.

        Returns:
            NodeType | None: The type of the function or the default value.
        """
        return self._entries.get(qualified_name, default)

    def __contains__(self, qualified_name: QualifiedName) -> bool:
        """Checks if a qualified name exists in the registry."""
        return qualified_name in self._entries

    def __getitem__(self, qualified_name: QualifiedName) -> NodeType:
        """Retrieves the type of a function by its qualified name using dictionary-style access."""
        return self._entries[qualified_name]

    def __setitem__(self, qualified_name: QualifiedName, func_type: NodeType) -> None:
        """Inserts or updates a function's type using dictionary-style access."""
        self.insert(qualified_name, func_type)

    def __delitem__(self, qualified_name: QualifiedName) -> None:
        """Deletes a function from the registry by its qualified name."""
        if qualified_name not in self._entries:
            return

        del self._entries[qualified_name]

        parts = qualified_name.split(cs.SEPARATOR_DOT)
        self._cleanup_trie_path(parts, self.root)

    def _cleanup_trie_path(self, parts: list[str], node: TrieNode) -> bool:
        """Recursively cleans up the trie path after a deletion.

        Args:
            parts (list[str]): The parts of the qualified name.
            node (TrieNode): The current node in the trie.

        Returns:
            bool: True if the current node can be deleted, False otherwise.
        """
        if not parts:
            node.pop(cs.TRIE_QN_KEY, None)
            node.pop(cs.TRIE_TYPE_KEY, None)
            return not node

        part = parts[0]
        if part not in node:
            return False

        child = node[part]
        assert isinstance(child, dict)
        if self._cleanup_trie_path(parts[1:], child):
            del node[part]

        is_endpoint = cs.TRIE_QN_KEY in node
        has_children = any(not key.startswith(cs.TRIE_INTERNAL_PREFIX) for key in node)
        return not has_children and not is_endpoint

    def _navigate_to_prefix(self, prefix: str) -> TrieNode | None:
        """Navigates to the node corresponding to a given prefix.

        Args:
            prefix (str): The prefix to navigate to.

        Returns:
            TrieNode | None: The node at the end of the prefix, or None if not found.
        """
        parts = prefix.split(cs.SEPARATOR_DOT) if prefix else []
        current: TrieNode = self.root
        for part in parts:
            if part not in current:
                return None
            child = current[part]
            assert isinstance(child, dict)
            current = child
        return current

    def _collect_from_subtree(
        self,
        node: TrieNode,
        filter_fn: Callable[[QualifiedName], bool] | None = None,
    ) -> list[tuple[QualifiedName, NodeType]]:
        """Collects all function entries from a subtree.

        Args:
            node (TrieNode): The root of the subtree to collect from.
            filter_fn (Callable[[QualifiedName], bool] | None, optional): A function
                to filter the results. Defaults to None.

        Returns:
            list[tuple[QualifiedName, NodeType]]: A list of (qualified_name, type) tuples.
        """
        results: list[tuple[QualifiedName, NodeType]] = []

        def dfs(n: TrieNode) -> None:
            if cs.TRIE_QN_KEY in n:
                qn = n[cs.TRIE_QN_KEY]
                func_type = n[cs.TRIE_TYPE_KEY]
                assert isinstance(qn, str) and isinstance(func_type, NodeType)
                if filter_fn is None or filter_fn(qn):
                    results.append((qn, func_type))

            for key, child in n.items():
                if not key.startswith(cs.TRIE_INTERNAL_PREFIX):
                    assert isinstance(child, dict)
                    dfs(child)

        dfs(node)
        return results

    def keys(self) -> KeysView[QualifiedName]:
        """Returns a view of all qualified names in the registry."""
        return self._entries.keys()

    def items(self) -> ItemsView[QualifiedName, NodeType]:
        """Returns a view of all (qualified_name, type) items in the registry."""
        return self._entries.items()

    def __len__(self) -> int:
        """Returns the number of functions in the registry."""
        return len(self._entries)

    def find_with_prefix_and_suffix(
        self, prefix: str, suffix: str
    ) -> list[QualifiedName]:
        """Finds qualified names that start with a prefix and end with a suffix.

        Args:
            prefix (str): The prefix to match.
            suffix (str): The suffix to match.

        Returns:
            list[QualifiedName]: A list of matching qualified names.
        """
        node = self._navigate_to_prefix(prefix)
        if node is None:
            return []
        suffix_pattern = f".{suffix}"
        matches = self._collect_from_subtree(
            node, lambda qn: qn.endswith(suffix_pattern)
        )
        return [qn for qn, _ in matches]

    def find_ending_with(self, suffix: str) -> list[QualifiedName]:
        """Finds qualified names that end with a given suffix.

        Uses the simple_name_lookup index for O(1) lookup if available, otherwise
        falls back to a linear scan.

        Args:
            suffix (str): The suffix (simple name) to search for.

        Returns:
            list[QualifiedName]: A list of matching qualified names.
        """
        if self._simple_name_lookup is not None and suffix in self._simple_name_lookup:
            # (H) O(1) lookup using the simple_name_lookup index
            return list(self._simple_name_lookup[suffix])
        # (H) Fallback to linear scan if no index available
        return [qn for qn in self._entries.keys() if qn.endswith(f".{suffix}")]

    def find_with_prefix(self, prefix: str) -> list[tuple[QualifiedName, NodeType]]:
        """Finds all functions with a given prefix.

        Args:
            prefix (str): The prefix to search for.

        Returns:
            list[tuple[QualifiedName, NodeType]]: A list of (qualified_name, type) tuples.
        """
        node = self._navigate_to_prefix(prefix)
        return [] if node is None else self._collect_from_subtree(node)


class BoundedASTCache:
    """A bounded cache for storing Abstract Syntax Trees (ASTs).

    This cache stores parsed ASTs to avoid re-parsing files. It has limits on both
    the number of entries and the total memory usage. When limits are exceeded,
    it evicts the least recently used (LRU) items.

    Attributes:
        cache (OrderedDict): The underlying cache storage.
        max_entries (int): The maximum number of entries in the cache.
        max_memory_bytes (int): The maximum memory usage in bytes.
    """

    def __init__(
        self,
        max_entries: int | None = None,
        max_memory_mb: int | None = None,
    ):
        """Initializes the BoundedASTCache.

        Args:
            max_entries (int | None, optional): Maximum number of ASTs to cache.
                Defaults to `settings.CACHE_MAX_ENTRIES`.
            max_memory_mb (int | None, optional): Maximum memory usage in megabytes.
                Defaults to `settings.CACHE_MAX_MEMORY_MB`.
        """
        self.cache: OrderedDict[Path, tuple[Node, cs.SupportedLanguage]] = OrderedDict()
        self.max_entries = (
            max_entries if max_entries is not None else settings.CACHE_MAX_ENTRIES
        )
        max_mem = (
            max_memory_mb if max_memory_mb is not None else settings.CACHE_MAX_MEMORY_MB
        )
        self.max_memory_bytes = max_mem * cs.BYTES_PER_MB

    def __setitem__(self, key: Path, value: tuple[Node, cs.SupportedLanguage]) -> None:
        """Adds or updates an AST in the cache."""
        if key in self.cache:
            del self.cache[key]

        self.cache[key] = value

        self._enforce_limits()

    def __getitem__(self, key: Path) -> tuple[Node, cs.SupportedLanguage]:
        """Retrieves an AST from the cache, marking it as recently used."""
        value = self.cache[key]
        self.cache.move_to_end(key)
        return value

    def __delitem__(self, key: Path) -> None:
        """Deletes an AST from the cache."""
        if key in self.cache:
            del self.cache[key]

    def __contains__(self, key: Path) -> bool:
        """Checks if an AST for a given path is in the cache."""
        return key in self.cache

    def items(self) -> ItemsView[Path, tuple[Node, cs.SupportedLanguage]]:
        """Returns a view of the items in the cache."""
        return self.cache.items()

    def _enforce_limits(self) -> None:
        """Enforces the cache's size and memory limits by evicting LRU items."""
        while len(self.cache) > self.max_entries:
            self.cache.popitem(last=False)  # (H) Remove least recently used

        if self._should_evict_for_memory():
            entries_to_remove = max(
                1, len(self.cache) // settings.CACHE_EVICTION_DIVISOR
            )
            for _ in range(entries_to_remove):
                if self.cache:
                    self.cache.popitem(last=False)

    def _should_evict_for_memory(self) -> bool:
        """Checks if the cache's memory usage exceeds the limit."""
        try:
            cache_size = sum(sys.getsizeof(v) for v in self.cache.values())
            return cache_size > self.max_memory_bytes
        except Exception:
            return (
                len(self.cache)
                > self.max_entries * settings.CACHE_MEMORY_THRESHOLD_RATIO
            )


class GraphUpdater:
    """Orchestrates the process of building and updating the code graph.

    This class coordinates the parsing of a repository, processing of files,
    and ingestion of data into a graph database. It manages the overall workflow,
    including structural analysis, definition and call processing, and generating
    semantic embeddings.

    Attributes:
        ingestor (IngestorProtocol): The service for writing data to the graph.
        repo_path (Path): The root path of the repository being analyzed.
        parsers (dict): A dictionary of tree-sitter parsers for supported languages.
        queries (dict): A dictionary of tree-sitter queries for supported languages.
        project_name (str): The name of the project.
        function_registry (FunctionRegistryTrie): The registry for all identified functions.
        ast_cache (BoundedASTCache): The cache for parsed ASTs.
        factory (ProcessorFactory): The factory for creating processors.
    """

    def __init__(
        self,
        ingestor: IngestorProtocol,
        repo_path: Path,
        parsers: dict[cs.SupportedLanguage, Parser],
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        unignore_paths: frozenset[str] | None = None,
        exclude_paths: frozenset[str] | None = None,
    ):
        """Initializes the GraphUpdater.

        Args:
            ingestor (IngestorProtocol): The data ingestion service.
            repo_path (Path): The path to the repository.
            parsers (dict): Language-specific parsers.
            queries (dict): Language-specific queries.
            unignore_paths (frozenset[str] | None): Paths to include even if ignored by gitignore.
            exclude_paths (frozenset[str] | None): Paths to exclude from processing.
        """
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.parsers = parsers
        self.queries = queries
        self.project_name = repo_path.resolve().name
        self.simple_name_lookup: SimpleNameLookup = defaultdict(set)
        self.function_registry = FunctionRegistryTrie(
            simple_name_lookup=self.simple_name_lookup
        )
        self.ast_cache = BoundedASTCache()
        self.unignore_paths = unignore_paths
        self.exclude_paths = exclude_paths

        self.factory = ProcessorFactory(
            ingestor=self.ingestor,
            repo_path=self.repo_path,
            project_name=self.project_name,
            queries=self.queries,
            function_registry=self.function_registry,
            simple_name_lookup=self.simple_name_lookup,
            ast_cache=self.ast_cache,
            unignore_paths=self.unignore_paths,
            exclude_paths=self.exclude_paths,
        )

    def _is_dependency_file(self, file_name: str, filepath: Path) -> bool:
        """Checks if a file is a dependency management file.

        Args:
            file_name (str): The name of the file.
            filepath (Path): The full path to the file.

        Returns:
            bool: True if it is a dependency file, False otherwise.
        """
        return (
            file_name.lower() in cs.DEPENDENCY_FILES
            or filepath.suffix.lower() == cs.CSPROJ_SUFFIX
        )

    def run(self) -> None:
        """Executes the full graph update process."""
        self.ingestor.ensure_node_batch(
            cs.NODE_PROJECT, {cs.KEY_NAME: self.project_name}
        )
        logger.info(ls.ENSURING_PROJECT.format(name=self.project_name))

        logger.info(ls.PASS_1_STRUCTURE)
        self.factory.structure_processor.identify_structure()

        logger.info(ls.PASS_2_FILES)
        self._process_files()

        logger.info(ls.FOUND_FUNCTIONS.format(count=len(self.function_registry)))
        logger.info(ls.PASS_3_CALLS)
        self._process_function_calls()

        self.factory.definition_processor.process_all_method_overrides()

        logger.info(ls.ANALYSIS_COMPLETE)
        self.ingestor.flush_all()

        self._generate_semantic_embeddings()

    def remove_file_from_state(self, file_path: Path) -> None:
        """Removes all state associated with a file from the updater.

        This includes removing the file's AST from the cache and deleting its
        function definitions from the function registry and simple name lookup.

        Args:
            file_path (Path): The absolute path of the file to remove.
        """
        logger.debug(ls.REMOVING_STATE.format(path=file_path))

        if file_path in self.ast_cache:
            del self.ast_cache[file_path]
            logger.debug(ls.REMOVED_FROM_CACHE)

        relative_path = file_path.relative_to(self.repo_path)
        path_parts = (
            relative_path.parent.parts
            if file_path.name == cs.INIT_PY
            else relative_path.with_suffix("").parts
        )
        module_qn_prefix = cs.SEPARATOR_DOT.join([self.project_name, *path_parts])

        qns_to_remove = set()

        for qn in list(self.function_registry.keys()):
            if qn.startswith(f"{module_qn_prefix}.") or qn == module_qn_prefix:
                qns_to_remove.add(qn)
                del self.function_registry[qn]

        if qns_to_remove:
            logger.debug(ls.REMOVING_QNS.format(count=len(qns_to_remove)))

        for simple_name, qn_set in self.simple_name_lookup.items():
            original_count = len(qn_set)
            new_qn_set = qn_set - qns_to_remove
            if len(new_qn_set) < original_count:
                self.simple_name_lookup[simple_name] = new_qn_set
                logger.debug(ls.CLEANED_SIMPLE_NAME.format(name=simple_name))

    def _process_files(self) -> None:
        """Iterates through all files in the repository and processes them."""
        for filepath in self.repo_path.rglob("*"):
            if filepath.is_file() and not should_skip_path(
                filepath,
                self.repo_path,
                exclude_paths=self.exclude_paths,
                unignore_paths=self.unignore_paths,
            ):
                lang_config = get_language_spec(filepath.suffix)
                if (
                    lang_config
                    and isinstance(lang_config.language, cs.SupportedLanguage)
                    and lang_config.language in self.parsers
                ):
                    result = self.factory.definition_processor.process_file(
                        filepath,
                        lang_config.language,
                        self.queries,
                        self.factory.structure_processor.structural_elements,
                    )
                    if result:
                        root_node, language = result
                        self.ast_cache[filepath] = (root_node, language)
                elif self._is_dependency_file(filepath.name, filepath):
                    self.factory.definition_processor.process_dependencies(filepath)

                self.factory.structure_processor.process_generic_file(
                    filepath, filepath.name
                )

    def _process_function_calls(self) -> None:
        """Processes function calls for all files with cached ASTs."""
        ast_cache_items = list(self.ast_cache.items())
        for file_path, (root_node, language) in ast_cache_items:
            self.factory.call_processor.process_calls_in_file(
                file_path, root_node, language, self.queries
            )

    def _generate_semantic_embeddings(self) -> None:
        """Generates and stores semantic embeddings for functions and methods."""
        if not has_semantic_dependencies():
            logger.info(ls.SEMANTIC_NOT_AVAILABLE)
            return

        if not isinstance(self.ingestor, QueryProtocol):
            logger.info(ls.INGESTOR_NO_QUERY)
            return

        try:
            from codebase_rag.data_models.vector_store import store_embedding

            from .embedder import embed_code

            logger.info(ls.PASS_4_EMBEDDINGS)

            project_name = str(self.project_name).rstrip(".")
            results = self.ingestor.fetch_all(
                cs.CYPHER_QUERY_EMBEDDINGS, {"project_name": project_name}
            )

            if not results:
                logger.info(ls.NO_FUNCTIONS_FOR_EMBEDDING)
                return

            logger.info(ls.GENERATING_EMBEDDINGS.format(count=len(results)))

            embedded_count = 0
            for row in results:
                parsed = self._parse_embedding_result(row)
                if parsed is None:
                    continue

                node_id = parsed[cs.KEY_NODE_ID]
                qualified_name = parsed[cs.KEY_QUALIFIED_NAME]
                start_line = parsed.get(cs.KEY_START_LINE)
                end_line = parsed.get(cs.KEY_END_LINE)
                file_path = parsed.get(cs.KEY_PATH)

                if start_line is None or end_line is None or file_path is None:
                    logger.debug(ls.NO_SOURCE_FOR.format(name=qualified_name))

                elif source_code := self._extract_source_code(
                    qualified_name, file_path, start_line, end_line
                ):
                    try:
                        embedding = embed_code(source_code)
                        store_embedding(node_id, embedding, qualified_name)
                        embedded_count += 1

                        if embedded_count % settings.EMBEDDING_PROGRESS_INTERVAL == 0:
                            logger.debug(
                                ls.EMBEDDING_PROGRESS.format(
                                    done=embedded_count, total=len(results)
                                )
                            )

                    except Exception as e:
                        logger.warning(
                            ls.EMBEDDING_FAILED.format(name=qualified_name, error=e)
                        )
                else:
                    logger.debug(ls.NO_SOURCE_FOR.format(name=qualified_name))
            logger.info(ls.EMBEDDINGS_COMPLETE.format(count=embedded_count))

        except Exception as e:
            logger.warning(ls.EMBEDDING_GENERATION_FAILED.format(error=e))

    def _extract_source_code(
        self, qualified_name: str, file_path: str, start_line: int, end_line: int
    ) -> str | None:
        """Extracts the source code of a function/method.

        It first tries to use an AST-based extractor for more precise extraction,
        and falls back to line-based extraction if the AST is not available or
        the extraction fails.

        Args:
            qualified_name (str): The FQN of the function.
            file_path (str): The path to the source file, relative to the repo root.
            start_line (int): The starting line number.
            end_line (int): The ending line number.

        Returns:
            str | None: The extracted source code, or None if it fails.
        """
        if not file_path or not start_line or not end_line:
            return None

        file_path_obj = self.repo_path / file_path

        ast_extractor = None
        if file_path_obj in self.ast_cache:
            root_node, language = self.ast_cache[file_path_obj]
            fqn_config = LANGUAGE_FQN_SPECS.get(language)

            if fqn_config:

                def ast_extractor_func(qname: str, path: Path) -> str | None:
                    return find_function_source_by_fqn(
                        root_node,
                        qname,
                        path,
                        self.repo_path,
                        self.project_name,
                        fqn_config,
                    )

                ast_extractor = ast_extractor_func

        return extract_source_with_fallback(
            file_path_obj, start_line, end_line, qualified_name, ast_extractor
        )

    def _parse_embedding_result(self, row: ResultRow) -> EmbeddingQueryResult | None:
        """Parses a database result row into an EmbeddingQueryResult.

        Args:
            row (ResultRow): The row from the database query.

        Returns:
            EmbeddingQueryResult | None: The parsed data, or None if parsing fails.
        """
        node_id = row.get(cs.KEY_NODE_ID)
        qualified_name = row.get(cs.KEY_QUALIFIED_NAME)

        if not isinstance(node_id, int) or not isinstance(qualified_name, str):
            return None

        start_line = row.get(cs.KEY_START_LINE)
        end_line = row.get(cs.KEY_END_LINE)
        file_path = row.get(cs.KEY_PATH)

        return EmbeddingQueryResult(
            node_id=node_id,
            qualified_name=qualified_name,
            start_line=start_line if isinstance(start_line, int) else None,
            end_line=end_line if isinstance(end_line, int) else None,
            path=file_path if isinstance(file_path, str) else None,
        )
