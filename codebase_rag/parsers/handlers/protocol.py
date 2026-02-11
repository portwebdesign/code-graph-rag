from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from codebase_rag.data_models.types_defs import ASTNode
    from codebase_rag.infrastructure.language_spec import LanguageSpec


class LanguageHandler(Protocol):
    """
    Protocol defining the interface for language-specific AST handlers.

    Handlers are responsible for extracting information from AST nodes, such as
    function names, qualified names, and other language-specific details.
    """

    def is_inside_method_with_object_literals(self, node: ASTNode) -> bool: ...

    def is_class_method(self, node: ASTNode) -> bool: ...

    def is_export_inside_function(self, node: ASTNode) -> bool: ...

    def extract_function_name(self, node: ASTNode) -> str | None: ...

    def build_function_qualified_name(
        self,
        node: ASTNode,
        module_qn: str,
        func_name: str,
        lang_config: LanguageSpec | None,
        file_path: Path | None,
        repo_path: Path,
        project_name: str,
    ) -> str: ...

    def is_function_exported(self, node: ASTNode) -> bool: ...

    def should_process_as_impl_block(self, node: ASTNode) -> bool: ...

    def extract_impl_target(self, node: ASTNode) -> str | None: ...

    def build_method_qualified_name(
        self,
        class_qn: str,
        method_name: str,
        method_node: ASTNode,
    ) -> str: ...

    def extract_base_class_name(self, base_node: ASTNode) -> str | None: ...

    def build_nested_function_qn(
        self,
        func_node: ASTNode,
        module_qn: str,
        func_name: str,
        lang_config: LanguageSpec,
    ) -> str | None: ...

    def extract_decorators(self, node: ASTNode) -> list[str]: ...
