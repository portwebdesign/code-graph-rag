from pathlib import Path
from unittest.mock import patch

from codebase_rag.core import constants as cs
from codebase_rag.core.main import _prepare_tmp_dir, _setup_common_initialization


class TestPrepareTmpDir:
    def test_removes_existing_tmp_entries(self, tmp_path: Path) -> None:
        tmp_dir = tmp_path / cs.TMP_DIR
        tmp_dir.mkdir()
        stale_file = tmp_dir / "stale.txt"
        stale_file.write_text("old", encoding="utf-8")

        prepared_dir = _prepare_tmp_dir(tmp_path)

        assert prepared_dir == tmp_dir
        assert tmp_dir.exists()
        assert list(tmp_dir.iterdir()) == []

    def test_keeps_running_when_locked_child_cannot_be_removed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        tmp_dir = tmp_path / cs.TMP_DIR
        tmp_dir.mkdir()
        blocked_dir = tmp_dir / "pytest"
        blocked_dir.mkdir()
        removable_file = tmp_dir / "old.txt"
        removable_file.write_text("old", encoding="utf-8")

        original_remove = __import__(
            "codebase_rag.core.main", fromlist=["_remove_path_with_retries"]
        )._remove_path_with_retries

        def fake_remove(
            path: Path, attempts: int = 3, delay_seconds: float = 0.2
        ) -> bool:
            if path == blocked_dir:
                return False
            return original_remove(path, attempts=attempts, delay_seconds=0)

        monkeypatch.setattr(
            "codebase_rag.core.main._remove_path_with_retries", fake_remove
        )

        _prepare_tmp_dir(tmp_path)

        assert tmp_dir.exists()
        assert blocked_dir.exists()
        assert not removable_file.exists()

    def test_replaces_file_with_directory(self, tmp_path: Path) -> None:
        tmp_path.joinpath(cs.TMP_DIR).write_text("stale", encoding="utf-8")

        prepared_dir = _prepare_tmp_dir(tmp_path)

        assert prepared_dir.is_dir()


class TestSetupCommonInitialization:
    def test_creates_analysis_and_tmp_directories(self, tmp_path: Path) -> None:
        with patch("codebase_rag.core.main.logger") as mock_logger:
            project_root = _setup_common_initialization(str(tmp_path))

        assert project_root == tmp_path.resolve()
        assert (tmp_path / "output" / "analysis").is_dir()
        assert (tmp_path / cs.TMP_DIR).is_dir()
        mock_logger.remove.assert_called_once()
        assert mock_logger.add.call_count == 2
