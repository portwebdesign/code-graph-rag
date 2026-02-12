"""
This module defines a collection of services that run after the main parsing and
definition ingestion phases are complete.

These "post-services" are responsible for building the richer, more complex
layers of the code graph. This includes resolving relationships between code
elements, generating semantic embeddings for search, running static analysis,
and logging performance metrics. Each service is encapsulated in its own class
to maintain a clear separation of concerns.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from loguru import logger
from tree_sitter import Node

from codebase_rag.analysis import AnalysisRunner
from codebase_rag.core import constants as cs
from codebase_rag.core import logs as ls
from codebase_rag.core.config import settings
from codebase_rag.data_models.types_defs import EmbeddingQueryResult, ResultRow
from codebase_rag.infrastructure.language_spec import LANGUAGE_FQN_SPECS
from codebase_rag.parsers.core.incremental_cache import GitDeltaCache
from codebase_rag.parsers.core.pre_scanner import PreScanIndex, PreScanner
from codebase_rag.parsers.pipeline.cross_file_resolver import CrossFileResolver
from codebase_rag.parsers.query.declarative_parser import DeclarativeParser
from codebase_rag.utils.dependencies import has_semantic_dependencies
from codebase_rag.utils.fqn_resolver import find_function_source_by_fqn
from codebase_rag.utils.git_delta import get_git_head
from codebase_rag.utils.source_extraction import extract_source_with_fallback

from .protocols import QueryProtocol


class AstCacheProtocol(Protocol):
    """A protocol defining the interface for an AST cache."""

    def items(self) -> Iterable[tuple[Path, tuple[Node, cs.SupportedLanguage]]]: ...

    def __contains__(self, key: Path) -> bool: ...

    def __getitem__(self, key: Path) -> tuple[Node, cs.SupportedLanguage]: ...


class ResolverPassService:
    """
    A service that orchestrates various relationship resolution passes.
    """

    def __init__(
        self,
        ingestor,
        repo_path: Path,
        project_name: str,
        queries: dict,
        function_registry,
        import_processor,
        module_qn_to_file_path: dict,
        pre_scan_index,
    ) -> None:
        """Initializes the resolver service."""
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.queries = queries
        self.function_registry = function_registry
        self.import_processor = import_processor
        self.module_qn_to_file_path = module_qn_to_file_path
        self.pre_scan_index = pre_scan_index

    def process_framework_links(self, simple_name_lookup) -> None:
        """Processes and links framework-specific entities like API endpoints."""
        try:
            from codebase_rag.parsers.frameworks.framework_linker import FrameworkLinker

            FrameworkLinker(
                repo_path=self.repo_path,
                project_name=self.project_name,
                ingestor=self.ingestor,
                function_registry=self.function_registry,
                simple_name_lookup=simple_name_lookup,
            ).link_repo()
        except Exception as exc:
            logger.warning("Framework linker failed: {}", exc)

    def process_tailwind_usage(self, ast_cache: AstCacheProtocol) -> None:
        """Processes and links Tailwind CSS class usage."""
        try:
            from codebase_rag.parsers.frameworks.tailwind_processor import (
                TailwindUsageProcessor,
            )

            TailwindUsageProcessor(
                ingestor=self.ingestor,
                repo_path=self.repo_path,
                project_name=self.project_name,
                queries=self.queries,
            ).process_ast_cache(ast_cache.items())
        except Exception as exc:
            logger.warning("Tailwind usage processor failed: {}", exc)

    def process_function_calls(
        self,
        ast_cache: AstCacheProtocol,
        call_processor,
        queries: dict,
    ) -> None:
        """Processes all function and method calls in the cached ASTs."""
        ast_cache_items = list(ast_cache.items())
        for file_path, (root_node, language) in ast_cache_items:
            call_processor.process_calls_in_file(
                file_path, root_node, language, queries
            )

    def process_resolver_pass(self, ast_cache: AstCacheProtocol) -> None:
        """Runs the main resolver pass to link imports and other relationships."""
        try:
            from codebase_rag.parsers.pipeline.resolver_pass import ResolverPass

            ResolverPass(
                ingestor=self.ingestor,
                repo_path=self.repo_path,
                project_name=self.project_name,
                queries=self.queries,
                function_registry=self.function_registry,
                import_processor=self.import_processor,
                module_qn_to_file_path=self.module_qn_to_file_path,
                pre_scan_index=self.pre_scan_index,
            ).process_ast_cache(ast_cache.items())
        except Exception as exc:
            logger.warning("Resolver pass failed: {}", exc)

    def process_type_relations(self, ast_cache: AstCacheProtocol) -> None:
        """Runs the pass to resolve and create type hierarchy relationships."""
        try:
            from codebase_rag.parsers.pipeline.type_relation_pass import (
                TypeRelationPass,
            )

            TypeRelationPass(
                ingestor=self.ingestor,
                repo_path=self.repo_path,
                project_name=self.project_name,
                queries=self.queries,
                function_registry=self.function_registry,
            ).process_ast_cache(ast_cache.items())
        except Exception as exc:
            logger.warning("Type relation pass failed: {}", exc)

    def process_extended_relations(self, ast_cache: AstCacheProtocol) -> None:
        """Runs the pass to create extended relationships like types and decorators."""
        try:
            from codebase_rag.parsers.pipeline.extended_relation_pass import (
                ExtendedRelationPass,
            )

            ExtendedRelationPass(
                ingestor=self.ingestor,
                repo_path=self.repo_path,
                project_name=self.project_name,
                queries=self.queries,
            ).process_ast_cache(ast_cache.items())
        except Exception as exc:
            logger.warning("Extended relation pass failed: {}", exc)

    def process_reparse_registry(self, ast_cache: AstCacheProtocol) -> None:
        """Runs the re-parse registry resolver for supplementary call resolution."""
        try:
            from codebase_rag.parsers.pipeline.reparse_registry_resolver import (
                ReparseRegistryResolver,
            )

            ReparseRegistryResolver(
                ingestor=self.ingestor,
                repo_path=self.repo_path,
                project_name=self.project_name,
                queries=self.queries,
                function_registry=self.function_registry,
                module_qn_to_file_path=self.module_qn_to_file_path,
            ).process_ast_cache(ast_cache.items())
        except Exception as exc:
            logger.warning("Reparse registry resolver failed: {}", exc)

    def process_context7_bridging(self) -> None:
        """Runs a bridging service to link code entities to Context7 documentation."""
        try:
            from codebase_rag.parsers.type_inference.context7_bridge import (
                Context7Bridge,
            )

            Context7Bridge(self.ingestor).run()
        except Exception as exc:
            logger.warning("Context7 bridging failed: {}", exc)


class SemanticEmbeddingService:
    """
    A service to generate and store semantic embeddings for code nodes.
    """

    def __init__(
        self,
        ingestor,
        repo_path: Path,
        ast_cache: AstCacheProtocol,
        project_name: str,
        phase2_integration_enabled: bool = True,
        phase2_embedding_strategy: str = "semantic",
    ) -> None:
        """Initializes the semantic embedding service."""
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.ast_cache = ast_cache
        self.project_name = project_name
        self.phase2_integration_enabled = phase2_integration_enabled
        self.phase2_embedding_strategy = phase2_embedding_strategy

    def generate_semantic_embeddings(self) -> None:
        """
        Fetches all functions from the graph and generates vector embeddings for them.

        This method queries the graph for all function/method nodes, extracts their
        source code, generates embeddings using the configured strategy, and stores
        them in the vector database.
        """
        if not has_semantic_dependencies():
            logger.info(ls.SEMANTIC_NOT_AVAILABLE)
            return

        if not isinstance(self.ingestor, QueryProtocol):
            logger.info(ls.INGESTOR_NO_QUERY)
            return

        try:
            from codebase_rag.data_models.vector_store import store_embedding
            from codebase_rag.parsers.pipeline.embedding_strategies import (
                EmbeddingStrategy,
                NodeInfo,
            )
            from codebase_rag.parsers.pipeline.phase2_integration import (
                Phase2EmbeddingStrategyMixin,
                Phase2FrameworkDetectionMixin,
            )
            from codebase_rag.services.embeddings_service import EmbeddingsService

            logger.info(ls.PASS_4_EMBEDDINGS)

            embeddings_service = EmbeddingsService()

            phase2_enabled = self.phase2_integration_enabled
            embedding_extractor = Phase2EmbeddingStrategyMixin()
            framework_detector = Phase2FrameworkDetectionMixin()

            try:
                embedding_strategy = EmbeddingStrategy(
                    (self.phase2_embedding_strategy or "semantic").lower()
                )
            except ValueError:
                embedding_strategy = EmbeddingStrategy.SEMANTIC

            embedding_extractor.set_embedding_strategy(embedding_strategy)
            file_text_cache: dict[str, str] = {}
            framework_cache: dict[tuple[str, str], str | None] = {}

            results = self.ingestor.fetch_all(
                cs.CYPHER_QUERY_EMBEDDINGS,
                {cs.KEY_PROJECT_NAME: self.project_name},
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
                        embedding_text = source_code
                        if phase2_enabled:
                            language = parsed.get("language")
                            language = (
                                language
                                if isinstance(language, str) and language
                                else "python"
                            )

                            name = parsed.get(cs.KEY_NAME)
                            if not isinstance(name, str) or not name:
                                name = qualified_name.split(cs.SEPARATOR_DOT)[-1]

                            labels = parsed.get("labels")
                            if isinstance(labels, list):
                                labels = [
                                    label for label in labels if isinstance(label, str)
                                ]
                            else:
                                labels = []

                            kind = "method" if "Method" in labels else "function"
                            decorators = parsed.get(cs.KEY_DECORATORS)
                            if isinstance(decorators, list):
                                decorators = [
                                    dec for dec in decorators if isinstance(dec, str)
                                ]
                            else:
                                decorators = []

                            parent_qn = parsed.get("parent_qn")
                            parent_class = None
                            if kind == "method" and isinstance(parent_qn, str):
                                parent_class = parent_qn.split(cs.SEPARATOR_DOT)[-1]

                            node_info = NodeInfo(
                                node_id=str(node_id),
                                kind=kind,
                                name=name,
                                signature=(
                                    parsed.get(cs.KEY_SIGNATURE)
                                    if isinstance(parsed.get(cs.KEY_SIGNATURE), str)
                                    else None
                                ),
                                signature_lite=(
                                    parsed.get(cs.KEY_SIGNATURE_LITE)
                                    if isinstance(
                                        parsed.get(cs.KEY_SIGNATURE_LITE), str
                                    )
                                    else None
                                ),
                                docstring=(
                                    parsed.get(cs.KEY_DOCSTRING)
                                    if isinstance(parsed.get(cs.KEY_DOCSTRING), str)
                                    else None
                                ),
                                body_text=source_code,
                                decorators=decorators,
                                parent_class=parent_class,
                                start_line=start_line,
                                end_line=end_line,
                            )

                            file_text = ""
                            if isinstance(file_path, str) and file_path:
                                file_text = file_text_cache.get(file_path, "")
                                if not file_text:
                                    try:
                                        file_text = (
                                            self.repo_path / file_path
                                        ).read_text(encoding="utf-8", errors="ignore")
                                    except Exception:
                                        file_text = ""
                                    if file_text:
                                        file_text_cache[file_path] = file_text

                            framework_key = (file_path or "", language)
                            framework = framework_cache.get(framework_key)
                            if framework is None:
                                framework = framework_detector.detect_framework(
                                    language, file_text or source_code, self.repo_path
                                )
                                framework_cache[framework_key] = framework

                            payload = embedding_extractor.extract_embedding_text(
                                node_info, framework=framework, language=language
                            )
                            if payload.get("text"):
                                embedding_text = payload["text"]
                                logger.debug(
                                    "Phase 2 embedding text used for {}",
                                    qualified_name,
                                )

                        embedding = embeddings_service.embed_text(embedding_text)
                        store_embedding(node_id, embedding, qualified_name)
                        embedded_count += 1

                        if embedded_count % settings.EMBEDDING_PROGRESS_INTERVAL == 0:
                            logger.debug(
                                ls.EMBEDDING_PROGRESS.format(
                                    done=embedded_count, total=len(results)
                                )
                            )

                    except Exception as exc:
                        logger.warning(
                            ls.EMBEDDING_FAILED.format(name=qualified_name, error=exc)
                        )
                else:
                    logger.debug(ls.NO_SOURCE_FOR.format(name=qualified_name))
            logger.info(ls.EMBEDDINGS_COMPLETE.format(count=embedded_count))

        except Exception as exc:
            logger.warning(ls.EMBEDDING_GENERATION_FAILED.format(error=exc))

    def _extract_source_code(
        self, qualified_name: str, file_path: str, start_line: int, end_line: int
    ) -> str | None:
        """Extracts the source code for a node using its location information."""
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
        """Parses a raw database row into a structured `EmbeddingQueryResult`."""
        node_id = row.get(cs.KEY_NODE_ID)
        qualified_name = row.get(cs.KEY_QUALIFIED_NAME)

        if not isinstance(node_id, int) or not isinstance(qualified_name, str):
            return None

        start_line = row.get(cs.KEY_START_LINE)
        end_line = row.get(cs.KEY_END_LINE)
        file_path = row.get(cs.KEY_PATH)

        parsed: EmbeddingQueryResult = EmbeddingQueryResult(
            node_id=node_id,
            qualified_name=qualified_name,
            start_line=start_line if isinstance(start_line, int) else None,
            end_line=end_line if isinstance(end_line, int) else None,
            path=file_path if isinstance(file_path, str) else None,
        )

        name = row.get(cs.KEY_NAME)
        if isinstance(name, str):
            parsed[cs.KEY_NAME] = name

        signature = row.get(cs.KEY_SIGNATURE)
        if isinstance(signature, str):
            parsed[cs.KEY_SIGNATURE] = signature

        signature_lite = row.get(cs.KEY_SIGNATURE_LITE)
        if isinstance(signature_lite, str):
            parsed[cs.KEY_SIGNATURE_LITE] = signature_lite

        docstring = row.get(cs.KEY_DOCSTRING)
        if isinstance(docstring, str):
            parsed[cs.KEY_DOCSTRING] = docstring

        decorators = row.get(cs.KEY_DECORATORS)
        if isinstance(decorators, list):
            parsed[cs.KEY_DECORATORS] = [
                dec for dec in decorators if isinstance(dec, str)
            ]

        parent_qn = row.get(cs.KEY_PARENT_QN)
        if isinstance(parent_qn, str):
            parsed[cs.KEY_PARENT_QN] = parent_qn

        labels = row.get("labels")
        if isinstance(labels, list):
            parsed["labels"] = [label for label in labels if isinstance(label, str)]

        language = row.get(cs.KEY_LANGUAGE)
        if isinstance(language, str):
            parsed["language"] = language

        return parsed


class PreScanService:
    """A service to run a pre-scan of the repository to build an initial symbol index."""

    def __init__(
        self,
        repo_path: Path,
        project_name: str,
        exclude_paths: frozenset[str] | None,
        unignore_paths: frozenset[str] | None,
    ) -> None:
        """Initializes the pre-scan service."""
        self.repo_path = repo_path
        self.project_name = project_name
        self.exclude_paths = exclude_paths
        self.unignore_paths = unignore_paths

    def run(self) -> PreScanIndex:
        """
        Runs the pre-scanner on the repository.

        Returns:
            A `PreScanIndex` containing mappings of symbols to the modules where they are defined.
        """
        logger.info("Running pre-scan")
        return PreScanner(
            repo_path=self.repo_path,
            project_name=self.project_name,
            exclude_paths=self.exclude_paths,
            unignore_paths=self.unignore_paths,
        ).scan_repo()


class DeclarativeParserService:
    """A service to run the declarative file parser."""

    def __init__(self, enabled: bool) -> None:
        """Initializes the declarative parser service."""
        self.enabled = enabled

    def run(
        self, declarative_parser: DeclarativeParser | None, ast_cache, queries
    ) -> None:
        """
        Runs the declarative parser if it is enabled.

        Args:
            declarative_parser (DeclarativeParser | None): The parser instance.
            ast_cache: The cache of parsed ASTs.
            queries: A dictionary of tree-sitter queries.
        """
        if not self.enabled or declarative_parser is None:
            return
        logger.info("Running declarative parser")
        declarative_parser.process_ast_cache(ast_cache.items(), queries)


class CrossFileResolverAnalyticsService:
    """A service to run analytics on cross-file dependencies."""

    def __init__(self, enabled: bool) -> None:
        """Initializes the analytics service."""
        self.enabled = enabled

    def log_summary(self, import_mapping: dict) -> None:
        """
        Logs a summary of cross-file dependencies if enabled.

        Args:
            import_mapping (dict): The import mapping from the `ImportProcessor`.
        """
        if not self.enabled:
            return
        logger.info("Running cross-file resolver analytics")
        CrossFileResolver(import_mapping).log_summary()


class AnalysisRunnerService:
    """A service to run the static analysis layer."""

    def __init__(self, enabled: bool) -> None:
        """Initializes the analysis runner service."""
        self.enabled = enabled

    def run(self, ingestor, repo_path: Path) -> None:
        """
        Runs the analysis runner if enabled.

        Args:
            ingestor: The graph database ingestor.
            repo_path (Path): The path to the repository.
        """
        if not self.enabled:
            return
        logger.info("Running analysis layer")
        AnalysisRunner(ingestor, repo_path).run_all()


class PerformanceProfileService:
    """A service to log performance profiling information."""

    def __init__(self, performance_optimizer) -> None:
        """Initializes the performance profile service."""
        self.performance_optimizer = performance_optimizer

    def log_summary_if_enabled(self) -> None:
        """Logs a summary of the memory profile if profiling is enabled."""
        if not self.performance_optimizer:
            return
        if not self.performance_optimizer.profile_enabled:
            return
        summary = self.performance_optimizer.get_profile_summary()
        logger.info(
            "Memory profile summary: samples={}, min_mb={}, max_mb={}, avg_mb={}, last_mb={}",
            summary["samples"],
            summary["min_mb"],
            summary["max_mb"],
            summary["avg_mb"],
            summary["last_mb"],
        )


class GitDeltaHeadService:
    """A service to update the last processed Git HEAD in the cache."""

    def __init__(self, enabled: bool, git_delta_cache: GitDeltaCache | None) -> None:
        """Initializes the Git delta head service."""
        self.enabled = enabled
        self.git_delta_cache = git_delta_cache

    def update_last_head(self, repo_path: Path) -> None:
        """
        Updates the last processed Git HEAD in the cache if delta processing is enabled.

        Args:
            repo_path (Path): The path to the Git repository.
        """
        if not self.enabled or not self.git_delta_cache:
            return
        head = get_git_head(repo_path)
        if head:
            self.git_delta_cache.set_last_head(repo_path, head)
