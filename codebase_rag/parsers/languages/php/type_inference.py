from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from codebase_rag.core import constants as cs
from codebase_rag.data_models.types_defs import (
    FunctionRegistryTrieProtocol,
    NodeType,
    TreeSitterNodeProtocol,
)
from codebase_rag.parsers.core.utils import safe_decode_text

if TYPE_CHECKING:
    from codebase_rag.parsers.pipeline.import_processor import ImportProcessor


class PhpTypeInferenceEngine:
    """PHP local variable type inference."""

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
            self._collect_parameters(caller_node, local_var_types, module_qn)
            self._collect_assignments(caller_node, local_var_types, module_qn)
        except Exception as exc:
            logger.debug(f"PHP type inference failed: {exc}")
        return local_var_types

    def _collect_parameters(
        self,
        root: TreeSitterNodeProtocol,
        local_var_types: dict[str, str],
        module_qn: str,
    ) -> None:
        stack: list[TreeSitterNodeProtocol] = [root]
        while stack:
            current = stack.pop()
            if current.type == "function_definition":
                get_child = getattr(current, "child_by_field_name", None)
                if get_child is None:
                    stack.extend(reversed(current.children))
                    continue
                params = get_child("parameters")
                if params:
                    for child in params.children:
                        if child.type == "simple_parameter":
                            get_param_child = getattr(
                                child, "child_by_field_name", None
                            )
                            if get_param_child is None:
                                continue
                            name_node = get_param_child("name")
                            type_node = get_param_child("type")
                            name = safe_decode_text(name_node) if name_node else None
                            type_name = self._type_to_string(type_node, module_qn)
                            if name and type_name:
                                local_var_types[name.lstrip("$")] = type_name
            stack.extend(reversed(current.children))

    def _collect_assignments(
        self,
        root: TreeSitterNodeProtocol,
        local_var_types: dict[str, str],
        module_qn: str,
    ) -> None:
        stack: list[TreeSitterNodeProtocol] = [root]
        while stack:
            current = stack.pop()
            if current.type == "assignment_expression":
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
        if left.type != "variable_name":
            return
        name = safe_decode_text(left)
        if not name:
            return
        inferred = self._infer_from_value(right, module_qn)
        if inferred:
            local_var_types[name.lstrip("$")] = inferred

    def _type_to_string(
        self, node: TreeSitterNodeProtocol | None, module_qn: str
    ) -> str | None:
        if not node:
            return None
        if decoded := safe_decode_text(node):
            return self._resolve_php_type(decoded.strip(), module_qn)
        return None

    def _resolve_php_type(self, type_name: str, module_qn: str) -> str:
        cleaned = type_name.strip()
        if cleaned.startswith("?"):
            cleaned = cleaned[1:]
        cleaned = cleaned.lstrip("\\")
        if cleaned in {"int", "string", "float", "bool", "array", "callable", "mixed"}:
            return cleaned
        if cleaned in self.import_processor.import_mapping.get(module_qn, {}):
            return self.import_processor.import_mapping[module_qn][cleaned]
        local_qn = f"{module_qn}{cs.SEPARATOR_DOT}{cleaned}"
        if local_qn in self.function_registry:
            if self.function_registry[local_qn] in {NodeType.CLASS, NodeType.TYPE}:
                return local_qn
        return cleaned

    def _infer_from_value(
        self, node: TreeSitterNodeProtocol, module_qn: str
    ) -> str | None:
        node_type = node.type
        if node_type == "string":
            return "string"
        if node_type in {"integer", "float"}:
            return "number"
        if node_type in {"true", "false", "boolean"}:
            return "bool"
        if node_type in {"null", "null_literal"}:
            return "null"
        if node_type == "array_creation_expression":
            return "array"
        if node_type == "new_expression":
            get_child = getattr(node, "child_by_field_name", None)
            if get_child is None:
                return None
            class_node = get_child("class") or get_child("name")
            if class_node and (decoded := safe_decode_text(class_node)):
                return self._resolve_php_type(decoded, module_qn)
        if node_type == "qualified_name":
            if decoded := safe_decode_text(node):
                return self._resolve_php_type(decoded, module_qn)
        if node_type == "call_expression":
            get_child = getattr(node, "child_by_field_name", None)
            if get_child is None:
                return None
            func_node = get_child("function")
            if func_node and (decoded := safe_decode_text(func_node)):
                return decoded.split(cs.SEPARATOR_DOT)[-1]
        return None
