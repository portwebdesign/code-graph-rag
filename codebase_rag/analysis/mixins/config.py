from __future__ import annotations

import os
from typing import Any, cast

from codebase_rag.core import constants as cs
from codebase_rag.graph_db.cypher_queries import CYPHER_GET_LATEST_GIT_HEAD

from ...services.protocols import QueryProtocol
from ...utils.git_delta import filter_existing, get_git_delta
from ..protocols import AnalysisRunnerProtocol


class AnalysisConfigMixin:
    def _resolve_modules(self: AnalysisRunnerProtocol) -> set[str] | None:
        raw = str(os.getenv("CODEGRAPH_ANALYSIS_MODULES", "")).strip()
        if not raw:
            return None
        return {item.strip() for item in raw.split(",") if item.strip()}

    @staticmethod
    def _should_run(name: str, modules: set[str] | None) -> bool:
        if modules is None:
            return True
        return name in modules

    @staticmethod
    def _needs_graph_data(modules: set[str] | None, use_db: bool) -> bool:
        if modules is None:
            return True
        db_only = {"usage", "dead_code", "unused_imports"}
        if use_db and modules.issubset(db_only):
            return False
        return True

    def _get_incremental_paths(self: AnalysisRunnerProtocol) -> list[str] | None:
        if str(os.getenv("CODEGRAPH_ANALYSIS_INCREMENTAL", "")).lower() not in {
            "1",
            "true",
            "yes",
        }:
            return None
        base_head = cast(Any, self)._get_latest_git_head()
        if not base_head:
            return None
        changed, _ = get_git_delta(self.repo_path, base_head)
        changed = filter_existing(changed)
        if not changed:
            return []
        return [str(path.relative_to(self.repo_path)) for path in changed]

    def _get_latest_git_head(self: AnalysisRunnerProtocol) -> str | None:
        try:
            ingestor = cast(QueryProtocol, self.ingestor)
            results = ingestor.fetch_all(
                CYPHER_GET_LATEST_GIT_HEAD, {cs.KEY_PROJECT_NAME: self.project_name}
            )
            if not results:
                return None
            return str(results[0].get(cs.KEY_GIT_HEAD) or "") or None
        except Exception:
            return None

    @staticmethod
    def _resolve_module_paths(file_paths: list[str] | None) -> list[str] | None:
        if file_paths is None:
            return None
        return file_paths
