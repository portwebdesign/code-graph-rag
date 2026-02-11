from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from codebase_rag.core import constants as cs
from codebase_rag.data_models.models import GraphNode, GraphRelationship
from codebase_rag.graph_db.graph_loader import GraphLoader


@dataclass
class MermaidConfig:
    direction: str = "LR"
    max_edges: int | None = None
    max_nodes: int | None = None


class MermaidExporter:
    def __init__(self, graph_file: str, config: MermaidConfig | None = None) -> None:
        self.graph_file = graph_file
        self.config = config or MermaidConfig()
        self.loader = GraphLoader(graph_file)
        self.loader.load()

    def export(self, diagram: str, output_path: str) -> Path:
        diagram = diagram.lower().strip()
        if diagram == "module":
            content = self._build_module_graph()
        elif diagram == "call":
            content = self._build_call_graph()
        elif diagram in {"flow", "flowchart"}:
            content = self._build_call_graph()
        elif diagram == "dependency":
            content = self._build_dependency_graph()
        elif diagram == "class":
            content = self._build_class_graph()
        elif diagram == "entity":
            content = self._build_entity_graph()
        else:
            raise ValueError(f"Unknown diagram type: {diagram}")

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding=cs.ENCODING_UTF8)
        return output

    def _build_module_graph(self) -> str:
        module_nodes = self._filter_nodes_by_label(cs.NodeLabel.MODULE.value)
        module_ids = {node.node_id for node in module_nodes}
        edges = self._filter_relationships(
            module_ids,
            module_ids,
            {cs.RelationshipType.IMPORTS.value},
        )
        return self._render_mermaid("Module Graph", module_nodes, edges)

    def _build_call_graph(self) -> str:
        function_nodes = self._filter_nodes_by_labels(
            {
                cs.NodeLabel.FUNCTION.value,
                cs.NodeLabel.METHOD.value,
            }
        )
        func_ids = {node.node_id for node in function_nodes}
        edges = self._filter_relationships(
            func_ids,
            func_ids,
            {cs.RelationshipType.CALLS.value},
        )
        return self._render_mermaid("Call Graph", function_nodes, edges)

    def _build_dependency_graph(self) -> str:
        project_nodes = self._filter_nodes_by_label(cs.NodeLabel.PROJECT.value)
        external_nodes = self._filter_nodes_by_label(
            cs.NodeLabel.EXTERNAL_PACKAGE.value
        )
        project_ids = {node.node_id for node in project_nodes}
        external_ids = {node.node_id for node in external_nodes}
        edges = self._filter_relationships(
            project_ids,
            external_ids,
            {cs.RelationshipType.DEPENDS_ON_EXTERNAL.value},
        )
        return self._render_mermaid(
            "Dependency Graph", project_nodes + external_nodes, edges
        )

    def _build_class_graph(self) -> str:
        class_nodes = self._filter_nodes_by_label(cs.NodeLabel.CLASS.value)
        interface_nodes = self._filter_nodes_by_label(cs.NodeLabel.INTERFACE.value)
        enum_nodes = self._filter_nodes_by_label(cs.NodeLabel.ENUM.value)
        nodes = class_nodes + interface_nodes + enum_nodes
        node_ids = {node.node_id for node in nodes}
        edges = self._filter_relationships(
            node_ids,
            node_ids,
            {
                cs.RelationshipType.INHERITS.value,
                cs.RelationshipType.IMPLEMENTS.value,
                cs.RelationshipType.OVERRIDES.value,
            },
        )
        return self._render_mermaid("Class Graph", nodes, edges)

    def _build_entity_graph(self) -> str:
        project_nodes = self._filter_nodes_by_label(cs.NodeLabel.PROJECT.value)
        folder_nodes = self._filter_nodes_by_label(cs.NodeLabel.FOLDER.value)
        file_nodes = self._filter_nodes_by_label(cs.NodeLabel.FILE.value)
        module_nodes = self._filter_nodes_by_label(cs.NodeLabel.MODULE.value)
        nodes = project_nodes + folder_nodes + file_nodes + module_nodes
        node_ids = {node.node_id for node in nodes}
        edges = self._filter_relationships(
            node_ids,
            node_ids,
            {
                cs.RelationshipType.CONTAINS_FOLDER.value,
                cs.RelationshipType.CONTAINS_FILE.value,
                cs.RelationshipType.CONTAINS_MODULE.value,
                cs.RelationshipType.CONTAINS_PACKAGE.value,
            },
        )
        return self._render_mermaid("Entity Graph", nodes, edges)

    def _filter_nodes_by_label(self, label: str) -> list[GraphNode]:
        return self.loader.find_nodes_by_label(label)

    def _filter_nodes_by_labels(self, labels: set[str]) -> list[GraphNode]:
        nodes: list[GraphNode] = []
        for label in labels:
            nodes.extend(self.loader.find_nodes_by_label(label))
        return nodes

    def _filter_relationships(
        self,
        from_ids: set[int],
        to_ids: set[int],
        allowed_types: set[str],
    ) -> list[GraphRelationship]:
        relationships = [
            rel
            for rel in self.loader.relationships
            if rel.from_id in from_ids
            and rel.to_id in to_ids
            and rel.type in allowed_types
        ]

        if self.config.max_edges is not None:
            relationships = relationships[: self.config.max_edges]
        return relationships

    def _render_mermaid(
        self,
        title: str,
        nodes: Iterable[GraphNode],
        relationships: Iterable[GraphRelationship],
    ) -> str:
        lines = [f"%% {title}", f"graph {self.config.direction}"]
        node_map: dict[int, str] = {}

        for node in nodes:
            if (
                self.config.max_nodes is not None
                and len(node_map) >= self.config.max_nodes
            ):
                break
            node_id = f"n{node.node_id}"
            label = self._node_label(node)
            node_map[node.node_id] = node_id
            lines.append(f'  {node_id}["{label}"]')

        for rel in relationships:
            from_node = node_map.get(rel.from_id)
            to_node = node_map.get(rel.to_id)
            if not from_node or not to_node:
                continue
            lines.append(f"  {from_node} --> {to_node}")

        if len(lines) == 2:
            logger.warning("Mermaid export produced an empty diagram: {}", title)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _node_label(node: GraphNode) -> str:
        name = node.properties.get(cs.KEY_NAME)
        if isinstance(name, str) and name:
            return name
        qn = node.properties.get(cs.KEY_QUALIFIED_NAME)
        if isinstance(qn, str) and qn:
            return qn
        path = node.properties.get(cs.KEY_PATH)
        if isinstance(path, str) and path:
            return path
        return str(node.node_id)
