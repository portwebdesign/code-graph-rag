from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from codebase_rag.core import constants as cs
from codebase_rag.graph_db.cypher_queries import (
    CYPHER_DELETE_CONTAINER_BY_PATH,
    CYPHER_LIST_PROJECT_RECONCILE_PATHS,
)
from codebase_rag.services.protocols import QueryProtocol
from codebase_rag.utils.path_utils import normalize_path_value


@dataclass(slots=True)
class ReconcilePruneSummary:
    scanned_paths: int = 0
    pruned_file_paths: list[str] = field(default_factory=list)
    pruned_directory_paths: list[str] = field(default_factory=list)


class GraphPruningService:
    def __init__(
        self,
        repo_path: Path,
        project_name: str,
        ingestor: QueryProtocol,
        prepare_file_update: Callable[[Path], None],
    ) -> None:
        self.repo_path = repo_path
        self.project_name = project_name
        self.ingestor = ingestor
        self.prepare_file_update = prepare_file_update

    def prune_path(self, relative_path: str) -> None:
        normalized_path = normalize_path_value(relative_path)
        if not normalized_path or normalized_path == ".":
            return
        self.prepare_file_update(self.repo_path / Path(normalized_path))

    def reconcile_startup(self) -> ReconcilePruneSummary:
        summary = ReconcilePruneSummary()
        rows = self.ingestor.fetch_all(
            CYPHER_LIST_PROJECT_RECONCILE_PATHS,
            {cs.KEY_PROJECT_NAME: self.project_name},
        )

        for row in rows:
            row_project = str(row.get(cs.KEY_PROJECT_NAME) or self.project_name).strip()
            if row_project and row_project != self.project_name:
                continue

            relative_path = str(row.get(cs.KEY_PATH) or "").strip()
            kind = str(row.get("kind") or "").strip().lower()
            if not relative_path or relative_path == ".":
                continue

            normalized_path = normalize_path_value(relative_path)
            candidate_path = self.repo_path / Path(normalized_path)
            summary.scanned_paths += 1

            if kind == "file":
                if candidate_path.is_file():
                    continue
                self.prepare_file_update(candidate_path)
                summary.pruned_file_paths.append(normalized_path)
                continue

            if kind == "directory":
                if candidate_path.is_dir():
                    continue
                self.ingestor.execute_write(
                    CYPHER_DELETE_CONTAINER_BY_PATH,
                    {
                        cs.KEY_PROJECT_NAME: self.project_name,
                        cs.KEY_PATH: normalized_path,
                    },
                )
                summary.pruned_directory_paths.append(normalized_path)

        if summary.pruned_file_paths or summary.pruned_directory_paths:
            logger.info(
                "Startup reconcile pruned {} file path(s) and {} directory path(s) for {}",
                len(summary.pruned_file_paths),
                len(summary.pruned_directory_paths),
                self.project_name,
            )
        else:
            logger.debug(
                "Startup reconcile found no stale graph paths for {}",
                self.project_name,
            )

        return summary
