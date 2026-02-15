from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest

from codebase_rag.services.context7_persistence import (
    Context7KnowledgeStore,
    Context7MemoryStore,
    Context7Persistence,
)
from codebase_rag.tools import context7_docs as context7_docs_module

pytestmark = [pytest.mark.integration]


class FakeClient:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {
            "library_id": "/tiangolo/fastapi",
            "docs": [{"title": "Remote", "content": "From API"}],
        }
        self.calls = 0

    async def get_docs(
        self, library: str, query: str, version: str | None = None
    ) -> dict[str, Any]:
        self.calls += 1
        return self.payload


class FakeKnowledgeStore:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload

    def lookup(self, library: str, query: str) -> dict[str, Any] | None:
        return self.payload


class FakeMemoryStore:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload

    def lookup(self, library: str, query: str) -> dict[str, Any] | None:
        return self.payload


@dataclass
class FakePersistence:
    calls: list[tuple[str, str, str, Any]]

    def persist(
        self, library_id: str, library_name: str, query: str, docs: Any
    ) -> None:
        self.calls.append((library_id, library_name, query, docs))


@pytest.mark.asyncio
async def test_context7_tool_returns_graph_cache_without_remote_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeClient()
    monkeypatch.setattr(context7_docs_module, "Context7Client", lambda: fake_client)

    cached = {
        "library": "fastapi",
        "query": "auth",
        "docs": [{"title": "Cached", "content": "Graph data"}],
        "source": "graph",
    }
    tool = context7_docs_module.create_context7_tool(
        knowledge_store=cast(Context7KnowledgeStore, FakeKnowledgeStore(cached)),
        memory_store=cast(Context7MemoryStore, FakeMemoryStore(None)),
        persistence=cast(Context7Persistence, FakePersistence([])),
    )

    result = await tool.function("fastapi", "auth")

    assert result == cached
    assert fake_client.calls == 0


@pytest.mark.asyncio
async def test_context7_tool_returns_memory_cache_without_remote_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeClient()
    monkeypatch.setattr(context7_docs_module, "Context7Client", lambda: fake_client)

    cached = {
        "library": "fastapi",
        "query": "auth",
        "docs": [{"title": "Cached", "content": "Memory data"}],
        "source": "memory",
    }
    tool = context7_docs_module.create_context7_tool(
        knowledge_store=cast(Context7KnowledgeStore, FakeKnowledgeStore(None)),
        memory_store=cast(Context7MemoryStore, FakeMemoryStore(cached)),
        persistence=cast(Context7Persistence, FakePersistence([])),
    )

    result = await tool.function("fastapi", "auth")

    assert result == cached
    assert fake_client.calls == 0


@pytest.mark.asyncio
async def test_context7_tool_fetches_and_persists_when_cache_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = {
        "library_id": "/tiangolo/fastapi",
        "docs": [{"title": "Security", "content": "Use OAuth2."}],
    }
    fake_client = FakeClient(remote)
    calls: list[tuple[str, str, str, Any]] = []
    monkeypatch.setattr(context7_docs_module, "Context7Client", lambda: fake_client)

    tool = context7_docs_module.create_context7_tool(
        knowledge_store=cast(Context7KnowledgeStore, FakeKnowledgeStore(None)),
        memory_store=cast(Context7MemoryStore, FakeMemoryStore(None)),
        persistence=cast(Context7Persistence, FakePersistence(calls)),
    )

    result = await tool.function("fastapi", "oauth2")

    assert result == remote
    assert fake_client.calls == 1
    assert calls == [
        (
            "/tiangolo/fastapi",
            "fastapi",
            "oauth2",
            remote["docs"],
        )
    ]
