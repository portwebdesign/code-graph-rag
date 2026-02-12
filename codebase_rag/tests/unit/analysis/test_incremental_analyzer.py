from __future__ import annotations

from pathlib import Path
from typing import cast

from codebase_rag.analysis.analysis_runner import AnalysisRunner
from codebase_rag.analysis.incremental_analyzer import IncrementalAnalyzer


class DummyRunner:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path

    def run_modules(self, modules=None, incremental_paths=None):
        return {
            "status": "ok",
            "incremental_paths": incremental_paths,
        }


def test_incremental_detect_changes(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('a')")
    (tmp_path / "b.py").write_text("print('b')")

    def fake_head(repo_path: Path):
        return "abc123"

    def fake_delta(repo_path: Path, base_rev: str):
        return {repo_path / "a.py"}, {repo_path / "b.py"}

    runner = DummyRunner(tmp_path)

    monkeypatch.setattr(
        "codebase_rag.analysis.incremental_analyzer.get_git_head", fake_head
    )
    monkeypatch.setattr(
        "codebase_rag.analysis.incremental_analyzer.get_git_delta", fake_delta
    )

    analyzer = IncrementalAnalyzer(cast(AnalysisRunner, runner))
    changes = analyzer.detect_changes()

    assert changes.base_rev == "abc123"
    assert "a.py" in changes.changed
    assert "b.py" in changes.deleted


def test_incremental_run(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('a')")

    def fake_head(repo_path: Path):
        return "abc123"

    def fake_delta(repo_path: Path, base_rev: str):
        return {repo_path / "a.py"}, set()

    runner = DummyRunner(tmp_path)

    monkeypatch.setattr(
        "codebase_rag.analysis.incremental_analyzer.get_git_head", fake_head
    )
    monkeypatch.setattr(
        "codebase_rag.analysis.incremental_analyzer.get_git_delta", fake_delta
    )

    analyzer = IncrementalAnalyzer(cast(AnalysisRunner, runner))
    result = analyzer.run()

    assert result["status"] == "ok"
    assert result["incremental_paths"] == ["a.py"]
