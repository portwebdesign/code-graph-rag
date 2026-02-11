import hashlib
from pathlib import Path

from ..core import constants as cs

_TEST_DIR_NAMES = frozenset({"test", "tests", "spec", "specs", "__tests__"})
_TEST_FILE_PREFIXES = ("test_", "spec_")
_TEST_FILE_SUFFIXES = ("_test", "_spec")
_TEST_FILE_MARKERS = (".test.", ".spec.")


def to_posix(path: Path) -> str:
    return path.as_posix()


def compute_file_hash(file_path: Path) -> str:
    try:
        return hashlib.sha256(file_path.read_bytes()).hexdigest()
    except OSError:
        return ""


def is_test_path(path: Path | str) -> bool:
    path_obj = Path(path)
    parts = [part.lower() for part in path_obj.parts]
    if any(part in _TEST_DIR_NAMES for part in parts[:-1]):
        return True
    name = parts[-1] if parts else ""
    stem = path_obj.stem.lower()
    if any(stem.startswith(prefix) for prefix in _TEST_FILE_PREFIXES):
        return True
    if any(stem.endswith(suffix) for suffix in _TEST_FILE_SUFFIXES):
        return True
    return any(marker in name for marker in _TEST_FILE_MARKERS)


def should_skip_path(
    path: Path,
    repo_path: Path,
    exclude_paths: frozenset[str] | None = None,
    unignore_paths: frozenset[str] | None = None,
) -> bool:
    if path.is_file() and path.suffix in cs.IGNORE_SUFFIXES:
        return True
    rel_path = path.relative_to(repo_path)
    rel_path_str = rel_path.as_posix()
    dir_parts = rel_path.parent.parts if path.is_file() else rel_path.parts
    if exclude_paths and (
        not exclude_paths.isdisjoint(dir_parts)
        or rel_path_str in exclude_paths
        or any(rel_path_str.startswith(f"{p}/") for p in exclude_paths)
    ):
        return True
    if unignore_paths and any(
        rel_path_str == p or rel_path_str.startswith(f"{p}/") for p in unignore_paths
    ):
        return False
    return not cs.IGNORE_PATTERNS.isdisjoint(dir_parts)
