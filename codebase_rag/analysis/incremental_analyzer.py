from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ..utils.git_delta import filter_existing, get_git_delta, get_git_head
from .analysis_runner import AnalysisRunner


@dataclass
class IncrementalChangeSet:
    changed: list[str]
    deleted: list[str]
    base_rev: str | None
    timestamp: str


class IncrementalAnalyzer:
    def __init__(self, runner: AnalysisRunner) -> None:
        self.runner = runner
        self.repo_path = runner.repo_path

    def detect_changes(self, base_rev: str | None = None) -> IncrementalChangeSet:
        resolved_base = base_rev or get_git_head(self.repo_path)
        if not resolved_base:
            return IncrementalChangeSet([], [], None, self._now())

        changed, deleted = get_git_delta(self.repo_path, resolved_base)
        changed = filter_existing(changed)
        deleted = filter_existing(deleted)

        changed_paths = [str(path.relative_to(self.repo_path)) for path in changed]
        deleted_paths = [str(path.relative_to(self.repo_path)) for path in deleted]

        return IncrementalChangeSet(
            changed_paths, deleted_paths, resolved_base, self._now()
        )

    def run(
        self, modules: set[str] | None = None, base_rev: str | None = None
    ) -> dict[str, object]:
        changes = self.detect_changes(base_rev)
        if not changes.changed:
            return {
                "status": "no_changes",
                "changed_files": [],
                "deleted_files": changes.deleted,
                "base_rev": changes.base_rev,
                "timestamp": changes.timestamp,
            }

        return self.runner.run_modules(
            modules=modules,
            incremental_paths=changes.changed,
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()
