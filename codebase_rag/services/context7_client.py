from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from codebase_rag.core.config import settings


@dataclass
class Context7Config:
    api_key: str | None
    api_url: str | None
    mcp_url: str | None


class Context7Client:
    def __init__(
        self,
        api_key: str | None = None,
        api_url: str | None = None,
        mcp_url: str | None = None,
    ) -> None:
        self.config = Context7Config(
            api_key=api_key or settings.CONTEXT7_API_KEY,
            api_url=api_url or settings.CONTEXT7_API_URL,
            mcp_url=mcp_url or settings.CONTEXT7_MCP_URL,
        )

    def is_configured(self) -> bool:
        return bool(
            self.config.api_key and (self.config.api_url or self.config.mcp_url)
        )

    async def search_library(
        self, library_name: str, query: str
    ) -> dict[str, Any] | list[dict[str, Any]]:
        if not library_name:
            return {"error": "library_required"}
        if not query:
            return {"error": "query_required"}
        payload = {"libraryName": library_name, "query": query}
        return await self._call_api("/api/v2/libs/search", payload, allow_get=True)

    async def resolve_docs(
        self, library_id: str, query: str
    ) -> dict[str, Any] | list[dict[str, Any]]:
        payload = {"libraryId": library_id, "query": query}
        return await self._call_api("/api/v2/context", payload, allow_get=True)

    async def resolve_library_id(
        self, library_name: str, query: str | None = None
    ) -> dict[str, Any]:
        if not library_name:
            return {"error": "library_required"}
        if self.config.api_url:
            search = await self.search_library(library_name, query or library_name)
            search_id = self._extract_library_id(search)
            if search_id:
                return {"libraryId": search_id, "search": search}
        payload = {"libraryName": library_name, "query": query or library_name}
        if self.config.mcp_url:
            result = await self._call_mcp_tool("resolve-library-id", payload)
            if self._is_mcp_error(result) and self.config.api_url:
                return await self._call_api(
                    "/api/v2/libs/search", payload, allow_get=True
                )
            return result
        return await self._call_api("/api/v2/libs/search", payload, allow_get=True)

    async def query_docs(
        self, library_id: str, query: str
    ) -> dict[str, Any] | list[dict[str, Any]]:
        if not library_id:
            return {"error": "library_id_required"}
        if not query:
            return {"error": "query_required"}
        payload = {"libraryId": library_id, "query": query}
        if self.config.mcp_url:
            result = await self._call_mcp_tool("query-docs", payload)
            if self._is_mcp_error(result) and self.config.api_url:
                return await self._call_api("/api/v2/context", payload, allow_get=True)
            return result
        return await self._call_api("/api/v2/context", payload, allow_get=True)

    async def get_docs(
        self, library: str, query: str, version: str | None = None
    ) -> dict[str, Any]:
        if not self.is_configured():
            return {"error": "context7_not_configured"}

        library_id = None
        if library.startswith("/"):
            library_id = library
        else:
            resolved = await self.resolve_library_id(library, query)
            library_id = self._extract_library_id(resolved)

        if not library_id:
            direct_docs = await self.resolve_docs(library, query)
            if not (isinstance(direct_docs, dict) and direct_docs.get("error")):
                return {
                    "library_id": library,
                    "query": query,
                    "docs": direct_docs,
                    "resolve": resolved,
                }
            direct_query = await self.query_docs(library, query)
            if not (isinstance(direct_query, dict) and direct_query.get("error")):
                return {
                    "library_id": library,
                    "query": query,
                    "docs": direct_query,
                    "resolve": resolved,
                }
            return {"error": "library_id_not_found", "resolve": resolved}

        if version and version not in library_id:
            library_id = f"{library_id}/{version}"

        docs = await self.resolve_docs(library_id, query)
        if isinstance(docs, dict) and docs.get("error") and self.config.api_url:
            docs = await self.query_docs(library_id, query)
        return {
            "library_id": library_id,
            "query": query,
            "docs": docs,
        }

    def detect_library(self, query: str) -> str | None:
        if not query:
            return None
        raw = settings.CONTEXT7_AUTO_LIBRARIES
        if not raw:
            return None
        query_lower = query.lower()
        candidates = [item.strip() for item in raw.split(",") if item.strip()]
        for candidate in candidates:
            if candidate and candidate in query_lower:
                return candidate
        return None

    async def auto_docs(self, query: str) -> dict[str, Any] | None:
        if not settings.CONTEXT7_AUTO_ENABLED:
            return None
        library = self.detect_library(query)
        if not library:
            return None
        result = await self.get_docs(library, query)
        if isinstance(result, dict) and result.get("error"):
            return None
        return result

    async def _call_api(
        self, path: str, payload: dict[str, Any], allow_get: bool = False
    ) -> dict[str, Any]:
        if not self.config.api_url or not self.config.api_key:
            return {"error": "context7_not_configured"}
        base_url = self.config.api_url.rstrip("/")
        if path.startswith("/api/v2") and base_url.endswith(("/api/v1", "/api/v2")):
            base_url = base_url.rsplit("/api/", 1)[0]
        url = f"{base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "X-API-Key": self.config.api_key,
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                if allow_get:
                    response = await client.get(url, params=payload, headers=headers)
                    if response.status_code in {400, 405}:
                        response = await client.post(url, json=payload, headers=headers)
                else:
                    response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError:
                    text = response.text
                    return {
                        "content": text,
                        "format": "text",
                        "status_code": response.status_code,
                    }
        except Exception as exc:
            logger.warning("Context7 API error: {error}", error=exc)
            return {"error": "context7_api_error", "detail": str(exc)}

    async def _call_mcp_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if not self.config.mcp_url or not self.config.api_key:
            return {"error": "context7_not_configured"}
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Accept": "application/json",
        }
        payload = {
            "jsonrpc": "2.0",
            "id": "context7",
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    self.config.mcp_url, json=payload, headers=headers
                )
                response.raise_for_status()
                data = response.json()
                return data.get("result", data)
        except Exception as exc:
            logger.warning("Context7 MCP error: {error}", error=exc)
            return {"error": "context7_mcp_error", "detail": str(exc)}

    @staticmethod
    def _extract_library_id(payload: dict[str, Any] | list[Any]) -> str | None:
        if isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict):
                for key in ("id", "libraryId", "library_id"):
                    value = first.get(key)
                    if isinstance(value, str):
                        return value
            return None
        if not isinstance(payload, dict):
            return None
        if isinstance(payload.get("libraryId"), str):
            return str(payload.get("libraryId"))
        results = payload.get("results")
        if isinstance(results, list) and results:
            first = results[0]
            if isinstance(first, dict):
                for key in ("id", "libraryId", "library_id"):
                    value = first.get(key)
                    if isinstance(value, str):
                        return value
        libraries = payload.get("libraries")
        if isinstance(libraries, list) and libraries:
            first = libraries[0]
            if isinstance(first, dict):
                for key in ("id", "libraryId", "library_id"):
                    value = first.get(key)
                    if isinstance(value, str):
                        return value
        return None

    @staticmethod
    def _is_mcp_error(payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        return payload.get("error") == "context7_mcp_error"
