import logging
from abc import ABC, abstractmethod
from typing import Any

from .context import (
    FunctionSignature,
    InferenceContext,
    TypeInferenceResult,
    TypeRegistry,
    VariableInfo,
)

logger = logging.getLogger(__name__)


class BaseTypeInferenceEngine(ABC):
    """
    Abstract base class for language-specific type inference engines.

    Each language implementation should:
    1. Extend this class
    2. Implement required abstract methods
    3. Register with the appropriate factory

    Type inference follows a strategy pattern:
    - Annotation strategy (highest confidence)
    - Inference strategy (medium confidence)
    - Registry strategy (lookup confidence)
    """

    def __init__(self, language: str, registry: TypeRegistry | None = None):
        """
        Initialize type inference engine.

        Args:
            language: Target language (python, javascript, etc.)
            registry: Optional shared type registry
        """
        self.language = language
        self.registry = registry or TypeRegistry(language)
        self.context: InferenceContext | None = None

        logger.debug(f"Initialized {self.__class__.__name__} for {language}")

    def set_context(self, context: InferenceContext) -> None:
        """Set the inference context for current analysis."""
        self.context = context
        context.language = self.language

    @abstractmethod
    def infer_variable_type(
        self, node: Any, context: InferenceContext | None = None
    ) -> TypeInferenceResult | None:
        """
        Infer type of a variable or parameter.

        Args:
            node: AST node representing the variable
            context: Optional inference context (uses self.context if not provided)

        Returns:
            TypeInferenceResult with inferred type and confidence, or None
        """
        pass

    @abstractmethod
    def infer_return_type(
        self, func_node: Any, context: InferenceContext | None = None
    ) -> TypeInferenceResult | None:
        """
        Infer return type of a function.

        Args:
            func_node: AST node representing the function definition
            context: Optional inference context

        Returns:
            TypeInferenceResult with inferred return type, or None
        """
        pass

    @abstractmethod
    def infer_method_call_return(
        self,
        method_name: str,
        receiver_type: str,
        args: list[str] | None = None,
        context: InferenceContext | None = None,
    ) -> TypeInferenceResult | None:
        """
        Infer return type of a method call.

        Args:
            method_name: Name of the method
            receiver_type: Type of the object on which method is called
            args: Optional list of argument types
            context: Optional inference context

        Returns:
            TypeInferenceResult with method return type, or None
        """
        pass

    @abstractmethod
    def resolve_call_target(
        self, call_node: Any, context: InferenceContext | None = None
    ) -> str | None:
        """
        Resolve the full qualified name of a call target.

        Args:
            call_node: AST node representing the function/method call
            context: Optional inference context

        Returns:
            Fully qualified name (e.g., 'module.ClassName.method'), or None
        """
        pass

    @abstractmethod
    def extract_function_signature(
        self, func_node: Any, context: InferenceContext | None = None
    ) -> FunctionSignature | None:
        """
        Extract complete function signature including parameter and return types.

        Args:
            func_node: AST node representing function definition
            context: Optional inference context

        Returns:
            FunctionSignature object, or None
        """
        pass

    def analyze_scope(
        self, scope_node: Any, context: InferenceContext | None = None
    ) -> dict[str, VariableInfo]:
        """
        Analyze a scope (function, class, module) and extract variables.

        Args:
            scope_node: AST node representing the scope
            context: Optional inference context

        Returns:
            Dictionary of variable names to VariableInfo objects
        """
        ctx = context or self.context
        if not ctx:
            ctx = InferenceContext(self.language)

        variables = {}
        try:
            variables = self._extract_variables(scope_node, ctx)
        except Exception as e:
            logger.error(f"Error analyzing scope: {e}")

        return variables

    def infer_type_with_strategies(
        self, node: Any, strategies: list[str], context: InferenceContext | None = None
    ) -> TypeInferenceResult | None:
        """
        Infer type by trying multiple strategies in order.

        Supported strategies:
        - 'annotation': Extract from explicit type annotation
        - 'inference': Infer from usage patterns
        - 'registry': Look up in type registry
        - 'builtin': Check built-in types

        Args:
            node: AST node to analyze
            strategies: List of strategy names to try in order
            context: Optional inference context

        Returns:
            TypeInferenceResult from first successful strategy, or None
        """
        ctx = context or self.context
        if not ctx:
            ctx = InferenceContext(self.language)

        for strategy in strategies:
            try:
                if strategy == "annotation":
                    result = self._infer_from_annotation(node, ctx)
                elif strategy == "inference":
                    result = self._infer_from_usage(node, ctx)
                elif strategy == "registry":
                    result = self._infer_from_registry(node, ctx)
                elif strategy == "builtin":
                    result = self._infer_builtin(node, ctx)
                else:
                    logger.warning(f"Unknown strategy: {strategy}")
                    continue

                if result:
                    logger.debug(
                        f"Type inferred via '{strategy}': {result.type_string} "
                        f"(confidence: {result.confidence})"
                    )
                    return result
            except Exception as e:
                logger.debug(f"Strategy '{strategy}' failed: {e}")
                continue

        return None

    def _infer_from_annotation(
        self, node: Any, context: InferenceContext
    ) -> TypeInferenceResult | None:
        """Strategy: Extract from explicit annotation (1.0 confidence)."""
        return None

    def _infer_from_usage(
        self, node: Any, context: InferenceContext
    ) -> TypeInferenceResult | None:
        """Strategy: Infer from usage patterns (0.6-0.8 confidence)."""
        return None

    def _infer_from_registry(
        self, node: Any, context: InferenceContext
    ) -> TypeInferenceResult | None:
        """Strategy: Look up in registry (0.5-0.7 confidence)."""
        return None

    def _infer_builtin(
        self, node: Any, context: InferenceContext
    ) -> TypeInferenceResult | None:
        """Strategy: Check if it's a built-in type (1.0 confidence)."""
        return None

    def _extract_variables(
        self, scope_node: Any, context: InferenceContext
    ) -> dict[str, VariableInfo]:
        """Extract variables from a scope. To be overridden by subclasses."""
        return {}

    def get_statistics(self) -> dict[str, Any]:
        """Get engine statistics."""
        return {
            "language": self.language,
            "registry_stats": self.registry.stats(),
            "context": self.context.to_dict() if self.context else None,
        }
