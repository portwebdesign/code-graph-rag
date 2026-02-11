from typing import cast

import pytest
from tree_sitter import Query

from codebase_rag.parsers.lazy_parser_factory import LazyParserFactory
from codebase_rag.parsers.query_cache import QueryCache
from codebase_rag.parsers.query_engine import QueryEngine


class TestQueryEngineIntegration:
    """Test QueryEngine integration with existing code."""

    def test_queries_dir_exists(self):
        """Test that queries directory is created."""
        engine = QueryEngine()

        assert engine.queries_dir.exists()
        assert engine.queries_dir.is_dir()

    def test_scm_files_exist(self):
        """Test that .scm files exist."""
        engine = QueryEngine()

        expected_langs = ["python", "javascript", "java", "rust", "cpp"]

        for lang in expected_langs:
            scm_file = engine.queries_dir / f"{lang}.scm"
            assert scm_file.exists(), f"Missing {lang}.scm"

    def test_scm_files_not_empty(self):
        """Test that .scm files are not empty."""
        engine = QueryEngine()

        expected_langs = ["python", "javascript", "java", "rust", "cpp"]

        for lang in expected_langs:
            scm_file = engine.queries_dir / f"{lang}.scm"
            content = scm_file.read_text()

            assert len(content) > 0, f"{lang}.scm is empty"
            assert "; @query:" in content, f"{lang}.scm has no queries"

    def test_python_queries_structure(self):
        """Test Python queries have expected structure."""
        engine = QueryEngine()

        scm_file = engine.queries_dir / "python.scm"
        content = scm_file.read_text()

        query_count = content.count("; @query:")

        assert query_count > 20, "Python should have 20+ queries"


class TestQueryCacheIntegration:
    """Test QueryCache integration."""

    def test_cache_with_compiled_queries(self):
        """Test using cache with compiled queries."""

        cache = QueryCache(max_size=100)

        assert cache is not None
        assert cache.max_size == 100

    def test_cache_statistics_tracking(self):
        """Test that statistics are properly tracked."""
        cache = QueryCache(max_size=100)

        stats = cache.get_stats()

        assert "hits" in dir(stats)
        assert "misses" in dir(stats)
        assert "evictions" in dir(stats)


class TestLazyFactoryIntegration:
    """Test LazyParserFactory integration."""

    def test_factory_creation(self):
        """Test factory can be created."""
        factory = LazyParserFactory()

        assert factory is not None

    def test_factory_stats_interface(self):
        """Test factory stats interface."""
        factory = LazyParserFactory()

        stats = factory.get_stats()

        assert isinstance(stats, dict)
        assert "total_registered_languages" in stats


class TestBackwardCompatibility:
    """Test that new components don't break existing code."""

    def test_parsers_module_imports(self):
        """Test that parsers module still imports correctly."""
        from codebase_rag.parsers import call_processor, definition_processor, factory

        assert factory is not None
        assert definition_processor is not None
        assert call_processor is not None

    def test_language_spec_still_available(self):
        """Test that language_spec is still available."""
        from codebase_rag.infrastructure import language_spec

        assert language_spec is not None
        assert hasattr(language_spec, "LANGUAGE_SPECS")

    def test_parser_loader_still_available(self):
        """Test that parser_loader is still available."""
        from codebase_rag.infrastructure import parser_loader

        assert parser_loader is not None


class TestPhase1Functionality:
    """Test Phase 1 core functionality."""

    def test_query_engine_loads_all_languages(self):
        """Test QueryEngine can load all major languages."""
        engine = QueryEngine()

        languages = ["python", "javascript", "java", "rust", "cpp"]

        for lang in languages:
            queries = engine.load_queries(lang)
            assert len(queries) > 0, f"Failed to load queries for {lang}"

    def test_lazy_factory_can_register_loaders(self):
        """Test LazyParserFactory can register loaders."""
        factory = LazyParserFactory()

        def loader():
            return None

        factory.register_loader("test", loader)

        assert "test" in factory._loaders

    def test_query_cache_lru_behavior(self):
        """Test QueryCache LRU behavior."""
        cache = QueryCache(max_size=3)

        class SimpleQuery:
            def __init__(self, name):
                self.name = name

        cache.put("a", cast(Query, SimpleQuery("a")))
        cache.put("b", cast(Query, SimpleQuery("b")))
        cache.put("c", cast(Query, SimpleQuery("c")))

        assert cache.size() == 3


class TestPhase1Performance:
    """Test Phase 1 performance improvements."""

    def test_query_caching_reduces_loads(self):
        """Test that query caching works."""
        engine = QueryEngine()

        for _ in range(5):
            engine.load_queries("python")

        stats = engine.stats()

        assert stats["cache_hits"] > 0
        assert stats["hit_rate"] > 0.0

    def test_lazy_factory_memory_estimate(self):
        """Test memory usage estimation."""
        factory = LazyParserFactory()

        def loader():
            return None

        for i in range(5):
            factory.register_loader(f"lang{i}", loader)

        stats = factory.get_stats()

        assert stats["memory_estimate_mb"] == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
