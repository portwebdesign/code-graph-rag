from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from codebase_rag.core import constants as cs
from codebase_rag.core import logs as ls
from codebase_rag.data_models.types_defs import (
    ASTNode,
    FunctionRegistryTrieProtocol,
    LanguageQueries,
    SimpleNameLookup,
)
from codebase_rag.parsers.pipeline.import_processor import ImportProcessor

from .method_resolver import JavaMethodResolverMixin
from .type_resolver import JavaTypeResolverMixin
from .utils import find_package_start_index
from .variable_analyzer import JavaVariableAnalyzerMixin

if TYPE_CHECKING:
    from codebase_rag.parsers.core.factory import ASTCacheProtocol


class JavaTypeInferenceEngine(
    JavaTypeResolverMixin,
    JavaVariableAnalyzerMixin,
    JavaMethodResolverMixin,
):
    """
    Main engine for inferring types in Java ASTs.

    Combines type resolution, variable analysis, and method resolution mixins.
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
        Build a reverse lookup map from simple class names to potential module QNs.

        Returns:
            Dictionary mapping class names (and suffixes) to lists of module QNs.
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
        Build a map of variable names to their resolved types within a scope.

        Args:
            scope_node: The AST node defining the scope.
            module_qn: The current module qualified name.

        Returns:
            Dictionary mapping variable names to their resolve type QNs.
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
        Resolve a Java method call AST node.

        Args:
            call_node: The method invocation node.
            local_var_types: Local variable types map.
            module_qn: Current module qualified name.

        Returns:
            A tuple (resolved_type, resolved_qn), or None if unresolved.
        """
        return self._do_resolve_java_method_call(call_node, local_var_types, module_qn)

    def _find_containing_java_class(self, node: ASTNode) -> ASTNode | None:
        """
        Find the AST node of the containing class for a given node.

        Args:
            node: The starting AST node.

        Returns:
            The class declaration AST node, or None if not found.
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
