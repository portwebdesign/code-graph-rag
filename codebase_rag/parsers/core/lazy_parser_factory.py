from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from tree_sitter import Language, Parser


@dataclass
class ParserConfig:
    """Configuration for a language parser."""

    language: str
    loader_fn: Callable[[], Language]
    eager_load: bool = False


class LazyParserFactory:
    """
    Factory for lazy-loading parser instances.

    Benefits:
    - Parsers only loaded when needed
    - Reduced memory footprint
    - Better startup time for large repos
    - Thread-safe loading

    Features:
    - Lazy initialization (on-demand)
    - Eager initialization (pre-load)
    - Thread-safe singleton per language
    - Loading state tracking
    - Statistics tracking
    """

    def __init__(self, num_loader_threads: int = 2):
        """
        Initialize LazyParserFactory.

        Args:
            num_loader_threads: Number of threads for background loading
        """
        self._parsers: dict[str, Parser | None] = {}
        self._languages: dict[str, Language | None] = {}
        self._loaders: dict[str, Callable[[], Language]] = {}
        self._is_loading: dict[str, bool] = {}
        self._load_locks: dict[str, threading.Lock] = {}
        self._load_errors: dict[str, Exception | None] = {}

        self._stats = {
            "total_loads": 0,
            "successful_loads": 0,
            "failed_loads": 0,
            "eager_loads": 0,
        }

        self._executor = ThreadPoolExecutor(max_workers=num_loader_threads)
        self._logger = logging.getLogger(__name__)

        self._logger.debug("LazyParserFactory initialized")

    def register_loader(
        self, language: str, loader_fn: Callable[[], Language], eager_load: bool = False
    ) -> None:
        """
        Register a language loader.

        Args:
            language: Language name
            loader_fn: Function that returns Language object
            eager_load: If True, load immediately
        """
        self._loaders[language] = loader_fn
        self._parsers[language] = None
        self._languages[language] = None
        self._is_loading[language] = False
        self._load_locks[language] = threading.Lock()
        self._load_errors[language] = None

        self._logger.debug(f"Registered loader for {language}")

        if eager_load:
            self._load_parser_in_background(language)

    def get_parser(self, language: str) -> Parser | None:
        """
        Get or load parser for language.

        Args:
            language: Language name

        Returns:
            Parser instance or None if not registered

        Raises:
            RuntimeError: If parser is already loading (circular dependency)
            Exception: If language loader fails
        """
        if language not in self._loaders:
            self._logger.warning(f"No loader registered for {language}")
            return None

        if self._parsers[language] is not None:
            self._logger.debug(f"Returning cached parser for {language}")
            return self._parsers[language]

        error = self._load_errors[language]
        if error is not None:
            raise error

        if self._is_loading[language]:
            raise RuntimeError(
                f"Parser for {language} is already loading (circular dependency?)"
            )

        return self._load_parser_sync(language)

    def get_language(self, language: str) -> Language | None:
        """
        Get or load language object.

        Args:
            language: Language name

        Returns:
            Language object or None
        """
        if language not in self._loaders:
            return None

        if self._languages[language] is not None:
            return self._languages[language]

        _ = self.get_parser(language)
        return self._languages[language]

    def preload_languages(self, languages: list[str]) -> None:
        """
        Pre-load multiple languages in background.

        Args:
            languages: List of language names
        """
        for lang in languages:
            self._load_parser_in_background(lang)

    def _load_parser_sync(self, language: str) -> Parser | None:
        """
        Synchronously load parser.

        Args:
            language: Language name

        Returns:
            Loaded Parser or None
        """
        with self._load_locks[language]:
            if self._parsers[language] is not None:
                return self._parsers[language]

            error = self._load_errors[language]
            if error is not None:
                raise error

            self._is_loading[language] = True
            self._stats["total_loads"] += 1

            try:
                self._logger.debug(f"Loading parser for {language}")

                lang_obj = self._loaders[language]()

                parser = Parser()
                set_language = getattr(parser, "set_language", None)
                if not set_language:
                    raise AttributeError("Parser.set_language is not available")
                set_language(lang_obj)

                self._parsers[language] = parser
                self._languages[language] = lang_obj

                self._stats["successful_loads"] += 1
                self._logger.info(f"Successfully loaded parser for {language}")

                return parser

            except Exception as e:
                self._load_errors[language] = e
                self._stats["failed_loads"] += 1
                self._logger.error(f"Failed to load parser for {language}: {e}")
                raise

            finally:
                self._is_loading[language] = False

    def _load_parser_in_background(self, language: str) -> None:
        """
        Load parser in background thread.

        Args:
            language: Language name
        """
        if language not in self._loaders:
            return

        def load_task():
            try:
                self._load_parser_sync(language)
                self._stats["eager_loads"] += 1
            except Exception as e:
                self._logger.error(f"Background load failed for {language}: {e}")

        self._executor.submit(load_task)

    def is_loaded(self, language: str) -> bool:
        """Check if parser is loaded."""
        return language in self._parsers and self._parsers[language] is not None

    def is_loading(self, language: str) -> bool:
        """Check if parser is currently loading."""
        return self._is_loading.get(language, False)

    def get_load_error(self, language: str) -> Exception | None:
        """Get error if parser failed to load."""
        return self._load_errors.get(language)

    def clear_cache(self) -> None:
        """Clear all cached parsers."""
        for lang in self._parsers:
            self._parsers[lang] = None
            self._languages[lang] = None
            self._is_loading[lang] = False
            self._load_errors[lang] = None

        self._logger.info("Parser cache cleared")

    def unload(self, language: str) -> None:
        """Unload a specific parser."""
        if language in self._parsers:
            self._parsers[language] = None
            self._languages[language] = None
            self._load_errors[language] = None
            self._logger.info(f"Unloaded parser for {language}")

    def get_stats(self) -> dict[str, Any]:
        """Get factory statistics."""
        loaded_count = sum(1 for p in self._parsers.values() if p is not None)

        return {
            "total_registered_languages": len(self._loaders),
            "loaded_languages": loaded_count,
            "total_load_attempts": self._stats["total_loads"],
            "successful_loads": self._stats["successful_loads"],
            "failed_loads": self._stats["failed_loads"],
            "eager_loads": self._stats["eager_loads"],
            "memory_estimate_mb": self._estimate_memory_usage(),
        }

    def _estimate_memory_usage(self) -> float:
        """
        Rough estimate of memory usage.
        Average parser ~5-10 MB.
        """
        loaded_count = sum(1 for p in self._parsers.values() if p is not None)
        return loaded_count * 7.5

    def shutdown(self) -> None:
        """Shutdown factory and cleanup resources."""
        self._executor.shutdown(wait=True)
        self.clear_cache()
        self._logger.info("LazyParserFactory shutdown")

    def __repr__(self) -> str:
        stats = self.get_stats()
        return (
            f"LazyParserFactory(loaded={stats['loaded_languages']}, "
            f"registered={stats['total_registered_languages']}, "
            f"memory={stats['memory_estimate_mb']:.1f}MB)"
        )


_global_factory: LazyParserFactory | None = None


def get_lazy_parser_factory() -> LazyParserFactory:
    """
    Get or create global LazyParserFactory instance.

    Returns:
        LazyParserFactory instance
    """
    global _global_factory

    if _global_factory is None:
        _global_factory = LazyParserFactory()

    return _global_factory


def reset_lazy_parser_factory() -> None:
    """Reset global factory (useful for testing)."""
    global _global_factory

    if _global_factory is not None:
        _global_factory.shutdown()
        _global_factory = None
