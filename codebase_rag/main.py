from __future__ import annotations

from codebase_rag.core import main as _core_main

__all__ = [name for name in dir(_core_main) if not name.startswith("_")]
globals().update({name: getattr(_core_main, name) for name in __all__})

_create_model_from_string = _core_main._create_model_from_string
_handle_model_command = _core_main._handle_model_command
