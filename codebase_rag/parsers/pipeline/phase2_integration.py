"""
This module provides integration points for "Phase 2" processing, which includes
advanced analysis like framework detection and sophisticated text extraction for
embeddings.

It defines mixin classes and an adapter (`Phase2EnrichedDefinitionProcessor`) that
can be composed with the primary definition processors. This approach allows for
the separation of concerns, keeping the core AST parsing logic clean while enabling
the addition of more complex, context-aware features. The goal is to enrich the
initial node data with framework-specific metadata and generate high-quality text
for semantic understanding and embedding.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codebase_rag.parsers.frameworks.framework_registry import FrameworkDetectorRegistry
from codebase_rag.parsers.pipeline.embedding_strategies import (
    EmbeddingStrategy,
    EmbeddingTextExtractor,
    NodeInfo,
)


@dataclass
class EnrichedNodeMetadata:
    """
    A data class holding extended metadata for an AST node after Phase 2 processing.

    This structure contains the original information from the initial parse, augmented
    with framework context and the text prepared for vector embedding.

    Attributes:
        node_id (str): A unique identifier for the node.
        framework_type (str | None): The name of the detected framework (e.g., "django", "spring").
        framework_context (dict[str, Any] | None): Framework-specific details, like API endpoints.
        embedding_text (str | None): The final text generated for the node, ready for embedding.
        embedding_strategy (str): The name of the strategy used to generate the embedding text.
        embedding_metadata (dict[str, Any]): Rich metadata related to the embedding content.
        original_metadata (dict[str, Any]): The original properties of the node from the first pass.
    """

    node_id: str
    framework_type: str | None = None
    framework_context: dict[str, Any] | None = None
    embedding_text: str | None = None
    embedding_strategy: str = "semantic"
    embedding_metadata: dict[str, Any] = field(default_factory=dict)
    original_metadata: dict[str, Any] = field(default_factory=dict)


class Phase2FrameworkDetectionMixin:
    """
    A mixin that provides framework detection capabilities.

    This class encapsulates the logic for detecting web frameworks (like Django,
    Spring, Rails, Express) from source code or project structure. It can be
    mixed into other processors to add framework awareness.
    """

    def __init__(self):
        """Initializes framework detectors for each supported language."""
        self.registry = FrameworkDetectorRegistry()

    def detect_framework(
        self, language: str, source_code: str, repo_root: Path | None = None
    ) -> str | None:
        """
        Detects the framework for a given language and source code.

        Args:
            language (str): The programming language (e.g., "python", "java").
            source_code (str): The source code content of a file.
            repo_root (Path | None): The repository root, for project-level detection.

        Returns:
            The name of the detected framework as a string, or None if no framework is detected.
        """
        language = language.lower()
        registry = (
            self.registry if not repo_root else FrameworkDetectorRegistry(repo_root)
        )
        result = registry.detect_for_language(language, source_code)
        return result.framework_type

    def get_framework_metadata(
        self, language: str, source_code: str, framework: str | None = None
    ) -> dict[str, Any]:
        """
        Extracts framework-specific metadata from the source code.

        For example, for a web framework, this might extract API endpoints, routes,
        or database models.

        Args:
            language (str): The programming language.
            source_code (str): The source code content.
            framework (str | None): An optional, pre-detected framework name to guide extraction.

        Returns:
            A dictionary containing the extracted framework-specific metadata.
        """
        language = language.lower()
        result = self.registry.detect_for_language(language, source_code)
        if result.metadata:
            if framework and not result.framework_type:
                result.metadata["framework_type"] = framework
            return result.metadata
        if framework:
            return {"framework_type": framework, "detected": False}
        return {"framework_type": None, "detected": False}


class Phase2EmbeddingStrategyMixin:
    """
    A mixin that adds support for different embedding text extraction strategies.

    This class allows for the generation of text for a code node using various
    levels of detail, from a raw code snippet to a rich, semantic description.
    """

    def __init__(self):
        """Initializes the embedding text extractor and sets a default strategy."""
        self.embedding_extractor = EmbeddingTextExtractor()
        self.embedding_strategy = EmbeddingStrategy.SEMANTIC

    def set_embedding_strategy(self, strategy: EmbeddingStrategy) -> None:
        """
        Sets the embedding strategy to be used for text extraction.

        Args:
            strategy (EmbeddingStrategy): The desired strategy (e.g., RAW, SEMANTIC, RICH).
        """
        self.embedding_strategy = strategy

    def extract_embedding_text(
        self,
        node_info: NodeInfo,
        framework: str | None = None,
        language: str = "python",
    ) -> dict[str, Any]:
        """
        Extracts the text for embedding from a `NodeInfo` object.

        Args:
            node_info (NodeInfo): A structured representation of the code node.
            framework (str | None): The name of the detected framework, for context.
            language (str): The programming language of the node.

        Returns:
            A dictionary containing the generated text, metadata, entity type, and strategy used.
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
        """
        Extracts embedding text from a dictionary representation of a node.

        This is a convenience method that wraps `extract_embedding_text`.

        Args:
            node_dict (dict[str, Any]): A dictionary containing node information.
            **kwargs: Additional arguments like `framework` and `language`.

        Returns:
            A dictionary containing the generated text and associated metadata.
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
    """
    An adapter that combines a base processor with Phase 2 enrichment capabilities.

    This class acts as a wrapper around a standard `DefinitionProcessor`. It uses
    composition to add framework detection and embedding text extraction without
    modifying the base processor's logic. It processes a file, gets the initial
    set of nodes, and then enriches each node with Phase 2 metadata.
    """

    def __init__(self, base_processor=None):
        """
        Initializes the enhanced processor.

        Args:
            base_processor: An optional instance of a `DefinitionProcessor` (or similar)
                            to be wrapped.
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
        """
        Processes a source file and returns a list of fully enriched node metadata.

        This method orchestrates the entire Phase 2 pipeline:
        1. Reads the source file.
        2. Detects the framework.
        3. Runs the base processor to get initial nodes.
        4. For each node, extracts the embedding text using the specified strategy.
        5. Bundles all information into `EnrichedNodeMetadata` objects.

        Args:
            file_path (str): The path to the source file.
            language (str): The programming language of the file.
            repo_root (str | None): The path to the repository root.
            embedding_strategy (EmbeddingStrategy): The strategy to use for embedding text.

        Returns:
            A list of `EnrichedNodeMetadata` objects, one for each processed node.
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
        """
        Converts a node object from the base processor into a standardized `NodeInfo` object.

        This acts as an adapter between the base processor's output format and the
        format expected by the embedding extractor.

        Args:
            base_node (Any): The node object from the base processor.
            source_code (str): The source code of the file for context.

        Returns:
            A `NodeInfo` object populated with data from the base node.
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
        """
        A convenience method that processes a file and returns the enriched data as dictionaries.

        This is useful for pipelines that will directly use the dictionary output, for example,
        to send to a vector database.

        Args:
            file_path (str): The path to the source file.
            language (str): The programming language.
            **kwargs: Additional arguments like `repo_root` and `embedding_strategy`.

        Returns:
            A list of dictionaries, where each dictionary represents an enriched node.
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
