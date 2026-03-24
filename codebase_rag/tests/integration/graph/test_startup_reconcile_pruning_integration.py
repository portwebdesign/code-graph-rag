from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    execute_project_cypher,
    run_fixture_update,
)

pytestmark = [pytest.mark.integration]


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _path_snapshot(
    memgraph_ingestor: object,
    *,
    project_name: str,
    path: str,
) -> list[dict[str, object]]:
    rows = execute_project_cypher(
        memgraph_ingestor,
        """
MATCH (n {project_name: $project_name})
WHERE n.path = $path
  AND any(label IN labels(n) WHERE label IN $labels)
RETURN labels(n) AS labels,
       CASE
           WHEN 'File' IN labels(n) THEN n.path
           ELSE coalesce(n.qualified_name, n.name, n.path, '')
       END AS identity,
       n.path AS path
ORDER BY identity
""",
        project_name=project_name,
        parameters={
            "path": path,
            "labels": ["File", "Module", "Function"],
        },
    )
    return [
        {
            "labels": sorted(cast(list[str], row["labels"])),
            "identity": row["identity"],
            "path": row["path"],
        }
        for row in rows
    ]


def test_startup_reconcile_prunes_stale_file_paths_from_memgraph_snapshot(
    temp_repo: Path,
    memgraph_ingestor: object,
) -> None:
    project = temp_repo / "startup_reconcile_pruning"
    _write_file(project / "src" / "live.py", "def live():\n    return 1\n")
    _write_file(project / "src" / "stale.py", "def stale():\n    return 2\n")

    run_fixture_update(project, memgraph_ingestor)

    initial_stale_snapshot = _path_snapshot(
        memgraph_ingestor,
        project_name=project.name,
        path="src/stale.py",
    )
    assert initial_stale_snapshot == [
        {
            "labels": ["File"],
            "identity": "src/stale.py",
            "path": "src/stale.py",
        },
        {
            "labels": ["Function"],
            "identity": "startup_reconcile_pruning.src.stale.stale",
            "path": "src/stale.py",
        },
        {
            "labels": ["Module"],
            "identity": "startup_reconcile_pruning.src.stale",
            "path": "src/stale.py",
        },
    ]

    (project / "src" / "stale.py").unlink()

    run_fixture_update(project, memgraph_ingestor)

    live_snapshot = _path_snapshot(
        memgraph_ingestor,
        project_name=project.name,
        path="src/live.py",
    )
    stale_snapshot = _path_snapshot(
        memgraph_ingestor,
        project_name=project.name,
        path="src/stale.py",
    )

    assert live_snapshot == [
        {
            "labels": ["File"],
            "identity": "src/live.py",
            "path": "src/live.py",
        },
        {
            "labels": ["Function"],
            "identity": "startup_reconcile_pruning.src.live.live",
            "path": "src/live.py",
        },
        {
            "labels": ["Module"],
            "identity": "startup_reconcile_pruning.src.live",
            "path": "src/live.py",
        },
    ]
    assert stale_snapshot == []
