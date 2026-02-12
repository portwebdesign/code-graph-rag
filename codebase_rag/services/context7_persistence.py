from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from loguru import logger

from codebase_rag.agents.memory import MemoryAgent
from codebase_rag.core.config import settings
from codebase_rag.core.constants import NodeLabel, RelationshipType
from codebase_rag.services.graph_service import MemgraphIngestor


@dataclass
class Context7Chunk:
    chunk_id: str
    title: str
    content: str
    source: str | None
    topic: str
    concepts: list[str]
    doc_version: str | None
    retrieved_at: float
    valid_until: float
    status: str
    checksum: str


class Context7DocParser:
    separator_pattern = re.compile(r"\n-{10,}\n")
    version_pattern = re.compile(r"\b(v?\d+\.\d+(?:\.\d+)?)\b", re.IGNORECASE)
    keyword_patterns = {
        "csrf": re.compile(r"\bcsrf\b", re.IGNORECASE),
        "session": re.compile(r"\bsession\b", re.IGNORECASE),
        "token": re.compile(r"\btoken\b", re.IGNORECASE),
        "cookie": re.compile(r"\bcookie\b", re.IGNORECASE),
        "nonce": re.compile(r"\bnonce\b", re.IGNORECASE),
        "jwt": re.compile(r"\bjwt\b", re.IGNORECASE),
        "oauth": re.compile(r"\boauth\b", re.IGNORECASE),
        "rate_limit": re.compile(r"rate\s*limit", re.IGNORECASE),
        "pagination": re.compile(r"pagination", re.IGNORECASE),
        "webhook": re.compile(r"webhook", re.IGNORECASE),
        "endpoint": re.compile(r"endpoint", re.IGNORECASE),
    }

    def normalize(self, docs: Any, topic: str, library_id: str) -> list[Context7Chunk]:
        if isinstance(docs, dict) and isinstance(docs.get("content"), str):
            return self._from_text(str(docs.get("content")), topic, library_id)
        if isinstance(docs, list):
            return self._from_list(docs, topic, library_id)
        if isinstance(docs, str):
            return self._from_text(docs, topic, library_id)
        return []

    def _from_list(
        self, items: list[Any], topic: str, library_id: str
    ) -> list[Context7Chunk]:
        chunks: list[Context7Chunk] = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or topic)
            content = str(entry.get("content") or "")
            source = entry.get("source")
            doc_version = self._extract_version(content)
            chunks.append(
                self._build_chunk(
                    title, content, source, topic, doc_version, library_id
                )
            )
        return chunks

    def _from_text(self, text: str, topic: str, library_id: str) -> list[Context7Chunk]:
        parts = [
            part.strip() for part in self.separator_pattern.split(text) if part.strip()
        ]
        chunks: list[Context7Chunk] = []
        for part in parts:
            title = self._extract_title(part) or topic
            source = self._extract_source(part)
            doc_version = self._extract_version(part)
            chunks.append(
                self._build_chunk(title, part, source, topic, doc_version, library_id)
            )
        if not chunks and text.strip():
            doc_version = self._extract_version(text)
            chunks.append(
                self._build_chunk(
                    topic,
                    text.strip(),
                    None,
                    topic,
                    doc_version,
                    library_id,
                )
            )
        return chunks

    def _extract_title(self, text: str) -> str | None:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("### "):
                return line.replace("### ", "", 1).strip()
        return None

    def _extract_source(self, text: str) -> str | None:
        for line in text.splitlines():
            line = line.strip()
            if line.lower().startswith("source:"):
                return line.split(":", 1)[-1].strip()
        return None

    def _build_chunk(
        self,
        title: str,
        content: str,
        source: str | None,
        topic: str,
        doc_version: str | None,
        library_id: str,
    ) -> Context7Chunk:
        retrieved_at = time.time()
        valid_until = retrieved_at + settings.CONTEXT7_DOC_TTL_DAYS * 86400
        raw = f"{library_id}|{topic}|{title}\n{content}\n{source or ''}"
        checksum = sha256(raw.encode("utf-8")).hexdigest()
        chunk_id = f"context7::{checksum}"
        concepts = self._extract_concepts(content, topic)
        if not doc_version:
            doc_version = "unknown"
        return Context7Chunk(
            chunk_id=chunk_id,
            title=title,
            content=content,
            source=source,
            topic=topic,
            concepts=concepts,
            doc_version=doc_version,
            retrieved_at=retrieved_at,
            valid_until=valid_until,
            status="ACTIVE",
            checksum=checksum,
        )

    def _extract_version(self, text: str) -> str | None:
        match = self.version_pattern.search(text)
        if match:
            return match.group(1)
        return None

    def _extract_concepts(self, text: str, topic: str) -> list[str]:
        concepts = {topic.strip()} if topic else set()
        for key, pattern in self.keyword_patterns.items():
            if pattern.search(text):
                concepts.add(key)
        hx_matches = re.findall(r"\bhx-[a-z-]+\b", text, re.IGNORECASE)
        for match in hx_matches[:5]:
            concepts.add(match.lower())
        return [item for item in concepts if item]

    def parse_version_tuple(self, version: str | None) -> tuple[int, ...] | None:
        if not version or version == "unknown":
            return None
        parts = re.findall(r"\d+", version)
        if not parts:
            return None
        return tuple(int(part) for part in parts[:4])


class Context7GraphWriter:
    def __init__(self, ingestor: MemgraphIngestor, project_root: str) -> None:
        self.ingestor = ingestor
        self.project_root = Path(project_root).resolve()
        self.project_name = self.project_root.name
        self.parser = Context7DocParser()

    def write(
        self,
        library_id: str,
        library_name: str,
        query: str,
        docs: Any,
    ) -> int:
        if not settings.CONTEXT7_PERSIST_GRAPH:
            return 0
        if not library_name:
            library_name = self._normalize_library_name(library_id)
        chunks = self.parser.normalize(docs, query, library_id)
        if not chunks:
            return 0
        self._ensure_project()
        self._ensure_library(library_name, library_id)
        inserted = 0
        for chunk in chunks[: settings.CONTEXT7_MAX_CHUNKS]:
            self._ensure_docchunk(library_name, library_id, chunk)
            self._ensure_relationships(library_name, chunk, query)
            self._ensure_code_links(library_name, chunk)
            self._ensure_supersedes(library_name, library_id, chunk)
            inserted += 1
        self.ingestor.flush_nodes()
        self.ingestor.flush_relationships()
        return inserted

    @staticmethod
    def _normalize_library_name(library_id: str) -> str:
        if not library_id:
            return "context7"
        name = library_id.strip("/").split("/")[-1]
        return name.replace("_", "-")

    def _ensure_project(self) -> None:
        self.ingestor.ensure_node_batch(
            NodeLabel.PROJECT.value,
            {"name": self.project_name},
        )

    def _ensure_library(self, library_name: str, library_id: str) -> None:
        self.ingestor.ensure_node_batch(
            NodeLabel.LIBRARY.value,
            {
                "name": library_name,
                "qualified_name": f"library.{library_name}",
                "library_id": library_id,
            },
        )
        self.ingestor.ensure_relationship_batch(
            (NodeLabel.PROJECT.value, "name", self.project_name),
            RelationshipType.USES_LIBRARY.value,
            (NodeLabel.LIBRARY.value, "name", library_name),
        )

    def _ensure_docchunk(
        self, library_name: str, library_id: str, chunk: Context7Chunk
    ) -> None:
        self.ingestor.ensure_node_batch(
            NodeLabel.DOC_CHUNK.value,
            {
                "qualified_name": chunk.chunk_id,
                "name": chunk.title,
                "title": chunk.title,
                "content": chunk.content,
                "topic": chunk.topic,
                "source": chunk.source,
                "library_id": library_id,
                "doc_version": chunk.doc_version,
                "retrieved_at": chunk.retrieved_at,
                "valid_until": chunk.valid_until,
                "status": chunk.status,
                "checksum": chunk.checksum,
            },
        )

    def _ensure_relationships(
        self, library_name: str, chunk: Context7Chunk, query: str
    ) -> None:
        self.ingestor.ensure_relationship_batch(
            (NodeLabel.LIBRARY.value, "name", library_name),
            RelationshipType.HAS_DOC.value,
            (NodeLabel.DOC_CHUNK.value, "qualified_name", chunk.chunk_id),
        )
        self.ingestor.ensure_relationship_batch(
            (NodeLabel.DOC_CHUNK.value, "qualified_name", chunk.chunk_id),
            RelationshipType.USED_IN.value,
            (NodeLabel.PROJECT.value, "name", self.project_name),
            {
                "agent": "context7_persistence",
                "confidence": 0.9,
                "query": query,
            },
        )
        for concept_name in chunk.concepts:
            safe_name = concept_name.strip()[:200] if concept_name else "context7"
            self.ingestor.ensure_node_batch(
                NodeLabel.CONCEPT.value,
                {"name": safe_name, "qualified_name": f"concept.{safe_name}"},
            )
            self.ingestor.ensure_relationship_batch(
                (NodeLabel.DOC_CHUNK.value, "qualified_name", chunk.chunk_id),
                RelationshipType.DESCRIBES.value,
                (NodeLabel.CONCEPT.value, "name", safe_name),
            )
        if chunk.source:
            self.ingestor.ensure_node_batch(
                NodeLabel.SOURCE.value,
                {"name": chunk.source, "qualified_name": f"source.{chunk.source}"},
            )
            self.ingestor.ensure_relationship_batch(
                (NodeLabel.DOC_CHUNK.value, "qualified_name", chunk.chunk_id),
                RelationshipType.SOURCED_FROM.value,
                (NodeLabel.SOURCE.value, "name", chunk.source),
            )

    def _ensure_code_links(self, library_name: str, chunk: Context7Chunk) -> None:
        cypher = """
MATCH (p:Project {name: $project})-[:DEPENDS_ON_EXTERNAL]->(e:ExternalPackage)
WHERE toLower(e.name) = toLower($library)
RETURN e.name AS name
LIMIT 1
"""
        rows = self.ingestor.fetch_all(
            cypher,
            {"project": self.project_name, "library": library_name},
        )
        if not rows:
            return
        package_name = rows[0].get("name")
        if not package_name:
            return
        self.ingestor.ensure_relationship_batch(
            (NodeLabel.DOC_CHUNK.value, "qualified_name", chunk.chunk_id),
            RelationshipType.DOCUMENTS_EXTERNAL.value,
            (NodeLabel.EXTERNAL_PACKAGE.value, "name", package_name),
        )

    def _ensure_supersedes(
        self, library_name: str, library_id: str, chunk: Context7Chunk
    ) -> None:
        cypher = """
MATCH (l:Library {name: $library})-[:HAS_DOC]->(d:DocChunk)
WHERE d.topic = $topic AND d.checksum <> $checksum
RETURN d.qualified_name AS qn,
       d.doc_version AS doc_version,
       d.retrieved_at AS retrieved_at
ORDER BY d.retrieved_at DESC
LIMIT 1
"""
        rows = self.ingestor.fetch_all(
            cypher,
            {
                "library": library_name,
                "topic": chunk.topic,
                "checksum": chunk.checksum,
            },
        )
        if not rows:
            return
        prior = rows[0]
        prior_qn = prior.get("qn")
        if not prior_qn:
            return
        prior_version = prior.get("doc_version")
        prior_tuple = self.parser.parse_version_tuple(
            str(prior_version) if prior_version else None
        )
        current_tuple = self.parser.parse_version_tuple(chunk.doc_version)
        if prior_tuple and current_tuple:
            if current_tuple < prior_tuple:
                self._update_doc_status(chunk.chunk_id, "STALE")
            else:
                self._update_doc_status(prior_qn, "DEPRECATED")
        else:
            self._update_doc_status(prior_qn, "DEPRECATED")
        self.ingestor.ensure_relationship_batch(
            (NodeLabel.DOC_CHUNK.value, "qualified_name", prior_qn),
            RelationshipType.SUPERSEDES.value,
            (NodeLabel.DOC_CHUNK.value, "qualified_name", chunk.chunk_id),
        )

    def _update_doc_status(self, qualified_name: str, status: str) -> None:
        cypher = """
MATCH (d:DocChunk {qualified_name: $qn})
SET d.status = $status
"""
        self.ingestor.execute_write(
            cypher,
            {"qn": qualified_name, "status": status},
        )


class Context7MemoryWriter:
    def __init__(self, project_root: str) -> None:
        self.agent = MemoryAgent(project_root)
        self.parser = Context7DocParser()

    def write(self, library_id: str, library_name: str, query: str, docs: Any) -> None:
        if not settings.CONTEXT7_PERSIST_MEMORY:
            return
        if not library_name:
            library_name = Context7GraphWriter._normalize_library_name(library_id)
        chunks = self.parser.normalize(docs, query, library_id)
        if not chunks:
            return
        first = chunks[0]
        summary = first.content.strip().replace("\n", " ")
        if len(summary) > settings.CONTEXT7_MEMORY_MAX_CHARS:
            summary = summary[: settings.CONTEXT7_MEMORY_MAX_CHARS - 3].rstrip() + "..."
        payload = {
            "type": "context7",
            "library": library_name,
            "library_id": library_id,
            "query": query,
            "summary": summary,
            "source": "context7",
            "doc_ids": [chunk.chunk_id for chunk in chunks],
            "retrieved_at": time.time(),
        }
        tags = [library_name, "context7"]
        self.agent.add_entry(text=json.dumps(payload, ensure_ascii=False), tags=tags)


class Context7MemoryStore:
    def __init__(self, project_root: str) -> None:
        self.agent = MemoryAgent(project_root)

    def lookup(self, library: str, query: str, limit: int = 5) -> dict[str, Any] | None:
        if not library:
            return None
        items: list[dict[str, Any]] = []
        for entry in self.agent.list_entries(limit=200):
            if library not in entry.tags:
                continue
            try:
                payload = json.loads(entry.text)
                if isinstance(payload, dict):
                    items.append(payload)
            except json.JSONDecodeError:
                items.append({"summary": entry.text, "tags": entry.tags})
            if len(items) >= limit:
                break
        if not items:
            return None
        return {
            "library": library,
            "query": query,
            "docs": items,
            "source": "memory",
        }


class Context7KnowledgeStore:
    def __init__(self, ingestor: MemgraphIngestor) -> None:
        self.ingestor = ingestor

    def lookup(self, library: str, query: str, limit: int = 5) -> dict[str, Any] | None:
        if not settings.CONTEXT7_PERSIST_GRAPH:
            return None
        library_name = library.strip()
        if not library_name:
            return None
        query_text = query.lower().strip()
        now = time.time()
        cypher_primary = """
MATCH (l:Library {name: $library})-[:HAS_DOC]->(d:DocChunk)
WHERE (toLower(d.topic) CONTAINS $query OR toLower(d.title) CONTAINS $query)
  AND (d.valid_until IS NULL OR d.valid_until > $now)
  AND (d.status IS NULL OR d.status <> 'DEPRECATED')
RETURN d.qualified_name AS id,
       d.title AS title,
       d.content AS content,
       d.source AS source,
       d.topic AS topic,
       d.doc_version AS doc_version,
       d.retrieved_at AS retrieved_at
ORDER BY d.retrieved_at DESC
LIMIT $limit
"""
        rows = self.ingestor.fetch_all(
            cypher_primary,
            {"library": library_name, "query": query_text, "now": now, "limit": limit},
        )
        if not rows:
            cypher_fallback = """
MATCH (l:Library {name: $library})-[:HAS_DOC]->(d:DocChunk)
WHERE toLower(d.content) CONTAINS $query
  AND (d.valid_until IS NULL OR d.valid_until > $now)
  AND (d.status IS NULL OR d.status <> 'DEPRECATED')
RETURN d.qualified_name AS id,
       d.title AS title,
       d.content AS content,
       d.source AS source,
       d.topic AS topic,
       d.doc_version AS doc_version,
       d.retrieved_at AS retrieved_at
ORDER BY d.retrieved_at DESC
LIMIT $limit
"""
            rows = self.ingestor.fetch_all(
                cypher_fallback,
                {
                    "library": library_name,
                    "query": query_text,
                    "now": now,
                    "limit": limit,
                },
            )
        if not rows:
            return None
        return {
            "library": library_name,
            "query": query,
            "docs": rows,
            "source": "graph",
        }


class Context7Persistence:
    def __init__(self, ingestor: MemgraphIngestor, project_root: str) -> None:
        self.graph_writer = Context7GraphWriter(ingestor, project_root)
        self.memory_writer = Context7MemoryWriter(project_root)

    def persist(
        self, library_id: str, library_name: str, query: str, docs: Any
    ) -> None:
        try:
            self.graph_writer.write(library_id, library_name, query, docs)
        except Exception as exc:
            logger.warning("Context7 graph persist failed: {error}", error=exc)
        try:
            self.memory_writer.write(library_id, library_name, query, docs)
        except Exception as exc:
            logger.warning("Context7 memory persist failed: {error}", error=exc)
