from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from codebase_rag.core import constants as cs

from .base_module import AnalysisContext, AnalysisModule


class ApiCallChainModule(AnalysisModule):
    def get_name(self) -> str:
        return "api_call_chain"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        if not context.nodes or not context.relationships:
            return {}
        return self._build_report(context)

    def _build_report(self, context: AnalysisContext) -> dict[str, Any]:
        rels_by_from: dict[int, list] = defaultdict(list)
        rels_by_to: dict[int, list] = defaultdict(list)
        for rel in context.relationships:
            rels_by_from[rel.from_id].append(rel)
            rels_by_to[rel.to_id].append(rel)

        endpoints = {
            node.node_id: node
            for node in context.nodes
            if cs.NodeLabel.ENDPOINT.value in node.labels
        }
        if not endpoints:
            context.runner._write_json_report("api_call_chain_report.json", [])
            return {"chains": 0, "endpoints": 0}

        chains: list[dict[str, Any]] = []
        max_calls = 25
        max_requesters = 10
        max_handlers = 10
        max_depth = 3

        for endpoint_id, endpoint in endpoints.items():
            requester_nodes = [
                context.node_by_id.get(rel.from_id)
                for rel in rels_by_to.get(endpoint_id, [])
                if rel.rel_type == cs.RelationshipType.REQUESTS_ENDPOINT
            ]
            requesters = [
                self._node_payload(node) for node in requester_nodes if node is not None
            ][:max_requesters]

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
            handlers = [
                self._node_payload(node) for node in handler_nodes if node is not None
            ][:max_handlers]

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
            db_hits: list[dict[str, Any]] = []
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
                    if self._looks_like_db(item):
                        db_hits.append(item)
                if len(call_chain) >= max_calls:
                    break

            chains.append(
                {
                    "endpoint": self._node_payload(endpoint),
                    "requesters": requesters,
                    "handlers": handlers,
                    "controllers": controllers,
                    "call_chain": call_chain[:max_calls],
                    "db_hits": db_hits[:max_calls],
                }
            )

        context.runner._write_json_report("api_call_chain_report.json", chains)
        return {"chains": len(chains), "endpoints": len(endpoints)}

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
                if rel.rel_type != cs.RelationshipType.CALLS:
                    continue
                target_id = rel.to_id
                if target_id in visited:
                    continue
                visited.add(target_id)
                node = node_by_id.get(target_id)
                if node is None:
                    continue
                results.append(ApiCallChainModule._node_payload(node))
                if len(results) >= max_nodes:
                    return results
                queue.append((target_id, depth + 1))

        return results

    @staticmethod
    def _looks_like_db(node_payload: dict[str, Any]) -> bool:
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
        )
        if any(token in lower for token in tokens):
            return True
        return cs.NodeLabel.EXTERNAL_PACKAGE.value in label_list
