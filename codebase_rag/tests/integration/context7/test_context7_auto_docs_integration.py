from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest

from codebase_rag.core import main as main_module
from codebase_rag.services.context7_client import Context7Client
from codebase_rag.services.context7_persistence import (
    Context7KnowledgeStore,
    Context7MemoryStore,
    Context7Persistence,
)


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


class FakeClient:
    def __init__(
        self,
        *,
        configured: bool = True,
        detected_library: str | None = "fastapi",
        auto_result: dict[str, Any] | None = None,
    ) -> None:
        self._configured = configured
        self._detected_library = detected_library
        self._auto_result = auto_result or {
            "library_id": "/tiangolo/fastapi",
            "docs": [{"title": "Auth", "content": "Use OAuth2PasswordBearer."}],
        }

    def is_configured(self) -> bool:
        return self._configured

    def detect_library(self, query: str) -> str | None:
        return self._detected_library

    async def auto_docs(self, query: str) -> dict[str, Any] | None:
        return self._auto_result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resolve_context7_auto_docs_uses_graph_cache_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module.settings, "CONTEXT7_AUTO_ENABLED", True)
    cached = {
        "library": "fastapi",
        "query": "fastapi auth",
        "docs": [{"title": "Cached", "content": "From graph"}],
        "source": "graph",
    }
    runtime = main_module.Context7AutoDocsRuntime(
        client=cast(Context7Client, FakeClient()),
        knowledge_store=cast(Context7KnowledgeStore, FakeKnowledgeStore(cached)),
        memory_store=cast(Context7MemoryStore, FakeMemoryStore(None)),
        persistence=cast(Context7Persistence, FakePersistence([])),
    )

    result = await main_module._resolve_context7_auto_docs("fastapi auth", runtime)

    assert result == cached


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resolve_context7_auto_docs_fetches_and_persists_when_cache_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module.settings, "CONTEXT7_AUTO_ENABLED", True)
    calls: list[tuple[str, str, str, Any]] = []
    auto_payload = {
        "library_id": "/tiangolo/fastapi",
        "docs": [{"title": "Security", "content": "Use dependency injection."}],
    }
    runtime = main_module.Context7AutoDocsRuntime(
        client=cast(Context7Client, FakeClient(auto_result=auto_payload)),
        knowledge_store=cast(Context7KnowledgeStore, FakeKnowledgeStore(None)),
        memory_store=cast(Context7MemoryStore, FakeMemoryStore(None)),
        persistence=cast(Context7Persistence, FakePersistence(calls)),
    )

    result = await main_module._resolve_context7_auto_docs("fastapi security", runtime)

    assert result == auto_payload
    assert calls == [
        (
            "/tiangolo/fastapi",
            "fastapi",
            "fastapi security",
            auto_payload["docs"],
        )
    ]


@pytest.mark.integration
def test_inject_context7_auto_docs_adds_reference_block() -> None:
    question = "How should I implement login flow?"
    payload = {
        "library_id": "/tiangolo/fastapi",
        "docs": [{"title": "Authentication", "content": "Use OAuth2PasswordBearer."}],
    }

    injected = main_module._inject_context7_auto_docs(question, payload)

    assert "[Context7 Auto Docs]" in injected
    assert "Library: /tiangolo/fastapi" in injected
    assert "Use OAuth2PasswordBearer." in injected
    assert question in injected
