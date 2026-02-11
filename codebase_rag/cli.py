from __future__ import annotations

from codebase_rag.core import cli as _core_cli

app = _core_cli.app
__all__ = [name for name in dir(_core_cli) if not name.startswith("_")]
globals().update({name: getattr(_core_cli, name) for name in __all__})

if __name__ == "__main__":
    app()
