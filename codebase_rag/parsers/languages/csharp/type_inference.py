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


class CSharpTypeInferenceEngine:
    """C# local variable type inference."""

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
            self._collect_declarations(caller_node, local_var_types, module_qn)
        except Exception as exc:
            logger.debug(f"C# type inference failed: {exc}")
        return local_var_types

    def _collect_declarations(
        self,
        root: TreeSitterNodeProtocol,
        local_var_types: dict[str, str],
        module_qn: str,
    ) -> None:
        stack: list[TreeSitterNodeProtocol] = [root]
        while stack:
            current = stack.pop()
            if current.type == "local_declaration_statement":
                self._process_local_declaration(current, local_var_types, module_qn)
            if current.type == "assignment_expression":
                self._process_assignment(current, local_var_types, module_qn)
            stack.extend(reversed(current.children))

    def _process_local_declaration(
        self,
        node: TreeSitterNodeProtocol,
        local_var_types: dict[str, str],
        module_qn: str,
    ) -> None:
        declaration = node.child_by_field_name("declaration")
        if not declaration:
            return
        type_node = declaration.child_by_field_name("type")
        type_name = self._type_to_string(type_node, module_qn)

        for child in declaration.children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                value_node = child.child_by_field_name("value")
                name = safe_decode_text(name_node) if name_node else None
                if not name:
                    continue
                inferred = type_name
                if not inferred and value_node:
                    inferred = self._infer_from_value(value_node, module_qn)
                if inferred:
                    local_var_types[name] = inferred

    def _process_assignment(
        self,
        node: TreeSitterNodeProtocol,
        local_var_types: dict[str, str],
        module_qn: str,
    ) -> None:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if not left or not right:
            return
        if left.type != "identifier":
            return
        name = safe_decode_text(left)
        if not name:
            return
        inferred = self._infer_from_value(right, module_qn)
        if inferred:
            local_var_types[name] = inferred

    def _type_to_string(
        self, node: TreeSitterNodeProtocol | None, module_qn: str
    ) -> str | None:
        if not node:
            return None
        if decoded := safe_decode_text(node):
            cleaned = decoded.strip()
            if cleaned == "var":
                return None
            return self._resolve_csharp_type(cleaned, module_qn)
        return None

    def _resolve_csharp_type(self, type_name: str, module_qn: str) -> str:
        cleaned = type_name.strip()
        if cs.SEPARATOR_DOT in cleaned:
            parts = cleaned.split(cs.SEPARATOR_DOT, 1)
            if module_qn in self.import_processor.import_mapping:
                import_map = self.import_processor.import_mapping[module_qn]
                if parts[0] in import_map:
                    return f"{import_map[parts[0]]}{cs.SEPARATOR_DOT}{parts[1]}"
            return cleaned
        local_qn = f"{module_qn}{cs.SEPARATOR_DOT}{cleaned}"
        if local_qn in self.function_registry:
            if self.function_registry[local_qn] in {NodeType.CLASS, NodeType.INTERFACE}:
                return local_qn
        return cleaned

    def _infer_from_value(
        self, node: TreeSitterNodeProtocol, module_qn: str
    ) -> str | None:
        node_type = node.type
        if node_type == "string_literal":
            return "string"
        if node_type == "character_literal":
            return "char"
        if node_type in {"integer_literal", "real_literal"}:
            return "number"
        if node_type in {"true", "false", "boolean_literal"}:
            return "bool"
        if node_type in {"null", "null_literal"}:
            return "null"
        if node_type == "object_creation_expression":
            type_node = node.child_by_field_name("type")
            return self._type_to_string(type_node, module_qn)
        if node_type == "array_creation_expression":
            type_node = node.child_by_field_name("type")
            if inferred := self._type_to_string(type_node, module_qn):
                return f"{inferred}[]"
        if node_type == "default_expression":
            type_node = node.child_by_field_name("type")
            return self._type_to_string(type_node, module_qn)
        if node_type == "invocation_expression":
            func_node = node.child_by_field_name("function")
            if func_node and (decoded := safe_decode_text(func_node)):
                return decoded.split(cs.SEPARATOR_DOT)[-1]
        return None
