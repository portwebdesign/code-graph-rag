import logging
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


class TypeSource(Enum):
    """Source of type information."""

    ANNOTATION = "annotation"
    INFERENCE = "inference"
    REGISTRY = "registry"
    BUILTIN = "builtin"
    EXTERNAL = "external"


@dataclass
class TypeInferenceResult:
    """Result of type inference operation."""

    type_string: str
    confidence: float
    source: TypeSource
    context: dict[str, Any] = field(default_factory=dict)
    language: str = "unknown"
    timestamp: float = field(default_factory=lambda: 0)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "type_string": self.type_string,
            "confidence": self.confidence,
            "source": self.source.value,
            "language": self.language,
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TypeInferenceResult":
        """Create from dictionary."""
        return cls(
            type_string=data.get("type_string", "Any"),
            confidence=float(data.get("confidence", 0.0)),
            source=TypeSource(data.get("source", "unknown")),
            language=data.get("language", "unknown"),
            context=data.get("context", {}),
        )


@dataclass
class VariableInfo:
    """Information about a variable."""

    name: str
    type_result: TypeInferenceResult | None = None
    file_path: str = ""
    line: int = 0
    column: int = 0
    scope: str = "module"
    initial_value: str | None = None
    assignments: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class FunctionSignature:
    """Function signature with type information."""

    name: str
    parameters: dict[str, TypeInferenceResult] = field(default_factory=dict)
    return_type: TypeInferenceResult | None = None
    file_path: str = ""
    line: int = 0
    module: str = ""
    language: str = "unknown"
    is_method: bool = False
    class_name: str | None = None


class InferenceContext:
    """
    Context for type inference operations.

    Tracks:
    - Current scope (file, class, function)
    - Available variables and their types
    - Function signatures
    - Import information
    - Language-specific context
    """

    def __init__(self, language: str = "python", file_path: str = ""):
        """Initialize inference context."""
        self.language = language
        self.file_path = file_path

        self.current_file = file_path
        self.current_class: str | None = None
        self.current_function: str | None = None
        self.scope_stack: list[str] = []

        self.variables: dict[str, VariableInfo] = {}
        self.functions: dict[str, FunctionSignature] = {}
        self.classes: dict[str, dict[str, Any]] = {}

        self.imports: dict[str, str] = {}
        self.from_imports: dict[str, list[str]] = {}

        self.type_hints: dict[str, TypeInferenceResult] = {}

        self.language_context: dict[str, Any] = {}

        self._lock = Lock()
        self._inference_cache: dict[str, TypeInferenceResult] = {}

        logger.debug(f"InferenceContext created for {language}:{file_path}")

    def enter_scope(self, scope_type: str, name: str = "") -> None:
        """Enter a new scope (class, function, block)."""
        with self._lock:
            scope_id = f"{scope_type}:{name}" if name else scope_type
            self.scope_stack.append(scope_id)

            if scope_type == "class":
                self.current_class = name
            elif scope_type == "function":
                self.current_function = name

            logger.debug(f"Entered scope: {scope_id}")

    def exit_scope(self) -> None:
        """Exit current scope."""
        with self._lock:
            if self.scope_stack:
                self.scope_stack.pop()

            if not self.scope_stack or not any(
                s.startswith("class:") for s in self.scope_stack
            ):
                self.current_class = None
            if not self.scope_stack or not any(
                s.startswith("function:") for s in self.scope_stack
            ):
                self.current_function = None

            logger.debug("Exited scope")

    def add_variable(self, var_info: VariableInfo) -> None:
        """Register a variable in current scope."""
        with self._lock:
            scope_key = self._make_scope_key(var_info.name)
            self.variables[scope_key] = var_info

            if var_info.type_result:
                logger.debug(
                    f"Variable {var_info.name}: {var_info.type_result.type_string} "
                    f"(confidence: {var_info.type_result.confidence})"
                )

    def get_variable(self, name: str) -> VariableInfo | None:
        """Get variable info from current scope."""
        with self._lock:
            scope_key = self._make_scope_key(name)
            return self.variables.get(scope_key)

    def add_function(self, sig: FunctionSignature) -> None:
        """Register function signature."""
        with self._lock:
            func_key = self._make_function_key(sig.name, sig.class_name)
            self.functions[func_key] = sig

            logger.debug(f"Function {func_key} registered")

    def get_function(
        self, name: str, class_name: str | None = None
    ) -> FunctionSignature | None:
        """Get function signature."""
        with self._lock:
            func_key = self._make_function_key(name, class_name)
            return self.functions.get(func_key)

    def add_import(self, alias: str, module: str) -> None:
        """Register import statement (import x as y)."""
        with self._lock:
            self.imports[alias] = module
            logger.debug(f"Import registered: {alias} -> {module}")

    def add_from_import(self, module: str, names: list[str]) -> None:
        """Register from import statement (from x import y, z)."""
        with self._lock:
            if module not in self.from_imports:
                self.from_imports[module] = []
            self.from_imports[module].extend(names)
            logger.debug(f"From import: {module} -> {names}")

    def resolve_import(self, name: str) -> str | None:
        """Resolve imported name to full module path."""
        with self._lock:
            if name in self.imports:
                return self.imports[name]

            for module, names in self.from_imports.items():
                if name in names:
                    return f"{module}.{name}"

            return None

    def cache_inference(self, key: str, result: TypeInferenceResult) -> None:
        """Cache type inference result."""
        with self._lock:
            self._inference_cache[key] = result

    def get_cached_inference(self, key: str) -> TypeInferenceResult | None:
        """Get cached type inference result."""
        with self._lock:
            return self._inference_cache.get(key)

    def clear_cache(self) -> None:
        """Clear inference cache."""
        with self._lock:
            self._inference_cache.clear()
            logger.debug("Inference cache cleared")

    def to_dict(self) -> dict[str, Any]:
        """Serialize context to dictionary."""
        with self._lock:
            return {
                "language": self.language,
                "file_path": self.file_path,
                "current_class": self.current_class,
                "current_function": self.current_function,
                "variables": {
                    k: {
                        "name": v.name,
                        "type": v.type_result.to_dict() if v.type_result else None,
                        "scope": v.scope,
                    }
                    for k, v in self.variables.items()
                },
                "functions": {
                    k: {
                        "name": v.name,
                        "return_type": v.return_type.to_dict()
                        if v.return_type
                        else None,
                        "parameters": {
                            pname: ptype.to_dict()
                            for pname, ptype in v.parameters.items()
                        },
                    }
                    for k, v in self.functions.items()
                },
                "imports": self.imports,
            }

    def _make_scope_key(self, name: str) -> str:
        """Create unique key for variable in current scope."""
        scope_path = ":".join(self.scope_stack) if self.scope_stack else "module"
        return f"{scope_path}:{name}"

    def _make_function_key(self, name: str, class_name: str | None = None) -> str:
        """Create unique key for function."""
        if class_name:
            return f"{class_name}.{name}"
        return name


class TypeRegistry:
    """
    Global registry of known types and function signatures.

    Maintains:
    - Built-in types for each language
    - Function signatures from standard libraries
    - User-defined types from analyzed code
    """

    def __init__(self, language: str = "python"):
        """Initialize type registry."""
        self.language = language
        self._types: dict[str, TypeInferenceResult] = {}
        self._function_signatures: dict[str, FunctionSignature] = {}
        self._type_mappings: dict[str, str] = {}
        self._lock = Lock()

        self._load_builtins()
        logger.debug(f"TypeRegistry initialized for {language}")

    def register_type(self, type_string: str, result: TypeInferenceResult) -> None:
        """Register a type."""
        with self._lock:
            self._types[type_string] = result

    def lookup_type(self, type_string: str) -> TypeInferenceResult | None:
        """Look up type information."""
        with self._lock:
            return self._types.get(type_string)

    def register_function(self, func_key: str, signature: FunctionSignature) -> None:
        """Register function signature."""
        with self._lock:
            self._function_signatures[func_key] = signature

    def lookup_function(self, func_key: str) -> FunctionSignature | None:
        """Look up function signature."""
        with self._lock:
            return self._function_signatures.get(func_key)

    def add_type_mapping(self, alias: str, canonical: str) -> None:
        """Register type alias mapping."""
        with self._lock:
            self._type_mappings[alias] = canonical

    def resolve_type_mapping(self, alias: str) -> str:
        """Resolve type alias to canonical type."""
        with self._lock:
            return self._type_mappings.get(alias, alias)

    def _load_builtins(self) -> None:
        """Load language-specific built-in types."""
        builtins = self._get_builtin_types()
        for type_name, type_str in builtins.items():
            result = TypeInferenceResult(
                type_string=type_name,
                confidence=1.0,
                source=TypeSource.BUILTIN,
                language=self.language,
            )
            self.register_type(type_name, result)

    def _get_builtin_types(self) -> dict[str, str]:
        """Get language-specific built-in types."""
        builtins = {
            "python": {
                "int": "int",
                "float": "float",
                "str": "str",
                "bool": "bool",
                "list": "list",
                "dict": "dict",
                "tuple": "tuple",
                "set": "set",
                "None": "NoneType",
                "Any": "Any",
            },
            "javascript": {
                "number": "number",
                "string": "string",
                "boolean": "boolean",
                "undefined": "undefined",
                "null": "null",
                "object": "object",
                "Array": "Array",
                "any": "any",
            },
            "typescript": {
                "number": "number",
                "string": "string",
                "boolean": "boolean",
                "undefined": "undefined",
                "null": "null",
                "any": "any",
                "void": "void",
                "never": "never",
            },
            "java": {
                "int": "int",
                "long": "long",
                "float": "float",
                "double": "double",
                "boolean": "boolean",
                "char": "char",
                "byte": "byte",
                "short": "short",
                "String": "java.lang.String",
                "Object": "java.lang.Object",
            },
        }
        return builtins.get(self.language, {})

    def stats(self) -> dict[str, Any]:
        """Get registry statistics."""
        with self._lock:
            return {
                "language": self.language,
                "types_registered": len(self._types),
                "functions_registered": len(self._function_signatures),
                "type_mappings": len(self._type_mappings),
            }
