from __future__ import annotations

import json
from pathlib import Path

from codebase_rag.parsers.frameworks.framework_registry import FrameworkDetectorRegistry


class FrameworkMetadataDetector:
    """
    Legacy wrapper for repo-level framework detection.

    Prefer FrameworkDetectorRegistry.detect_repo for new code.
    """

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path
        self._registry = FrameworkDetectorRegistry(repo_path)

    def detect(self) -> tuple[str | None, str | None]:
        result = self._registry.detect_repo()
        if not result.metadata:
            return result.framework_type, None
        return result.framework_type, json.dumps(result.metadata, ensure_ascii=False)
