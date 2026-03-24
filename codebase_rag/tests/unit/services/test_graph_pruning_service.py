from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.graph_db.cypher_queries import CYPHER_DELETE_CONTAINER_BY_PATH
from codebase_rag.graph_db.graph_updater import GraphUpdater
from codebase_rag.infrastructure.parser_loader import load_parsers
from codebase_rag.services.graph_pruning_service import GraphPruningService
from codebase_rag.services.graph_service import MemgraphIngestor


def test_reconcile_startup_prunes_missing_file_and_directory_paths(
    temp_repo: Path,
) -> None:
    (temp_repo / "src").mkdir(parents=True)
    (temp_repo / "src" / "live.py").write_text("x = 1\n", encoding="utf-8")

    ingestor = MagicMock()
    ingestor.fetch_all.return_value = [
        {
            cs.KEY_PROJECT_NAME: temp_repo.name,
            cs.KEY_PATH: "src/live.py",
            "kind": "file",
        },
        {
            cs.KEY_PROJECT_NAME: temp_repo.name,
            cs.KEY_PATH: "src/missing.py",
            "kind": "file",
        },
        {
            cs.KEY_PROJECT_NAME: temp_repo.name,
            cs.KEY_PATH: "old_dir",
            "kind": "directory",
        },
    ]
    prepare_file_update = MagicMock()
    service = GraphPruningService(
        repo_path=temp_repo,
        project_name=temp_repo.name,
        ingestor=ingestor,
        prepare_file_update=prepare_file_update,
    )

    summary = service.reconcile_startup()

    prepare_file_update.assert_called_once_with(temp_repo / "src" / "missing.py")
    ingestor.execute_write.assert_called_once_with(
        CYPHER_DELETE_CONTAINER_BY_PATH,
        {cs.KEY_PROJECT_NAME: temp_repo.name, cs.KEY_PATH: "old_dir"},
    )
    assert summary.pruned_file_paths == ["src/missing.py"]
    assert summary.pruned_directory_paths == ["old_dir"]


def test_reconcile_startup_skips_rows_from_other_projects(temp_repo: Path) -> None:
    ingestor = MagicMock()
    ingestor.fetch_all.return_value = [
        {cs.KEY_PROJECT_NAME: "other-project", cs.KEY_PATH: "ghost.py", "kind": "file"},
    ]
    prepare_file_update = MagicMock()
    service = GraphPruningService(
        repo_path=temp_repo,
        project_name=temp_repo.name,
        ingestor=ingestor,
        prepare_file_update=prepare_file_update,
    )

    summary = service.reconcile_startup()

    prepare_file_update.assert_not_called()
    ingestor.execute_write.assert_not_called()
    assert summary.scanned_paths == 0


def test_graph_updater_runs_startup_reconcile(monkeypatch, temp_repo: Path) -> None:
    (temp_repo / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    parsers, queries = load_parsers()
    ingestor = MagicMock(spec=MemgraphIngestor)
    pruning_service = MagicMock()
    pruning_factory = MagicMock(return_value=pruning_service)
    monkeypatch.setattr(
        "codebase_rag.graph_db.graph_updater.GraphPruningService",
        pruning_factory,
    )

    updater = GraphUpdater(
        ingestor=ingestor,
        repo_path=temp_repo,
        parsers=parsers,
        queries=queries,
    )

    updater.run()

    pruning_service.reconcile_startup.assert_called_once()
