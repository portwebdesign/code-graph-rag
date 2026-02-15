from __future__ import annotations

from typing import Any

import pytest

from codebase_rag.services.context7_client import Context7Client

pytestmark = [pytest.mark.integration]


@pytest.mark.asyncio
async def test_get_docs_returns_not_configured_error_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "codebase_rag.services.context7_client.settings.CONTEXT7_API_KEY", None
    )
    monkeypatch.setattr(
        "codebase_rag.services.context7_client.settings.CONTEXT7_API_URL", None
    )
    monkeypatch.setattr(
        "codebase_rag.services.context7_client.settings.CONTEXT7_MCP_URL", None
    )

    client = Context7Client()

    result = await client.get_docs("fastapi", "auth")

    assert result == {"error": "context7_not_configured"}


@pytest.mark.asyncio
async def test_get_docs_appends_version_to_resolved_library_id() -> None:
    client = Context7Client(
        api_key="k", api_url="https://context7.example", mcp_url=None
    )

    async def fake_resolve_library_id(
        library_name: str, query: str | None = None
    ) -> dict[str, Any]:
        assert library_name == "fastapi"
        assert query == "security"
        return {"libraryId": "/tiangolo/fastapi"}

    async def fake_resolve_docs(library_id: str, query: str) -> list[dict[str, str]]:
        assert library_id == "/tiangolo/fastapi/1.0.0"
        assert query == "security"
        return [{"title": "Security", "content": "Use OAuth2."}]

    async def fake_query_docs(library_id: str, query: str) -> dict[str, str]:
        raise AssertionError(
            "query_docs should not be called when resolve_docs succeeds"
        )

    client.resolve_library_id = fake_resolve_library_id  # type: ignore[method-assign]
    client.resolve_docs = fake_resolve_docs  # type: ignore[method-assign]
    client.query_docs = fake_query_docs  # type: ignore[method-assign]

    result = await client.get_docs("fastapi", "security", version="1.0.0")

    assert result["library_id"] == "/tiangolo/fastapi/1.0.0"
    assert result["query"] == "security"
    assert isinstance(result["docs"], list)


@pytest.mark.asyncio
async def test_get_docs_falls_back_to_query_docs_when_resolve_docs_errors() -> None:
    client = Context7Client(
        api_key="k", api_url="https://context7.example", mcp_url=None
    )

    async def fake_resolve_library_id(
        library_name: str, query: str | None = None
    ) -> dict[str, Any]:
        return {"libraryId": "/tiangolo/fastapi"}

    async def fake_resolve_docs(library_id: str, query: str) -> dict[str, str]:
        return {"error": "upstream_error"}

    async def fake_query_docs(library_id: str, query: str) -> list[dict[str, str]]:
        return [{"title": "Fallback", "content": "Use Depends()."}]

    client.resolve_library_id = fake_resolve_library_id  # type: ignore[method-assign]
    client.resolve_docs = fake_resolve_docs  # type: ignore[method-assign]
    client.query_docs = fake_query_docs  # type: ignore[method-assign]

    result = await client.get_docs("fastapi", "dependency injection")

    assert result["library_id"] == "/tiangolo/fastapi"
    assert isinstance(result["docs"], list)
    assert result["docs"][0]["title"] == "Fallback"


@pytest.mark.asyncio
async def test_auto_docs_returns_none_when_feature_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "codebase_rag.services.context7_client.settings.CONTEXT7_AUTO_ENABLED", False
    )

    client = Context7Client(
        api_key="k", api_url="https://context7.example", mcp_url=None
    )

    async def fail_get_docs(
        library: str, query: str, version: str | None = None
    ) -> dict[str, str]:
        raise AssertionError("get_docs should not be called when auto docs is disabled")

    client.get_docs = fail_get_docs  # type: ignore[method-assign]

    result = await client.auto_docs("fastapi auth")

    assert result is None
