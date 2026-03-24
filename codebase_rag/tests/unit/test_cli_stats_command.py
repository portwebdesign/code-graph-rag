from __future__ import annotations

from typer.testing import CliRunner

from codebase_rag.core.cli import app


class _DummyIngestorContext:
    def __init__(self, ingestor: object) -> None:
        self._ingestor = ingestor

    def __enter__(self) -> object:
        return self._ingestor

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def test_stats_command_renders_graph_and_dependency_sections(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "codebase_rag.core.cli.connect_memgraph",
        lambda batch_size: _DummyIngestorContext(object()),
    )
    monkeypatch.setattr(
        "codebase_rag.core.cli.get_graph_stats",
        lambda ingestor: {
            "nodes": 12,
            "relationships": 34,
            "labels": [{"label": "Function", "count": 5}],
            "relationship_types": [{"type": "CALLS", "count": 8}],
        },
    )
    monkeypatch.setattr(
        "codebase_rag.core.cli.get_dependency_stats",
        lambda ingestor: {
            "total_imports": 21,
            "top_importers": [{"module": "pkg.mod", "count": 3}],
            "top_dependents": [{"target": "numpy", "count": 2}],
        },
    )

    result = CliRunner().invoke(app, ["stats"])

    assert result.exit_code == 0
    assert "Graph Stats" in result.output
    assert "Nodes" in result.output
    assert "12" in result.output
    assert "Dependency Stats" in result.output
    assert "pkg.mod" in result.output
    assert "numpy" in result.output


def test_stats_command_returns_exit_code_one_on_failure(monkeypatch) -> None:
    def _raise_error(batch_size: int | None) -> _DummyIngestorContext:
        raise RuntimeError("memgraph unavailable")

    monkeypatch.setattr("codebase_rag.core.cli.connect_memgraph", _raise_error)

    result = CliRunner().invoke(app, ["stats", "--no-dependencies"])

    assert result.exit_code == 1
    assert "Failed to read graph statistics" in result.output
