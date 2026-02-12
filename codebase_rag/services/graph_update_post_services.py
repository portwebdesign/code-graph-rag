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
from codebase_rag.utils.dependencies import has_semantic_dependencies
from codebase_rag.utils.fqn_resolver import find_function_source_by_fqn
from codebase_rag.utils.git_delta import get_git_head
from codebase_rag.utils.source_extraction import extract_source_with_fallback

from ..parsers.cross_file_resolver import CrossFileResolver
from ..parsers.declarative_parser import DeclarativeParser
from ..parsers.incremental_cache import GitDeltaCache
from ..parsers.pre_scanner import PreScanIndex, PreScanner
from .protocols import QueryProtocol


class AstCacheProtocol(Protocol):
    def items(self) -> Iterable[tuple[Path, tuple[Node, cs.SupportedLanguage]]]: ...

    def __contains__(self, key: Path) -> bool: ...

    def __getitem__(self, key: Path) -> tuple[Node, cs.SupportedLanguage]: ...


class ResolverPassService:
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
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.queries = queries
        self.function_registry = function_registry
        self.import_processor = import_processor
        self.module_qn_to_file_path = module_qn_to_file_path
        self.pre_scan_index = pre_scan_index

    def process_framework_links(self, simple_name_lookup) -> None:
        try:
            from ..parsers.framework_linker import FrameworkLinker

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
        try:
            from ..parsers.tailwind_processor import TailwindUsageProcessor

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
        ast_cache_items = list(ast_cache.items())
        for file_path, (root_node, language) in ast_cache_items:
            call_processor.process_calls_in_file(
                file_path, root_node, language, queries
            )

    def process_resolver_pass(self, ast_cache: AstCacheProtocol) -> None:
        try:
            from ..parsers.resolver_pass import ResolverPass

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
        try:
            from ..parsers.type_relation_pass import TypeRelationPass

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
        try:
            from ..parsers.extended_relation_pass import ExtendedRelationPass

            ExtendedRelationPass(
                ingestor=self.ingestor,
                repo_path=self.repo_path,
                project_name=self.project_name,
                queries=self.queries,
            ).process_ast_cache(ast_cache.items())
        except Exception as exc:
            logger.warning("Extended relation pass failed: {}", exc)

    def process_reparse_registry(self, ast_cache: AstCacheProtocol) -> None:
        try:
            from ..parsers.reparse_registry_resolver import ReparseRegistryResolver

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
        try:
            from ..parsers.context7_bridge import Context7Bridge

            Context7Bridge(self.ingestor).run()
        except Exception as exc:
            logger.warning("Context7 bridging failed: {}", exc)


class SemanticEmbeddingService:
    def __init__(
        self,
        ingestor,
        repo_path: Path,
        ast_cache: AstCacheProtocol,
        project_name: str,
    ) -> None:
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.ast_cache = ast_cache
        self.project_name = project_name

    def generate_semantic_embeddings(self) -> None:
        if not has_semantic_dependencies():
            logger.info(ls.SEMANTIC_NOT_AVAILABLE)
            return

        if not isinstance(self.ingestor, QueryProtocol):
            logger.info(ls.INGESTOR_NO_QUERY)
            return

        try:
            from codebase_rag.data_models.vector_store import store_embedding
            from codebase_rag.services.embeddings_service import EmbeddingsService

            logger.info(ls.PASS_4_EMBEDDINGS)

            embeddings_service = EmbeddingsService()

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
                        embedding = embeddings_service.embed_text(source_code)
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


class PreScanService:
    def __init__(
        self,
        repo_path: Path,
        project_name: str,
        exclude_paths: frozenset[str] | None,
        unignore_paths: frozenset[str] | None,
    ) -> None:
        self.repo_path = repo_path
        self.project_name = project_name
        self.exclude_paths = exclude_paths
        self.unignore_paths = unignore_paths

    def run(self) -> PreScanIndex:
        logger.info("Running pre-scan")
        return PreScanner(
            repo_path=self.repo_path,
            project_name=self.project_name,
            exclude_paths=self.exclude_paths,
            unignore_paths=self.unignore_paths,
        ).scan_repo()


class DeclarativeParserService:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def run(
        self, declarative_parser: DeclarativeParser | None, ast_cache, queries
    ) -> None:
        if not self.enabled or declarative_parser is None:
            return
        logger.info("Running declarative parser")
        declarative_parser.process_ast_cache(ast_cache.items(), queries)


class CrossFileResolverAnalyticsService:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def log_summary(self, import_mapping: dict) -> None:
        if not self.enabled:
            return
        logger.info("Running cross-file resolver analytics")
        CrossFileResolver(import_mapping).log_summary()


class AnalysisRunnerService:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def run(self, ingestor, repo_path: Path) -> None:
        if not self.enabled:
            return
        logger.info("Running analysis layer")
        AnalysisRunner(ingestor, repo_path).run_all()


class PerformanceProfileService:
    def __init__(self, performance_optimizer) -> None:
        self.performance_optimizer = performance_optimizer

    def log_summary_if_enabled(self) -> None:
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
    def __init__(self, enabled: bool, git_delta_cache: GitDeltaCache | None) -> None:
        self.enabled = enabled
        self.git_delta_cache = git_delta_cache

    def update_last_head(self, repo_path: Path) -> None:
        if not self.enabled or not self.git_delta_cache:
            return
        head = get_git_head(repo_path)
        if head:
            self.git_delta_cache.set_last_head(repo_path, head)
