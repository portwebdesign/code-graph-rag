from __future__ import annotations

from codebase_rag.core import constants as cs

from ..protocols import AnalysisRunnerProtocol
from ..types import NodeRecord


class StructureMixin:
    def _extract_parameters(
        self: AnalysisRunnerProtocol, nodes: list[NodeRecord]
    ) -> int:
        count = 0
        for node in nodes:
            if (
                cs.NodeLabel.FUNCTION.value not in node.labels
                and cs.NodeLabel.METHOD.value not in node.labels
            ):
                continue

            parameters = node.properties.get(cs.KEY_PARAMETERS)
            if not parameters or not isinstance(parameters, list):
                continue

            function_qn = str(node.properties.get(cs.KEY_QUALIFIED_NAME) or "")
            function_name = str(node.properties.get(cs.KEY_NAME) or "")
            start_line = int(str(node.properties.get(cs.KEY_START_LINE) or 0))
            path = str(node.properties.get(cs.KEY_PATH) or "")

            for index, param in enumerate(parameters):
                if isinstance(param, dict):
                    name = str(param.get("name") or param.get("param") or "param")
                    param_type = param.get("type")
                    default_value = param.get("default")
                    is_optional = bool(
                        param.get("is_optional") or param.get("optional")
                    )
                    is_rest = bool(param.get("is_rest") or param.get("rest"))
                else:
                    name = str(param)
                    param_type = None
                    default_value = None
                    is_optional = False
                    is_rest = False

                param_qn = f"{function_qn}{cs.SEPARATOR_DOT}param.{index}.{name}"
                self.ingestor.ensure_node_batch(
                    cs.NodeLabel.PARAMETER,
                    {
                        cs.KEY_QUALIFIED_NAME: param_qn,
                        cs.KEY_NAME: name,
                        cs.KEY_PATH: path,
                        cs.KEY_START_LINE: start_line,
                        "parameter_index": index,
                        "function_name": function_name,
                        "function_qn": function_qn,
                        "parameter_type": param_type,
                        "default_value": default_value,
                        "is_optional": is_optional,
                        "is_rest": is_rest,
                    },
                )
                self.ingestor.ensure_relationship_batch(
                    (
                        (
                            cs.NodeLabel.METHOD
                            if cs.NodeLabel.METHOD.value in node.labels
                            else cs.NodeLabel.FUNCTION
                        ),
                        cs.KEY_QUALIFIED_NAME,
                        function_qn,
                    ),
                    cs.RelationshipType.HAS_PARAMETER,
                    (cs.NodeLabel.PARAMETER, cs.KEY_QUALIFIED_NAME, param_qn),
                    {
                        "parameter_index": index,
                        "parameter_name": name,
                    },
                )
                count += 1
        return count

    def _detect_nested_functions(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        module_path_map: dict[str, str],
    ) -> int:
        count = 0
        function_nodes = [
            node
            for node in nodes
            if cs.NodeLabel.FUNCTION.value in node.labels
            or cs.NodeLabel.METHOD.value in node.labels
        ]

        function_nodes.sort(
            key=lambda n: (
                str(n.properties.get(cs.KEY_PATH) or ""),
                int(str(n.properties.get(cs.KEY_START_LINE) or 0)),
            )
        )

        for i, inner in enumerate(function_nodes):
            inner_qn = str(inner.properties.get(cs.KEY_QUALIFIED_NAME) or "")
            inner_path = self._resolve_node_path(inner, module_path_map)
            inner_start = int(str(inner.properties.get(cs.KEY_START_LINE) or 0))
            inner_end = int(str(inner.properties.get(cs.KEY_END_LINE) or 0))
            if not inner_path or not inner_start or not inner_end:
                continue

            for outer in function_nodes[:i]:
                outer_qn = str(outer.properties.get(cs.KEY_QUALIFIED_NAME) or "")
                outer_path = self._resolve_node_path(outer, module_path_map)
                outer_start = int(str(outer.properties.get(cs.KEY_START_LINE) or 0))
                outer_end = int(str(outer.properties.get(cs.KEY_END_LINE) or 0))
                if not outer_path or outer_path != inner_path:
                    continue
                if outer_start <= inner_start and outer_end >= inner_end:
                    self.ingestor.ensure_relationship_batch(
                        (
                            (
                                cs.NodeLabel.METHOD
                                if cs.NodeLabel.METHOD.value in outer.labels
                                else cs.NodeLabel.FUNCTION
                            ),
                            cs.KEY_QUALIFIED_NAME,
                            outer_qn,
                        ),
                        cs.RelationshipType.CONTAINS,
                        (
                            (
                                cs.NodeLabel.METHOD
                                if cs.NodeLabel.METHOD.value in inner.labels
                                else cs.NodeLabel.FUNCTION
                            ),
                            cs.KEY_QUALIFIED_NAME,
                            inner_qn,
                        ),
                        {cs.KEY_RELATION_TYPE: "nested_function"},
                    )
                    count += 1
                    break
        return count

    def _primary_label(self: AnalysisRunnerProtocol, node: NodeRecord) -> str:
        if cs.NodeLabel.FUNCTION.value in node.labels:
            return cs.NodeLabel.FUNCTION.value
        if cs.NodeLabel.METHOD.value in node.labels:
            return cs.NodeLabel.METHOD.value
        if cs.NodeLabel.CLASS.value in node.labels:
            return cs.NodeLabel.CLASS.value
        if cs.NodeLabel.MODULE.value in node.labels:
            return cs.NodeLabel.MODULE.value
        return node.labels[0] if node.labels else ""
