import logging
from typing import Any

from .base import BaseTypeInferenceEngine
from .context import (
    FunctionSignature,
    InferenceContext,
    TypeInferenceResult,
    TypeSource,
)

logger = logging.getLogger(__name__)


class PythonAnnotationStrategy:
    """Extract type from explicit Python type annotations."""

    def infer(self, node: Any, context: InferenceContext) -> TypeInferenceResult | None:
        """
        Extract type from annotation.

        Handles:
        - Parameter annotations: def foo(x: int)
        - Return annotations: def foo() -> str
        - Variable annotations: x: int = 5
        - Annotated assignments: x: List[str] = []
        """
        try:
            if not hasattr(node, "child_by_field_name"):
                return None

            annotation_node = node.child_by_field_name("type")
            if not annotation_node:
                return None

            type_str = self._extract_type_string(annotation_node)
            if type_str:
                return TypeInferenceResult(
                    type_string=type_str,
                    confidence=1.0,
                    source=TypeSource.ANNOTATION,
                    language="python",
                    context={"annotation_type": "explicit"},
                )
        except Exception as e:
            logger.debug(f"Annotation extraction failed: {e}")

        return None

    def _extract_type_string(self, annotation_node: Any) -> str | None:
        """Extract type string from annotation node."""
        try:
            if annotation_node.type == "identifier":
                return (
                    annotation_node.text.decode()
                    if isinstance(annotation_node.text, bytes)
                    else annotation_node.text
                )

            if annotation_node.type == "subscript":
                parts = []
                for child in annotation_node.children:
                    if child.type == "identifier":
                        parts.append(
                            child.text.decode()
                            if isinstance(child.text, bytes)
                            else child.text
                        )
                    elif child.type == "[":
                        parts.append("[")
                    elif child.type == "]":
                        parts.append("]")
                    elif child.type == ",":
                        parts.append(", ")
                return "".join(parts)

            text = annotation_node.text
            return text.decode() if isinstance(text, bytes) else text
        except Exception as e:
            logger.debug(f"Type string extraction failed: {e}")
            return None


class PythonInferenceStrategy:
    """Infer type from assignments, calls, and usage patterns."""

    def infer(self, node: Any, context: InferenceContext) -> TypeInferenceResult | None:
        """
        Infer type from usage.

        Handles:
        - Assignment: x = 5 (infer int)
        - List literal: x = [1, 2, 3] (infer list[int])
        - Dict literal: x = {"a": 1} (infer dict[str, int])
        - Call result: x = foo() (infer from foo's return type)
        """
        try:
            if node.type == "assignment":
                return self._infer_from_assignment(node, context)

            if node.type in ("call", "function_call"):
                return self._infer_from_call(node, context)

            if node.type == "list":
                return self._infer_from_literal(node, "list")
            if node.type == "dictionary":
                return self._infer_from_literal(node, "dict")
        except Exception as e:
            logger.debug(f"Inference strategy failed: {e}")

        return None

    def _infer_from_assignment(
        self, node: Any, context: InferenceContext
    ) -> TypeInferenceResult | None:
        """Infer type from assignment right-hand side."""
        try:
            rhs = node.child_by_field_name("value")
            if not rhs:
                return None

            type_str = self._infer_value_type(rhs)
            if type_str:
                return TypeInferenceResult(
                    type_string=type_str,
                    confidence=0.7,
                    source=TypeSource.INFERENCE,
                    language="python",
                    context={"inference_type": "assignment"},
                )
        except Exception as e:
            logger.debug(f"Assignment inference failed: {e}")

        return None

    def _infer_from_call(
        self, node: Any, context: InferenceContext
    ) -> TypeInferenceResult | None:
        """Infer type from function call result."""
        try:
            func_name = None
            if hasattr(node, "child_by_field_name"):
                func_node = node.child_by_field_name("function")
                if func_node:
                    func_name = self._extract_name(func_node)

            if not func_name:
                return None

            sig = context.get_function(func_name)
            if sig and sig.return_type:
                return TypeInferenceResult(
                    type_string=sig.return_type.type_string,
                    confidence=0.6,
                    source=TypeSource.INFERENCE,
                    language="python",
                    context={"inference_type": "call", "function": func_name},
                )
        except Exception as e:
            logger.debug(f"Call inference failed: {e}")

        return None

    def _infer_from_literal(
        self, node: Any, literal_type: str
    ) -> TypeInferenceResult | None:
        """Infer type from literal value."""
        try:
            if literal_type == "list":
                return TypeInferenceResult(
                    type_string="list",
                    confidence=0.8,
                    source=TypeSource.INFERENCE,
                    language="python",
                    context={"inference_type": "literal", "literal_kind": "list"},
                )
            elif literal_type == "dict":
                return TypeInferenceResult(
                    type_string="dict",
                    confidence=0.8,
                    source=TypeSource.INFERENCE,
                    language="python",
                    context={"inference_type": "literal", "literal_kind": "dict"},
                )
        except Exception as e:
            logger.debug(f"Literal inference failed: {e}")

        return None

    def _infer_value_type(self, value_node: Any) -> str | None:
        """Infer type from value node."""
        try:
            if value_node.type == "integer":
                return "int"
            if value_node.type == "float":
                return "float"
            if value_node.type == "string":
                return "str"
            if value_node.type in ("true", "false"):
                return "bool"
            if value_node.type == "list":
                return "list"
            if value_node.type == "dictionary":
                return "dict"
            if value_node.type == "tuple":
                return "tuple"
        except Exception:
            pass

        return None

    def _extract_name(self, node: Any) -> str | None:
        """Extract name from node."""
        try:
            if hasattr(node, "text"):
                text = node.text
                return text.decode() if isinstance(text, bytes) else text
        except Exception:
            pass
        return None


class PythonRegistryStrategy:
    """Look up type from function registry and known modules."""

    def infer(self, node: Any, context: InferenceContext) -> TypeInferenceResult | None:
        """
        Look up type from registry.

        Handles:
        - Built-in functions: len() -> int
        - Standard library: os.path.join() -> str
        - User-defined: resolve from context
        """
        try:
            name = None
            if hasattr(node, "text"):
                text = node.text
                name = text.decode() if isinstance(text, bytes) else text

            if not name:
                return None

            result = (
                context.registry.lookup_type(name)  # ty: ignore[unresolved-attribute]
                if hasattr(context, "registry")
                else None
            )
            if result:
                return TypeInferenceResult(
                    type_string=result.type_string,
                    confidence=0.5,
                    source=TypeSource.REGISTRY,
                    language="python",
                    context={"registry_lookup": name},
                )
        except Exception as e:
            logger.debug(f"Registry lookup failed: {e}")

        return None


class PythonTypeInferenceEngine(BaseTypeInferenceEngine):
    """Python-specific type inference engine with modular strategies."""

    def __init__(self, registry=None):
        """Initialize Python type inference engine."""
        super().__init__("python", registry)

        self.annotation_strategy = PythonAnnotationStrategy()
        self.inference_strategy = PythonInferenceStrategy()
        self.registry_strategy = PythonRegistryStrategy()

        logger.debug("PythonTypeInferenceEngine initialized")

    def infer_variable_type(
        self, node: Any, context: InferenceContext | None = None
    ) -> TypeInferenceResult | None:
        """
        Infer variable type using strategy chain.

        1. Annotation (explicit type hint) - 1.0 confidence
        2. Inference (from assignment) - 0.6-0.8 confidence
        3. Registry (from function return) - 0.5 confidence
        """
        ctx = context or self.context
        if not ctx:
            return None

        return self.infer_type_with_strategies(
            node, ["annotation", "inference", "registry"], ctx
        )

    def infer_return_type(
        self, func_node: Any, context: InferenceContext | None = None
    ) -> TypeInferenceResult | None:
        """
        Infer function return type.

        1. Explicit return annotation
        2. Infer from return statements
        3. Assume Any if unknown
        """
        ctx = context or self.context
        if not ctx:
            return None

        try:
            if hasattr(func_node, "child_by_field_name"):
                return_type_node = func_node.child_by_field_name("return_type")
                if return_type_node:
                    result = self.annotation_strategy.infer(return_type_node, ctx)
                    if result:
                        return result

            return TypeInferenceResult(
                type_string="Any",
                confidence=0.0,
                source=TypeSource.INFERENCE,
                language="python",
            )
        except Exception as e:
            logger.debug(f"Return type inference failed: {e}")
            return None

    def infer_method_call_return(
        self,
        method_name: str,
        receiver_type: str,
        args: list[str] | None = None,
        context: InferenceContext | None = None,
    ) -> TypeInferenceResult | None:
        """Infer return type of method call."""
        ctx = context or self.context
        if not ctx:
            return None

        try:
            sig = ctx.get_function(method_name, receiver_type)
            if sig and sig.return_type:
                return sig.return_type
        except Exception as e:
            logger.debug(f"Method call inference failed: {e}")

        return None

    def resolve_call_target(
        self, call_node: Any, context: InferenceContext | None = None
    ) -> str | None:
        """Resolve fully qualified name of call target."""
        ctx = context or self.context
        if not ctx:
            return None

        try:
            func_name = None
            if hasattr(call_node, "child_by_field_name"):
                func_node = call_node.child_by_field_name("function")
                if func_node and hasattr(func_node, "text"):
                    text = func_node.text
                    func_name = text.decode() if isinstance(text, bytes) else text

            if not func_name:
                return None

            import_resolved = ctx.resolve_import(func_name)
            if import_resolved:
                return import_resolved

            if ctx.current_class:
                return f"{ctx.current_class}.{func_name}"
            elif ctx.current_function:
                return f"{ctx.current_function}.{func_name}"

            return func_name
        except Exception as e:
            logger.debug(f"Call target resolution failed: {e}")

        return None

    def extract_function_signature(
        self, func_node: Any, context: InferenceContext | None = None
    ) -> FunctionSignature | None:
        """Extract complete function signature."""
        ctx = context or self.context
        if not ctx:
            return None

        try:
            name = None
            if hasattr(func_node, "child_by_field_name"):
                name_node = func_node.child_by_field_name("name")
                if name_node and hasattr(name_node, "text"):
                    text = name_node.text
                    name = text.decode() if isinstance(text, bytes) else text

            if not name:
                return None

            sig = FunctionSignature(
                name=name,
                language="python",
                is_method=bool(ctx.current_class),
                class_name=ctx.current_class,
            )

            sig.return_type = self.infer_return_type(func_node, ctx)

            return sig
        except Exception as e:
            logger.debug(f"Function signature extraction failed: {e}")

        return None

    def _infer_from_annotation(
        self, node: Any, context: InferenceContext
    ) -> TypeInferenceResult | None:
        """Use annotation strategy."""
        return self.annotation_strategy.infer(node, context)

    def _infer_from_usage(
        self, node: Any, context: InferenceContext
    ) -> TypeInferenceResult | None:
        """Use inference strategy."""
        return self.inference_strategy.infer(node, context)

    def _infer_from_registry(
        self, node: Any, context: InferenceContext
    ) -> TypeInferenceResult | None:
        """Use registry strategy."""
        return self.registry_strategy.infer(node, context)
