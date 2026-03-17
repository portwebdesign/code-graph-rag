from __future__ import annotations

from pathlib import Path

from codebase_rag.core.semantic_schema_metadata import (
    SEMANTIC_SCHEMA_VERSION,
    build_semantic_schema_metadata,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_semantic_docs_reference_schema_version_and_planes() -> None:
    metadata = build_semantic_schema_metadata()
    readme = _read("README.md")
    versioning_doc = _read("docs/architecture/semantic-schema-versioning.md")
    release_doc = _read("docs/architecture/semantic-release-closure.md")

    assert f"`{SEMANTIC_SCHEMA_VERSION}`" in versioning_doc
    for plane in ("static", "runtime", "heuristic"):
        assert plane in versioning_doc

    assert "semantic-schema-versioning.md" in readme
    assert "semantic-release-closure.md" in readme
    assert "semantic-validation-matrix.md" in readme

    capability_ids = {
        str(capability["id"])
        for capability in metadata["capabilities"]
        if isinstance(capability, dict)
    }
    for capability_id in capability_ids:
        assert capability_id in versioning_doc

    compatibility = metadata["compatibility"]
    assert isinstance(compatibility, dict)
    assert str(compatibility["breaking_change_policy"]) in versioning_doc
    assert str(compatibility["consumer_guidance"]) in versioning_doc
    assert "Known limits" in release_doc


def test_semantic_capability_docs_paths_exist_and_are_indexed() -> None:
    metadata = build_semantic_schema_metadata()
    versioning_doc = _read("docs/architecture/semantic-schema-versioning.md")

    for capability in metadata["capabilities"]:
        assert isinstance(capability, dict)
        docs_path = str(capability["docs_path"])
        assert (REPO_ROOT / docs_path).exists()
        assert docs_path in versioning_doc
