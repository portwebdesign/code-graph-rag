from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path

from loguru import logger


def get_git_head(repo_path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except Exception as exc:
        logger.debug("Failed to resolve git head: {}", exc)
        return None


def get_git_delta(repo_path: Path, base_rev: str) -> tuple[set[Path], set[Path]]:
    changed: set[Path] = set()
    deleted: set[Path] = set()

    try:
        result = subprocess.run(
            ["git", "diff", "--name-status", f"{base_rev}..HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return changed, deleted

        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            status, path = line.split("\t", 1)
            file_path = repo_path / path.strip()
            if status.upper().startswith("D"):
                deleted.add(file_path)
            else:
                changed.add(file_path)
    except Exception as exc:
        logger.debug("Failed to compute git delta: {}", exc)

    return changed, deleted


def filter_existing(paths: Iterable[Path]) -> set[Path]:
    return {path for path in paths if path.exists()}
