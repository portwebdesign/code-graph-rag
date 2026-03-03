from __future__ import annotations

from pathlib import Path

from loguru import logger

from codebase_rag.data_models.vector_store import (
    delete_embeddings_by_node_ids,
    wipe_embeddings_collection,
)
from codebase_rag.parsers.core.incremental_cache import (
    GitDeltaCache,
    IncrementalParsingCache,
)


class CleanupService:
    """Coordinates cleanup across parser caches, git-delta state, and vector store."""

    def __init__(self) -> None:
        self.incremental_cache = IncrementalParsingCache()
        self.git_delta_cache = GitDeltaCache()

    def clear_repo_parser_state(self, repo_path: Path) -> dict[str, int | bool]:
        """Clear parser-related local state for a specific repository."""
        repo_root = Path(repo_path).resolve()
        parse_report = self.incremental_cache.clear_for_repo(repo_root)
        git_delta_removed = self.git_delta_cache.remove_repo(repo_root)
        report: dict[str, int | bool] = {
            **parse_report,
            "git_delta_entry_removed": git_delta_removed,
        }
        logger.info("Repo parser state cleanup complete: {}", report)
        return report

    def clear_all_parser_state(self) -> dict[str, int]:
        """Clear parser caches and git-delta state globally."""
        self.incremental_cache.clear_all()
        git_delta_entries = self.git_delta_cache.clear_all()
        report = {"git_delta_entries_removed": git_delta_entries}
        logger.info("Global parser state cleanup complete: {}", report)
        return report

    def delete_project_embeddings(self, node_ids: list[int]) -> int:
        """Delete project-scoped embeddings by graph node IDs."""
        deleted = delete_embeddings_by_node_ids(node_ids)
        logger.info("Deleted {} project embeddings", deleted)
        return deleted

    def wipe_embeddings(self) -> bool:
        """Fully reset embedding collection."""
        ok = wipe_embeddings_collection()
        logger.info("Embedding collection wipe status: {}", ok)
        return ok
