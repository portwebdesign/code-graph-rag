"""
This module defines the `ProcessorFactory`, a class responsible for creating
and providing access to the various processors used in the parsing pipeline.

It uses lazy initialization for each processor, meaning a processor is only
instantiated the first time it is requested. This factory ensures that all
processors share the same context, such as the project path, ingestor, and
shared data structures like the function registry.

The factory provides access to:
-   `ImportProcessor`
-   `StructureProcessor`
-   `DefinitionProcessor`
-   `TypeInferenceEngine`
-   `CallProcessor`
"""

from pathlib import Path

from codebase_rag.core.constants import SupportedLanguage
from codebase_rag.data_models.types_defs import (
    ASTCacheProtocol,
    FunctionRegistryTrieProtocol,
    LanguageQueries,
    SimpleNameLookup,
)

from ..services import IngestorProtocol
from .call_processor import CallProcessor
from .definition_processor import DefinitionProcessor
from .import_processor import ImportProcessor
from .structure_processor import StructureProcessor
from .type_inference import TypeInferenceEngine


class ProcessorFactory:
    """
    A factory for creating and managing parser processor instances.

    This class ensures that processors are created with shared context and are
    lazily instantiated upon first access.
    """

    def __init__(
        self,
        ingestor: IngestorProtocol,
        repo_path: Path,
        project_name: str,
        queries: dict[SupportedLanguage, LanguageQueries],
        function_registry: FunctionRegistryTrieProtocol,
        simple_name_lookup: SimpleNameLookup,
        ast_cache: ASTCacheProtocol,
        unignore_paths: frozenset[str] | None = None,
        exclude_paths: frozenset[str] | None = None,
    ) -> None:
        """
        Initializes the ProcessorFactory.

        Args:
            ingestor (IngestorProtocol): The data ingestion service.
            repo_path (Path): The root path of the repository.
            project_name (str): The name of the project.
            queries (dict): A dictionary of tree-sitter queries for each language.
            function_registry (FunctionRegistryTrieProtocol): The shared function registry.
            simple_name_lookup (SimpleNameLookup): The shared simple name lookup map.
            ast_cache (ASTCacheProtocol): The shared AST cache.
            unignore_paths (frozenset[str] | None): Paths to include even if ignored.
            exclude_paths (frozenset[str] | None): Paths to exclude from processing.
        """
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.queries = queries
        self.function_registry = function_registry
        self.simple_name_lookup = simple_name_lookup
        self.ast_cache = ast_cache
        self.unignore_paths = unignore_paths
        self.exclude_paths = exclude_paths

        self.module_qn_to_file_path: dict[str, Path] = {}

        self._import_processor: ImportProcessor | None = None
        self._structure_processor: StructureProcessor | None = None
        self._definition_processor: DefinitionProcessor | None = None
        self._type_inference: TypeInferenceEngine | None = None
        self._call_processor: CallProcessor | None = None

    @property
    def import_processor(self) -> ImportProcessor:
        """
        Lazily initializes and returns the `ImportProcessor`.

        Returns:
            ImportProcessor: The singleton instance of the import processor.
        """
        if self._import_processor is None:
            self._import_processor = ImportProcessor(
                repo_path=self.repo_path,
                project_name=self.project_name,
                ingestor=self.ingestor,
                function_registry=self.function_registry,
            )
        return self._import_processor

    @property
    def structure_processor(self) -> StructureProcessor:
        """
        Lazily initializes and returns the `StructureProcessor`.

        Returns:
            StructureProcessor: The singleton instance of the structure processor.
        """
        if self._structure_processor is None:
            self._structure_processor = StructureProcessor(
                ingestor=self.ingestor,
                repo_path=self.repo_path,
                project_name=self.project_name,
                queries=self.queries,
                unignore_paths=self.unignore_paths,
                exclude_paths=self.exclude_paths,
            )
        return self._structure_processor

    @property
    def definition_processor(self) -> DefinitionProcessor:
        """
        Lazily initializes and returns the `DefinitionProcessor`.

        Returns:
            DefinitionProcessor: The singleton instance of the definition processor.
        """
        if self._definition_processor is None:
            self._definition_processor = DefinitionProcessor(
                ingestor=self.ingestor,
                repo_path=self.repo_path,
                project_name=self.project_name,
                function_registry=self.function_registry,
                simple_name_lookup=self.simple_name_lookup,
                import_processor=self.import_processor,
                module_qn_to_file_path=self.module_qn_to_file_path,
            )
        return self._definition_processor

    @property
    def type_inference(self) -> TypeInferenceEngine:
        """
        Lazily initializes and returns the `TypeInferenceEngine`.

        Returns:
            TypeInferenceEngine: The singleton instance of the type inference engine.
        """
        if self._type_inference is None:
            self._type_inference = TypeInferenceEngine(
                import_processor=self.import_processor,
                function_registry=self.function_registry,
                repo_path=self.repo_path,
                project_name=self.project_name,
                ast_cache=self.ast_cache,
                queries=self.queries,
                module_qn_to_file_path=self.module_qn_to_file_path,
                class_inheritance=self.definition_processor.class_inheritance,
                simple_name_lookup=self.simple_name_lookup,
            )
        return self._type_inference

    @property
    def call_processor(self) -> CallProcessor:
        """
        Lazily initializes and returns the `CallProcessor`.

        Returns:
            CallProcessor: The singleton instance of the call processor.
        """
        if self._call_processor is None:
            self._call_processor = CallProcessor(
                ingestor=self.ingestor,
                repo_path=self.repo_path,
                project_name=self.project_name,
                function_registry=self.function_registry,
                import_processor=self.import_processor,
                type_inference=self.type_inference,
                class_inheritance=self.definition_processor.class_inheritance,
            )
        return self._call_processor
