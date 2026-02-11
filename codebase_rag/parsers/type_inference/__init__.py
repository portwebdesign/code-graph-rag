from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from .base import BaseTypeInferenceEngine
from .context import (
    FunctionSignature,
    InferenceContext,
    TypeInferenceResult,
    TypeRegistry,
    TypeSource,
    VariableInfo,
)
from .js_ts_engine import (
    JSDocStrategy,
    JSTypeScriptInferenceEngine,
    TypeScriptAnnotationStrategy,
)
from .python_engine import PythonTypeInferenceEngine

_legacy_engine = None
_legacy_path = Path(__file__).resolve().parent.parent / "type_inference.py"
if _legacy_path.exists():
    spec = spec_from_file_location(
        "codebase_rag.parsers._legacy_type_inference", _legacy_path
    )
    if spec and spec.loader:
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        _legacy_engine = getattr(module, "TypeInferenceEngine", None)

TypeInferenceEngine = _legacy_engine or BaseTypeInferenceEngine

__all__ = [
    "TypeInferenceResult",
    "TypeSource",
    "InferenceContext",
    "VariableInfo",
    "FunctionSignature",
    "TypeRegistry",
    "BaseTypeInferenceEngine",
    "TypeInferenceEngine",
    "PythonTypeInferenceEngine",
    "JSTypeScriptInferenceEngine",
    "JSDocStrategy",
    "TypeScriptAnnotationStrategy",
]

__version__ = "1.0.0"
__author__ = "Code Graph RAG Team"
