import json
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger
from tree_sitter import Parser

from codebase_rag.core import constants as cs
from codebase_rag.core import logs as ls
from codebase_rag.data_models.types_defs import (
    EmbeddingQueryResult,
    LanguageQueries,
    ResultRow,
    SimpleNameLookup,
)
from codebase_rag.parsers.core.factory import ProcessorFactory
from codebase_rag.parsers.core.incremental_cache import (
    GitDeltaCache,
    IncrementalParsingCache,
)
from codebase_rag.parsers.core.performance_optimizer import ParserPerformanceOptimizer
from codebase_rag.parsers.core.pre_scanner import PreScanIndex
from codebase_rag.parsers.core.process_manager import ParserProcessManager
from codebase_rag.parsers.frameworks.framework_registry import FrameworkDetectorRegistry
from codebase_rag.parsers.query.declarative_parser import DeclarativeParser
from codebase_rag.parsers.query.query_engine import QueryEngine
from codebase_rag.services import (
    AnalysisRunnerService,
    CrossFileResolverAnalyticsService,
    DeclarativeParserService,
    FileProcessingService,
    GitDeltaHeadService,
    GitDeltaService,
    GraphStateService,
    GraphUpdateConfigService,
    GraphUpdateOrchestrator,
    GraphUpdaterContext,
    IngestorProtocol,
    ParsePreparationService,
    PerformanceProfileService,
    PreScanService,
    QueryProtocol,
    ResolverPassService,
    SemanticEmbeddingService,
)
from codebase_rag.state.registry_cache import BoundedASTCache, FunctionRegistryTrie
from codebase_rag.utils.file_utils import is_dependency_file


class GraphUpdater:
    def __init__(
        self,
        ingestor: IngestorProtocol,
        repo_path: Path,
        parsers: dict[cs.SupportedLanguage, Parser],
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        unignore_paths: frozenset[str] | None = None,
        exclude_paths: frozenset[str] | None = None,
        progress_logger: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.parsers = parsers
        self.queries = queries
        self.project_name = repo_path.resolve().name
        if hasattr(self.ingestor, "__dict__"):
            from typing import cast

            cast(Any, self.ingestor).project_name = self.project_name
            cast(Any, self.ingestor).repo_path = self.repo_path
        self.progress_logger = progress_logger

        config = GraphUpdateConfigService().load()
        self.config = config

        self.simple_name_lookup: SimpleNameLookup = defaultdict(set)
        self.function_registry = FunctionRegistryTrie(
            simple_name_lookup=self.simple_name_lookup
        )
        self.ast_cache = BoundedASTCache(ttl_seconds=config.ast_cache_ttl)
        self.unignore_paths = unignore_paths
        self.exclude_paths = exclude_paths

        self.selective_update_enabled = config.selective_update_enabled
        self.edge_only_update_enabled = config.edge_only_update_enabled

        self.incremental_cache_enabled = config.incremental_cache_enabled
        self.incremental_cache = (
            IncrementalParsingCache(ttl_seconds=config.parse_cache_ttl)
            if self.incremental_cache_enabled
            else None
        )

        self.git_delta_enabled = config.git_delta_enabled
        self.git_delta_cache = GitDeltaCache() if self.git_delta_enabled else None
        self.git_delta_changed: set[Path] | None = None
        self.git_delta_deleted: set[Path] | None = None

        self.batch_parse_enabled = config.batch_parse_enabled
        self.batch_parse_threaded = config.batch_parse_threaded
        self.batch_parse_manager = (
            ParserProcessManager(num_workers=config.batch_workers)
            if self.batch_parse_enabled
            else None
        )

        self.pre_scan_enabled = config.pre_scan_enabled
        self.pre_scan_index: PreScanIndex | None = None

        self.perf_optimizer_enabled = config.perf_optimizer_enabled
        self.performance_optimizer = (
            ParserPerformanceOptimizer(
                enforce_limits=self.ast_cache.enforce_limits,
                memory_threshold_mb=config.perf_memory,
                check_interval=config.perf_interval,
                profile_enabled=config.profile_enabled,
                profile_interval_seconds=config.profile_interval,
                max_snapshots=config.profile_max,
            )
            if self.perf_optimizer_enabled
            else None
        )

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

        self.state_service = GraphStateService(
            repo_path=self.repo_path,
            project_name=self.project_name,
            ast_cache=self.ast_cache,
            function_registry=self.function_registry,
            simple_name_lookup=self.simple_name_lookup,
        )

        self.declarative_enabled = config.declarative_enabled
        self.declarative_parser = (
            DeclarativeParser(QueryEngine()) if self.declarative_enabled else None
        )

        self.cross_file_enabled = config.cross_file_enabled
        self.analysis_enabled = config.analysis_enabled
        self.parse_strict_enabled = config.parse_strict_enabled

        self.dependency_file_checker = is_dependency_file

    def run(self) -> None:
        self._progress("ingest_stage", {"stage": "project_init"})
        project_props = {cs.KEY_NAME: self.project_name}
        registry = FrameworkDetectorRegistry(self.repo_path)
        result = registry.detect_repo()
        if result.framework_type:
            project_props[cs.KEY_FRAMEWORK] = result.framework_type
        if result.metadata:
            project_props[cs.KEY_FRAMEWORK_METADATA] = json.dumps(
                result.metadata, ensure_ascii=False
            )

        self.ingestor.ensure_node_batch(cs.NODE_PROJECT, project_props)
        logger.info(ls.ENSURING_PROJECT.format(name=self.project_name))

        self._progress("ingest_stage", {"stage": "structure"})
        logger.info(ls.PASS_1_STRUCTURE)
        self.factory.structure_processor.identify_structure()

        if self.pre_scan_enabled:
            self._progress("ingest_stage", {"stage": "pre_scan"})
            self.pre_scan_index = PreScanService(
                repo_path=self.repo_path,
                project_name=self.project_name,
                exclude_paths=self.exclude_paths,
                unignore_paths=self.unignore_paths,
            ).run()

        self._progress("ingest_stage", {"stage": "parse"})
        logger.info(ls.PASS_2_FILES)
        if self.git_delta_enabled and self.git_delta_cache:
            had_last_head = bool(self.git_delta_cache.get_last_head(self.repo_path))
            git_delta_service = GitDeltaService(
                repo_path=self.repo_path,
                git_delta_cache=self.git_delta_cache,
                incremental_cache_enabled=self.incremental_cache_enabled,
                incremental_cache=self.incremental_cache,
                remove_file_from_state=self.state_service.remove_file_from_state,
            )
            self.git_delta_changed, self.git_delta_deleted = (
                git_delta_service.handle_git_delta()
            )
            git_delta_no_changes = (
                had_last_head
                and not self.git_delta_changed
                and not self.git_delta_deleted
            )
        else:
            git_delta_no_changes = False
        parse_service = ParsePreparationService(
            repo_path=self.repo_path,
            project_name=self.project_name,
            ingestor=self.ingestor,
            factory=self.factory,
            queries=self.queries,
            incremental_cache_enabled=self.incremental_cache_enabled,
            incremental_cache=self.incremental_cache,
            remove_file_from_state=self.state_service.remove_file_from_state,
        )
        FileProcessingService(self, parse_service).process_files()

        self._progress("ingest_stage", {"stage": "resolve"})
        resolver_service = ResolverPassService(
            ingestor=self.ingestor,
            repo_path=self.repo_path,
            project_name=self.project_name,
            queries=self.queries,
            function_registry=self.function_registry,
            import_processor=self.factory.import_processor,
            module_qn_to_file_path=self.factory.module_qn_to_file_path,
            pre_scan_index=self.pre_scan_index,
        )
        context = GraphUpdaterContext(
            ingestor=self.ingestor,
            repo_path=self.repo_path,
            project_name=self.project_name,
            factory=self.factory,
            ast_cache=self.ast_cache,
            function_registry=self.function_registry,
            simple_name_lookup=self.simple_name_lookup,
            queries=self.queries,
            pre_scan_index=self.pre_scan_index,
            declarative_parser=self.declarative_parser,
            config=self.config,
            resolver_service=resolver_service,
            declarative_parser_service=DeclarativeParserService(
                self.declarative_enabled
            ),
            cross_file_resolver_service=CrossFileResolverAnalyticsService(
                self.cross_file_enabled
            ),
        )
        GraphUpdateOrchestrator(context).run_linking_and_passes()

        self._progress("ingest_stage", {"stage": "analysis"})
        logger.info(ls.ANALYSIS_COMPLETE)
        self.ingestor.flush_all()

        AnalysisRunnerService(self.analysis_enabled).run(self.ingestor, self.repo_path)

        self._progress("ingest_stage", {"stage": "embeddings"})
        SemanticEmbeddingService(
            ingestor=self.ingestor,
            repo_path=self.repo_path,
            ast_cache=self.ast_cache,
            project_name=self.project_name,
            phase2_integration_enabled=self.config.phase2_integration_enabled,
            phase2_embedding_strategy=self.config.phase2_embedding_strategy,
        ).generate_semantic_embeddings()

        if isinstance(self.ingestor, QueryProtocol):
            self._progress("ingest_stage", {"stage": "graph_algorithms"})
            from codebase_rag.tools.graph_algorithms import GraphAlgorithms

            logger.info("Running MAGE Graph Algorithms...")
            GraphAlgorithms(self.ingestor).run_all(has_changes=not git_delta_no_changes)

        PerformanceProfileService(self.performance_optimizer).log_summary_if_enabled()

        GitDeltaHeadService(
            self.git_delta_enabled, self.git_delta_cache
        ).update_last_head(self.repo_path)

        self._progress("ingest_stage", {"stage": "completed"})

    def remove_file_from_state(self, file_path: Path) -> None:
        self.state_service.remove_file_from_state(file_path)

    def _progress(self, kind: str, payload: dict[str, Any]) -> None:
        if self.progress_logger:
            self.progress_logger(kind, payload)

    def _process_function_calls(self) -> None:
        for file_path, (root_node, language) in self.ast_cache.items():
            self.factory.call_processor.process_calls_in_file(
                file_path, root_node, language, self.queries
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
