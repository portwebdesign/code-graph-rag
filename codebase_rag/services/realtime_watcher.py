from __future__ import annotations

import time
from pathlib import Path

from loguru import logger
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from codebase_rag.core import logs
from codebase_rag.core.config import settings
from codebase_rag.core.constants import (
    IGNORE_PATTERNS,
    IGNORE_SUFFIXES,
    WATCHER_SLEEP_INTERVAL,
    EventType,
    SupportedLanguage,
)
from codebase_rag.graph_db.graph_updater import GraphUpdater
from codebase_rag.infrastructure.language_spec import get_language_spec
from codebase_rag.infrastructure.parser_loader import load_parsers
from codebase_rag.services import QueryProtocol
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.services.graph_update_post_services import SemanticEmbeddingService


class CodeChangeEventHandler(FileSystemEventHandler):
    def __init__(
        self,
        updater: GraphUpdater,
        refresh_embeddings: bool = False,
        debounce_seconds: float = 2.0,
    ) -> None:
        self.updater = updater
        self.refresh_embeddings = refresh_embeddings
        self.debounce_seconds = max(0.0, min(float(debounce_seconds), 5.0))
        self._last_processed_by_path: dict[str, float] = {}
        self.ignore_patterns = IGNORE_PATTERNS
        self.ignore_suffixes = IGNORE_SUFFIXES
        logger.info(logs.WATCHER_ACTIVE)

    def _is_relevant(self, path_str: str) -> bool:
        path = Path(path_str)
        if any(path.name.endswith(suffix) for suffix in self.ignore_suffixes):
            return False
        return all(part not in self.ignore_patterns for part in path.parts)

    def dispatch(self, event: FileSystemEvent) -> None:
        src_path = event.src_path
        if isinstance(src_path, bytes):
            src_path = src_path.decode()

        if event.is_directory or not self._is_relevant(src_path):
            return

        ingestor = self.updater.ingestor
        if not isinstance(ingestor, QueryProtocol):
            logger.warning(logs.WATCHER_SKIP_NO_QUERY)
            return

        path = Path(src_path)
        now = time.monotonic()
        last_processed = self._last_processed_by_path.get(str(path), 0.0)
        if (now - last_processed) < self.debounce_seconds:
            return
        self._last_processed_by_path[str(path)] = now

        relative_path_str = str(path.relative_to(self.updater.repo_path))

        logger.warning(
            logs.CHANGE_DETECTED.format(event_type=event.event_type, path=path)
        )

        pruning_service = self.updater.pruning_service
        if pruning_service is None:
            raise RuntimeError(
                "Realtime watcher requires an initialized pruning service"
            )
        pruning_service.prune_path(relative_path_str)

        if event.event_type in (EventType.MODIFIED, EventType.CREATED):
            lang_config = get_language_spec(path.suffix)
            if (
                lang_config
                and isinstance(lang_config.language, SupportedLanguage)
                and lang_config.language in self.updater.parsers
            ):
                if result := self.updater.factory.definition_processor.process_file(
                    path,
                    lang_config.language,
                    self.updater.queries,
                    self.updater.factory.structure_processor.structural_elements,
                ):
                    root_node, language = result
                    self.updater.ast_cache[path] = (root_node, language)
                    self.updater.factory.call_processor.process_calls_in_file(
                        path,
                        root_node,
                        language,
                        self.updater.queries,
                    )

        self.updater.ingestor.flush_all()

        if self.refresh_embeddings:
            try:
                SemanticEmbeddingService(
                    ingestor=self.updater.ingestor,
                    repo_path=self.updater.repo_path,
                    ast_cache=self.updater.ast_cache,
                    project_name=self.updater.project_name,
                    phase2_integration_enabled=self.updater.config.phase2_integration_enabled,
                    phase2_embedding_strategy=self.updater.config.phase2_embedding_strategy,
                ).generate_semantic_embeddings()
            except Exception as exc:
                logger.warning("Realtime embedding refresh failed: {}", exc)

        logger.success(logs.GRAPH_UPDATED.format(name=path.name))


def run_watcher_loop(
    ingestor: MemgraphIngestor,
    repo_path_obj: Path,
    *,
    refresh_embeddings: bool = False,
    debounce_seconds: float = settings.REALTIME_WATCHER_DEBOUNCE_SECONDS,
) -> None:
    parsers, queries = load_parsers()
    updater = GraphUpdater(ingestor, repo_path_obj, parsers, queries)

    logger.info(logs.INITIAL_SCAN)
    updater.run()
    logger.success(logs.INITIAL_SCAN_DONE)

    event_handler = CodeChangeEventHandler(
        updater,
        refresh_embeddings=refresh_embeddings,
        debounce_seconds=debounce_seconds,
    )
    observer = Observer()
    observer.schedule(event_handler, str(repo_path_obj), recursive=True)
    observer.start()
    logger.info(logs.WATCHING.format(path=repo_path_obj))

    try:
        while True:
            time.sleep(WATCHER_SLEEP_INTERVAL)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def start_watcher(
    repo_path: str,
    host: str,
    port: int,
    batch_size: int | None = None,
    refresh_embeddings: bool = False,
    debounce_seconds: float = settings.REALTIME_WATCHER_DEBOUNCE_SECONDS,
) -> None:
    repo_path_obj = Path(repo_path).resolve()
    effective_batch_size = settings.resolve_batch_size(batch_size)

    with MemgraphIngestor(
        host=host,
        port=port,
        batch_size=effective_batch_size,
        username=settings.MEMGRAPH_USERNAME,
        password=settings.MEMGRAPH_PASSWORD,
    ) as ingestor:
        run_watcher_loop(
            ingestor,
            repo_path_obj,
            refresh_embeddings=refresh_embeddings,
            debounce_seconds=debounce_seconds,
        )
