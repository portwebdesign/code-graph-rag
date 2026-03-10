from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, cast

from codebase_rag.core import constants as cs

from .api_compliance import ApiComplianceModule
from .base_module import AnalysisContext, AnalysisModule


class ApiCallChainModule(AnalysisModule):
    def get_name(self) -> str:
        return "api_call_chain"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        if not context.nodes or not context.relationships:
            context.runner._write_json_report(
                "api_call_chain_report.json",
                {
                    "summary": {"chains": 0, "endpoints": 0},
                    "reason": "No graph nodes/relationships available for API chain analysis",
                    "chains": [],
                },
            )
            return {}
        return self._build_report(context)

    def _build_report(self, context: AnalysisContext) -> dict[str, Any]:
        rels_by_from: dict[int, list] = defaultdict(list)
        rels_by_to: dict[int, list] = defaultdict(list)
        for rel in context.relationships:
            rels_by_from[rel.from_id].append(rel)
            rels_by_to[rel.to_id].append(rel)

        endpoint_entries = self._resolve_endpoints(
            context,
            rels_by_from=rels_by_from,
            rels_by_to=rels_by_to,
        )
        if not endpoint_entries:
            context.runner._write_json_report(
                "api_call_chain_report.json",
                {
                    "summary": {"chains": 0, "endpoints": 0},
                    "reason": "No Endpoint nodes detected in graph",
                    "chains": [],
                },
            )
            return {"chains": 0, "endpoints": 0}

        chains: list[dict[str, Any]] = []
        max_calls = 25
        max_requesters = 10
        max_handlers = 10
        max_depth = 3

        for entry in endpoint_entries:
            endpoint_id = entry.get("node_id")
            endpoint_node = entry.get("node")
            requesters: list[dict[str, Any]] = []
            handler_nodes = self._resolve_handler_nodes(
                context,
                entry=entry,
                rels_by_from=rels_by_from,
                rels_by_to=rels_by_to,
                limit=max_handlers,
            )
            if isinstance(endpoint_id, int):
                requester_nodes = [
                    context.node_by_id.get(rel.from_id)
                    for rel in rels_by_to.get(endpoint_id, [])
                    if rel.rel_type == cs.RelationshipType.REQUESTS_ENDPOINT
                ]
                requesters = [
                    self._node_payload(node)
                    for node in requester_nodes
                    if node is not None
                ][:max_requesters]

            handlers = [
                self._node_payload(node) for node in handler_nodes if node is not None
            ][:max_handlers]

            controller_nodes = []
            if isinstance(endpoint_id, int):
                controller_nodes = [
                    context.node_by_id.get(rel.to_id)
                    for rel in rels_by_from.get(endpoint_id, [])
                    if rel.rel_type == cs.RelationshipType.ROUTES_TO_CONTROLLER
                ]
            controllers = [
                self._node_payload(node)
                for node in controller_nodes
                if node is not None
            ]

            call_chain: list[dict[str, Any]] = []
            infra_hits: list[dict[str, Any]] = []
            for handler in handler_nodes[:max_handlers]:
                if handler is None:
                    continue
                chain = self._collect_calls(
                    handler.node_id,
                    rels_by_from,
                    context.node_by_id,
                    max_depth,
                    max_calls,
                )
                for item in chain:
                    call_chain.append(item)
                    if self._looks_like_infra(item):
                        infra_hits.append(item)
                if len(call_chain) >= max_calls:
                    break

            chains.append(
                {
                    "endpoint": (
                        self._node_payload(endpoint_node)
                        if endpoint_node is not None
                        else cast(dict[str, Any], entry.get("endpoint", {}))
                    ),
                    "source_mode": str(entry.get("source_mode", "graph")),
                    "requesters": requesters,
                    "handlers": handlers,
                    "controllers": controllers,
                    "call_chain": call_chain[:max_calls],
                    "infra_hits": infra_hits[:max_calls],
                }
            )

        context.runner._write_json_report(
            "api_call_chain_report.json",
            {
                "summary": {
                    "chains": len(chains),
                    "endpoints": len(endpoint_entries),
                    "graph_backed_endpoints": sum(
                        1
                        for entry in endpoint_entries
                        if str(entry.get("source_mode", "")) == "graph"
                    ),
                    "inferred_endpoints": sum(
                        1
                        for entry in endpoint_entries
                        if str(entry.get("source_mode", "")) != "graph"
                    ),
                },
                "reason": (
                    None
                    if any(
                        str(entry.get("source_mode", "")) == "graph"
                        for entry in endpoint_entries
                    )
                    else "Endpoint chains inferred from source scan fallback"
                ),
                "chains": chains,
            },
        )
        return {"chains": len(chains), "endpoints": len(endpoint_entries)}

    def _resolve_endpoints(
        self,
        context: AnalysisContext,
        *,
        rels_by_from: dict[int, list],
        rels_by_to: dict[int, list],
    ) -> list[dict[str, object]]:
        _ = rels_by_from, rels_by_to
        graph_endpoints = [
            {
                "node_id": node.node_id,
                "node": node,
                "endpoint": self._node_payload(node),
                "source_mode": "graph",
            }
            for node in context.nodes
            if cs.NodeLabel.ENDPOINT.value in node.labels
        ]
        if graph_endpoints:
            return graph_endpoints

        inferred_endpoints = self._infer_endpoints_from_source(context)
        return [
            {
                "node_id": None,
                "node": None,
                "endpoint": endpoint,
                "source_mode": "source_scan",
            }
            for endpoint in inferred_endpoints
        ]

    def _infer_endpoints_from_source(
        self,
        context: AnalysisContext,
    ) -> list[dict[str, object]]:
        endpoints: list[dict[str, object]] = []
        repo_path = context.runner.repo_path
        seen: set[tuple[str, str, str]] = set()
        for file_path in ApiComplianceModule._iter_files(
            repo_path, context.module_paths
        ):
            try:
                source = file_path.read_text(
                    encoding=cs.ENCODING_UTF8,
                    errors="ignore",
                )
            except Exception:
                continue
            relative = file_path.relative_to(repo_path).as_posix()
            for endpoint in ApiComplianceModule._extract_endpoints(source, file_path):
                method = str(endpoint.get("method", "")).strip().upper()
                route_path = str(endpoint.get("path", "")).strip()
                endpoint_file = relative
                endpoint_key = (method, route_path, endpoint_file)
                if endpoint_key in seen:
                    continue
                seen.add(endpoint_key)
                endpoints.append(
                    {
                        "qualified_name": (
                            f"{context.runner.project_name}.endpoint."
                            f"{method.lower()}.{route_path.strip('/').replace('/', '.') or 'root'}"
                        ),
                        "name": route_path or endpoint_file,
                        "path": endpoint_file,
                        "file_path": endpoint_file,
                        "route_path": route_path,
                        "method": method,
                        "labels": [cs.NodeLabel.ENDPOINT.value],
                        "inferred": True,
                    }
                )
        return endpoints[:40]

    def _resolve_handler_nodes(
        self,
        context: AnalysisContext,
        *,
        entry: dict[str, object],
        rels_by_from: dict[int, list],
        rels_by_to: dict[int, list],
        limit: int,
    ) -> list[Any]:
        endpoint_id = entry.get("node_id")
        if isinstance(endpoint_id, int):
            handler_nodes = [
                context.node_by_id.get(rel.from_id)
                for rel in rels_by_to.get(endpoint_id, [])
                if rel.rel_type == cs.RelationshipType.HAS_ENDPOINT
            ]
            handler_nodes.extend(
                [
                    context.node_by_id.get(rel.to_id)
                    for rel in rels_by_from.get(endpoint_id, [])
                    if rel.rel_type == cs.RelationshipType.ROUTES_TO_ACTION
                ]
            )
            deduped: list[Any] = []
            seen_node_ids: set[int] = set()
            for node in handler_nodes:
                if node is None or node.node_id in seen_node_ids:
                    continue
                seen_node_ids.add(node.node_id)
                deduped.append(node)
            return deduped[:limit]

        endpoint_payload = cast(dict[str, object], entry.get("endpoint", {}))
        endpoint_path = str(
            endpoint_payload.get("file_path") or endpoint_payload.get("path") or ""
        ).replace("\\", "/")
        if not endpoint_path:
            return []
        candidates = []
        for node in context.nodes:
            node_path = str(node.properties.get(cs.KEY_PATH) or "").replace("\\", "/")
            if node_path != endpoint_path:
                continue
            if not (
                cs.NodeLabel.FUNCTION.value in node.labels
                or cs.NodeLabel.METHOD.value in node.labels
                or cs.NodeLabel.CLASS.value in node.labels
                or cs.NodeLabel.SERVICE.value in node.labels
            ):
                continue
            candidates.append(node)
        return candidates[:limit]

    @staticmethod
    def _node_payload(node) -> dict[str, Any]:
        if node is None:
            return {}
        props = node.properties
        return {
            "qualified_name": props.get(cs.KEY_QUALIFIED_NAME),
            "name": props.get(cs.KEY_NAME),
            "path": props.get(cs.KEY_PATH),
            "labels": node.labels,
        }

    @staticmethod
    def _collect_calls(
        start_id: int,
        rels_by_from: dict[int, list],
        node_by_id: dict[int, Any],
        max_depth: int,
        max_nodes: int,
    ) -> list[dict[str, Any]]:
        visited = {start_id}
        queue = deque([(start_id, 0)])
        results: list[dict[str, Any]] = []

        while queue:
            current_id, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for rel in rels_by_from.get(current_id, []):
                if rel.rel_type not in {
                    cs.RelationshipType.CALLS,
                    cs.RelationshipType.CONNECTS_TO_DATASTORE,
                    cs.RelationshipType.USES_CACHE,
                    cs.RelationshipType.USES_QUEUE,
                    cs.RelationshipType.OWNS_GRAPHQL_OPERATION,
                    cs.RelationshipType.CALLS_SERVICE,
                }:
                    continue
                target_id = rel.to_id
                if target_id in visited:
                    continue
                visited.add(target_id)
                node = node_by_id.get(target_id)
                if node is None:
                    continue
                payload = ApiCallChainModule._node_payload(node)
                payload["relationship_type"] = str(rel.rel_type)
                results.append(payload)
                if len(results) >= max_nodes:
                    return results
                queue.append((target_id, depth + 1))

        return results

    @staticmethod
    def _looks_like_infra(node_payload: dict[str, Any]) -> bool:
        name = str(node_payload.get("qualified_name") or node_payload.get("name") or "")
        label_list = node_payload.get("labels") or []
        lower = name.lower()
        tokens = (
            "sql",
            "postgres",
            "psycopg",
            "pg",
            "prisma",
            "sequelize",
            "typeorm",
            "knex",
            "mongoose",
            "mongo",
            "redis",
            "dynamo",
            "cassandra",
            "queue",
            "graphql",
        )
        if any(token in lower for token in tokens):
            return True
        return any(
            label in label_list
            for label in {
                cs.NodeLabel.EXTERNAL_PACKAGE.value,
                cs.NodeLabel.DATA_STORE.value,
                cs.NodeLabel.CACHE_STORE.value,
                cs.NodeLabel.QUEUE.value,
                cs.NodeLabel.GRAPHQL_OPERATION.value,
                cs.NodeLabel.SERVICE.value,
            }
        )
