"""
This module provides the `EmbeddingsService`, a service responsible for generating
vector embeddings for text and code.

It acts as a wrapper around the core embedding model, providing a consistent
interface for embedding different types of content, such as raw text, function
definitions, and entire files. A key feature is its in-memory caching mechanism
(using an LRU cache) to avoid re-computing embeddings for the same text, which
can significantly speed up the indexing process.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from loguru import logger

from codebase_rag.ai.embedder import embed_code
from codebase_rag.core.config import settings


class EmbeddingsService:
    """
    A service for generating and caching vector embeddings for text and code.

    This class provides methods to convert text into high-dimensional vectors
    and includes an in-memory LRU (Least Recently Used) cache to optimize performance
    by avoiding redundant computations.
    """

    def __init__(self, max_cache_size: int | None = None) -> None:
        """
        Initializes the EmbeddingsService.

        Args:
            max_cache_size (int | None): The maximum number of embeddings to store
                in the cache. Defaults to the value in `settings.EMBEDDING_CACHE_SIZE`.
        """
        self.max_cache_size = max_cache_size or settings.EMBEDDING_CACHE_SIZE
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._embedded = 0

    @property
    def cache_hit_rate(self) -> float:
        """
        Calculates the cache hit rate.

        Returns:
            The hit rate as a float between 0.0 and 1.0.
        """
        total = self._hits + self._misses
        return self._hits / total if total else 0.0

    @property
    def embedded_count(self) -> int:
        """
        Returns the total number of times a new embedding has been generated.

        Returns:
            The count of embedding generation calls.
        """
        return self._embedded

    def embed_text(self, text: str) -> list[float]:
        """
        Generates an embedding for a given piece of text.

        It first checks the cache for an existing embedding. If not found, it
        generates a new one, stores it in the cache, and returns it.

        Args:
            text (str): The text to embed.

        Returns:
            A list of floats representing the vector embedding.
        """
        cached = self._cache_get(text)
        if cached is not None:
            return cached

        embedding = embed_code(text)
        self._embedded += 1
        self._cache_set(text, embedding)
        return embedding

    def embed_function(self, func: dict) -> dict[str, list[float]]:
        """
        Generates multiple embeddings for different aspects of a function.

        It creates separate embeddings for the function's name, signature, docstring,
        and a combined "semantic" context.

        Args:
            func (dict): A dictionary representing a function, containing keys like
                         "name", "signature", and "docstring".

        Returns:
            A dictionary mapping aspect names (e.g., "name_embedding") to their
            corresponding vector embeddings.
        """
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
        """
        Reads a file and generates an embedding for its entire content.

        Args:
            file_path (str | Path): The path to the file.

        Returns:
            A list of floats representing the vector embedding of the file content.
        """
        path = Path(file_path)
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            logger.warning("Embedding source missing: {}", path)
            content = ""
        return self.embed_text(content)

    def batch_embed(self, texts: list[str]) -> list[list[float]]:
        """
        Generates embeddings for a list of texts in a batch.

        Args:
            texts (list[str]): A list of text strings to embed.

        Returns:
            A list of vector embeddings, one for each input text.
        """
        return [self.embed_text(text) for text in texts]

    def _build_semantic_context(self, func: dict) -> str:
        """
        Builds a combined string of a function's key semantic parts for embedding.

        Args:
            func (dict): A dictionary representing a function.

        Returns:
            A single string combining the function's qualified name, signature, and docstring.
        """
        parts = [
            str(func.get("qualified_name") or ""),
            str(func.get("signature") or func.get("signature_lite") or ""),
            str(func.get("docstring") or ""),
        ]
        return "\n".join(part for part in parts if part)

    def _cache_get(self, text: str) -> list[float] | None:
        """
        Retrieves an embedding from the cache if it exists.

        This is an internal method that also handles cache statistics and LRU logic.

        Args:
            text (str): The text to look up.

        Returns:
            The cached embedding as a list of floats, or None if not found.
        """
        if text in self._cache:
            self._hits += 1
            self._cache.move_to_end(text)
            return self._cache[text]
        self._misses += 1
        return None

    def _cache_set(self, text: str, embedding: list[float]) -> None:
        """
        Adds a new embedding to the cache and maintains the cache size.

        Args:
            text (str): The text key.
            embedding (list[float]): The vector embedding to store.
        """
        self._cache[text] = embedding
        self._cache.move_to_end(text)
        if len(self._cache) > self.max_cache_size:
            self._cache.popitem(last=False)
