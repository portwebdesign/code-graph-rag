from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import (
    FunctionRegistryTrieProtocol,
    NodeType,
    TreeSitterNodeProtocol,
)

from ..utils import safe_decode_text

if TYPE_CHECKING:
    from ..import_processor import ImportProcessor


class RubyTypeInferenceEngine:
    """Ruby local variable type inference."""

    def __init__(
        self,
        import_processor: ImportProcessor,
        function_registry: FunctionRegistryTrieProtocol,
        project_name: str,
    ) -> None:
        self.import_processor = import_processor
        self.function_registry = function_registry
        self.project_name = project_name

    def build_local_variable_type_map(
        self, caller_node: TreeSitterNodeProtocol, module_qn: str
    ) -> dict[str, str]:
        local_var_types: dict[str, str] = {}
        try:
            self._collect_assignments(caller_node, local_var_types, module_qn)
        except Exception as exc:
            logger.debug(f"Ruby type inference failed: {exc}")
        return local_var_types

    def _collect_assignments(
        self,
        root: TreeSitterNodeProtocol,
        local_var_types: dict[str, str],
        module_qn: str,
    ) -> None:
        stack: list[TreeSitterNodeProtocol] = [root]
        while stack:
            current = stack.pop()
            if current.type in {"assignment", "multiple_assignment"}:
                self._process_assignment(current, local_var_types, module_qn)
            stack.extend(reversed(current.children))

    def _process_assignment(
        self,
        node: TreeSitterNodeProtocol,
        local_var_types: dict[str, str],
        module_qn: str,
    ) -> None:
        get_child = getattr(node, "child_by_field_name", None)
        if get_child is None:
            return
        left = get_child("left")
        right = get_child("right")
        if not left or not right:
            return
        names = self._extract_names(left)
        values = self._extract_values(right)
        for index, name in enumerate(names):
            if index >= len(values):
                continue
            inferred = self._infer_from_value(values[index], module_qn)
            if inferred:
                local_var_types[name] = inferred

    def _extract_names(self, node: TreeSitterNodeProtocol) -> list[str]:
        if node.type == "identifier":
            if decoded := safe_decode_text(node):
                return [decoded]
        names: list[str] = []
        for child in node.children:
            if child.type == "identifier":
                if decoded := safe_decode_text(child):
                    names.append(decoded)
        return names

    def _extract_values(
        self, node: TreeSitterNodeProtocol
    ) -> list[TreeSitterNodeProtocol]:
        if node.type == "array":
            return [
                child
                for child in node.children
                if child.type not in cs.PUNCTUATION_TYPES
            ]
        return [node]

    def _resolve_ruby_type(self, type_name: str, module_qn: str) -> str:
        cleaned = type_name.strip()
        if module_qn in self.import_processor.import_mapping:
            import_map = self.import_processor.import_mapping[module_qn]
            if cleaned in import_map:
                return import_map[cleaned]
        local_qn = f"{module_qn}{cs.SEPARATOR_DOT}{cleaned}"
        if local_qn in self.function_registry:
            if self.function_registry[local_qn] in {NodeType.CLASS, NodeType.MODULE}:
                return local_qn
        return cleaned

    def _infer_from_value(
        self, node: TreeSitterNodeProtocol, module_qn: str
    ) -> str | None:
        node_type = node.type
        if node_type == "string":
            return "String"
        if node_type == "integer":
            return "Integer"
        if node_type == "float":
            return "Float"
        if node_type == "symbol":
            return "Symbol"
        if node_type == "array":
            return "Array"
        if node_type == "hash":
            return "Hash"
        if node_type in {"true", "false"}:
            return "Boolean"
        if node_type in {"nil", "nil_literal"}:
            return "NilClass"
        if node_type in {"call", "method_call"}:
            const_name = self._extract_constructor_name(node)
            if const_name:
                return self._resolve_ruby_type(const_name, module_qn)
        return None

    def _extract_constructor_name(self, node: TreeSitterNodeProtocol) -> str | None:
        children = node.children
        if not children:
            return None
        text = safe_decode_text(node)
        if text and text.endswith(".new"):
            return text.split(".new")[0]
        for child in children:
            if child.type == "constant":
                if decoded := safe_decode_text(child):
                    return decoded
        return None
