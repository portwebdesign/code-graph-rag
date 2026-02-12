import time
from typing import cast

import pytest
from tree_sitter import Query

from codebase_rag.parsers.core.query_cache import (
    CacheStats,
    CompositeQueryCache,
    QueryCache,
    make_cache_key,
)


class MockQuery:
    """Mock Query object for testing."""

    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other):
        return isinstance(other, MockQuery) and self.name == other.name

    def __hash__(self) -> int:
        return hash(self.name)


class TestQueryCache:
    """Test QueryCache functionality."""

    @pytest.fixture
    def cache(self):
        """Create cache instance."""
        return QueryCache(max_size=100)

    def test_cache_initialization(self, cache):
        """Test cache initialization."""
        assert cache.max_size == 100
        assert cache.size() == 0
        assert cache.stats.hits == 0
        assert cache.stats.misses == 0

    def test_put_and_get(self, cache):
        """Test put and get operations."""
        query = MockQuery("test")

        cache.put("key1", cast(Query, query))
        assert cache.size() == 1

        retrieved = cache.get("key1")
        assert retrieved == query

    def test_cache_hits(self, cache):
        """Test cache hit counting."""
        query = MockQuery("test")
        cache.put("key1", cast(Query, query))

        stats_before = cache.stats.hits

        cache.get("key1")
        cache.get("key1")

        stats_after = cache.stats.hits
        assert stats_after == stats_before + 2

    def test_cache_misses(self, cache):
        """Test cache miss counting."""
        stats_before = cache.stats.misses

        cache.get("nonexistent")
        cache.get("also_nonexistent")

        stats_after = cache.stats.misses
        assert stats_after == stats_before + 2

    def test_lru_eviction(self):
        """Test LRU eviction when max_size reached."""
        cache = QueryCache(max_size=3)

        cache.put("key1", cast(Query, MockQuery("q1")))
        cache.put("key2", cast(Query, MockQuery("q2")))
        cache.put("key3", cast(Query, MockQuery("q3")))

        assert cache.size() == 3

        cache.put("key4", cast(Query, MockQuery("q4")))

        assert cache.size() == 3
        assert cache.get("key1") is None
        assert cache.get("key4") is not None

    def test_lru_move_to_end(self, cache):
        """Test that accessed items are moved to end (most recently used)."""
        cache.put("key1", cast(Query, MockQuery("q1")))
        cache.put("key2", cast(Query, MockQuery("q2")))
        cache.put("key3", cast(Query, MockQuery("q3")))

        cache.get("key1")

        cache2 = QueryCache(max_size=3)
        cache2.put("key1", cast(Query, MockQuery("q1")))
        cache2.put("key2", cast(Query, MockQuery("q2")))
        cache2.put("key3", cast(Query, MockQuery("q3")))
        cache2.get("key1")

        cache2.put("key4", cast(Query, MockQuery("q4")))

        assert cache2.get("key1") is not None
        assert cache2.get("key2") is None

    def test_update_existing_key(self, cache):
        """Test updating existing key."""
        q1 = MockQuery("query1")
        q2 = MockQuery("query2")

        cache.put("key", cast(Query, q1))
        assert cache.get("key") == q1

        cache.put("key", cast(Query, q2))
        assert cache.get("key") == q2

    def test_hit_rate(self, cache):
        """Test hit rate calculation."""
        query = MockQuery("test")
        cache.put("key1", cast(Query, query))

        cache.get("key1")
        cache.get("key1")
        cache.get("nonexistent")

        stats = cache.stats
        assert stats.hits == 2
        assert stats.misses == 1
        assert stats.hit_rate == 2 / 3

    def test_contains(self, cache):
        """Test contains check."""
        cache.put("key1", cast(Query, MockQuery("test")))

        assert cache.contains("key1") is True
        assert cache.contains("key2") is False

    def test_clear(self, cache):
        """Test cache clearing."""
        cache.put("key1", cast(Query, MockQuery("test")))
        cache.put("key2", cast(Query, MockQuery("test")))

        assert cache.size() == 2

        cache.clear()

        assert cache.size() == 0
        assert cache.get("key1") is None

    def test_eviction_rate(self):
        """Test eviction rate calculation."""
        cache = QueryCache(max_size=2)

        cache.put("key1", cast(Query, MockQuery("q1")))
        cache.put("key2", cast(Query, MockQuery("q2")))

        assert cache.eviction_rate() == 0.0

        cache.put("key3", cast(Query, MockQuery("q3")))
        cache.put("key4", cast(Query, MockQuery("q4")))

        stats = cache.stats
        assert stats.evictions == 2

    def test_stats_object(self):
        """Test CacheStats object."""
        stats = CacheStats(hits=10, misses=2)

        assert stats.hits == 10
        assert stats.misses == 2
        assert stats.hit_rate == 10 / 12

        stats_str = str(stats)
        assert "hits" in stats_str
        assert "hit_rate" in stats_str


class TestCompositeQueryCache:
    """Test CompositeQueryCache functionality."""

    @pytest.fixture
    def composite_cache(self):
        """Create composite cache."""
        return CompositeQueryCache(l1_size=100)

    def test_composite_cache_initialization(self, composite_cache):
        """Test initialization."""
        assert composite_cache.l1_cache is not None
        assert composite_cache.l1_cache.max_size == 100

    def test_composite_put_and_get(self, composite_cache):
        """Test put and get."""
        query = MockQuery("test")

        composite_cache.put("key1", cast(Query, query))
        retrieved = composite_cache.get("key1")

        assert retrieved == query

    def test_composite_clear(self, composite_cache):
        """Test clearing composite cache."""
        composite_cache.put("key1", cast(Query, MockQuery("test")))

        assert composite_cache.l1_cache.size() > 0

        composite_cache.clear()

        assert composite_cache.l1_cache.size() == 0

    def test_composite_stats(self, composite_cache):
        """Test stats retrieval."""
        composite_cache.put("key1", cast(Query, MockQuery("test")))
        composite_cache.get("key1")

        stats = composite_cache.stats()

        assert "l1" in stats
        assert "hits" in stats["l1"]
        assert "misses" in stats["l1"]


class TestCacheKey:
    """Test cache key generation."""

    def test_make_cache_key_simple(self):
        """Test simple cache key."""
        key = make_cache_key("python", "function_definitions")

        assert key == "python:function_definitions"

    def test_make_cache_key_with_hash(self):
        """Test cache key with hash."""
        key = make_cache_key("python", "function_definitions", query_hash=12345)

        assert key == "python:function_definitions:12345"


class TestQueryCachePerformance:
    """Test cache performance characteristics."""

    def test_cache_access_time(self):
        """Test that cached access is fast."""
        cache = QueryCache(max_size=1000)
        query = MockQuery("test")

        cache.put("key1", cast(Query, query))

        start = time.perf_counter_ns()
        for _ in range(10000):
            cache.get("key1")
        elapsed_ns = time.perf_counter_ns() - start

        avg_time_ns = elapsed_ns / 10000

        assert avg_time_ns < 1000  # nanoseconds

    def test_cache_stats_tracking(self):
        """Test statistics are properly tracked."""
        cache = QueryCache(max_size=100)
        query = MockQuery("test")

        cache.put("key1", cast(Query, query))

        for i in range(100):
            if i % 3 == 0:
                cache.get("nonexistent")
            else:
                cache.get("key1")

        stats = cache.stats

        assert stats.hits >= 60
        assert stats.misses >= 30
        assert stats.hit_rate > 0.6


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
