from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from types import SimpleNamespace
from typing import Any, cast

from codebase_rag.data_models.models import ToolMetadata

ToolFactory = Callable[
    [Any], ToolMetadata | dict[str, ToolMetadata] | Iterable[ToolMetadata]
]


class ToolRegistry:
    _factories: dict[str, ToolFactory] = {}
    _categories: dict[str, set[str]] = defaultdict(set)

    @classmethod
    def register(
        cls, name: str, category: str | None = None
    ) -> Callable[[ToolFactory], ToolFactory]:
        def decorator(factory: ToolFactory) -> ToolFactory:
            cls._factories[name] = factory
            if category:
                cls._categories[category].add(name)
            return factory

        return decorator

    @classmethod
    def build(cls, registry: Any) -> dict[str, ToolMetadata]:
        registry = cls._ensure_registry(registry)
        tools: dict[str, ToolMetadata] = {}
        for factory in cls._factories.values():
            result = factory(registry)
            if isinstance(result, ToolMetadata):
                tools[result.name] = result
                continue
            if isinstance(result, dict):
                tools.update(cast("dict[str, ToolMetadata]", result))
                continue
            for item in result:
                tools[item.name] = item
        return tools

    @staticmethod
    def _ensure_registry(registry: Any) -> Any:
        if registry is None or not hasattr(registry, "__dict__"):
            registry = SimpleNamespace()
        missing_methods = (
            "list_projects",
            "delete_project",
            "wipe_database",
            "show_storage_info",
            "show_config",
        )
        for method_name in missing_methods:
            if not hasattr(registry, method_name):
                setattr(registry, method_name, lambda *args, **kwargs: None)
        return registry

    @classmethod
    def list_categories(cls) -> dict[str, set[str]]:
        return {category: set(names) for category, names in cls._categories.items()}
