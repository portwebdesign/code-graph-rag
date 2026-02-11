from __future__ import annotations

from pathlib import Path

from codebase_rag.core import constants as cs


def is_dependency_file(file_name: str, filepath: Path) -> bool:
    return (
        file_name.lower() in cs.DEPENDENCY_FILES
        or filepath.suffix.lower() == cs.CSPROJ_SUFFIX
    )
