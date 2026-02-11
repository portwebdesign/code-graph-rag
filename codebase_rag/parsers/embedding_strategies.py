from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EmbeddingStrategy(Enum):
    """Text extraction strategy for embeddings."""

    RAW = "raw"
    SEMANTIC = "semantic"
    RICH = "rich"


@dataclass
class EmbeddingPayload:
    """Result of embedding text extraction.

    Attributes:
        text (str): The main text to be embedded
        metadata (Dict[str, Any]): Rich metadata about the extracted content
        entity_type (str): Type of entity (function, class, endpoint, etc.)
        language (str): Programming language
        framework (Optional[str]): Detected framework (if any)
        source_file (Optional[str]): Optional source file path
    """

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    entity_type: str = "unknown"
    language: str = ""
    framework: str | None = None
    source_file: str | None = None

    def get_summary(self) -> str:
        """Get a one-line summary of the payload."""
        return f"{self.entity_type} - {len(self.text)} chars - {self.language}"


@dataclass
class NodeInfo:
    """Information extracted from an AST node.

    Attributes:
        node_id (str): Unique identifier for the node
        kind (str): Type of node (function, class, etc.)
        name (str): Name of entity
        signature (Optional[str]): Function/method signature
        signature_lite (Optional[str]): Minimal signature
        docstring (Optional[str]): Documentation string
        body_text (Optional[str]): Main implementation text
        parameters (List[str]): Parameter names and types
        return_type (Optional[str]): Return type annotation
        decorators (List[str]): Applied decorators
        parent_class (Optional[str]): Parent class name if applicable
        start_line (Optional[int]): Starting line number
        end_line (Optional[int]): Ending line number
    """

    node_id: str
    kind: str
    name: str
    signature: str | None = None
    signature_lite: str | None = None
    docstring: str | None = None
    body_text: str | None = None
    parameters: list[str] = field(default_factory=list)
    return_type: str | None = None
    decorators: list[str] = field(default_factory=list)
    parent_class: str | None = None
    start_line: int | None = None
    end_line: int | None = None


class EmbeddingTextExtractor:
    """Extract text for embeddings using different strategies.

    The extractor provides three strategies for preparing text for embedding:

    1. RAW: Minimal text - just the function/method body
       - Fast embedding
       - Lower context
       - Good for simple search

    2. SEMANTIC: Context-aware - signature + docstring + body
       - Balanced approach
       - Medium context
       - Good for semantic search
       - Recommended default

    3. RICH: Complete context - everything
       - Maximum context
       - Includes framework info, type hints, decorators
       - Best semantic understanding
       - Slower embedding
       - Larger embeddings

    Example:
        extractor = EmbeddingTextExtractor()

        node_info = NodeInfo(
            node_id="func_123",
            kind="function",
            name="calculate_total",
            signature="def calculate_total(items: List[Item]) -> float:",
            docstring="Calculate total price of items.",
            body_text="return sum(item.price for item in items)",
            parameters=["items"],
            return_type="float",
        )

        payload = extractor.extract(
            node_info,
            strategy=EmbeddingStrategy.RICH,
            framework="django"
        )

        print(f"Embedding text length: {len(payload.text)}")
        print(f"Metadata: {payload.metadata}")
    """

    def __init__(self, max_body_chars: int = 5000):
        """Initialize extractor.

        Args:
            max_body_chars (int): Maximum number of characters to include from body
        """
        self.max_body_chars = max_body_chars

    def extract(
        self,
        node_info: NodeInfo,
        strategy: EmbeddingStrategy = EmbeddingStrategy.SEMANTIC,
        framework: str | None = None,
        language: str = "python",
    ) -> EmbeddingPayload:
        """Extract embedding text from node information.

        Args:
            node_info (NodeInfo): Extracted node information
            strategy (EmbeddingStrategy): Which extraction strategy to use
            framework (Optional[str]): Detected framework name if any
            language (str): Programming language

        Returns:
            EmbeddingPayload: Payload with text and metadata

        Example:
            payload = extractor.extract(
                node_info,
                strategy=EmbeddingStrategy.RICH,
                framework="flask",
                language="python"
            )
        """
        if strategy == EmbeddingStrategy.RAW:
            return self._extract_raw(node_info, language, framework)
        elif strategy == EmbeddingStrategy.SEMANTIC:
            return self._extract_semantic(node_info, language, framework)
        elif strategy == EmbeddingStrategy.RICH:
            return self._extract_rich(node_info, language, framework)
        else:
            return self._extract_semantic(node_info, language, framework)

    def _extract_raw(
        self, node_info: NodeInfo, language: str, framework: str | None
    ) -> EmbeddingPayload:
        """Extract RAW strategy: just the body.

        This is the minimal approach - only the implementation code.
        Suitable for quick embeddings with reduced context.

        Args:
            node_info (NodeInfo): Node information
            language (str): Programming language
            framework (Optional[str]): Detected framework

        Returns:
            EmbeddingPayload: Content for embedding
        """
        body = node_info.body_text or ""

        if len(body) > self.max_body_chars:
            body = body[: self.max_body_chars] + "\n[... truncated ...]"

        metadata = {
            "node_id": node_info.node_id,
            "entity_type": node_info.kind,
            "name": node_info.name,
            "start_line": node_info.start_line,
            "end_line": node_info.end_line,
            "strategy": EmbeddingStrategy.RAW.value,
        }

        return EmbeddingPayload(
            text=body or f"{node_info.kind} {node_info.name}",
            metadata=metadata,
            entity_type=node_info.kind,
            language=language,
            framework=framework,
        )

    def _extract_semantic(
        self, node_info: NodeInfo, language: str, framework: str | None
    ) -> EmbeddingPayload:
        """Extract SEMANTIC strategy: signature + docstring + body.

        This is the balanced approach - provides context without excessive length.
        Includes documentation and type information.
        Recommended for most use cases.

        Args:
            node_info (NodeInfo): Node information
            language (str): Programming language
            framework (Optional[str]): Detected framework

        Returns:
            EmbeddingPayload: Content for embedding
        """
        components = []

        if node_info.signature or node_info.signature_lite:
            signature = node_info.signature or node_info.signature_lite
            components.append(f"Signature: {signature}")
        else:
            sig = f"{node_info.kind.title()} {node_info.name}"
            if node_info.parameters:
                sig += f"({', '.join(node_info.parameters)})"
            components.append(sig)

        if node_info.docstring:
            components.append(f"\nDocumentation:\n{node_info.docstring}")

        if node_info.body_text:
            body = node_info.body_text
            if len(body) > self.max_body_chars:
                body = body[: self.max_body_chars] + "\n[... truncated ...]"
            components.append(f"\nImplementation:\n{body}")

        text = "".join(components)

        metadata = {
            "node_id": node_info.node_id,
            "entity_type": node_info.kind,
            "name": node_info.name,
            "strategy": EmbeddingStrategy.SEMANTIC.value,
            "has_signature": bool(node_info.signature or node_info.signature_lite),
            "has_docstring": bool(node_info.docstring),
            "has_body": bool(node_info.body_text),
            "start_line": node_info.start_line,
            "end_line": node_info.end_line,
        }

        if node_info.parameters:
            metadata["parameters"] = node_info.parameters

        if node_info.return_type:
            metadata["return_type"] = node_info.return_type

        if node_info.decorators:
            metadata["decorators"] = node_info.decorators

        if node_info.parent_class:
            metadata["parent_class"] = node_info.parent_class

        return EmbeddingPayload(
            text=text,
            metadata=metadata,
            entity_type=node_info.kind,
            language=language,
            framework=framework,
        )

    def _extract_rich(
        self, node_info: NodeInfo, language: str, framework: str | None
    ) -> EmbeddingPayload:
        """Extract RICH strategy: everything including framework context.

        This is the complete approach - provides maximum context for
        embedding-based search. Includes framework-specific patterns,
        type information, relationships, and more.
        Best for deep semantic understanding.

        Args:
            node_info (NodeInfo): Node information
            language (str): Programming language
            framework (Optional[str]): Detected framework

        Returns:
            EmbeddingPayload: Content for embedding
        """
        semantic_payload = self._extract_semantic(node_info, language, framework)
        components = [semantic_payload.text]
        metadata = semantic_payload.metadata.copy()

        if node_info.parameters or node_info.return_type:
            type_info = []

            if node_info.return_type:
                type_info.append(f"Returns: {node_info.return_type}")

            if node_info.parameters:
                type_info.append(f"Parameters: {', '.join(node_info.parameters)}")

            if type_info:
                components.append("\nType Information:\n" + "\n".join(type_info))
                metadata["has_type_info"] = True

        if node_info.decorators:
            decorators_str = "\n".join(f"  - {dec}" for dec in node_info.decorators)
            components.append(f"\nDecorators:\n{decorators_str}")
            metadata["decorators_count"] = len(node_info.decorators)

        if framework:
            framework_context = self._get_framework_context(
                node_info, framework, language
            )
            if framework_context:
                components.append(
                    f"\nFramework Context ({framework}):\n{framework_context}"
                )
                metadata["framework_context"] = True

        if node_info.parent_class:
            components.append(f"\nBelongs to: {node_info.parent_class}")
            metadata["has_parent_class"] = True

        code_metrics = self._compute_code_metrics(node_info)
        if code_metrics:
            metrics_str = ", ".join(f"{k}: {v}" for k, v in code_metrics.items())
            components.append(f"\nMetrics: {metrics_str}")
            metadata["metrics"] = code_metrics

        text = "".join(components)
        metadata["strategy"] = EmbeddingStrategy.RICH.value

        return EmbeddingPayload(
            text=text,
            metadata=metadata,
            entity_type=node_info.kind,
            language=language,
            framework=framework,
        )

    def _get_framework_context(
        self, node_info: NodeInfo, framework: str, language: str
    ) -> str:
        """Generate framework-specific context.

        Args:
            node_info (NodeInfo): Node information
            framework (str): Framework name (django, flask, spring, etc.)
            language (str): Programming language

        Returns:
            str: Formatted framework context string
        """
        context_parts = []

        if language == "python":
            if framework == "django":
                if (
                    "view" in node_info.kind.lower()
                    or "handler" in node_info.name.lower()
                ):
                    context_parts.append("Django View Handler")
                    if (
                        "get" in node_info.name.lower()
                        or "post" in node_info.name.lower()
                    ):
                        method = "GET" if "get" in node_info.name else "POST"
                        context_parts.append(f"HTTP Method: {method}")
                elif "model" in node_info.kind.lower():
                    context_parts.append("Django ORM Model")
                    if node_info.decorators:
                        context_parts.append(
                            f"Decorators: {', '.join(node_info.decorators)}"
                        )

            elif framework == "flask":
                if "route" in node_info.name.lower():
                    context_parts.append("Flask Route Handler")
                    if node_info.decorators:
                        context_parts.append(
                            f"Route decorators: {', '.join(node_info.decorators)}"
                        )

            elif framework == "fastapi":
                if "endpoint" in node_info.name.lower() or node_info.decorators:
                    context_parts.append("FastAPI Endpoint")
                    if node_info.decorators:
                        context_parts.append(
                            f"HTTP methods: {', '.join(node_info.decorators)}"
                        )

        elif language == "java":
            if framework == "spring":
                if (
                    "controller" in node_info.parent_class.lower()
                    if node_info.parent_class
                    else False
                ):
                    context_parts.append("Spring REST Controller")
                    if "endpoint" in node_info.name.lower():
                        context_parts.append("REST Endpoint Handler")
                elif "entity" in node_info.kind.lower():
                    context_parts.append("JPA Entity")
                elif "repository" in node_info.kind.lower():
                    context_parts.append("Spring Data Repository")

        elif language in {"javascript", "typescript"}:
            if framework == "react":
                context_parts.append("React Component")
                if "class" in node_info.kind.lower():
                    context_parts.append("Class Component")
                else:
                    context_parts.append("Functional Component")

            elif framework == "nestjs":
                if "controller" in node_info.kind.lower():
                    context_parts.append("NestJS Controller")
                    if node_info.decorators:
                        context_parts.append(
                            f"Route decorators: {', '.join(node_info.decorators)}"
                        )
                elif "service" in node_info.kind.lower():
                    context_parts.append("NestJS Service/Provider")

            elif framework == "express":
                if (
                    "route" in node_info.name.lower()
                    or "handler" in node_info.name.lower()
                ):
                    context_parts.append("Express Route Handler")

        return "\n".join(f"  - {part}" for part in context_parts)

    def _compute_code_metrics(self, node_info: NodeInfo) -> dict[str, Any]:
        """Compute code metrics from node information.

        Args:
            node_info (NodeInfo): Node information

        Returns:
            Dict[str, Any]: Dictionary of code metrics
        """
        metrics = {}

        if node_info.start_line and node_info.end_line:
            loc = node_info.end_line - node_info.start_line + 1
            metrics["lines_of_code"] = loc

        if node_info.parameters:
            metrics["parameters"] = len(node_info.parameters)

        if node_info.body_text:
            body = node_info.body_text.lower()
            complexity_indicators = {
                "if": body.count("if "),
                "for": body.count("for "),
                "while": body.count("while "),
            }
            total_complexity = sum(complexity_indicators.values())
            if total_complexity > 0:
                metrics["complexity"] = min(total_complexity, 10)

        if node_info.decorators:
            metrics["decorators"] = len(node_info.decorators)

        return metrics

    def extract_from_dict(
        self,
        node_dict: dict[str, Any],
        strategy: EmbeddingStrategy = EmbeddingStrategy.SEMANTIC,
        **kwargs,
    ) -> EmbeddingPayload:
        """Extract embedding text from dictionary representation.

        Args:
            node_dict (Dict[str, Any]): Dictionary with node information
            strategy (EmbeddingStrategy): Extraction strategy to use
            **kwargs: Additional arguments (framework, language, etc.)

        Returns:
            EmbeddingPayload: Payload with text and metadata

        Example:
            node_dict = {
                "node_id": "func_123",
                "kind": "function",
                "name": "calculate",
                "signature": "def calculate(x, y):",
                "docstring": "Calculates sum.",
                "body_text": "return x + y",
            }

            payload = extractor.extract_from_dict(
                node_dict,
                strategy=EmbeddingStrategy.RICH,
                framework="django"
            )
        """
        node_info = NodeInfo(
            node_id=node_dict.get("node_id", "unknown"),
            kind=node_dict.get("kind", "unknown"),
            name=node_dict.get("name", "unnamed"),
            signature=node_dict.get("signature"),
            signature_lite=node_dict.get("signature_lite"),
            docstring=node_dict.get("docstring"),
            body_text=node_dict.get("body_text"),
            parameters=node_dict.get("parameters", []),
            return_type=node_dict.get("return_type"),
            decorators=node_dict.get("decorators", []),
            parent_class=node_dict.get("parent_class"),
            start_line=node_dict.get("start_line"),
            end_line=node_dict.get("end_line"),
        )

        return self.extract(
            node_info,
            strategy=strategy,
            framework=kwargs.get("framework"),
            language=kwargs.get("language", "python"),
        )
