"""
This module defines the `DefinitionProcessor`, the central class for parsing source
files and ingesting code definitions (like functions, classes, and modules) into
the graph database.

It acts as an orchestrator, combining the functionality of various mixins
(`FunctionIngestMixin`, `ClassIngestMixin`, etc.) to handle different types of
code constructs. The processor is responsible for reading files, triggering the
AST parsing, processing imports, and then delegating to the appropriate mixin to
extract and ingest detailed information about each function, class, and method.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from codebase_rag.core import constants as cs
from codebase_rag.core import logs as ls
from codebase_rag.data_models.types_defs import (
    ASTNode,
    FunctionRegistryTrieProtocol,
    NodeType,
    SimpleNameLookup,
)
from codebase_rag.parsers.class_processing import ClassIngestMixin
from codebase_rag.parsers.core.utils import (
    get_parent_qualified_name,
    safe_decode_with_fallback,
)
from codebase_rag.parsers.frameworks.framework_registry import FrameworkDetectorRegistry
from codebase_rag.parsers.handlers import get_handler
from codebase_rag.parsers.languages.js_ts.ingest import JsTsIngestMixin
from codebase_rag.utils.path_utils import is_test_path, to_posix

from .dependency_parser import parse_dependencies
from .function_ingest import FunctionIngestMixin

if TYPE_CHECKING:
    from codebase_rag.data_models.types_defs import LanguageQueries
    from codebase_rag.parsers.handlers import LanguageHandler
    from codebase_rag.services import IngestorProtocol

    from .import_processor import ImportProcessor


class DefinitionProcessor(
    FunctionIngestMixin,
    ClassIngestMixin,
    JsTsIngestMixin,
):
    """
    The main processor for parsing source files and ingesting definitions.

    This class coordinates the entire definition extraction process. It is composed
    of several mixins, each responsible for a specific type of code element
    (e.g., functions, classes). It manages the file processing workflow, from
    reading the source and parsing the AST to handling imports and dispatching
    to the correct ingestion logic based on the language and code structure.

    Attributes:
        ingestor (IngestorProtocol): The service for writing data to the graph.
        repo_path (Path): The root path of the repository.
        project_name (str): The name of the project.
        function_registry (FunctionRegistryTrieProtocol): A trie for storing all found functions/classes.
        simple_name_lookup (SimpleNameLookup): A mapping from simple names to a set of FQNs.
        import_processor (ImportProcessor): The processor for handling import statements.
        module_qn_to_file_path (dict[str, Path]): A mapping from module FQNs to their file paths.
        module_qn_to_file_hash (dict[str, str]): A mapping from module FQNs to their file content hashes.
        class_inheritance (dict[str, list[str]]): A map of class inheritance relationships.
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
            ingestor (IngestorProtocol): An ingestor instance for database operations.
            repo_path (Path): The absolute path to the repository root.
            project_name (str): The name of the project.
            function_registry (FunctionRegistryTrieProtocol): A registry for storing found functions/classes.
            simple_name_lookup (SimpleNameLookup): A lookup table for simple names to qualified names.
            import_processor (ImportProcessor): The processor for handling imports.
            module_qn_to_file_path (dict[str, Path]): A mapping of module FQNs to their file paths.
        """
        super().__init__()
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.function_registry = function_registry
        self.simple_name_lookup = simple_name_lookup
        self.import_processor = import_processor
        self.module_qn_to_file_path = module_qn_to_file_path
        self.module_qn_to_file_hash: dict[str, str] = {}
        self.class_inheritance: dict[str, list[str]] = {}
        self._handler = get_handler(cs.SupportedLanguage.PYTHON)
        self._framework_registry = FrameworkDetectorRegistry(repo_path=self.repo_path)

    def process_file(
        self,
        file_path: Path,
        language: cs.SupportedLanguage,
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        structural_elements: dict[Path, str | None],
        parsed_root: ASTNode | None = None,
        source_bytes: bytes | None = None,
        source_text: str | None = None,
    ) -> tuple[ASTNode, cs.SupportedLanguage] | None:
        """
        Processes a single source file to extract and ingest its definitions.

        This is the main entry point for file processing. It handles AST parsing,
        module creation, import processing, and then calls the various ingestion
        methods for functions, classes, etc.

        Args:
            file_path (Path): The path to the file to process.
            language (cs.SupportedLanguage): The programming language of the file.
            queries (dict): A dictionary of tree-sitter queries for all supported languages.
            structural_elements (dict): A map of file/folder paths to their qualified names.
            parsed_root (ASTNode | None): A pre-parsed AST root node, if available.
            source_bytes (bytes | None): The raw source code in bytes, if available.
            source_text (str | None): The decoded source code as a string, if available.

        Returns:
            A tuple containing the parsed AST root and the language if successful, otherwise None.
        """
        if isinstance(file_path, str):
            file_path = Path(file_path)
        relative_path = file_path.relative_to(self.repo_path)
        relative_path_str = to_posix(relative_path)
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
            lang_queries = queries[language]
            if parsed_root is None:
                parser = lang_queries.get(cs.KEY_PARSER)
                if not parser:
                    logger.warning(ls.DEF_NO_PARSER.format(language=language))
                    return None
                if source_bytes is None:
                    source_bytes = file_path.read_bytes()
                tree = parser.parse(source_bytes)
                root_node = tree.root_node
            else:
                root_node = parsed_root
            if source_bytes is None:
                source_bytes = file_path.read_bytes()
            if source_text is None:
                source_text = self._safe_decode_source(source_bytes)

            file_hash = hashlib.sha256(source_bytes).hexdigest() if source_bytes else ""

            module_qn = cs.SEPARATOR_DOT.join(
                [self.project_name] + list(relative_path.with_suffix("").parts)
            )
            if file_path.name in (cs.INIT_PY, cs.MOD_RS):
                module_qn = cs.SEPARATOR_DOT.join(
                    [self.project_name] + list(relative_path.parent.parts)
                )
            self.module_qn_to_file_path[module_qn] = file_path
            if file_hash:
                self.module_qn_to_file_hash[module_qn] = file_hash

            parent_qn = get_parent_qualified_name(module_qn)
            namespace = parent_qn
            module_props = {
                cs.KEY_QUALIFIED_NAME: module_qn,
                cs.KEY_NAME: file_path.name,
                cs.KEY_PATH: relative_path_str,
                cs.KEY_LANGUAGE: language.value,
                cs.KEY_MODULE_QN: module_qn,
                cs.KEY_REPO_REL_PATH: relative_path_str,
                cs.KEY_ABS_PATH: file_path.resolve().as_posix(),
                cs.KEY_SYMBOL_KIND: cs.NodeLabel.MODULE.value.lower(),
                cs.KEY_PARENT_QN: parent_qn or self.project_name,
                cs.KEY_IS_TEST: is_test_path(relative_path),
            }
            if namespace:
                module_props[cs.KEY_NAMESPACE] = namespace
                module_props[cs.KEY_PACKAGE] = namespace
            if file_hash:
                module_props[cs.KEY_FILE_HASH] = file_hash

            if self._framework_metadata_enabled():
                framework_type, framework_metadata = self._detect_framework_metadata(
                    language, source_text, root_node
                )
                if framework_type:
                    module_props[cs.KEY_FRAMEWORK] = framework_type
                if framework_metadata:
                    module_props[cs.KEY_FRAMEWORK_METADATA] = json.dumps(
                        framework_metadata, ensure_ascii=False
                    )

            self.ingestor.ensure_node_batch(cs.NodeLabel.MODULE, module_props)

            parent_rel_path = relative_path.parent
            parent_container_qn = structural_elements.get(parent_rel_path)
            parent_label, parent_key, parent_val = (
                (cs.NodeLabel.PACKAGE, cs.KEY_QUALIFIED_NAME, parent_container_qn)
                if parent_container_qn
                else (
                    (cs.NodeLabel.FOLDER, cs.KEY_PATH, to_posix(parent_rel_path))
                    if parent_rel_path != Path("..")
                    else (cs.NodeLabel.PROJECT, cs.KEY_NAME, self.project_name)
                )
            )
            self.ingestor.ensure_relationship_batch(
                (parent_label, parent_key, parent_val),
                cs.RelationshipType.CONTAINS_MODULE,
                (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
            )
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.FILE, cs.KEY_PATH, relative_path_str),
                cs.RelationshipType.CONTAINS_MODULE,
                (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
            )

            self.import_processor.parse_imports(root_node, module_qn, language, queries)
            safe_roots = self._get_error_tolerant_roots(root_node)
            for safe_root in safe_roots:
                self._ingest_missing_import_patterns(
                    safe_root, module_qn, language, queries
                )
                if language == cs.SupportedLanguage.CPP:
                    self._ingest_cpp_module_declarations(
                        safe_root, module_qn, file_path
                    )
                self._ingest_all_functions(safe_root, module_qn, language, queries)
                self._ingest_classes_and_methods(
                    safe_root, module_qn, language, queries
                )
                if language == cs.SupportedLanguage.CPP and source_text:
                    self._ingest_cpp_module_class_fallback(
                        source_text,
                        module_qn,
                        file_path,
                        language,
                    )
                self._ingest_object_literal_methods(
                    safe_root, module_qn, language, queries
                )
                self._ingest_commonjs_exports(safe_root, module_qn, language, queries)
                self._ingest_es6_exports(safe_root, module_qn, language, queries)
                self._ingest_assignment_arrow_functions(
                    safe_root, module_qn, language, queries
                )
                self._ingest_prototype_inheritance(
                    safe_root, module_qn, language, queries
                )

            return (root_node, language)

        except Exception as e:
            logger.error(ls.DEF_PARSE_FAILED.format(path=file_path, error=e))
            return None

    @staticmethod
    def _safe_decode_source(source_bytes: bytes) -> str:
        """
        Safely decodes source bytes into text using UTF-8 with a fallback.

        Args:
            source_bytes (bytes): The raw bytes read from a source file.

        Returns:
            The decoded string, with invalid characters ignored if needed.
        """
        try:
            return source_bytes.decode(cs.ENCODING_UTF8)
        except Exception:
            return source_bytes.decode(cs.ENCODING_UTF8, errors="ignore")

    def _get_error_tolerant_roots(self, root_node: ASTNode) -> list[ASTNode]:
        """
        Returns a list of safe AST roots by bypassing syntax error nodes if present.

        Args:
            root_node (ASTNode): The original root node.

        Returns:
            list[ASTNode]: A list containing the root node if clean, or its children if it has errors.
        """
        if not self._node_has_error(root_node):
            return [root_node]
        safe_roots: list[ASTNode] = [root_node]
        for child in root_node.children:
            safe_roots.extend(self._flatten_error_nodes(child))
        seen: set[int] = set()
        unique_roots: list[ASTNode] = []
        for node in safe_roots:
            node_id = id(node)
            if node_id in seen:
                continue
            seen.add(node_id)
            unique_roots.append(node)
        return unique_roots

    @staticmethod
    def _node_has_error(node: ASTNode) -> bool:
        """
        Checks if a node or any of its descendants is an ERROR node.

        Args:
            node (ASTNode): The node to check.

        Returns:
            True if an ERROR node is found in the subtree, False otherwise.
        """
        has_error = getattr(node, "has_error", None)
        if isinstance(has_error, bool):
            return has_error
        return any(child.type == "ERROR" for child in node.children)

    def _flatten_error_nodes(self, node: ASTNode) -> list[ASTNode]:
        """
        Recursively traverses down from an ERROR node to find valid, non-error sub-nodes.

        Args:
            node (ASTNode): The starting node, typically an ERROR node.

        Returns:
            A list of safe, non-error descendant nodes.
        """
        if node.type != "ERROR":
            return [node]
        safe_nodes: list[ASTNode] = []
        for child in node.children:
            safe_nodes.extend(self._flatten_error_nodes(child))
        return safe_nodes

    @staticmethod
    def _framework_metadata_enabled() -> bool:
        """
        Checks if file-level framework metadata detection is enabled.

        Returns:
            True if `CODEGRAPH_FILE_LEVEL_FRAMEWORK_METADATA` is truthy or unset.
        """
        raw_value = os.getenv("CODEGRAPH_FILE_LEVEL_FRAMEWORK_METADATA")
        if raw_value is None:
            return True
        return raw_value.lower() in {"1", "true", "yes"}

    def _detect_framework_metadata(
        self,
        language: cs.SupportedLanguage,
        source_text: str,
        module_node: ASTNode | None,
    ) -> tuple[str | None, dict | None]:
        """
        Detects framework usage and extracts metadata for supported languages.

        Args:
            language (cs.SupportedLanguage): The language of the file.
            source_text (str): The source code.
            module_node (ASTNode | None): Parsed AST root for language-specific metadata.

        Returns:
            tuple[str | None, dict | None]: A tuple of (framework_name, metadata_dict) or (None, None).
        """
        try:
            result = self._framework_registry.detect_for_language(
                language, source_text, module_node
            )
        except Exception:
            return (None, None)

        return (result.framework_type, result.metadata)

    def _ingest_cpp_module_class_fallback(
        self,
        source_text: str,
        module_qn: str,
        file_path: Path,
        language: cs.SupportedLanguage,
    ) -> None:
        """
        A fallback mechanism to ingest C++ classes and enums using regex.

        This is used for header files or when the tree-sitter parser fails to
        capture all definitions in C++ module files.

        Args:
            source_text (str): The source code of the file.
            module_qn (str): The qualified name of the module.
            file_path (Path): The path to the C++ file.
            language (cs.SupportedLanguage): The language enum.
        """
        if file_path.suffix not in cs.CPP_MODULE_EXTENSIONS and not any(
            part in cs.CPP_MODULE_PATH_MARKERS for part in file_path.parts
        ):
            return

        cleaned = re.sub(r"/\*.*?\*/", "", source_text, flags=re.S)
        cleaned_lines = [re.sub(r"//.*", "", line) for line in cleaned.splitlines()]
        cleaned = "\n".join(cleaned_lines)

        class_names = set(
            match.group(2)
            for match in re.finditer(r"\b(class|struct)\s+([A-Za-z_]\w*)", cleaned)
        )
        enum_names = set(
            match.group(1)
            for match in re.finditer(r"\benum\s+class\s+([A-Za-z_]\w*)", cleaned)
        )

        module_parts = module_qn.split(cs.SEPARATOR_DOT)
        if any(part in cs.CPP_MODULE_PATH_MARKERS for part in module_parts):
            base_qn = cs.SEPARATOR_DOT.join([module_parts[0], module_parts[-1]])
        else:
            base_qn = module_qn

        namespace = (
            module_qn.rsplit(cs.SEPARATOR_DOT, 1)[0]
            if cs.SEPARATOR_DOT in module_qn
            else None
        )
        file_hash = self.module_qn_to_file_hash.get(module_qn)
        relative_path = file_path.relative_to(self.repo_path).as_posix()
        abs_path = file_path.resolve().as_posix()
        is_test_file = is_test_path(file_path.relative_to(self.repo_path))

        for class_name in sorted(class_names):
            class_qn = f"{base_qn}.{class_name}"
            if class_qn in self.function_registry:
                continue
            class_props = {
                cs.KEY_QUALIFIED_NAME: class_qn,
                cs.KEY_NAME: class_name,
                cs.KEY_START_LINE: 1,
                cs.KEY_END_LINE: 1,
                cs.KEY_DOCSTRING: None,
                cs.KEY_IS_EXPORTED: "export" in cleaned,
                cs.KEY_LANGUAGE: language.value,
                cs.KEY_MODULE_QN: module_qn,
                cs.KEY_SYMBOL_KIND: cs.NodeLabel.CLASS.value.lower(),
                cs.KEY_PARENT_QN: module_qn,
                cs.KEY_PATH: relative_path,
                cs.KEY_REPO_REL_PATH: relative_path,
                cs.KEY_ABS_PATH: abs_path,
                cs.KEY_IS_TEST: is_test_file,
            }
            if namespace:
                class_props[cs.KEY_NAMESPACE] = namespace
                class_props[cs.KEY_PACKAGE] = namespace
            if file_hash:
                class_props[cs.KEY_FILE_HASH] = file_hash
            self.ingestor.ensure_node_batch(cs.NodeLabel.CLASS, class_props)
            self.function_registry[class_qn] = NodeType.CLASS
            self.simple_name_lookup[class_name].add(class_qn)
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                cs.RelationshipType.DEFINES,
                (cs.NodeLabel.CLASS, cs.KEY_QUALIFIED_NAME, class_qn),
            )

        for enum_name in sorted(enum_names):
            enum_qn = f"{base_qn}.{enum_name}"
            if enum_qn in self.function_registry:
                continue
            enum_props = {
                cs.KEY_QUALIFIED_NAME: enum_qn,
                cs.KEY_NAME: enum_name,
                cs.KEY_START_LINE: 1,
                cs.KEY_END_LINE: 1,
                cs.KEY_DOCSTRING: None,
                cs.KEY_IS_EXPORTED: "export" in cleaned,
                cs.KEY_LANGUAGE: language.value,
                cs.KEY_MODULE_QN: module_qn,
                cs.KEY_SYMBOL_KIND: cs.NodeLabel.ENUM.value.lower(),
                cs.KEY_PARENT_QN: module_qn,
                cs.KEY_PATH: relative_path,
                cs.KEY_REPO_REL_PATH: relative_path,
                cs.KEY_ABS_PATH: abs_path,
                cs.KEY_IS_TEST: is_test_file,
            }
            if namespace:
                enum_props[cs.KEY_NAMESPACE] = namespace
                enum_props[cs.KEY_PACKAGE] = namespace
            if file_hash:
                enum_props[cs.KEY_FILE_HASH] = file_hash
            self.ingestor.ensure_node_batch(cs.NodeLabel.ENUM, enum_props)
            self.function_registry[enum_qn] = NodeType.ENUM
            self.simple_name_lookup[enum_name].add(enum_qn)
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn),
                cs.RelationshipType.DEFINES,
                (cs.NodeLabel.ENUM, cs.KEY_QUALIFIED_NAME, enum_qn),
            )

    def process_dependencies(self, filepath: Path) -> None:
        """
        Parses a dependency file (e.g., package.json, requirements.txt) and ingests the dependencies.

        Args:
            filepath (Path): The path to the dependency file.
        """
        logger.info(ls.DEF_PARSING_DEPENDENCY.format(path=filepath))

        dependencies = parse_dependencies(filepath)
        for dep in dependencies:
            self._add_dependency(dep.name, dep.spec, dep.properties)

    def _add_dependency(
        self, dep_name: str, dep_spec: str, properties: dict[str, str] | None = None
    ) -> None:
        """
        Ingests a single dependency node and links it to the main project node.

        Args:
            dep_name (str): The name of the dependency package.
            dep_spec (str): The version specification for the dependency.
            properties (dict[str, str] | None): Additional properties for the dependency relationship.
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
        Extracts the docstring from a function or class definition node.

        This implementation is specific to Python's docstring convention.

        Args:
            node (ASTNode): The definition node (function or class).

        Returns:
            The extracted docstring as a string, or None if not found.
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
        Extracts decorator names from a definition node by delegating to the language handler.

        Args:
            node (ASTNode): The definition node.

        Returns:
            A list of decorator names as strings.
        """
        return self._handler.extract_decorators(node)
