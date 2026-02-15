from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from codebase_rag.services.context7_persistence import (
    Context7KnowledgeStore,
    Context7MemoryStore,
    Context7MemoryWriter,
    Context7Persistence,
)

pytestmark = [pytest.mark.integration]


class FakeIngestor:
    def __init__(self, responses: list[list[dict[str, Any]]] | None = None) -> None:
        self.responses = responses or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def fetch_all(self, cypher: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((cypher, params))
        if self.responses:
            return self.responses.pop(0)
        return []


@dataclass
class Tracker:
    memory_calls: int = 0


def _sample_docs() -> list[dict[str, str]]:
    return [{"title": "Authentication", "content": "Use OAuth2 with bearer token."}]


def test_memory_store_lookup_returns_recent_context7_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "codebase_rag.services.context7_persistence.settings.CONTEXT7_PERSIST_MEMORY",
        True,
    )

    writer = Context7MemoryWriter(str(tmp_path))
    writer.write("/tiangolo/fastapi", "fastapi", "auth", _sample_docs())

    store = Context7MemoryStore(str(tmp_path))
    result = store.lookup("fastapi", "auth")

    assert result is not None
    assert result["source"] == "memory"
    assert result["library"] == "fastapi"
    assert isinstance(result["docs"], list)
    assert result["docs"][0]["library_id"] == "/tiangolo/fastapi"


def test_knowledge_store_lookup_falls_back_to_content_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "codebase_rag.services.context7_persistence.settings.CONTEXT7_PERSIST_GRAPH",
        True,
    )

    rows = [
        [],
        [
            {
                "id": "context7::abc",
                "title": "Security",
                "content": "OAuth2 and JWT",
                "source": "https://docs.example",
                "topic": "auth",
                "doc_version": "1.0",
                "retrieved_at": 123.0,
            }
        ],
    ]
    ingestor = FakeIngestor(rows)
    store = Context7KnowledgeStore(ingestor)  # type: ignore[arg-type]

    result = store.lookup("fastapi", "jwt")

    assert result is not None
    assert result["source"] == "graph"
    assert len(ingestor.calls) == 2
    assert "toLower(d.content) CONTAINS" in ingestor.calls[1][0]


def test_context7_persistence_continues_when_graph_write_fails(
    tmp_path: Path,
) -> None:
    persistence = Context7Persistence(FakeIngestor(), str(tmp_path))  # type: ignore[arg-type]
    tracker = Tracker()

    def failing_graph_write(
        library_id: str, library_name: str, query: str, docs: Any
    ) -> int:
        raise RuntimeError("graph unavailable")

    def track_memory_write(
        library_id: str, library_name: str, query: str, docs: Any
    ) -> None:
        tracker.memory_calls += 1

    persistence.graph_writer.write = failing_graph_write  # type: ignore[method-assign]
    persistence.memory_writer.write = track_memory_write  # type: ignore[method-assign]

    persistence.persist("/tiangolo/fastapi", "fastapi", "auth", _sample_docs())

    assert tracker.memory_calls == 1
