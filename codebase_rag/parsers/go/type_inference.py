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


class GoTypeInferenceEngine:
    """Go local variable type inference."""

    def __init__(
        self,
        import_processor: ImportProcessor,
        function_registry: FunctionRegistryTrieProtocol,
        project_name: str,
    ) -> None:
        self.import_processor = import_processor
        self.function_registry = function_registry
        self.project_name = project_name

    def _get_child_by_field_name(
        self, node: TreeSitterNodeProtocol, field_name: str
    ) -> TreeSitterNodeProtocol | None:
        get_child = getattr(node, "child_by_field_name", None)
        if not get_child:
            return None
        return get_child(field_name)

    def build_local_variable_type_map(
        self, caller_node: TreeSitterNodeProtocol, module_qn: str
    ) -> dict[str, str]:
        local_var_types: dict[str, str] = {}
        try:
            self._collect_parameter_types(caller_node, local_var_types, module_qn)
            self._traverse_assignments(caller_node, local_var_types, module_qn)
        except Exception as exc:
            logger.debug(f"Go type inference failed: {exc}")
        return local_var_types

    def _collect_parameter_types(
        self,
        root: TreeSitterNodeProtocol,
        local_var_types: dict[str, str],
        module_qn: str,
    ) -> None:
        stack: list[TreeSitterNodeProtocol] = [root]
        while stack:
            current = stack.pop()
            if current.type == cs.TS_GO_FUNCTION_DECLARATION:
                params = self._get_child_by_field_name(current, "parameters")
                if params:
                    for child in params.children:
                        if child.type == "parameter_declaration":
                            names = self._extract_identifier_list(
                                self._get_child_by_field_name(child, "name") or child
                            )
                            type_node = self._get_child_by_field_name(child, "type")
                            type_name = self._type_to_string(type_node, module_qn)
                            if type_name:
                                for name in names:
                                    local_var_types[name] = type_name
            stack.extend(reversed(current.children))

    def _traverse_assignments(
        self,
        root: TreeSitterNodeProtocol,
        local_var_types: dict[str, str],
        module_qn: str,
    ) -> None:
        stack: list[TreeSitterNodeProtocol] = [root]
        while stack:
            current = stack.pop()
            if current.type in {
                "short_var_declaration",
                "var_declaration",
                "assignment_statement",
            }:
                self._process_assignment(current, local_var_types, module_qn)
            stack.extend(reversed(current.children))

    def _process_assignment(
        self,
        node: TreeSitterNodeProtocol,
        local_var_types: dict[str, str],
        module_qn: str,
    ) -> None:
        if node.type == "var_declaration":
            for child in node.children:
                if child.type == "var_spec":
                    self._process_var_spec(child, local_var_types, module_qn)
            return

        left = self._get_child_by_field_name(node, "left")
        right = self._get_child_by_field_name(node, "right")
        if not left or not right:
            return

        left_names = self._extract_identifier_list(left)
        right_values = self._extract_value_list(right)

        for index, name in enumerate(left_names):
            if index >= len(right_values):
                continue
            inferred = self._infer_from_value(right_values[index], module_qn)
            if inferred:
                local_var_types[name] = inferred

    def _process_var_spec(
        self,
        node: TreeSitterNodeProtocol,
        local_var_types: dict[str, str],
        module_qn: str,
    ) -> None:
        name_node = self._get_child_by_field_name(node, "name")
        type_node = self._get_child_by_field_name(node, "type")
        value_node = self._get_child_by_field_name(node, "value")

        names = self._extract_identifier_list(name_node or node)
        type_name = self._type_to_string(type_node, module_qn)

        if type_name:
            for name in names:
                local_var_types[name] = type_name
            return

        if value_node:
            values = self._extract_value_list(value_node)
            for index, name in enumerate(names):
                if index >= len(values):
                    continue
                inferred = self._infer_from_value(values[index], module_qn)
                if inferred:
                    local_var_types[name] = inferred

    def _extract_identifier_list(
        self, node: TreeSitterNodeProtocol | None
    ) -> list[str]:
        if not node:
            return []
        if node.type == "identifier":
            if decoded := safe_decode_text(node):
                return [decoded]
        identifiers: list[str] = []
        for child in node.children:
            if child.type == "identifier":
                if decoded := safe_decode_text(child):
                    identifiers.append(decoded)
        return identifiers

    def _extract_value_list(
        self, node: TreeSitterNodeProtocol | None
    ) -> list[TreeSitterNodeProtocol]:
        if not node:
            return []
        if node.type == "expression_list":
            return [
                child
                for child in node.children
                if child.type not in cs.PUNCTUATION_TYPES
            ]
        return [node]

    def _type_to_string(
        self, node: TreeSitterNodeProtocol | None, module_qn: str
    ) -> str | None:
        if not node:
            return None
        if decoded := safe_decode_text(node):
            return self._resolve_go_type(decoded, module_qn)
        return None

    def _resolve_go_type(self, type_name: str, module_qn: str) -> str:
        cleaned = type_name.strip()
        if cs.SEPARATOR_DOT in cleaned:
            parts = cleaned.split(cs.SEPARATOR_DOT, 1)
            if module_qn in self.import_processor.import_mapping:
                import_map = self.import_processor.import_mapping[module_qn]
                if parts[0] in import_map:
                    module_path = import_map[parts[0]].replace(
                        cs.SEPARATOR_SLASH, cs.SEPARATOR_DOT
                    )
                    return f"{module_path}{cs.SEPARATOR_DOT}{parts[1]}"
            return cleaned

        local_qn = f"{module_qn}{cs.SEPARATOR_DOT}{cleaned}"
        if local_qn in self.function_registry:
            if self.function_registry[local_qn] in {NodeType.CLASS, NodeType.TYPE}:
                return local_qn
        return cleaned

    def _infer_from_value(
        self, node: TreeSitterNodeProtocol, module_qn: str
    ) -> str | None:
        node_type = node.type
        if node_type in {
            "interpreted_string_literal",
            "raw_string_literal",
            "string_literal",
        }:
            return "string"
        if node_type in {"int_literal", "integer_literal"}:
            return "int"
        if node_type in {"float_literal", "imaginary_literal"}:
            return "float64"
        if node_type == "rune_literal":
            return "rune"
        if node_type in {"true", "false"}:
            return "bool"
        if node_type == "composite_literal":
            type_node = self._get_child_by_field_name(node, "type")
            return self._type_to_string(type_node, module_qn)
        if node_type == "unary_expression":
            operator = self._get_child_by_field_name(node, "operator")
            operand = self._get_child_by_field_name(node, "operand")
            if operator and safe_decode_text(operator) == "&" and operand:
                inferred = self._infer_from_value(operand, module_qn)
                return f"*{inferred}" if inferred else None
        if node_type == cs.TS_GO_CALL_EXPRESSION:
            func_node = self._get_child_by_field_name(node, "function")
            if func_node and func_node.type == "identifier":
                func_name = safe_decode_text(func_node)
                if func_name in {"make", "new"}:
                    args = self._get_child_by_field_name(node, "arguments")
                    if args:
                        for child in args.children:
                            if child.type in cs.PUNCTUATION_TYPES:
                                continue
                            return self._type_to_string(child, module_qn)
                if func_name:
                    return func_name
            if func_node and func_node.type == "selector_expression":
                if decoded := safe_decode_text(func_node):
                    return decoded
        return None
