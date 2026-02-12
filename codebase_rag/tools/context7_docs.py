from __future__ import annotations

from loguru import logger
from pydantic_ai import Tool

from codebase_rag.core import logs as ls
from codebase_rag.services.context7_client import Context7Client
from codebase_rag.services.context7_persistence import (
    Context7KnowledgeStore,
    Context7MemoryStore,
    Context7Persistence,
)

from . import tool_descriptions as td


def create_context7_tool(
    knowledge_store: Context7KnowledgeStore | None = None,
    memory_store: Context7MemoryStore | None = None,
    persistence: Context7Persistence | None = None,
) -> Tool:
    client = Context7Client()

    async def context7_docs(
        library: str,
        query: str,
        version: str | None = None,
    ) -> dict[str, object]:
        logger.info(ls.TOOL_QUERY_RECEIVED.format(query=query))
        if knowledge_store and library and query:
            cached = knowledge_store.lookup(library, query)
            if cached:
                return cached
        if memory_store and library and query:
            cached = memory_store.lookup(library, query)
            if cached:
                return cached
        result = await client.get_docs(library, query, version)
        if persistence and isinstance(result, dict) and "error" not in result:
            persistence.persist(
                str(result.get("library_id", "")),
                library,
                query,
                result.get("docs"),
            )
        return result

    return Tool(
        function=context7_docs,
        name=td.AgenticToolName.CONTEXT7_DOCS,
        description=td.CONTEXT7_DOCS,
    )
