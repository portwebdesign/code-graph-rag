from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from codebase_rag.parsers.frameworks.detectors import (
    JavaFrameworkDetector,
    JsFrameworkDetector,
    PythonFrameworkDetector,
    RubyFrameworkDetector,
)
from codebase_rag.parsers.frameworks.detectors.python_framework_detector import (
    PythonFrameworkType,
)
from codebase_rag.parsers.pipeline.embedding_strategies import (
    EmbeddingStrategy,
    EmbeddingTextExtractor,
    NodeInfo,
)


@dataclass
class EnrichedNodeMetadata:
    """Extended metadata for AST nodes with Phase 2 information.

    Attributes:
        node_id: Unique identifier
        framework_type: Detected framework (if any)
        framework_context: Framework-specific information
        embedding_text: Text prepared for embedding
        embedding_strategy: Which strategy was used
        embedding_metadata: Rich metadata about embedding
        original_metadata: Original node metadata
    """

    node_id: str
    framework_type: str | None = None
    framework_context: dict[str, Any] | None = None
    embedding_text: str | None = None
    embedding_strategy: str = "semantic"
    embedding_metadata: dict[str, Any] = field(default_factory=dict)
    original_metadata: dict[str, Any] = field(default_factory=dict)


class Phase2FrameworkDetectionMixin:
    """Mixin for adding framework detection to DefinitionProcessor.

    This mixin adds framework detection capabilities without modifying
    the existing DefinitionProcessor class.

    Usage:
        processor = DefinitionProcessor(...)
        Phase2FrameworkDetectionMixin.enhance(processor)
    """

    def __init__(self):
        """Initialize framework detectors for all languages."""
        self.py_detector = PythonFrameworkDetector()
        self.java_detector = JavaFrameworkDetector()
        self.rb_detector = RubyFrameworkDetector()
        self.js_detector = JsFrameworkDetector()

    def detect_framework(
        self, language: str, source_code: str, repo_root: Path | None = None
    ) -> str | None:
        """Detect framework for given language.

        Args:
            language: Programming language (python, java, ruby, javascript)
            source_code: Source code content
            repo_root: Repository root for project-level detection

        Returns:
            Framework name or None if not detected

        Example:
            framework = processor.detect_framework("python", source_code)
            if framework:
                print(f"Detected {framework}")
        """
        language = language.lower()

        if language == "python":
            framework_type = self.py_detector.detect_framework(None, source_code)
            return framework_type.value if framework_type else None

        elif language == "java":
            framework_type = self.java_detector.detect_framework(source_code)
            return framework_type.value if framework_type else None

        elif language == "ruby":
            if repo_root:
                framework_type = self.rb_detector.detect_from_project(Path(repo_root))
            else:
                framework_type = self.rb_detector.detect_from_source(source_code)
            return framework_type.value if framework_type else None

        elif language in ["javascript", "typescript", "js", "ts"]:
            framework_type = self.js_detector.detect_from_source(source_code)
            return framework_type.value if framework_type else None

        return None

    def get_framework_metadata(
        self, language: str, source_code: str, framework: str | None = None
    ) -> dict[str, Any]:
        """Get framework-specific metadata.

        Args:
            language: Programming language
            source_code: Source code content
            framework: Optional pre-detected framework name

        Returns:
            Dictionary with framework metadata

        Example:
            metadata = processor.get_framework_metadata("python", source_code)
            print(f"Endpoints: {metadata.get('endpoints', [])}")
        """
        language = language.lower()
        framework = framework or self.detect_framework(language, source_code)

        if not framework:
            return {"framework": None, "detected": False}

        if language == "python":
            return self.py_detector.get_framework_metadata(
                cast(PythonFrameworkType, framework), None, source_code
            )

        elif language == "java":
            return self.java_detector.get_framework_metadata(source_code)

        elif language == "ruby":
            return self.rb_detector.get_framework_metadata(source_code=source_code)

        elif language in ["javascript", "typescript", "js", "ts"]:
            return self.js_detector.get_framework_metadata(source_code)

        return {"framework": framework, "detected": True}


class Phase2EmbeddingStrategyMixin:
    """Mixin for adding embedding strategy support to DefinitionProcessor.

    This mixin adds capability to extract embedding text using different
    strategies (RAW, SEMANTIC, RICH).

    Usage:
        processor = DefinitionProcessor(...)
        Phase2EmbeddingStrategyMixin.enhance(processor)
    """

    def __init__(self):
        """Initialize embedding text extractor."""
        self.embedding_extractor = EmbeddingTextExtractor()
        self.embedding_strategy = EmbeddingStrategy.SEMANTIC

    def set_embedding_strategy(self, strategy: EmbeddingStrategy) -> None:
        """Set the embedding strategy to use.

        Args:
            strategy: EmbeddingStrategy.RAW, SEMANTIC, or RICH

        Example:
            processor.set_embedding_strategy(EmbeddingStrategy.RICH)
        """
        self.embedding_strategy = strategy

    def extract_embedding_text(
        self,
        node_info: NodeInfo,
        framework: str | None = None,
        language: str = "python",
    ) -> dict[str, Any]:
        """Extract embedding text from node.

        Args:
            node_info: Node information
            framework: Detected framework name
            language: Programming language

        Returns:
            Dictionary with embedding_text and metadata

        Example:
            embedding = processor.extract_embedding_text(
                node_info,
                framework="django",
                language="python"
            )
            print(f"Text length: {len(embedding['text'])}")
        """
        payload = self.embedding_extractor.extract(
            node_info,
            strategy=self.embedding_strategy,
            framework=framework,
            language=language,
        )

        return {
            "text": payload.text,
            "metadata": payload.metadata,
            "entity_type": payload.entity_type,
            "strategy": self.embedding_strategy.value,
        }

    def extract_embedding_from_dict(
        self, node_dict: dict[str, Any], **kwargs
    ) -> dict[str, Any]:
        """Extract embedding text from dictionary representation.

        Args:
            node_dict: Dictionary with node information
            **kwargs: framework, language, etc.

        Returns:
            Dictionary with embedding_text and metadata

        Example:
            node = {
                "node_id": "func_1",
                "kind": "function",
                "name": "process_order",
                "docstring": "Process customer order",
                "body_text": "return OrderProcessor().handle(order)",
            }

            embedding = processor.extract_embedding_from_dict(
                node,
                framework="django",
                language="python"
            )
        """
        payload = self.embedding_extractor.extract_from_dict(
            node_dict, strategy=self.embedding_strategy, **kwargs
        )

        return {
            "text": payload.text,
            "metadata": payload.metadata,
            "entity_type": payload.entity_type,
            "strategy": self.embedding_strategy.value,
        }


class Phase2EnrichedDefinitionProcessor:
    """Complete Phase 2 enhanced definition processor.

    Combines framework detection and embedding strategy capabilities.
    This is a composition-based adapter that wraps existing DefinitionProcessor
    without modifying it.

    Features:
        - Framework detection
        - Embedding text extraction with multiple strategies
        - Metadata enrichment
        - Backward compatible

    Example:
        # Create standard processor
        base_processor = DefinitionProcessor(...)

        # Wrap with Phase 2 capabilities
        enhanced = Phase2EnrichedDefinitionProcessor(base_processor)

        # Use enhanced features
        enhanced.set_embedding_strategy(EmbeddingStrategy.RICH)
        nodes = enhanced.process_with_enrichment(
            file_path,
            language,
            repo_root
        )

        # Each node now has framework and embedding metadata
    """

    def __init__(self, base_processor=None):
        """Initialize enhanced processor.

        Args:
            base_processor: Optional existing DefinitionProcessor to wrap
        """
        self.base_processor = base_processor
        self.framework_detector = Phase2FrameworkDetectionMixin()
        self.embedding_extractor = Phase2EmbeddingStrategyMixin()

    def process_with_enrichment(
        self,
        file_path: str,
        language: str,
        repo_root: str | None = None,
        embedding_strategy: EmbeddingStrategy = EmbeddingStrategy.SEMANTIC,
    ) -> list[EnrichedNodeMetadata]:
        """Process file with Phase 2 enrichment.

        Args:
            file_path: Path to source file
            language: Programming language
            repo_root: Repository root for project-level detection
            embedding_strategy: Which embedding strategy to use

        Returns:
            List of EnrichedNodeMetadata objects

        Example:
            enriched_nodes = processor.process_with_enrichment(
                "app/views.py",
                "python",
                ".",
                EmbeddingStrategy.RICH
            )

            for node in enriched_nodes:
                print(f"{node.node_id}: {node.framework_type}")
                print(f"Embedding text length: {len(node.embedding_text)}")
        """
        self.embedding_extractor.set_embedding_strategy(embedding_strategy)

        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            return []

        source_code = file_path_obj.read_text(encoding="utf-8", errors="ignore")

        framework = self.framework_detector.detect_framework(
            language, source_code, Path(repo_root) if repo_root else None
        )

        framework_metadata = self.framework_detector.get_framework_metadata(
            language, source_code, framework
        )

        base_nodes = []
        if self.base_processor and hasattr(self.base_processor, "process_file"):
            base_nodes = self.base_processor.process_file(file_path_obj, language)

        enriched_nodes = []
        for base_node in base_nodes:
            node_info = self._convert_to_node_info(base_node, source_code)

            embedding_result = self.embedding_extractor.extract_embedding_text(
                node_info,
                framework=framework,
                language=language,
            )

            enriched = EnrichedNodeMetadata(
                node_id=node_info.node_id,
                framework_type=framework,
                framework_context=framework_metadata,
                embedding_text=embedding_result["text"],
                embedding_strategy=embedding_result["strategy"],
                embedding_metadata=embedding_result["metadata"],
                original_metadata=getattr(base_node, "__dict__", {}),
            )

            enriched_nodes.append(enriched)

        return enriched_nodes

    def _convert_to_node_info(self, base_node: Any, source_code: str) -> NodeInfo:
        """Convert base processor node to NodeInfo.

        This is a placeholder - actual implementation depends on
        the base processor's node structure.

        Args:
            base_node: Node from base processor
            source_code: Source code for context

        Returns:
            NodeInfo object
        """
        node_id = getattr(base_node, "id", "unknown")
        kind = getattr(base_node, "kind", "unknown")
        name = getattr(base_node, "name", "unnamed")
        signature = getattr(base_node, "signature", None)
        docstring = getattr(base_node, "docstring", None)
        body_text = getattr(base_node, "body", None)

        return NodeInfo(
            node_id=node_id,
            kind=kind,
            name=name,
            signature=signature,
            docstring=docstring,
            body_text=body_text,
        )

    def process_and_embed(
        self, file_path: str, language: str, **kwargs
    ) -> list[dict[str, Any]]:
        """Process file and return enriched nodes as dictionaries.

        Args:
            file_path: Path to source file
            language: Programming language
            **kwargs: Additional arguments (repo_root, embedding_strategy, etc.)

        Returns:
            List of enriched node dictionaries

        Example:
            nodes = processor.process_and_embed(
                "app/models.py",
                "python",
                repo_root=".",
                embedding_strategy=EmbeddingStrategy.RICH
            )

            for node in nodes:
                # node has keys: node_id, framework_type, embedding_text, etc.
                ingest_to_vector_db(node['embedding_text'], node['metadata'])
        """
        embedding_strategy = kwargs.pop(
            "embedding_strategy", EmbeddingStrategy.SEMANTIC
        )
        repo_root = kwargs.pop("repo_root", None)

        enriched = self.process_with_enrichment(
            file_path, language, repo_root, embedding_strategy
        )

        result = []
        for node in enriched:
            result.append(
                {
                    "node_id": node.node_id,
                    "framework_type": node.framework_type,
                    "framework_context": node.framework_context,
                    "embedding_text": node.embedding_text,
                    "embedding_strategy": node.embedding_strategy,
                    "embedding_metadata": node.embedding_metadata,
                    "original_metadata": node.original_metadata,
                }
            )

        return result
