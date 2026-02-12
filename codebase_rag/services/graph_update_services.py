"""
This module provides high-level services that orchestrate the graph update process.

It includes services for:
- `GitDeltaService`: Detecting file changes since the last run by comparing Git
  revisions, which enables incremental parsing.
- `ParsePreparationService`: Preparing files for parsing by clearing their old
  state from the graph and caches. It also handles computing "structure signatures"
  to detect if only the relationships (edges) need updating, which is an optimization.
- `FileProcessingService`: The main service that iterates through all relevant
  project files, determines if they need parsing (based on Git delta or cache
  state), and dispatches them to the `DefinitionProcessor`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from loguru import logger
from tree_sitter import Node, QueryCursor

from codebase_rag.core import constants as cs
from codebase_rag.core import logs as ls
from codebase_rag.data_models.types_defs import ASTCacheProtocol
from codebase_rag.graph_db.cypher_queries import (
    CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH,
    CYPHER_DELETE_MODULE_BY_PATH,
)
from codebase_rag.infrastructure.language_spec import get_language_spec_for_path
from codebase_rag.parsers.core.factory import ProcessorFactory
from codebase_rag.parsers.core.incremental_cache import (
    GitDeltaCache,
    IncrementalParsingCache,
)
from codebase_rag.parsers.core.performance_optimizer import ParserPerformanceOptimizer
from codebase_rag.parsers.core.process_manager import ParserProcessManager
from codebase_rag.parsers.core.utils import (
    get_function_captures,
    is_method_node,
    safe_decode_with_fallback,
)

from ..parsers.languages.cpp import utils as cpp_utils
from ..utils.git_delta import filter_existing, get_git_delta, get_git_head
from ..utils.path_utils import should_skip_path, to_posix


class GitDeltaService:
    """
    A service to handle incremental updates based on Git changes.

    This service compares the current Git HEAD with the last processed HEAD to
    determine which files have been added, modified, or deleted. This allows the
    update process to only parse the files that have actually changed.
    """

    def __init__(
        self,
        repo_path: Path,
        git_delta_cache: GitDeltaCache,
        incremental_cache_enabled: bool,
        incremental_cache: IncrementalParsingCache | None,
        remove_file_from_state: Callable[[Path], None],
    ) -> None:
        """
        Initializes the GitDeltaService.

        Args:
            repo_path (Path): The path to the Git repository.
            git_delta_cache (GitDeltaCache): The cache for storing the last processed Git HEAD.
            incremental_cache_enabled (bool): Flag to enable/disable incremental parsing.
            incremental_cache (IncrementalParsingCache | None): The cache for parsing results.
            remove_file_from_state (Callable): A function to call to remove a deleted file's state.
        """
        self.repo_path = repo_path
        self.git_delta_cache = git_delta_cache
        self.incremental_cache_enabled = incremental_cache_enabled
        self.incremental_cache = incremental_cache
        self.remove_file_from_state = remove_file_from_state

    def handle_git_delta(self) -> tuple[set[Path] | None, set[Path] | None]:
        """
        Determines the set of changed and deleted files since the last run.

        Returns:
            A tuple containing (set of changed files, set of deleted files).
            Returns (None, None) if no changes are detected or if it's the first run.
        """
        head = get_git_head(self.repo_path)
        last_head = self.git_delta_cache.get_last_head(self.repo_path)
        if head and last_head and head != last_head:
            changed, deleted = get_git_delta(self.repo_path, last_head)
            changed_filtered = filter_existing(changed)
            for deleted_path in deleted:
                self.remove_file_from_state(deleted_path)
                if self.incremental_cache_enabled and self.incremental_cache:
                    self.incremental_cache.invalidate(deleted_path)
            logger.info(
                "Git delta enabled: {} changed, {} deleted",
                len(changed_filtered),
                len(deleted),
            )
            return changed_filtered, deleted
        if head and not last_head:
            logger.info("Git delta enabled: no previous revision, full parse")
        else:
            logger.info("Git delta enabled: no changes detected")
        return None, None


class FileProcessingContext(Protocol):
    """
    A protocol defining the shared context required for file processing services.
    """

    repo_path: Path
    git_delta_enabled: bool
    git_delta_changed: set[Path] | None
    incremental_cache_enabled: bool
    incremental_cache: IncrementalParsingCache | None
    batch_parse_enabled: bool
    batch_parse_manager: ParserProcessManager | None
    batch_parse_threaded: bool
    selective_update_enabled: bool
    edge_only_update_enabled: bool
    factory: ProcessorFactory
    queries: dict
    parsers: dict
    ast_cache: ASTCacheProtocol
    performance_optimizer: ParserPerformanceOptimizer | None
    unignore_paths: frozenset[str] | None
    exclude_paths: frozenset[str] | None

    dependency_file_checker: Callable[[str, Path], bool]
    parse_strict_enabled: bool


class ParsePreparationService:
    """
    A service for preparing files for re-parsing.

    This includes clearing old data from the graph and computing structural
    signatures to optimize updates.
    """

    def __init__(
        self,
        repo_path: Path,
        project_name: str,
        ingestor,
        factory: ProcessorFactory,
        queries: dict,
        incremental_cache_enabled: bool,
        incremental_cache: IncrementalParsingCache | None,
        remove_file_from_state: Callable[[Path], None],
    ) -> None:
        """
        Initializes the ParsePreparationService.

        Args:
            repo_path (Path): The path to the repository.
            project_name (str): The name of the project.
            ingestor: The graph database ingestor.
            factory (ProcessorFactory): The factory for creating processors.
            queries (dict): A dictionary of tree-sitter queries.
            incremental_cache_enabled (bool): Flag to enable/disable incremental parsing.
            incremental_cache (IncrementalParsingCache | None): The cache for parsing results.
            remove_file_from_state (Callable): A function to remove a file's state.
        """
        self.repo_path = repo_path
        self.project_name = project_name
        self.ingestor = ingestor
        self.factory = factory
        self.queries = queries
        self.incremental_cache_enabled = incremental_cache_enabled
        self.incremental_cache = incremental_cache
        self.remove_file_from_state = remove_file_from_state

    def prepare_file_update(self, file_path: Path) -> None:
        """
        Prepares for a full update of a file by deleting its old module data from the graph.

        Args:
            file_path (Path): The path of the file to be updated.
        """
        self.remove_file_from_state(file_path)

        module_qn = self._module_qn_for_path(file_path)
        self.factory.import_processor.remove_module(module_qn)

        if hasattr(self.ingestor, "execute_write"):
            try:
                relative_path = to_posix(file_path.relative_to(self.repo_path))
                self.ingestor.execute_write(
                    CYPHER_DELETE_MODULE_BY_PATH,
                    {cs.KEY_PATH: relative_path},
                )
            except Exception as exc:
                logger.warning("Selective graph delete failed: {}", exc)

    def prepare_edge_update(self, file_path: Path) -> None:
        """
        Prepares for an edge-only update by deleting only the dynamic relationships of a file.

        This is used when the file's structure (functions, classes) has not changed,
        so only the calls and other relationships need to be re-processed.

        Args:
            file_path (Path): The path of the file to be updated.
        """
        module_qn = self._module_qn_for_path(file_path)
        self.factory.import_processor.remove_module(module_qn)

        if hasattr(self.ingestor, "execute_write"):
            try:
                relative_path = to_posix(file_path.relative_to(self.repo_path))
                self.ingestor.execute_write(
                    CYPHER_DELETE_DYNAMIC_EDGES_BY_PATH,
                    {cs.KEY_PATH: relative_path},
                )
            except Exception as exc:
                logger.warning("Edge-only graph delete failed: {}", exc)

    def get_cached_structure_signature(self, file_path: Path) -> str | None:
        """
        Retrieves the cached structural signature for a file.

        Args:
            file_path (Path): The path of the file.

        Returns:
            The cached signature string, or None if not found.
        """
        if not self.incremental_cache_enabled or not self.incremental_cache:
            return None
        return self.incremental_cache.get_cached_structure_signature(file_path)

    def parse_with_signature(
        self, file_path: Path, language: cs.SupportedLanguage
    ) -> tuple[Node, bytes, str, str] | None:
        """
        Parses a file and computes its structural signature.

        Args:
            file_path (Path): The path of the file.
            language (cs.SupportedLanguage): The language of the file.

        Returns:
            A tuple containing the AST root, source bytes, source text, and signature, or None on failure.
        """
        lang_queries = self.queries.get(language)
        if not lang_queries:
            return None
        parser = lang_queries.get(cs.KEY_PARSER)
        if not parser:
            return None
        source_bytes = file_path.read_bytes()
        source_text = self._decode_source(source_bytes)
        tree = parser.parse(source_bytes)
        root_node = tree.root_node
        signature = self.compute_structure_signature(root_node, language)
        return root_node, source_bytes, source_text, signature

    def compute_structure_signature(
        self, root_node: Node, language: cs.SupportedLanguage
    ) -> str:
        """
        Computes a signature based on the structural elements (classes, functions, methods) of a file.

        This signature can be used to quickly determine if the fundamental structure
        of a file has changed, which is useful for incremental parsing optimizations.

        Args:
            root_node (Node): The root AST node of the file.
            language (cs.SupportedLanguage): The language of the file.

        Returns:
            A SHA256 hash representing the file's structure.
        """
        items: list[str] = []
        class_query = self.queries[language].get(cs.QUERY_CLASSES)
        if class_query:
            cursor = QueryCursor(class_query)
            captures = cursor.captures(root_node)
            class_nodes = captures.get(cs.CAPTURE_CLASS, [])
            for class_node in class_nodes:
                if not isinstance(class_node, Node):
                    continue
                class_name = self._get_class_name_for_node(class_node, language)
                if class_name:
                    items.append(f"class:{class_name}")

        function_result = get_function_captures(root_node, language, self.queries)
        if function_result:
            lang_config, captures = function_result
            func_nodes = captures.get(cs.CAPTURE_FUNCTION, [])
            for func_node in func_nodes:
                if not isinstance(func_node, Node):
                    continue
                if language == cs.SupportedLanguage.CPP:
                    func_name = cpp_utils.extract_function_name(func_node)
                else:
                    func_name = self._get_node_name(func_node)
                if not func_name:
                    continue
                if is_method_node(func_node, lang_config):
                    class_name = self._find_enclosing_class_name(
                        func_node, language, lang_config
                    )
                    if class_name:
                        items.append(f"method:{class_name}.{func_name}")
                    else:
                        items.append(f"method:{func_name}")
                else:
                    items.append(f"function:{func_name}")

        normalized = "|".join(sorted(items))
        return hashlib.sha256(normalized.encode(cs.ENCODING_UTF8)).hexdigest()

    @staticmethod
    def _decode_source(source_bytes: bytes) -> str:
        """Safely decodes source bytes to a string."""
        try:
            return source_bytes.decode(cs.ENCODING_UTF8)
        except Exception:
            return source_bytes.decode(cs.ENCODING_UTF8, errors="ignore")

    def _get_node_name(self, node: Node, field: str = cs.FIELD_NAME) -> str | None:
        """Extracts the name from a node's named field."""
        name_node = node.child_by_field_name(field)
        if not name_node:
            return None
        return safe_decode_with_fallback(name_node)

    def _get_rust_impl_class_name(self, class_node: Node) -> str | None:
        """Extracts the class name from a Rust `impl` block."""
        class_name = self._get_node_name(class_node, cs.FIELD_TYPE)
        if class_name:
            return class_name
        for child in class_node.children:
            if child.type == cs.TS_TYPE_IDENTIFIER and child.is_named and child.text:
                return child.text.decode(cs.ENCODING_UTF8)
        return None

    def _get_class_name_for_node(
        self, class_node: Node, language: cs.SupportedLanguage
    ) -> str | None:
        """Gets the class name, handling language-specific cases like Rust `impl`."""
        if language == cs.SupportedLanguage.RUST and class_node.type == cs.TS_IMPL_ITEM:
            return self._get_rust_impl_class_name(class_node)
        return self._get_node_name(class_node)

    def _find_enclosing_class_name(
        self, node: Node, language: cs.SupportedLanguage, lang_config
    ) -> str | None:
        """Finds the name of the class enclosing a given node."""
        current = node.parent
        while current and current.type not in lang_config.module_node_types:
            if current.type in lang_config.class_node_types:
                return self._get_class_name_for_node(current, language)
            current = current.parent
        return None

    def _module_qn_for_path(self, file_path: Path) -> str:
        """Generates a module qualified name from a file path."""
        relative_path = file_path.relative_to(self.repo_path)
        parts = list(relative_path.with_suffix("").parts)
        if file_path.name in (cs.INIT_PY, cs.MOD_RS):
            parts = list(relative_path.parent.parts)
        return cs.SEPARATOR_DOT.join([self.project_name, *parts])


class FileProcessingService:
    """
    The main service for orchestrating the processing of all files in the repository.
    """

    def __init__(
        self,
        context: FileProcessingContext,
        parse_service: ParsePreparationService,
    ) -> None:
        """
        Initializes the FileProcessingService.

        Args:
            context (FileProcessingContext): The shared context for file processing.
            parse_service (ParsePreparationService): The service for preparing files for parsing.
        """
        self.context = context
        self.parse_service = parse_service

    def process_files(self) -> None:
        """
        Iterates through all relevant files in the repository and processes them.

        This method handles filtering files, checking caches, and dispatching
        files to the `DefinitionProcessor` either individually or in a batch.
        """
        ctx = self.context
        batch_jobs: list[tuple[str, str, Callable[[str, str], bool]]] = []
        target_paths: set[Path] | None = None

        if ctx.git_delta_enabled and ctx.git_delta_changed is not None:
            target_paths = set(ctx.git_delta_changed)
            if not target_paths:
                logger.info("Git delta: no changed files to process")
                return

        for filepath in ctx.repo_path.rglob("*"):
            if filepath.is_file() and not should_skip_path(
                filepath,
                ctx.repo_path,
                exclude_paths=ctx.exclude_paths,
                unignore_paths=ctx.unignore_paths,
            ):
                try:
                    if target_paths is not None and filepath not in target_paths:
                        continue
                    lang_config = get_language_spec_for_path(filepath)

                    if (
                        lang_config
                        and isinstance(lang_config.language, cs.SupportedLanguage)
                        and lang_config.language in ctx.parsers
                    ):
                        if ctx.incremental_cache_enabled and ctx.incremental_cache:
                            if not ctx.incremental_cache.needs_parsing(filepath):
                                logger.debug(
                                    ls.SKIPPED_FILE.format(path=filepath)
                                    if hasattr(ls, "SKIPPED_FILE")
                                    else f"Skipping unchanged file: {filepath}"
                                )
                                ctx.factory.structure_processor.process_generic_file(
                                    filepath, filepath.name
                                )
                                continue

                        if ctx.batch_parse_enabled and ctx.batch_parse_manager:
                            batch_jobs.append(
                                (
                                    str(filepath),
                                    lang_config.language.value,
                                    self._make_parse_job(),
                                )
                            )
                        else:
                            signature: str | None = None
                            parsed_root: Node | None = None
                            source_bytes: bytes | None = None
                            source_text: str | None = None
                            if ctx.selective_update_enabled:
                                parsed = self.parse_service.parse_with_signature(
                                    filepath, lang_config.language
                                )
                                if parsed:
                                    (
                                        parsed_root,
                                        source_bytes,
                                        source_text,
                                        signature,
                                    ) = parsed
                                    cached_signature = self.parse_service.get_cached_structure_signature(
                                        filepath
                                    )
                                    if (
                                        ctx.edge_only_update_enabled
                                        and cached_signature
                                        and cached_signature == signature
                                    ):
                                        self.parse_service.prepare_edge_update(filepath)
                                    else:
                                        self.parse_service.prepare_file_update(filepath)
                                else:
                                    self.parse_service.prepare_file_update(filepath)

                            result = ctx.factory.definition_processor.process_file(
                                filepath,
                                lang_config.language,
                                ctx.queries,
                                ctx.factory.structure_processor.structural_elements,
                                parsed_root=parsed_root,
                                source_bytes=source_bytes,
                                source_text=source_text,
                            )
                            if ctx.dependency_file_checker(filepath.name, filepath):
                                ctx.factory.definition_processor.process_dependencies(
                                    filepath
                                )
                            if result:
                                root_node, language = result
                                ctx.ast_cache[filepath] = (root_node, language)
                                if signature is None:
                                    signature = (
                                        self.parse_service.compute_structure_signature(
                                            root_node, language
                                        )
                                    )

                            if ctx.incremental_cache_enabled and ctx.incremental_cache:
                                metadata = (
                                    {"structure_signature": signature}
                                    if signature
                                    else None
                                )
                                ctx.incremental_cache.cache_result(
                                    filepath,
                                    {
                                        "parsed": True,
                                        "language": lang_config.language.value,
                                    },
                                    language=lang_config.language.value,
                                    metadata=metadata,
                                )

                            if ctx.performance_optimizer:
                                ctx.performance_optimizer.checkpoint()

                    elif ctx.dependency_file_checker(filepath.name, filepath):
                        ctx.factory.definition_processor.process_dependencies(filepath)

                    ctx.factory.structure_processor.process_generic_file(
                        filepath, filepath.name
                    )

                    if ctx.performance_optimizer:
                        ctx.performance_optimizer.checkpoint()
                except Exception as exc:
                    logger.error("File processing failed: {}", filepath)
                    logger.exception(exc)
                    if ctx.parse_strict_enabled:
                        raise

        if ctx.batch_parse_enabled and ctx.batch_parse_manager and batch_jobs:
            if ctx.batch_parse_threaded:
                result = ctx.batch_parse_manager.run_batch_threaded(batch_jobs)
            else:
                result = ctx.batch_parse_manager.run_batch_inline(batch_jobs)
            logger.info(
                "Batch parse completed: {}/{}",
                result.completed,
                result.total_jobs,
            )

    def _make_parse_job(self) -> Callable[[str, str], bool]:
        """
        Creates a closure for a parsing job to be run in a separate process or thread.

        Returns:
            A callable function that takes a file path and language and performs the parsing.
        """
        ctx = self.context

        def _parse(file_path: str, language: str) -> bool:
            path = Path(file_path)
            lang = cs.SupportedLanguage(language)
            signature: str | None = None
            parsed_root: Node | None = None
            source_bytes: bytes | None = None
            source_text: str | None = None

            if ctx.selective_update_enabled:
                parsed = self.parse_service.parse_with_signature(path, lang)
                if parsed:
                    parsed_root, source_bytes, source_text, signature = parsed
                    cached_signature = (
                        self.parse_service.get_cached_structure_signature(path)
                    )
                    if (
                        ctx.edge_only_update_enabled
                        and cached_signature
                        and cached_signature == signature
                    ):
                        self.parse_service.prepare_edge_update(path)
                    else:
                        self.parse_service.prepare_file_update(path)
                else:
                    self.parse_service.prepare_file_update(path)
            result = ctx.factory.definition_processor.process_file(
                path,
                lang,
                ctx.queries,
                ctx.factory.structure_processor.structural_elements,
                parsed_root=parsed_root,
                source_bytes=source_bytes,
                source_text=source_text,
            )
            if ctx.dependency_file_checker(path.name, path):
                ctx.factory.definition_processor.process_dependencies(path)
            if result:
                root_node, detected_language = result
                ctx.ast_cache[path] = (root_node, detected_language)
                if signature is None:
                    signature = self.parse_service.compute_structure_signature(
                        root_node, detected_language
                    )

            if ctx.incremental_cache_enabled and ctx.incremental_cache:
                metadata = {"structure_signature": signature} if signature else None
                ctx.incremental_cache.cache_result(
                    path,
                    {"parsed": True, "language": lang.value},
                    language=lang.value,
                    metadata=metadata,
                )

            return True

        return _parse
