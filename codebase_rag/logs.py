from __future__ import annotations

from typing import Any

from .core import logs as _core_logs

__all__ = [name for name in dir(_core_logs) if name.isupper()]
globals().update({name: getattr(_core_logs, name) for name in __all__})


def __getattr__(name: str) -> Any:
    return getattr(_core_logs, name)


def __dir__() -> list[str]:
    return sorted(set(__all__))
