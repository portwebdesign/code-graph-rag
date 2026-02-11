import pytest

from codebase_rag.parsers.lazy_parser_factory import (
    LazyParserFactory,
    ParserConfig,
    get_lazy_parser_factory,
    reset_lazy_parser_factory,
)


class MockLanguage:
    """Mock Language for testing."""

    def __init__(self, name: str):
        self.name = name


def make_loader(name: str):
    def _loader():
        return MockLanguage(name)

    return _loader


class TestLazyParserFactory:
    """Test LazyParserFactory functionality."""

    @pytest.fixture
    def factory(self):
        """Create factory instance."""
        return LazyParserFactory()

    def test_factory_initialization(self, factory):
        """Test factory initialization."""
        assert factory is not None
        assert len(factory._loaders) == 0
        assert len(factory._parsers) == 0

    def test_register_loader(self, factory):
        """Test registering a loader."""
        loader = make_loader("python")

        factory.register_loader("python", loader)

        assert "python" in factory._loaders
        assert factory._loaders["python"] == loader

    def test_loader_count(self, factory):
        """Test tracking of registered loaders."""
        loader = make_loader("test")

        factory.register_loader("lang1", loader)
        factory.register_loader("lang2", loader)
        factory.register_loader("lang3", loader)

        stats = factory.get_stats()
        assert stats["total_registered_languages"] == 3

    def test_is_loaded(self, factory):
        """Test is_loaded check."""
        loader = make_loader("python")
        factory.register_loader("python", loader)

        assert not factory.is_loaded("python")

    def test_is_loading(self, factory):
        """Test is_loading check."""
        loader = make_loader("python")
        factory.register_loader("python", loader)

        assert not factory.is_loading("python")

    def test_unload(self, factory):
        """Test unloading a parser."""
        loader = make_loader("python")
        factory.register_loader("python", loader)

        factory.unload("python")

        assert factory._parsers["python"] is None

    def test_clear_cache(self, factory):
        """Test clearing cache."""
        loader = make_loader("python")
        factory.register_loader("python", loader)
        factory.register_loader("java", loader)

        factory.clear_cache()

        assert factory._parsers["python"] is None
        assert factory._parsers["java"] is None

    def test_get_stats(self, factory):
        """Test statistics retrieval."""
        loader = make_loader("python")

        factory.register_loader("python", loader)
        factory.register_loader("java", loader)

        stats = factory.get_stats()

        assert "total_registered_languages" in stats
        assert "loaded_languages" in stats
        assert "total_load_attempts" in stats
        assert "successful_loads" in stats
        assert "failed_loads" in stats
        assert "memory_estimate_mb" in stats

    def test_memory_estimate(self, factory):
        """Test memory usage estimation."""
        loader = make_loader("test")

        factory.register_loader("lang1", loader)
        factory.register_loader("lang2", loader)

        stats = factory.get_stats()

        assert stats["memory_estimate_mb"] == 0.0

    def test_get_load_error(self, factory):
        """Test getting load error."""
        loader = make_loader("python")
        factory.register_loader("python", loader)

        error = factory.get_load_error("python")

        assert error is None

    def test_repr(self, factory):
        """Test string representation."""
        loader = make_loader("python")
        factory.register_loader("python", loader)

        repr_str = repr(factory)

        assert "LazyParserFactory" in repr_str
        assert "loaded" in repr_str

    def test_unregister_nonexistent(self, factory):
        """Test getting unregistered language."""
        parser = factory.get_parser("nonexistent")

        assert parser is None

    def test_load_error_tracking(self, factory):
        """Test error tracking."""

        def failing_loader():
            raise ValueError("Test error")

        factory.register_loader("failing", failing_loader)

        with pytest.raises(ValueError):
            factory.get_parser("failing")

        error = factory.get_load_error("failing")
        assert isinstance(error, ValueError)


class TestLazyParserFactoryPerformance:
    """Test performance characteristics."""

    def test_lazy_initialization(self):
        """Test that parsers are not loaded until needed."""
        factory = LazyParserFactory()
        loader_call_count = {"count": 0}

        def counting_loader():
            loader_call_count["count"] += 1
            return MockLanguage("test")

        factory.register_loader("test", counting_loader)

        assert loader_call_count["count"] == 0

    def test_stats_reset(self):
        """Test factory statistics."""
        factory = LazyParserFactory()
        loader = make_loader("python")

        factory.register_loader("python", loader)

        stats1 = factory.get_stats()
        assert stats1["total_registered_languages"] == 1

        factory.register_loader("java", loader)

        stats2 = factory.get_stats()
        assert stats2["total_registered_languages"] == 2


class TestGlobalInstance:
    """Test global factory instance."""

    def test_get_global_instance(self):
        """Test getting global instance."""
        reset_lazy_parser_factory()

        factory1 = get_lazy_parser_factory()
        factory2 = get_lazy_parser_factory()

        assert factory1 is factory2

    def test_reset_global_instance(self):
        """Test resetting global instance."""
        factory1 = get_lazy_parser_factory()
        reset_lazy_parser_factory()
        factory2 = get_lazy_parser_factory()

        assert factory1 is not factory2


class TestParserConfig:
    """Test ParserConfig dataclass."""

    def test_parser_config_creation(self):
        """Test creating parser config."""
        loader = make_loader("python")
        config = ParserConfig(language="python", loader_fn=loader, eager_load=False)

        assert config.language == "python"
        assert config.loader_fn == loader
        assert config.eager_load is False

    def test_parser_config_eager_load(self):
        """Test eager_load flag."""
        loader = make_loader("python")
        config = ParserConfig(language="python", loader_fn=loader, eager_load=True)

        assert config.eager_load is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
