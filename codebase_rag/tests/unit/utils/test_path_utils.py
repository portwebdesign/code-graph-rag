from __future__ import annotations

from pathlib import Path

from codebase_rag.core import constants as cs
from codebase_rag.utils.path_utils import (
    add_absolute_path_aliases,
    build_runtime_event_path_fields,
    get_canonical_absolute_path,
    get_canonical_relative_path,
    is_absolute_path_value,
    resolve_absolute_path,
    resolve_repo_relative_path,
)


def test_resolve_repo_relative_path_keeps_relative_paths() -> None:
    assert resolve_repo_relative_path("src/app/main.py") == "src/app/main.py"


def test_resolve_repo_relative_path_converts_absolute_repo_path(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    target = repo_path / "src" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("pass\n", encoding="utf-8")

    resolved = resolve_repo_relative_path(target.resolve().as_posix(), repo_path)

    assert resolved == "src/app.py"


def test_resolve_absolute_path_builds_from_relative_path(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    resolved = resolve_absolute_path("src/app.py", repo_path)

    assert resolved == (repo_path / "src" / "app.py").resolve().as_posix()


def test_get_canonical_paths_prefer_repo_relative_and_abs_path(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    payload = {
        cs.KEY_PATH: (repo_path / "src" / "project.py").resolve().as_posix(),
        cs.KEY_REPO_REL_PATH: "src/project.py",
        cs.KEY_ABS_PATH: (repo_path / "src" / "project.py").resolve().as_posix(),
    }

    assert get_canonical_relative_path(payload, repo_path) == "src/project.py"
    assert (
        get_canonical_absolute_path(payload, repo_path)
        == (repo_path / "src" / "project.py").resolve().as_posix()
    )


def test_is_absolute_path_value_supports_windows_and_posix_paths() -> None:
    assert is_absolute_path_value("C:/repo/src/app.py") is True
    assert is_absolute_path_value("/repo/src/app.py") is True
    assert is_absolute_path_value("src/app.py") is False


def test_build_runtime_event_path_fields_sets_canonical_fields(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    payload = build_runtime_event_path_fields("src/runtime/events.json", repo_path)

    assert payload["file_path"] == "src/runtime/events.json"
    assert payload[cs.KEY_PATH] == "src/runtime/events.json"
    assert payload[cs.KEY_REPO_REL_PATH] == "src/runtime/events.json"
    assert (
        payload[cs.KEY_ABS_PATH]
        == (repo_path / "src" / "runtime" / "events.json").resolve().as_posix()
    )


def test_add_absolute_path_aliases_uses_abs_path_field() -> None:
    payload = [{cs.KEY_ABS_PATH: "D:/repo/src/app.py", "name": "app"}]

    aliased = add_absolute_path_aliases(payload)

    assert aliased == [
        {
            cs.KEY_ABS_PATH: "D:/repo/src/app.py",
            "absolute_path": "D:/repo/src/app.py",
            "name": "app",
        }
    ]
