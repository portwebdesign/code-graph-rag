from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from loguru import logger

from codebase_rag.ai.embedder import embed_code
from codebase_rag.core.config import settings


class EmbeddingsService:
    def __init__(self, max_cache_size: int | None = None) -> None:
        self.max_cache_size = max_cache_size or settings.EMBEDDING_CACHE_SIZE
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._embedded = 0

    @property
    def cache_hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total else 0.0

    @property
    def embedded_count(self) -> int:
        return self._embedded

    def embed_text(self, text: str) -> list[float]:
        cached = self._cache_get(text)
        if cached is not None:
            return cached

        embedding = embed_code(text)
        self._embedded += 1
        self._cache_set(text, embedding)
        return embedding

    def embed_function(self, func: dict) -> dict[str, list[float]]:
        name = str(func.get("name") or "")
        signature = str(func.get("signature") or func.get("signature_lite") or "")
        docstring = str(func.get("docstring") or "")
        semantic = self._build_semantic_context(func)

        return {
            "name_embedding": self.embed_text(name),
            "signature_embedding": self.embed_text(signature),
            "docstring_embedding": self.embed_text(docstring),
            "semantic_embedding": self.embed_text(semantic),
        }

    def embed_file(self, file_path: str | Path) -> list[float]:
        path = Path(file_path)
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            logger.warning("Embedding source missing: {}", path)
            content = ""
        return self.embed_text(content)

    def batch_embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(text) for text in texts]

    def _build_semantic_context(self, func: dict) -> str:
        parts = [
            str(func.get("qualified_name") or ""),
            str(func.get("signature") or func.get("signature_lite") or ""),
            str(func.get("docstring") or ""),
        ]
        return "\n".join(part for part in parts if part)

    def _cache_get(self, text: str) -> list[float] | None:
        if text in self._cache:
            self._hits += 1
            self._cache.move_to_end(text)
            return self._cache[text]
        self._misses += 1
        return None

    def _cache_set(self, text: str, embedding: list[float]) -> None:
        self._cache[text] = embedding
        self._cache.move_to_end(text)
        if len(self._cache) > self.max_cache_size:
            self._cache.popitem(last=False)
