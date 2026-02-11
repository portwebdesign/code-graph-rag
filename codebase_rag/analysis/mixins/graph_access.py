from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, cast

from codebase_rag.core import constants as cs
from codebase_rag.graph_db.cypher_queries import (
    CYPHER_EXPORT_PROJECT_NODES,
    CYPHER_EXPORT_PROJECT_NODES_PAGED,
    CYPHER_EXPORT_PROJECT_RELATIONSHIPS,
    CYPHER_EXPORT_PROJECT_RELATIONSHIPS_PAGED,
)

from ...utils.git_delta import get_git_head
from ..protocols import AnalysisRunnerProtocol
from ..types import NodeRecord, RelationshipRecord

if TYPE_CHECKING:
    from ...services.protocols import QueryProtocol


class AnalysisGraphAccessMixin:
    def _load_graph_data(
        self: AnalysisRunnerProtocol, ingestor: QueryProtocol
    ) -> tuple[list[NodeRecord], list[RelationshipRecord]]:
        params = {cs.KEY_PROJECT_NAME: self.project_name}
        page_size = int(os.getenv("CODEGRAPH_ANALYSIS_PAGE_SIZE", "0"))
        use_cache = str(os.getenv("CODEGRAPH_ANALYSIS_CACHE", "")).lower() in {
            "1",
            "true",
            "yes",
        }

        cache_path = self.repo_path / "output" / "analysis" / "graph_cache.json"
        if use_cache and cache_path.exists():
            try:
                cache_payload = json.loads(
                    cache_path.read_text(encoding=cs.ENCODING_UTF8)
                )
                if cache_payload.get(
                    "project_name"
                ) == self.project_name and cache_payload.get(
                    "git_head"
                ) == get_git_head(self.repo_path):
                    return (
                        [NodeRecord(**item) for item in cache_payload.get("nodes", [])],
                        [
                            RelationshipRecord(**item)
                            for item in cache_payload.get("relationships", [])
                        ],
                    )
            except Exception:
                pass

        if page_size > 0:
            raw_nodes = cast(Any, self)._fetch_paged(
                ingestor,
                CYPHER_EXPORT_PROJECT_NODES_PAGED,
                params,
                page_size,
            )
            raw_rels = cast(Any, self)._fetch_paged(
                ingestor,
                CYPHER_EXPORT_PROJECT_RELATIONSHIPS_PAGED,
                params,
                page_size,
            )
        else:
            raw_nodes = ingestor.fetch_all(CYPHER_EXPORT_PROJECT_NODES, params)
            raw_rels = ingestor.fetch_all(CYPHER_EXPORT_PROJECT_RELATIONSHIPS, params)

        nodes: list[NodeRecord] = []
        for row in raw_nodes:
            node_id_val = row.get(cs.KEY_NODE_ID)
            node_id = int(str(node_id_val if node_id_val is not None else 0))

            labels_val = row.get(cs.KEY_LABELS)
            labels = list(labels_val) if isinstance(labels_val, list) else []

            props_val = row.get(cs.KEY_PROPERTIES)
            props = dict(props_val) if isinstance(props_val, dict) else {}

            nodes.append(NodeRecord(node_id=node_id, labels=labels, properties=props))

        rels: list[RelationshipRecord] = []
        for row in raw_rels:
            from_id_val = row.get(cs.KEY_FROM_ID)
            to_id_val = row.get(cs.KEY_TO_ID)
            type_val = row.get(cs.KEY_TYPE)
            props_val = row.get(cs.KEY_PROPERTIES)

            rels.append(
                RelationshipRecord(
                    from_id=int(str(from_id_val if from_id_val is not None else 0)),
                    to_id=int(str(to_id_val if to_id_val is not None else 0)),
                    rel_type=str(type_val if type_val is not None else ""),
                    properties=dict(props_val) if isinstance(props_val, dict) else {},
                )
            )

        if use_cache:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    "project_name": self.project_name,
                    "git_head": get_git_head(self.repo_path),
                    "nodes": [node.__dict__ for node in nodes],
                    "relationships": [rel.__dict__ for rel in rels],
                }
                cache_path.write_text(
                    json.dumps(payload, ensure_ascii=False),
                    encoding=cs.ENCODING_UTF8,
                )
            except Exception:
                pass

        return nodes, rels

    def _fetch_paged(
        self,
        ingestor: QueryProtocol,
        query: str,
        params: dict[str, object],
        page_size: int,
    ) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        offset = 0
        while True:
            page = ingestor.fetch_all(
                query,
                cast(
                    dict[str, Any] | None,
                    {**params, "offset": offset, "limit": page_size},
                ),
            )
            if not page:
                break
            results.extend([dict(row) for row in page])
            if len(page) < page_size:
                break
            offset += page_size
        return results

    def _build_module_path_map(
        self: AnalysisRunnerProtocol, nodes: list[NodeRecord]
    ) -> dict[str, str]:
        module_paths: dict[str, str] = {}
        for node in nodes:
            if cs.NodeLabel.MODULE.value in node.labels:
                qn = str(node.properties.get(cs.KEY_QUALIFIED_NAME) or "")
                path = str(node.properties.get(cs.KEY_PATH) or "")
                if qn and path:
                    module_paths[qn] = path
        return module_paths

    def _collect_file_paths(self, nodes: list[NodeRecord]) -> list[str]:
        file_paths = [
            node.properties.get(cs.KEY_PATH)
            for node in nodes
            if cs.NodeLabel.FILE.value in node.labels
        ]
        return [str(path) for path in file_paths if isinstance(path, str)]

    def _resolve_node_path(
        self: AnalysisRunnerProtocol, node: NodeRecord, module_path_map: dict[str, str]
    ) -> str | None:
        path = node.properties.get(cs.KEY_PATH)
        if isinstance(path, str) and path:
            return path
        qn = str(node.properties.get(cs.KEY_QUALIFIED_NAME) or "")
        if not qn:
            return None
        for module_qn, module_path in module_path_map.items():
            if qn.startswith(module_qn):
                return module_path
        return None
