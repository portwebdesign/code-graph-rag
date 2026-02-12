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


class KotlinTypeInferenceEngine:
    """Kotlin local variable type inference."""

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
            self._collect_property_declarations(caller_node, local_var_types, module_qn)
        except Exception as exc:
            logger.debug(f"Kotlin type inference failed: {exc}")
        return local_var_types

    def _collect_property_declarations(
        self,
        root: TreeSitterNodeProtocol,
        local_var_types: dict[str, str],
        module_qn: str,
    ) -> None:
        stack: list[TreeSitterNodeProtocol] = [root]
        while stack:
            current = stack.pop()
            if current.type in {"property_declaration", "variable_declaration"}:
                self._process_property(current, local_var_types, module_qn)
            stack.extend(reversed(current.children))

    def _process_property(
        self,
        node: TreeSitterNodeProtocol,
        local_var_types: dict[str, str],
        module_qn: str,
    ) -> None:
        get_child = getattr(node, "child_by_field_name", None)
        if not get_child:
            return

        name_node = get_child("name")
        type_node = get_child("type")
        value_node = get_child("initializer")

        name = safe_decode_text(name_node) if name_node else None
        if not name:
            return

        type_name = self._type_to_string(type_node, module_qn)
        if type_name:
            local_var_types[name] = type_name
            return

        if value_node:
            inferred = self._infer_from_value(value_node, module_qn)
            if inferred:
                local_var_types[name] = inferred

    def _type_to_string(
        self, node: TreeSitterNodeProtocol | None, module_qn: str
    ) -> str | None:
        if not node:
            return None
        if decoded := safe_decode_text(node):
            cleaned = decoded.strip()
            if cs.SEPARATOR_DOT in cleaned:
                parts = cleaned.split(cs.SEPARATOR_DOT, 1)
                if module_qn in self.import_processor.import_mapping:
                    import_map = self.import_processor.import_mapping[module_qn]
                    if parts[0] in import_map:
                        return f"{import_map[parts[0]]}{cs.SEPARATOR_DOT}{parts[1]}"
                return cleaned
            local_qn = f"{module_qn}{cs.SEPARATOR_DOT}{cleaned}"
            if local_qn in self.function_registry:
                if self.function_registry[local_qn] in {NodeType.CLASS, NodeType.TYPE}:
                    return local_qn
            return cleaned
        return None

    def _infer_from_value(
        self, node: TreeSitterNodeProtocol, module_qn: str
    ) -> str | None:
        node_type = node.type
        if node_type in {"string_literal", "line_string_literal"}:
            return "String"
        if node_type in {"integer_literal"}:
            return "Int"
        if node_type in {"float_literal"}:
            return "Double"
        if node_type in {"true", "false", "boolean_literal"}:
            return "Boolean"
        if node_type in {"null", "null_literal"}:
            return "Nothing?"
        if node_type in {"call_expression", "constructor_invocation"}:
            get_child = getattr(node, "child_by_field_name", None)
            if get_child is None:
                return None
            callee = get_child("function") or get_child("callee")
            if callee and (decoded := safe_decode_text(callee)):
                return decoded.split(cs.SEPARATOR_DOT)[-1]
        return None
