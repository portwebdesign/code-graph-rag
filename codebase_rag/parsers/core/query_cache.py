from __future__ import annotations

import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tree_sitter import Query


@dataclass
class CacheStats:
    """Cache statistics."""

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    total_time_ns: int = 0  # nanoseconds

    @property
    def hit_rate(self) -> float:
        """Calculate hit rate."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    @property
    def avg_time_ns(self) -> float:
        """Average access time."""
        total = self.hits + self.misses
        return self.total_time_ns / total if total > 0 else 0.0

    def __repr__(self) -> str:
        return (
            f"CacheStats(hits={self.hits}, misses={self.misses}, "
            f"evictions={self.evictions}, hit_rate={self.hit_rate:.2%}, "
            f"avg_time={self.avg_time_ns / 1e6:.3f}ms)"
        )


class QueryCache:
    """
    LRU cache for compiled tree-sitter queries.

    Features:
    - Automatic eviction when max_size reached
    - Hit/miss statistics
    - Performance metrics
    - Optional TTL (time-to-live)
    """

    def __init__(self, max_size: int = 1000):
        """
        Initialize QueryCache.

        Args:
            max_size: Maximum number of cached queries
        """
        self.max_size = max_size

        self._cache: OrderedDict[str, Query] = OrderedDict()

        self.stats = CacheStats()

        logging.getLogger(__name__).debug(
            f"QueryCache initialized with max_size={max_size}"
        )

    def get(self, key: str) -> Query | None:
        """
        Get query from cache.

        Args:
            key: Cache key (format: "language:query_name:hash")

        Returns:
            Cached Query or None
        """
        start_ns = time.perf_counter_ns()

        if key in self._cache:
            self._cache.move_to_end(key)
            self.stats.hits += 1

            elapsed_ns = time.perf_counter_ns() - start_ns
            self.stats.total_time_ns += elapsed_ns

            return self._cache[key]

        self.stats.misses += 1

        elapsed_ns = time.perf_counter_ns() - start_ns
        self.stats.total_time_ns += elapsed_ns

        return None

    def put(self, key: str, query: Query) -> None:
        """
        Store query in cache.

        Args:
            key: Cache key
            query: Compiled Query object
        """
        if key in self._cache:
            del self._cache[key]
        elif len(self._cache) >= self.max_size:
            oldest_key, _ = self._cache.popitem(last=False)
            self.stats.evictions += 1
            logging.getLogger(__name__).debug(f"Evicted cache entry: {oldest_key}")

        self._cache[key] = query

    def contains(self, key: str) -> bool:
        """Check if key is in cache."""
        return key in self._cache

    def size(self) -> int:
        """Get current cache size."""
        return len(self._cache)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()
        logging.getLogger(__name__).info("Query cache cleared")

    def get_stats(self) -> CacheStats:
        """Get cache statistics."""
        return self.stats

    def reset_stats(self) -> None:
        """Reset statistics counters."""
        self.stats = CacheStats()

    def eviction_rate(self) -> float:
        """Calculate eviction rate."""
        total_puts = self.stats.evictions + self.size()
        return self.stats.evictions / total_puts if total_puts > 0 else 0.0


class CompositeQueryCache:
    """
    Multi-level cache for queries.

    Supports:
    - L1: Fast in-memory cache (compiled queries)
    - L2: Serialized cache (optional file-based)
    """

    def __init__(
        self,
        l1_size: int = 1000,
        stats_callback: Callable[[CacheStats], None] | None = None,
    ):
        """
        Initialize composite cache.

        Args:
            l1_size: L1 cache size
            stats_callback: Optional callback for stats updates
        """
        self.l1_cache = QueryCache(max_size=l1_size)
        self.stats_callback = stats_callback

    def get(self, key: str) -> Query | None:
        """Get from L1 cache."""
        result = self.l1_cache.get(key)

        if self.stats_callback:
            self.stats_callback(self.l1_cache.get_stats())

        return result

    def put(self, key: str, query: Query) -> None:
        """Put to L1 cache."""
        self.l1_cache.put(key, query)

    def clear(self) -> None:
        """Clear all levels."""
        self.l1_cache.clear()

    def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        return {
            "l1": {
                "hits": self.l1_cache.stats.hits,
                "misses": self.l1_cache.stats.misses,
                "hit_rate": self.l1_cache.stats.hit_rate,
                "size": self.l1_cache.size(),
                "max_size": self.l1_cache.max_size,
                "eviction_rate": self.l1_cache.eviction_rate(),
            }
        }


def make_cache_key(language: str, query_name: str, query_hash: int = 0) -> str:
    """
    Create a cache key for a query.

    Args:
        language: Language name
        query_name: Query name
        query_hash: Optional hash for multiple versions

    Returns:
        Cache key string
    """
    if query_hash:
        return f"{language}:{query_name}:{query_hash}"
    return f"{language}:{query_name}"
