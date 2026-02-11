from __future__ import annotations

from typing import Any

from codebase_rag.core import constants as cs

from .base_module import AnalysisContext, AnalysisModule


class SchemaValidatorModule(AnalysisModule):
    def get_name(self) -> str:
        return "schema_validator"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        if not context.nodes or not context.relationships:
            return {}

        node_ids = {node.node_id for node in context.nodes}
        contains_types = {
            cs.RelationshipType.CONTAINS_PACKAGE.value,
            cs.RelationshipType.CONTAINS_FOLDER.value,
            cs.RelationshipType.CONTAINS_FILE.value,
            cs.RelationshipType.CONTAINS_MODULE.value,
            cs.RelationshipType.CONTAINS.value,
        }

        incoming_contains: set[int] = set()
        for rel in context.relationships:
            if rel.rel_type in contains_types:
                incoming_contains.add(rel.to_id)

        orphan_nodes = [
            node.node_id
            for node in context.nodes
            if node.node_id not in incoming_contains
            and cs.NodeLabel.PROJECT.value not in node.labels
        ]

        type_edge_types = {
            cs.RelationshipType.RETURNS_TYPE.value,
            cs.RelationshipType.PARAMETER_TYPE.value,
        }
        type_edges_by_source = {
            rel.from_id
            for rel in context.relationships
            if rel.rel_type in type_edge_types
        }

        missing_types = [
            node.node_id
            for node in context.nodes
            if (
                cs.NodeLabel.FUNCTION.value in node.labels
                or cs.NodeLabel.METHOD.value in node.labels
            )
            and node.node_id not in type_edges_by_source
        ]

        broken_refs = [
            rel
            for rel in context.relationships
            if rel.from_id not in node_ids or rel.to_id not in node_ids
        ]

        return {
            "orphan_nodes": len(orphan_nodes),
            "missing_types": len(missing_types),
            "broken_refs": len(broken_refs),
        }
