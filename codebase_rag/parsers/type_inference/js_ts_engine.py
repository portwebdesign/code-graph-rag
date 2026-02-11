import logging
import re
from typing import Any

from .base import BaseTypeInferenceEngine
from .context import (
    FunctionSignature,
    InferenceContext,
    TypeInferenceResult,
    TypeSource,
)

logger = logging.getLogger(__name__)


class JSDocStrategy:
    """Extract type from JSDoc comments."""

    JSDOC_PATTERN = re.compile(
        r"@type\s+\{([^}]+)\}|@param\s+\{([^}]+)\}\s+(\w+)|@returns?\s+\{([^}]+)\}"
    )

    def infer(self, node: Any, context: InferenceContext) -> TypeInferenceResult | None:
        """
        Extract type from JSDoc comment.

        Handles:
        - @type {TypeName}
        - @param {TypeName} paramName
        - @returns {TypeName}
        """
        try:
            comment_text = self._get_comment(node)
            if not comment_text:
                return None

            matches = self.JSDOC_PATTERN.findall(comment_text)
            if not matches:
                return None

            for match in matches:
                type_str = match[0] or match[1] or match[3]
                if type_str:
                    return TypeInferenceResult(
                        type_string=type_str.strip(),
                        confidence=0.95,
                        source=TypeSource.ANNOTATION,
                        language="javascript",
                        context={"annotation_type": "jsdoc"},
                    )
        except Exception as e:
            logger.debug(f"JSDoc parsing failed: {e}")

        return None

    def _get_comment(self, node: Any) -> str | None:
        """Get comment text from node."""
        try:
            if hasattr(node, "doc_comment"):
                return node.doc_comment
        except Exception:
            pass
        return None


class TypeScriptAnnotationStrategy:
    """Extract type from TypeScript type annotations."""

    def infer(self, node: Any, context: InferenceContext) -> TypeInferenceResult | None:
        """
        Extract type from TypeScript annotation.

        Handles:
        - Parameter types: function foo(x: number)
        - Return types: function foo(): string
        - Variable types: let x: number = 5
        - Generic types: Array<string>, Map<string, number>
        """
        try:
            if not hasattr(node, "child_by_field_name"):
                return None

            type_node = node.child_by_field_name("type")
            if not type_node:
                return None

            type_str = self._extract_type_string(type_node)
            if type_str:
                return TypeInferenceResult(
                    type_string=type_str,
                    confidence=1.0,
                    source=TypeSource.ANNOTATION,
                    language="typescript",
                    context={"annotation_type": "typescript"},
                )
        except Exception as e:
            logger.debug(f"TypeScript annotation extraction failed: {e}")

        return None

    def _extract_type_string(self, type_node: Any) -> str | None:
        """Extract type string from TypeScript type node."""
        try:
            if type_node.type == "type_identifier":
                text = type_node.text
                return text.decode() if isinstance(text, bytes) else text

            if type_node.type == "generic_type":
                parts = []
                for child in type_node.children:
                    if child.type in ("type_identifier", "<", ">", ","):
                        text = child.text
                        parts.append(text.decode() if isinstance(text, bytes) else text)
                return "".join(parts)

            if type_node.type == "union_type":
                types = []
                for child in type_node.children:
                    if child.type == "type_identifier":
                        text = child.text
                        types.append(text.decode() if isinstance(text, bytes) else text)
                return " | ".join(types)

            text = type_node.text
            return text.decode() if isinstance(text, bytes) else text
        except Exception as e:
            logger.debug(f"Type string extraction failed: {e}")

        return None


class JSInferenceStrategy:
    """Infer types from JavaScript/TypeScript usage patterns."""

    def infer(self, node: Any, context: InferenceContext) -> TypeInferenceResult | None:
        """
        Infer type from usage.

        Handles:
        - Literals: 42 (number), "str" (string), true (boolean)
        - Arrays: [] (Array), [1,2,3] (Array<number>)
        - Objects: {} (Object), {a: 1} (object with properties)
        - Constructor calls: new MyClass()
        """
        try:
            if node.type == "number":
                return TypeInferenceResult(
                    type_string="number",
                    confidence=0.9,
                    source=TypeSource.INFERENCE,
                    language="javascript",
                    context={"inference_type": "literal"},
                )

            if node.type == "string":
                return TypeInferenceResult(
                    type_string="string",
                    confidence=0.9,
                    source=TypeSource.INFERENCE,
                    language="javascript",
                    context={"inference_type": "literal"},
                )

            if node.type in ("true", "false"):
                return TypeInferenceResult(
                    type_string="boolean",
                    confidence=0.9,
                    source=TypeSource.INFERENCE,
                    language="javascript",
                    context={"inference_type": "literal"},
                )

            if node.type == "array":
                return TypeInferenceResult(
                    type_string="Array",
                    confidence=0.8,
                    source=TypeSource.INFERENCE,
                    language="javascript",
                    context={"inference_type": "literal"},
                )

            if node.type == "object":
                return TypeInferenceResult(
                    type_string="object",
                    confidence=0.8,
                    source=TypeSource.INFERENCE,
                    language="javascript",
                    context={"inference_type": "literal"},
                )

            if node.type == "new_expression":
                class_name = self._extract_class_name(node)
                if class_name:
                    return TypeInferenceResult(
                        type_string=class_name,
                        confidence=0.9,
                        source=TypeSource.INFERENCE,
                        language="javascript",
                        context={"inference_type": "constructor"},
                    )
        except Exception as e:
            logger.debug(f"Inference strategy failed: {e}")

        return None

    def _extract_class_name(self, node: Any) -> str | None:
        """Extract class name from new expression."""
        try:
            if hasattr(node, "child_by_field_name"):
                constructor = node.child_by_field_name("constructor")
                if constructor and hasattr(constructor, "text"):
                    text = constructor.text
                    return text.decode() if isinstance(text, bytes) else text
        except Exception:
            pass
        return None


class JSTypeScriptInferenceEngine(BaseTypeInferenceEngine):
    """JavaScript/TypeScript type inference engine with multiple strategies."""

    def __init__(self, language: str = "typescript", registry=None):
        """
        Initialize JS/TS type inference engine.

        Args:
            language: "javascript" or "typescript"
            registry: Optional shared type registry
        """
        super().__init__(language, registry)

        self.jsdoc_strategy = JSDocStrategy()
        self.annotation_strategy = TypeScriptAnnotationStrategy()
        self.inference_strategy = JSInferenceStrategy()

        logger.debug(f"JSTypeScriptInferenceEngine initialized for {language}")

    def infer_variable_type(
        self, node: Any, context: InferenceContext | None = None
    ) -> TypeInferenceResult | None:
        """
        Infer variable type using strategy chain.

        1. TypeScript annotation (1.0 confidence)
        2. JSDoc (0.95 confidence)
        3. Inference (0.8-0.9 confidence)
        4. Registry (0.5 confidence)
        """
        ctx = context or self.context
        if not ctx:
            return None

        if self.language == "typescript":
            result = self.annotation_strategy.infer(node, ctx)
            if result:
                return result

        result = self.jsdoc_strategy.infer(node, ctx)
        if result:
            return result

        return self.infer_type_with_strategies(node, ["inference", "registry"], ctx)

    def infer_return_type(
        self, func_node: Any, context: InferenceContext | None = None
    ) -> TypeInferenceResult | None:
        """
        Infer function return type.

        1. TypeScript return type annotation
        2. JSDoc @returns annotation
        3. Infer from return statements
        4. Assume any if unknown
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

            jsdoc_result = self.jsdoc_strategy.infer(func_node, ctx)
            if jsdoc_result:
                return jsdoc_result

            return TypeInferenceResult(
                type_string="any",
                confidence=0.0,
                source=TypeSource.INFERENCE,
                language=self.language,
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
                callee = call_node.child_by_field_name("function")
                if callee and hasattr(callee, "text"):
                    text = callee.text
                    func_name = text.decode() if isinstance(text, bytes) else text

            if not func_name:
                return None

            import_resolved = ctx.resolve_import(func_name)
            if import_resolved:
                return import_resolved

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
                language=self.language,
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
        if hasattr(node, "text"):
            text = node.text
            name = text.decode() if isinstance(text, bytes) else text
            result = self.registry.lookup_type(name)
            if result:
                return result
        return None
