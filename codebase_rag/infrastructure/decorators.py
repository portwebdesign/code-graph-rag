"""
This module provides a collection of decorators used throughout the application
to add common functionalities like timing, error handling, path validation, and
recursion prevention.

These decorators help to keep the core logic of functions clean and separate
from cross-cutting concerns.

Decorators:
-   `ensure_loaded`: Ensures a resource is loaded before a method is called.
-   `timing_decorator`: Logs the execution time of a synchronous function.
-   `async_timing_decorator`: Logs the execution time of an asynchronous function.
-   `validate_project_path`: Validates that a file path argument is within the
    project's root directory.
-   `recursion_guard`: Prevents a function from being called recursively with the
    same key.
-   `log_operation`: Logs a message before and after a function is executed.
-   `mcp_try_except`: A try-except block for MCP (Multi-turn Conversation Protocol)
    tool handlers to catch and format errors.
"""

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from functools import wraps

from loguru import logger

from codebase_rag.core import logs as ls
from codebase_rag.data_models.types_defs import (
    LoadableProtocol,
    PathValidatorProtocol,
)
from codebase_rag.infrastructure import exceptions as ex


def ensure_loaded[T](func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator to ensure that a resource is loaded before a method is called.

    It expects the class instance (`self`) to have a `_ensure_loaded` method.

    Args:
        func: The function to wrap.

    Returns:
        The wrapped function.
    """

    @wraps(func)
    def wrapper(self: LoadableProtocol, *args, **kwargs) -> T:
        self._ensure_loaded()
        return func(self, *args, **kwargs)

    return wrapper


def timing_decorator[**P, T](func: Callable[P, T]) -> Callable[P, T]:
    """
    Decorator that logs the execution time of a synchronous function.

    Args:
        func: The function to wrap.

    Returns:
        The wrapped function.
    """

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        start = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            logger.info(ls.FUNC_TIMING.format(func=func.__qualname__, time=elapsed))

    return wrapper


def async_timing_decorator[**P, T](
    func: Callable[P, Awaitable[T]],
) -> Callable[P, Awaitable[T]]:
    """
    Decorator that logs the execution time of an asynchronous function.

    Args:
        func: The async function to wrap.

    Returns:
        The wrapped async function.
    """

    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        start = time.perf_counter()
        try:
            return await func(*args, **kwargs)
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            logger.info(ls.FUNC_TIMING.format(func=func.__qualname__, time=elapsed))

    return wrapper


def validate_project_path[T](
    result_factory: type[T],
    path_arg_name: str,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """
    Decorator factory to validate a file path argument against the project root.

    This prevents path traversal attacks by ensuring the resolved path is within
    the project directory.

    Args:
        result_factory: A class or function to call to create an error response
                        if validation fails.
        path_arg_name: The name of the argument in the decorated function that
                       holds the file path.

    Returns:
        A decorator function.
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        sig = inspect.signature(func)

        @wraps(func)
        async def wrapper(self: PathValidatorProtocol, *args, **kwargs) -> T:
            bound = sig.bind(self, *args, **kwargs)
            bound.apply_defaults()
            file_path_str = bound.arguments.get(path_arg_name)

            if not isinstance(file_path_str, str):
                return result_factory(
                    file_path=str(file_path_str), error_message=ex.ACCESS_DENIED
                )
            try:
                full_path = (self.project_root / file_path_str).resolve()
                project_root = self.project_root.resolve()
                full_path.relative_to(project_root)
            except (ValueError, RuntimeError):
                return result_factory(
                    file_path=file_path_str,
                    error_message=ls.FILE_OUTSIDE_ROOT.format(action="access"),
                )

            bound.arguments[path_arg_name] = full_path
            return await func(*bound.args, **bound.kwargs)

        return wrapper

    return decorator


_GUARD_REGISTRY: dict[str, ContextVar[set[str] | None]] = {}


def recursion_guard[**P, T](
    key_func: Callable[..., str],
    guard_name: str | None = None,
) -> Callable[[Callable[P, T | None]], Callable[P, T | None]]:
    """
    Decorator factory to prevent recursion for a function based on a generated key.

    It uses a `ContextVar` to track keys within the current execution context,
    making it safe for concurrent execution.

    Args:
        key_func: A function that takes the same arguments as the decorated
                  function and returns a unique string key for the call.
        guard_name: An optional name to share the guard context between different
                    functions.

    Returns:
        A decorator function.
    """
    if guard_name:
        context_var = _GUARD_REGISTRY.get(guard_name)
        if context_var is None:
            new_var = ContextVar[set[str] | None](guard_name, default=None)
            context_var = _GUARD_REGISTRY.setdefault(guard_name, new_var)
    else:
        name = getattr(key_func, "__name__", "guard")
        context_var = ContextVar[set[str] | None](name, default=None)

    def decorator(func: Callable[P, T | None]) -> Callable[P, T | None]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T | None:
            guard_set = context_var.get()
            if guard_set is None:
                guard_set = set()
                context_var.set(guard_set)

            key = key_func(*args, **kwargs)
            if key in guard_set:
                return None
            guard_set.add(key)
            try:
                return func(*args, **kwargs)
            finally:
                guard_set.discard(key)

        return wrapper

    return decorator


def log_operation[T](
    start_msg: str,
    end_msg: str,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator factory that logs a message before and after a function executes.

    Args:
        start_msg: The message to log before execution.
        end_msg: The message to log after execution.

    Returns:
        A decorator function.
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            logger.info(start_msg)
            result = func(*args, **kwargs)
            logger.info(end_msg)
            return result

        return wrapper

    return decorator


def mcp_try_except[T](
    error_factory: Callable[[str], T],
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """
    Decorator factory that wraps an async function in a try...except block.

    This is specifically for MCP (Multi-turn Conversation Protocol) tool handlers
    to catch any exceptions and return a formatted error object.

    Args:
        error_factory: A function or class that takes an error message string
                       and returns an error response object of type T.

    Returns:
        A decorator function.
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            try:
                return await func(*args, **kwargs)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                return error_factory(str(e))

        return wrapper

    return decorator
