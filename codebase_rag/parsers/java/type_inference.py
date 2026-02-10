"""
This module defines the `JavaTypeInferenceEngine`, the main class for handling
Java-specific type inference and method resolution.

It combines several mixins to provide a comprehensive solution for understanding
Java's type system and resolving method calls, which is crucial for building an
accurate call graph.

Key functionalities:
-   `JavaTypeResolverMixin`: For resolving type names to their FQNs.
-   `JavaVariableAnalyzerMixin`: For analyzing variable declarations and assignments
    to infer their types.
-   `JavaMethodResolverMixin`: For resolving method calls, including handling
    inheritance and interfaces.

The engine builds and uses several internal data structures, such as a map from
class FQNs to the modules they are defined in, to speed up lookups.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from codebase_rag.data_models.types_defs import (
    ASTNode,
    FunctionRegistryTrieProtocol,
    LanguageQueries,
    SimpleNameLookup,
)

from ... import constants as cs
from ... import logs as ls
from ..import_processor import ImportProcessor
from .method_resolver import JavaMethodResolverMixin
from .type_resolver import JavaTypeResolverMixin
from .utils import find_package_start_index
from .variable_analyzer import JavaVariableAnalyzerMixin

if TYPE_CHECKING:
    from ..factory import ASTCacheProtocol


class JavaTypeInferenceEngine(
    JavaTypeResolverMixin,
    JavaVariableAnalyzerMixin,
    JavaMethodResolverMixin,
):
    """
    Orchestrates type inference and method resolution for Java code.
    """

    def __init__(
        self,
        import_processor: ImportProcessor,
        function_registry: FunctionRegistryTrieProtocol,
        repo_path: Path,
        project_name: str,
        ast_cache: "ASTCacheProtocol",
        queries: dict[cs.SupportedLanguage, LanguageQueries],
        module_qn_to_file_path: dict[str, Path],
        class_inheritance: dict[str, list[str]],
        simple_name_lookup: SimpleNameLookup,
    ):
        """
        Initializes the JavaTypeInferenceEngine.

        Args:
            import_processor (ImportProcessor): The shared import processor.
            function_registry (FunctionRegistryTrieProtocol): The shared function registry.
            repo_path (Path): The root path of the repository.
            project_name (str): The name of the project.
            ast_cache (ASTCacheProtocol): The shared AST cache.
            queries (dict): A dictionary of tree-sitter queries.
            module_qn_to_file_path (dict): A map from module FQNs to file paths.
            class_inheritance (dict): A map of classes to their parent classes.
            simple_name_lookup (SimpleNameLookup): A map from simple names to FQNs.
        """
        self.import_processor = import_processor
        self.function_registry = function_registry
        self.repo_path = repo_path
        self.project_name = project_name
        self.ast_cache = ast_cache
        self.queries = queries
        self.module_qn_to_file_path = module_qn_to_file_path
        self.class_inheritance = class_inheritance
        self.simple_name_lookup = simple_name_lookup

        self._lookup_cache: dict[str, str | None] = {}
        self._lookup_in_progress: set[str] = set()

        self._fqn_to_module_qn: dict[str, list[str]] = self._build_fqn_lookup_map()

    def _build_fqn_lookup_map(self) -> dict[str, list[str]]:
        """
        Builds a reverse map from a Java FQN to the internal module FQN(s)
        where it might be defined.

        Returns:
            dict[str, list[str]]: The lookup map.
        """
        fqn_map: dict[str, list[str]] = {}

        def _add_mapping(key: str, value: str) -> None:
            modules = fqn_map.setdefault(key, [])
            if value not in modules:
                modules.append(value)

        for module_qn in self.module_qn_to_file_path.keys():
            parts = module_qn.split(cs.SEPARATOR_DOT)
            if package_start_idx := find_package_start_index(parts):
                if simple_class_name := cs.SEPARATOR_DOT.join(
                    parts[package_start_idx:]
                ):
                    _add_mapping(simple_class_name, module_qn)

                    class_parts = simple_class_name.split(cs.SEPARATOR_DOT)
                    for j in range(1, len(class_parts)):
                        suffix = cs.SEPARATOR_DOT.join(class_parts[j:])
                        _add_mapping(suffix, module_qn)

        return fqn_map

    def build_variable_type_map(
        self, scope_node: ASTNode, module_qn: str
    ) -> dict[str, str]:
        """
        Builds a map of variable names to their inferred types for a given scope.

        Args:
            scope_node (ASTNode): The AST node representing the scope (e.g., a method body).
            module_qn (str): The qualified name of the module.

        Returns:
            dict[str, str]: A dictionary mapping variable names to their inferred type FQNs.
        """
        local_var_types: dict[str, str] = {}

        try:
            self._collect_all_variable_types(scope_node, local_var_types, module_qn)
            logger.debug(ls.JAVA_VAR_TYPE_MAP_BUILT.format(count=len(local_var_types)))

        except Exception as e:
            logger.error(ls.JAVA_VAR_TYPE_MAP_FAILED.format(error=e))

        return local_var_types

    def resolve_java_method_call(
        self, call_node: ASTNode, local_var_types: dict[str, str], module_qn: str
    ) -> tuple[str, str] | None:
        """
        Resolves a Java method call to its fully qualified name.

        Args:
            call_node (ASTNode): The `method_invocation` AST node.
            local_var_types (dict[str, str]): A map of local variables to their types.
            module_qn (str): The FQN of the current module.

        Returns:
            tuple[str, str] | None: A tuple of (node_type, fqn) if resolved, else None.
        """
        return self._do_resolve_java_method_call(call_node, local_var_types, module_qn)

    def _find_containing_java_class(self, node: ASTNode) -> ASTNode | None:
        """
        Finds the containing class for a given node by traversing up the AST.

        Args:
            node (ASTNode): The starting node.

        Returns:
            ASTNode | None: The AST node of the containing class, or None if not found.
        """
        current = node.parent
        while current:
            match current.type:
                case (
                    cs.TS_CLASS_DECLARATION
                    | cs.TS_INTERFACE_DECLARATION
                    | cs.TS_ENUM_DECLARATION
                    | cs.TS_RECORD_DECLARATION
                ):
                    return current
                case _:
                    pass
            current = current.parent
        return None
