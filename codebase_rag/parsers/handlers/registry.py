"""
This module manages a registry of language-specific handlers.

It provides a centralized way to obtain the correct handler instance for a given
programming language. A factory function, `get_handler`, looks up the appropriate
handler class from a registry dictionary and returns an instantiated, cached
version of it. If no specific handler is found for a language, it returns a
default `BaseLanguageHandler` instance.

The use of `lru_cache` ensures that handler instances are singletons, improving
performance by avoiding repeated instantiation.
"""

from __future__ import annotations

from functools import lru_cache

from codebase_rag.core.constants import SupportedLanguage

from .base import BaseLanguageHandler
from .cpp import CppHandler
from .java import JavaHandler
from .js_ts import JsTsHandler
from .lua import LuaHandler
from .protocol import LanguageHandler
from .python import PythonHandler
from .rust import RustHandler

_HANDLERS: dict[SupportedLanguage, type[BaseLanguageHandler]] = {
    SupportedLanguage.PYTHON: PythonHandler,
    SupportedLanguage.JS: JsTsHandler,
    SupportedLanguage.TS: JsTsHandler,
    SupportedLanguage.CPP: CppHandler,
    SupportedLanguage.RUST: RustHandler,
    SupportedLanguage.JAVA: JavaHandler,
    SupportedLanguage.LUA: LuaHandler,
}
"""A dictionary mapping languages to their specific handler classes."""

_DEFAULT_HANDLER = BaseLanguageHandler
"""The default handler to use if no specific handler is registered for a language."""


@lru_cache(maxsize=16)
def get_handler(language: SupportedLanguage) -> LanguageHandler:
    """
    Retrieves a cached instance of the language handler for a given language.

    Args:
        language (SupportedLanguage): The language for which to get the handler.

    Returns:
        LanguageHandler: An instance of the appropriate language handler.
    """
    handler_class = _HANDLERS.get(language, _DEFAULT_HANDLER)
    return handler_class()
