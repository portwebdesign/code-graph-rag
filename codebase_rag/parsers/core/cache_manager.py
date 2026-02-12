from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass

from loguru import logger


@dataclass
class CacheStats:
    """
    Statistics for cache usage.

    Attributes:
        hits (int): Number of cache hits.
        misses (int): Number of cache misses.
        evictions (int): Number of evicted entries.
        expirations (int): Number of expired entries.
    """

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0


class CacheManager[T]:
    """
    A generic cache manager with LRU eviction and TTL support.

    Attributes:
        max_entries (int): Maximum number of entries in the cache.
        ttl_seconds (float | None): Time-to-live for cache entries in seconds.
        cleanup_interval_seconds (float): Interval between cleanup of expired entries.
    """

    def __init__(
        self,
        max_entries: int = 512,
        ttl_seconds: float | None = None,
        cleanup_interval_seconds: float = 30.0,
    ) -> None:
        """
        Initialize the CacheManager.

        Args:
            max_entries (int): Maximum number of entries to store. Defaults to 512.
            ttl_seconds (float | None): Time-to-live in seconds. None for no expiration.
            cleanup_interval_seconds (float): Seconds between expiration checks. Defaults to 30.0.
        """
        self.max_entries = max(1, max_entries)
        self.ttl_seconds = ttl_seconds
        self.cleanup_interval_seconds = max(0.0, cleanup_interval_seconds)
        self._entries: OrderedDict[str, tuple[T, float]] = OrderedDict()
        self._stats = CacheStats()
        self._last_cleanup = 0.0

    def get(self, key: str) -> T | None:
        """
        Retrieve a value from the cache.

        Args:
            key (str): The key to retrieve.

        Returns:
            T | None: The cached value if found and valid, otherwise None.
        """
        if key not in self._entries:
            self._stats.misses += 1
            return None

        value, created_at = self._entries[key]
        if self._is_expired(created_at):
            del self._entries[key]
            self._stats.expirations += 1
            self._stats.misses += 1
            return None

        self._entries.move_to_end(key)
        self._stats.hits += 1
        return value

    def set(self, key: str, value: T) -> None:
        """
        Set a value in the cache.

        Args:
            key (str): The key to set.
            value (T): The value to cache.
        """
        if key in self._entries:
            del self._entries[key]
        self._entries[key] = (value, time.time())
        self._entries.move_to_end(key)
        self._evict_if_needed()

    def stats(self) -> CacheStats:
        """
        Get current cache statistics.

        Returns:
            CacheStats: A snapshot of cache statistics.
        """
        return self._stats

    def size(self) -> int:
        """
        Get the current number of entries in the cache.

        Returns:
            int: Number of entries.
        """
        return len(self._entries)

    def keys(self) -> list[str]:
        """
        Get a list of all keys in the cache.

        Returns:
            list[str]: List of cache keys.
        """
        return list(self._entries.keys())

    def clear(self) -> None:
        """
        Clear all entries from the cache.
        """
        self._entries.clear()

    def __contains__(self, key: str) -> bool:
        """
        Check if a key is in the cache (without expiration check or LRU update).

        Args:
            key (str): The key to check.

        Returns:
            bool: True if key exists, False otherwise.
        """
        return key in self._entries

    def __delitem__(self, key: str) -> None:
        """
        Delete a specific item from the cache.

        Args:
            key (str): Key to delete.
        """
        if key in self._entries:
            del self._entries[key]

    def _evict_if_needed(self) -> None:
        """
        Evict least recently used items if cache exceeds max size.
        """
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
            self._stats.evictions += 1
        if self.ttl_seconds is not None:
            now = time.time()
            if (now - self._last_cleanup) >= self.cleanup_interval_seconds:
                self._cleanup_expired()
                self._last_cleanup = now

    def _cleanup_expired(self) -> None:
        """
        Remove all expired entries from the cache.
        """
        if self.ttl_seconds is None:
            return
        now = time.time()
        expired_keys = [
            key
            for key, (_, created_at) in self._entries.items()
            if now - created_at > self.ttl_seconds
        ]
        for key in expired_keys:
            del self._entries[key]
            self._stats.expirations += 1

    def _is_expired(self, created_at: float) -> bool:
        """
        Check if an entry is expired based on its creation time.

        Args:
            created_at (float): Timestamp when the entry was created.

        Returns:
            bool: True if expired, False otherwise.
        """
        if self.ttl_seconds is None:
            return False
        return (time.time() - created_at) > self.ttl_seconds

    def log_stats(self, label: str) -> None:
        """
        Log cache statistics with a given label.

        Args:
            label (str): Label to identify the cache in logs.
        """
        stats = self._stats
        logger.debug(
            "{} cache stats: hits={}, misses={}, evictions={}, expirations={}, size={}",
            label,
            stats.hits,
            stats.misses,
            stats.evictions,
            stats.expirations,
            len(self._entries),
        )
