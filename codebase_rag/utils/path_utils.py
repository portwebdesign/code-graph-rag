import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..core import constants as cs

_TEST_DIR_NAMES = frozenset({"test", "tests", "spec", "specs", "__tests__"})
_TEST_FILE_PREFIXES = ("test_", "spec_")
_TEST_FILE_SUFFIXES = ("_test", "_spec")
_TEST_FILE_MARKERS = (".test.", ".spec.")
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[A-Za-z]:[/\\]")


def to_posix(path: Path) -> str:
    return path.as_posix()


def normalize_path_value(path_value: str) -> str:
    return path_value.strip().replace("\\", "/")


def is_absolute_path_value(path_value: str) -> bool:
    normalized = normalize_path_value(path_value)
    return (
        normalized.startswith("/")
        or normalized.startswith("//")
        or bool(_WINDOWS_ABSOLUTE_PATH_RE.match(normalized))
    )


def resolve_repo_relative_path(
    path_value: str | None,
    repo_path: Path | None = None,
) -> str | None:
    if not isinstance(path_value, str):
        return None

    normalized = normalize_path_value(path_value)
    if not normalized:
        return None
    if not is_absolute_path_value(normalized):
        return normalized
    if repo_path is None:
        return None

    repo_root = repo_path.resolve().as_posix()
    repo_root_with_sep = f"{repo_root.rstrip('/')}/"
    normalized_lower = normalized.lower()
    repo_root_lower = repo_root.lower()
    repo_root_with_sep_lower = repo_root_with_sep.lower()

    if normalized_lower == repo_root_lower:
        return "."
    if normalized_lower.startswith(repo_root_with_sep_lower):
        return normalized[len(repo_root_with_sep) :]
    return None


def resolve_absolute_path(
    path_value: str | None,
    repo_path: Path | None = None,
) -> str | None:
    if not isinstance(path_value, str):
        return None

    normalized = normalize_path_value(path_value)
    if not normalized:
        return None
    if is_absolute_path_value(normalized):
        return normalized
    if repo_path is None:
        return None
    if normalized == ".":
        return repo_path.resolve().as_posix()
    return (repo_path / Path(normalized)).resolve().as_posix()


def get_canonical_relative_path(
    properties: Mapping[str, object],
    repo_path: Path | None = None,
) -> str | None:
    repo_rel_path = properties.get(cs.KEY_REPO_REL_PATH)
    if isinstance(repo_rel_path, str):
        resolved_repo_rel = resolve_repo_relative_path(repo_rel_path, repo_path)
        if resolved_repo_rel:
            return resolved_repo_rel

    path = properties.get(cs.KEY_PATH)
    if isinstance(path, str):
        return resolve_repo_relative_path(path, repo_path)
    return None


def get_canonical_absolute_path(
    properties: Mapping[str, object],
    repo_path: Path | None = None,
) -> str | None:
    abs_path = properties.get(cs.KEY_ABS_PATH)
    if isinstance(abs_path, str):
        resolved_abs_path = resolve_absolute_path(abs_path, repo_path)
        if resolved_abs_path:
            return resolved_abs_path

    path = properties.get(cs.KEY_PATH)
    if isinstance(path, str):
        resolved_path = resolve_absolute_path(path, repo_path)
        if resolved_path:
            return resolved_path

    repo_rel_path = properties.get(cs.KEY_REPO_REL_PATH)
    if isinstance(repo_rel_path, str):
        return resolve_absolute_path(repo_rel_path, repo_path)
    return None


def build_runtime_event_path_fields(
    path_value: str | None,
    repo_path: Path | None = None,
) -> dict[str, str]:
    if not isinstance(path_value, str):
        return {}

    normalized = normalize_path_value(path_value)
    if not normalized:
        return {}

    payload: dict[str, str] = {"file_path": normalized}
    repo_relative_path = resolve_repo_relative_path(normalized, repo_path)
    if repo_relative_path:
        payload[cs.KEY_PATH] = repo_relative_path
        payload[cs.KEY_REPO_REL_PATH] = repo_relative_path
    absolute_path = resolve_absolute_path(normalized, repo_path)
    if absolute_path:
        payload[cs.KEY_ABS_PATH] = absolute_path
    return payload


def add_absolute_path_aliases(
    payload: object,
    repo_path: Path | None = None,
) -> object:
    if isinstance(payload, list):
        return [add_absolute_path_aliases(item, repo_path) for item in payload]

    if not isinstance(payload, dict):
        return payload

    enriched: dict[str, Any] = {
        str(key): add_absolute_path_aliases(value, repo_path)
        for key, value in payload.items()
    }

    if "absolute_path" not in enriched:
        abs_path = enriched.get(cs.KEY_ABS_PATH)
        if isinstance(abs_path, str):
            resolved_abs_path = resolve_absolute_path(abs_path, repo_path)
            if resolved_abs_path:
                enriched["absolute_path"] = resolved_abs_path
        else:
            repo_rel_path = enriched.get(cs.KEY_REPO_REL_PATH)
            if isinstance(repo_rel_path, str):
                resolved_abs_path = resolve_absolute_path(repo_rel_path, repo_path)
                if resolved_abs_path:
                    enriched["absolute_path"] = resolved_abs_path
    return enriched


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
