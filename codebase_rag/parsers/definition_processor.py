"""
This module defines the `DefinitionProcessor`, which is responsible for parsing
source code files to identify and ingest definitions of code constructs like
functions, classes, methods, and their relationships.

It acts as a primary orchestrator for the parsing of a single file, combining
functionality from various mixins (`FunctionIngestMixin`, `ClassIngestMixin`, etc.)
to handle different aspects of definition processing.

Key functionalities:
-   Parsing a file's source code into an AST using the appropriate `tree-sitter` parser.
-   Ingesting the `Module` node and its relationship to its parent container (Package/Folder).
-   Delegating to `ImportProcessor` to handle import statements.
-   Identifying and ingesting all functions, classes, and methods within the file.
-   Handling language-specific constructs like C++ module declarations, JavaScript/TypeScript
    exports, and prototype-based inheritance.
-   Processing dependency files (e.g., `pyproject.toml`) to add dependency relationships
    to the graph.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from codebase_rag.data_models.types_defs import (
    ASTNode,
    FunctionRegistryTrieProtocol,
    SimpleNameLookup,
)

from ..core import constants as cs
from ..core import logs as ls
from .class_ingest import ClassIngestMixin
from .dependency_parser import parse_dependencies
from .function_ingest import FunctionIngestMixin
from .handlers import get_handler
from .js_ts.ingest import JsTsIngestMixin
from .utils import safe_decode_with_fallback

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import LanguageQueries

    from ..services import IngestorProtocol
    from .handlers import LanguageHandler
    from .import_processor import ImportProcessor


class DefinitionProcessor(
    FunctionIngestMixin,
    ClassIngestMixin,
    JsTsIngestMixin,
):
    """
    Processes a source file to identify and ingest code definitions.

    This class combines multiple mixins to handle the extraction and ingestion of
    functions, classes, methods, imports, and other language constructs from a
    parsed Abstract Syntax Tree (AST).
    """

    _handler: LanguageHandler

    def __init__(
        self,
        ingestor: IngestorProtocol,
        repo_path: Path,
        project_name: str,
        function_registry: FunctionRegistryTrieProtocol,
        simple_name_lookup: SimpleNameLookup,
        import_processor: ImportProcessor,
        module_qn_to_file_path: dict[str, Path],
    ):
        """
        Initializes the DefinitionProcessor.

        Args:
            ingestor (IngestorProtocol): The data ingestion service.
            repo_path (Path): The root path of the repository.
            project_name (str): The name of the project.
            function_registry (FunctionRegistryTrieProtocol): The registry for function FQNs.
            simple_name_lookup (SimpleNameLookup): A map from simple names to FQNs.
            import_processor (ImportProcessor): The processor for handling imports.
            module_qn_to_file_path (dict[str, Path]): A map from module FQNs to file paths.
        """
        super().__init__()
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.function_registry = function_registry
        self.simple_name_lookup = simple_name_lookup
        self.import_processor = import_processor
        self.module_qn_to_file_path = module_qn_to_file_path
        self.class_inheritance: dict[str, list[str]] = {}
        self._handler = get_handler(cs.SupportedLanguage.PYTHON)

    def process_file(
        self,
        file_path: Path,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        structural_elements: dict[Path, str | None],
    ) -> tuple[ASTNode, cs.SupportedLanguage] | None:
        """
        Parses a single file and ingests all definitions found within it.

        Args:
            file_path (Path): The absolute path to the file.
            language (cs.SupportedLanguage): The language of the file.
            queries (dict): A dictionary of tree-sitter queries.
            structural_elements (dict): A map of directory paths to their qualified names.

        Returns:
            tuple[ASTNode, cs.SupportedLanguage] | None: A tuple of the root AST node
                and the language if parsing was successful, otherwise None.
        """
        if isinstance(file_path, str):
            file_path = Path(file_path)
        relative_path = file_path.relative_to(self.repo_path)
        relative_path_str = str(relative_path)
        logger.info(
            ls.DEF_PARSING_AST.format(language=language, path=relative_path_str)
        )

        try:
            if language not in queries:
                logger.warning(
                    ls.DEF_UNSUPPORTED_LANGUAGE.format(
                        language=language, path=file_path
                    )
                )
                return None

            self._handler = get_handler(language)
            source_bytes = file_path.read_bytes()
            lang_queries = queries[language]
            parser = lang_queries.get(cs.KEY_PARSER)
            if not parser:
                logger.warning(ls.DEF_NO_PARSER.format(language=language))
                return None

            tree = parser.parse(source_bytes)
            root_node = tree.root_node

            module_qn = cs.SEPARATOR_DOT.join(
                [self.project_name] + list(relative_path.with_suffix("").parts)
            )
            if file_path.name in (cs.INIT_PY, cs.MOD_RS):
                module_qn = cs.SEPARATOR_DOT.join(
                    [self.project_name] + list(relative_path.parent.parts)
                )
            self.module_qn_to_file_path[module_qn] = file_path

            self.ingestor.ensure_node_batch(
                cs.NodeLabel.MODULE,
                {
                    cs.KEY_QUALIFIED_NAME: module_qn,
                    cs.KEY_NAME: file_path.name,
                    cs.KEY_PATH: relative_path_str,
                },
            )

            parent_rel_path = relative_path.parent
            parent_container_qn = structural_elements.get(parent_rel_path)
            parent_label, parent_key, parent_val = (
                (cs.NodeLabel.PACKAGE, cs.KEY_QUALIFIED_NAME, parent_container_qn)
                if parent_container_qn
                else (
                    (cs.NodeLabel.FOLDER, cs.KEY_PATH, str(parent_rel_path))
                    if parent_rel_path != Path(".")
                    else (cs.NodeLabel.PROJECT, cs.KEY_NAME, self.project_name)
                )
            )
            self.ingestor.ensure_relationship_batch(
                (parent_label, parent_key, parent_val),
                cs.RelationshipType.CONTAINS_MODULE,
                (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
            )

            self.import_processor.parse_imports(root_node, module_qn, language, queries)
            self._ingest_missing_import_patterns(
                root_node, module_qn, language, queries
            )
            if language == cs.SupportedLanguage.CPP:
                self._ingest_cpp_module_declarations(root_node, module_qn, file_path)
            self._ingest_all_functions(root_node, module_qn, language, queries)
            self._ingest_classes_and_methods(root_node, module_qn, language, queries)
            self._ingest_object_literal_methods(root_node, module_qn, language, queries)
            self._ingest_commonjs_exports(root_node, module_qn, language, queries)
            if language in {cs.SupportedLanguage.JS, cs.SupportedLanguage.TS}:
                self._ingest_es6_exports(root_node, module_qn, language, queries)
            self._ingest_assignment_arrow_functions(
                root_node, module_qn, language, queries
            )
            self._ingest_prototype_inheritance(root_node, module_qn, language, queries)

            return (root_node, language)

        except Exception as e:
            logger.error(ls.DEF_PARSE_FAILED.format(path=file_path, error=e))
            return None

    def process_dependencies(self, filepath: Path) -> None:
        """
        Parses a dependency file and ingests the dependencies into the graph.

        Args:
            filepath (Path): The path to the dependency file (e.g., 'pyproject.toml').
        """
        logger.info(ls.DEF_PARSING_DEPENDENCY.format(path=filepath))

        dependencies = parse_dependencies(filepath)
        for dep in dependencies:
            self._add_dependency(dep.name, dep.spec, dep.properties)

    def _add_dependency(
        self, dep_name: str, dep_spec: str, properties: dict[str, str] | None = None
    ) -> None:
        """
        Adds a single dependency node and its relationship to the project.

        Args:
            dep_name (str): The name of the dependency.
            dep_spec (str): The version specifier for the dependency.
            properties (dict[str, str] | None): Additional properties for the relationship.
        """
        if not dep_name or dep_name.lower() in cs.EXCLUDED_DEPENDENCY_NAMES:
            return

        logger.info(ls.DEF_FOUND_DEPENDENCY.format(name=dep_name, spec=dep_spec))
        self.ingestor.ensure_node_batch(
            cs.NodeLabel.EXTERNAL_PACKAGE, {cs.KEY_NAME: dep_name}
        )

        rel_properties = {cs.KEY_VERSION_SPEC: dep_spec} if dep_spec else {}
        if properties:
            rel_properties |= properties

        self.ingestor.ensure_relationship_batch(
            (cs.NodeLabel.PROJECT, cs.KEY_NAME, self.project_name),
            cs.RelationshipType.DEPENDS_ON_EXTERNAL,
            (cs.NodeLabel.EXTERNAL_PACKAGE, cs.KEY_NAME, dep_name),
            properties=rel_properties,
        )

    def _get_docstring(self, node: ASTNode) -> str | None:
        """
        Extracts the docstring from a function or class node.

        Args:
            node (ASTNode): The tree-sitter node for the function or class.

        Returns:
            str | None: The extracted docstring, or None if not found.
        """
        body_node = node.child_by_field_name(cs.FIELD_BODY)
        if not body_node or not body_node.children:
            return None
        first_statement = body_node.children[0]
        if (
            first_statement.type == cs.TS_PY_EXPRESSION_STATEMENT
            and first_statement.children[0].type == cs.TS_PY_STRING
        ):
            text = first_statement.children[0].text
            if text is not None:
                result: str = safe_decode_with_fallback(
                    first_statement.children[0]
                ).strip(cs.DOCSTRING_STRIP_CHARS)
                return result
        return None

    def _extract_decorators(self, node: ASTNode) -> list[str]:
        """
        Extracts a list of decorator names from a node.

        Args:
            node (ASTNode): The decorated node.

        Returns:
            list[str]: A list of decorator names.
        """
        return self._handler.extract_decorators(node)
