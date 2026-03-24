from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Any, cast

from codebase_rag.core import constants as cs
from codebase_rag.utils.path_utils import (
    get_canonical_relative_path,
    resolve_repo_relative_path,
)

from .api_compliance import ApiComplianceModule
from .base_module import AnalysisContext, AnalysisModule


class ApiCallChainModule(AnalysisModule):
    _PY_ROUTE_HANDLER_PATTERN = re.compile(
        r"@(?:app|router|bp)\.(get|post|put|delete|patch|api_route)\(\s*['\"]([^'\"]+)['\"][\s\S]*?\)\s*(?:async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)",
        re.IGNORECASE,
    )
    _FLASK_ROUTE_HANDLER_PATTERN = re.compile(
        r"@(?:app|bp)\.route\(\s*['\"]([^'\"]+)['\"](?:\s*,\s*methods=\[([^\]]+)\])?[\s\S]*?\)\s*(?:async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)",
        re.IGNORECASE,
    )

    def get_name(self) -> str:
        return "api_call_chain"

    def run(self, context: AnalysisContext) -> dict[str, Any]:
        if not context.nodes:
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
            requester_components: list[dict[str, Any]] = []
            requester_pages: list[dict[str, Any]] = []
            request_path_chain: list[dict[str, Any]] = []
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
                (
                    requester_components,
                    requester_pages,
                    request_path_chain,
                ) = self._collect_frontend_request_context(
                    endpoint_id,
                    rels_by_from=rels_by_from,
                    rels_by_to=rels_by_to,
                    context=context,
                    max_chains=max_requesters,
                    max_depth=max_depth + 2,
                )

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
                    context,
                    max_depth,
                    max_calls,
                    endpoint_payload=cast(dict[str, Any], entry.get("endpoint", {})),
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
                        {
                            **self._node_payload(endpoint_node),
                            **cast(dict[str, Any], entry.get("endpoint", {})),
                        }
                        if endpoint_node is not None
                        else cast(dict[str, Any], entry.get("endpoint", {}))
                    ),
                    "source_mode": str(entry.get("source_mode", "graph")),
                    "requesters": requesters,
                    "requester_components": requester_components,
                    "requester_pages": requester_pages,
                    "request_path_chain": request_path_chain,
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
        graph_endpoints = self._normalize_graph_endpoint_entries(
            context,
            rels_by_from=rels_by_from,
            rels_by_to=rels_by_to,
        )
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
                handler_name = self._resolve_inferred_handler_name(
                    source,
                    method,
                    route_path,
                    endpoint_file,
                )
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
                        "handler_name": handler_name,
                        "labels": [cs.NodeLabel.ENDPOINT.value],
                        "inferred": True,
                    }
                )
        return endpoints[:200]

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
        endpoint_path = resolve_repo_relative_path(
            str(
                endpoint_payload.get("path") or endpoint_payload.get("file_path") or ""
            ),
            context.runner.repo_path,
        ) or str(
            endpoint_payload.get("path") or endpoint_payload.get("file_path") or ""
        ).replace("\\", "/")
        if not endpoint_path:
            return []
        handler_name = str(endpoint_payload.get("handler_name") or "").strip()
        candidates = []
        for node in context.nodes:
            node_path = (
                get_canonical_relative_path(node.properties, context.runner.repo_path)
                or ""
            ).replace("\\", "/")
            if node_path != endpoint_path:
                continue
            if not context.runner._is_runtime_source_path(node_path):
                continue
            if not (
                cs.NodeLabel.FUNCTION.value in node.labels
                or cs.NodeLabel.METHOD.value in node.labels
                or cs.NodeLabel.CLASS.value in node.labels
                or cs.NodeLabel.SERVICE.value in node.labels
            ):
                continue
            if (
                handler_name
                and str(node.properties.get(cs.KEY_NAME) or "") != handler_name
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
            "path": get_canonical_relative_path(props) or props.get(cs.KEY_PATH),
            "framework": props.get(cs.KEY_FRAMEWORK),
            "method": props.get(cs.KEY_HTTP_METHOD),
            "route_path": props.get(cs.KEY_ROUTE_PATH),
            "local_route_path": props.get("local_route_path"),
            "next_kind": props.get("next_kind"),
            "next_route_path": props.get("next_route_path"),
            "hooks_used": props.get("hooks_used"),
            "props": props.get(cs.KEY_PROPS),
            "labels": node.labels,
        }

    def _collect_frontend_request_context(
        self,
        endpoint_id: int,
        *,
        rels_by_from: dict[int, list],
        rels_by_to: dict[int, list],
        context: AnalysisContext,
        max_chains: int,
        max_depth: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        direct_requesters = [
            context.node_by_id.get(rel.from_id)
            for rel in rels_by_to.get(endpoint_id, [])
            if rel.rel_type == cs.RelationshipType.REQUESTS_ENDPOINT
            and rel.from_id in context.node_by_id
        ]
        endpoint_node = context.node_by_id.get(endpoint_id)
        endpoint_payload = (
            self._node_payload(endpoint_node) if endpoint_node is not None else {}
        )
        component_map: dict[str, dict[str, Any]] = {}
        page_map: dict[str, dict[str, Any]] = {}
        request_chains: list[dict[str, Any]] = []

        for requester in direct_requesters:
            if requester is None or not self._is_frontend_request_node(
                context, requester
            ):
                continue
            raw_paths = self._walk_requester_paths(
                requester.node_id,
                rels_by_to=rels_by_to,
                context=context,
                max_depth=max_depth,
                max_paths=max_chains,
            )
            if not raw_paths:
                raw_paths = [[requester.node_id]]

            for path in raw_paths[:max_chains]:
                nodes = [
                    context.node_by_id[node_id]
                    for node_id in path
                    if node_id in context.node_by_id
                ]
                if not nodes:
                    continue
                chain_nodes = [self._node_payload(node) for node in nodes]
                chain_nodes.append(endpoint_payload)
                relationship_types = self._relationship_chain_for_path(
                    path,
                    rels_by_from=rels_by_from,
                )
                relationship_types.append(cs.RelationshipType.REQUESTS_ENDPOINT)
                request_chains.append(
                    {
                        "entrypoint": chain_nodes[0],
                        "direct_requester": self._node_payload(nodes[-1]),
                        "nodes": chain_nodes,
                        "relationships": relationship_types,
                    }
                )
                for node in nodes:
                    if cs.NodeLabel.COMPONENT.value not in node.labels:
                        continue
                    payload = self._node_payload(node)
                    qn = str(payload.get("qualified_name") or "").strip()
                    if not qn:
                        continue
                    component_map[qn] = payload
                    if str(payload.get("next_kind") or "").strip() in {
                        "page",
                        "layout",
                    }:
                        page_map[qn] = payload

        return (
            list(component_map.values())[:max_chains],
            list(page_map.values())[:max_chains],
            request_chains[:max_chains],
        )

    def _walk_requester_paths(
        self,
        start_id: int,
        *,
        rels_by_to: dict[int, list],
        context: AnalysisContext,
        max_depth: int,
        max_paths: int,
    ) -> list[list[int]]:
        paths: list[list[int]] = []
        queue = deque([(start_id, [start_id])])
        while queue and len(paths) < max_paths:
            current_id, path = queue.popleft()
            if len(path) > max_depth:
                paths.append(path)
                continue

            upstream: list[tuple[int, str]] = []
            for rel in rels_by_to.get(current_id, []):
                if rel.rel_type not in {
                    cs.RelationshipType.CALLS,
                    cs.RelationshipType.USES_COMPONENT,
                }:
                    continue
                upstream_node = context.node_by_id.get(rel.from_id)
                if upstream_node is None:
                    continue
                if not self._is_frontend_request_node(context, upstream_node):
                    continue
                if rel.from_id in path:
                    continue
                upstream.append((rel.from_id, str(rel.rel_type)))

            if not upstream:
                paths.append(path)
                continue

            for upstream_id, _rel_type in upstream:
                queue.append((upstream_id, [upstream_id, *path]))
        return paths

    @staticmethod
    def _relationship_chain_for_path(
        path: list[int],
        *,
        rels_by_from: dict[int, list],
    ) -> list[str]:
        relationship_types: list[str] = []
        for index in range(len(path) - 1):
            source_id = path[index]
            target_id = path[index + 1]
            rel_type = next(
                (
                    str(rel.rel_type)
                    for rel in rels_by_from.get(source_id, [])
                    if rel.to_id == target_id
                ),
                "",
            )
            if rel_type:
                relationship_types.append(rel_type)
        return relationship_types

    @staticmethod
    def _is_frontend_request_node(context: AnalysisContext, node: Any) -> bool:
        if cs.NodeLabel.COMPONENT.value in node.labels:
            return True
        path = (
            (
                get_canonical_relative_path(node.properties, context.runner.repo_path)
                or ""
            )
            .replace("\\", "/")
            .lower()
        )
        if not context.runner._is_runtime_source_path(path):
            return False
        if any(part in {"tests", "test", "__tests__"} for part in path.split("/")):
            return False
        return path.endswith((".ts", ".tsx", ".js", ".jsx"))

    def _normalize_graph_endpoint_entries(
        self,
        context: AnalysisContext,
        *,
        rels_by_from: dict[int, list],
        rels_by_to: dict[int, list],
    ) -> list[dict[str, object]]:
        raw_entries: list[dict[str, object]] = []
        for node in context.nodes:
            if cs.NodeLabel.ENDPOINT.value not in node.labels:
                continue
            route_path = str(node.properties.get(cs.KEY_ROUTE_PATH) or "").strip()
            if not route_path:
                continue

            handler_qns = {
                str(
                    context.node_by_id[rel.from_id].properties.get(
                        cs.KEY_QUALIFIED_NAME
                    )
                    or ""
                ).strip()
                for rel in rels_by_to.get(node.node_id, [])
                if rel.rel_type == cs.RelationshipType.HAS_ENDPOINT
                and rel.from_id in context.node_by_id
            }
            handler_qns.update(
                {
                    str(
                        context.node_by_id[rel.to_id].properties.get(
                            cs.KEY_QUALIFIED_NAME
                        )
                        or ""
                    ).strip()
                    for rel in rels_by_from.get(node.node_id, [])
                    if rel.rel_type == cs.RelationshipType.ROUTES_TO_ACTION
                    and rel.to_id in context.node_by_id
                }
            )
            exposed_module_paths = {
                (
                    get_canonical_relative_path(
                        context.node_by_id[rel.from_id].properties,
                        context.runner.repo_path,
                    )
                    or ""
                )
                .replace("\\", "/")
                .strip()
                for rel in rels_by_to.get(node.node_id, [])
                if rel.rel_type == cs.RelationshipType.EXPOSES_ENDPOINT
                and rel.from_id in context.node_by_id
            }
            prefix_module_paths = {
                (
                    get_canonical_relative_path(
                        context.node_by_id[rel.from_id].properties,
                        context.runner.repo_path,
                    )
                    or ""
                )
                .replace("\\", "/")
                .strip()
                for rel in rels_by_to.get(node.node_id, [])
                if rel.rel_type == cs.RelationshipType.PREFIXES_ENDPOINT
                and rel.from_id in context.node_by_id
            }
            raw_entries.append(
                {
                    "node_id": node.node_id,
                    "node": node,
                    "qualified_name": str(
                        node.properties.get(cs.KEY_QUALIFIED_NAME) or ""
                    ).strip(),
                    "method": str(node.properties.get(cs.KEY_HTTP_METHOD) or "REQUEST")
                    .strip()
                    .upper(),
                    "path": route_path,
                    "local_route_path": str(
                        node.properties.get("local_route_path") or route_path
                    ).strip(),
                    "file": (
                        get_canonical_relative_path(
                            node.properties,
                            context.runner.repo_path,
                        )
                        or ""
                    )
                    .replace("\\", "/")
                    .strip(),
                    "framework": str(
                        node.properties.get(cs.KEY_FRAMEWORK) or ""
                    ).strip(),
                    "handler_qns": sorted(qn for qn in handler_qns if qn),
                    "exposed_module_paths": sorted(
                        path for path in exposed_module_paths if path
                    ),
                    "prefix_module_paths": sorted(
                        path for path in prefix_module_paths if path
                    ),
                    "expose_count": len(
                        [path for path in exposed_module_paths if path]
                    ),
                    "prefix_count": len([path for path in prefix_module_paths if path]),
                }
            )

        normalized = ApiComplianceModule._normalize_graph_endpoints(
            raw_entries,
            module_paths=context.module_paths,
        )
        entry_by_key = {
            (
                str(entry.get("method", "")).strip().upper(),
                str(entry.get("path", "")).strip(),
                str(entry.get("file", "")).replace("\\", "/").strip(),
            ): entry
            for entry in raw_entries
        }
        results: list[dict[str, object]] = []
        for endpoint in normalized:
            if not self._is_graph_analysis_endpoint(cast(dict[str, object], endpoint)):
                continue
            key = (
                str(endpoint.get("method", "")).strip().upper(),
                str(endpoint.get("path", "")).strip(),
                str(endpoint.get("file", "")).replace("\\", "/").strip(),
            )
            source_entry = entry_by_key.get(key)
            if source_entry is None:
                continue
            endpoint_payload = self._node_payload(cast(Any, source_entry["node"]))
            endpoint_payload["canonical_route_layer"] = str(
                endpoint.get("canonical_route_layer") or "direct_endpoint_node"
            )
            endpoint_payload["exposed_module_paths"] = endpoint.get(
                "exposed_module_paths", []
            )
            endpoint_payload["prefix_module_paths"] = endpoint.get(
                "prefix_module_paths", []
            )
            results.append(
                {
                    "node_id": source_entry["node_id"],
                    "node": source_entry["node"],
                    "endpoint": endpoint_payload,
                    "source_mode": "graph",
                }
            )
        return results

    @staticmethod
    def _is_graph_analysis_endpoint(endpoint: dict[str, object]) -> bool:
        framework = str(endpoint.get("framework") or "").strip().lower()
        if framework in {"http", "graphql"}:
            return False
        return (
            int(cast(Any, endpoint.get("expose_count", 0)) or 0)
            + int(cast(Any, endpoint.get("prefix_count", 0)) or 0)
            + len(cast(list[object], endpoint.get("handler_qns", [])))
            > 0
        )

    @staticmethod
    def _collect_calls(
        start_id: int,
        rels_by_from: dict[int, list],
        node_by_id: dict[int, Any],
        context: AnalysisContext,
        max_depth: int,
        max_nodes: int,
        *,
        endpoint_payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        visited = {start_id}
        queue = deque([(start_id, 0)])
        results: list[dict[str, Any]] = []
        include_graphql = (
            "graphql" in str(endpoint_payload.get("route_path") or "").lower()
        )

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
                if not ApiCallChainModule._should_include_chain_node(
                    context,
                    node,
                    rel_type=str(rel.rel_type),
                    include_graphql=include_graphql,
                ):
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
            "cypher",
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

    @classmethod
    def _extract_source_endpoint_bindings(
        cls,
        source: str,
        file_path: str,
    ) -> list[dict[str, str]]:
        _ = file_path
        bindings: list[dict[str, str]] = []

        for match in cls._PY_ROUTE_HANDLER_PATTERN.finditer(source):
            bindings.append(
                {
                    "method": match.group(1).upper(),
                    "path": match.group(2),
                    "handler_name": match.group(3),
                }
            )

        for match in cls._FLASK_ROUTE_HANDLER_PATTERN.finditer(source):
            methods_raw = match.group(2) or "GET"
            methods = re.findall(r"['\"]([A-Za-z]+)['\"]", methods_raw) or [methods_raw]
            for method in methods:
                bindings.append(
                    {
                        "method": str(method).strip().upper(),
                        "path": match.group(1),
                        "handler_name": match.group(3),
                    }
                )

        return bindings

    @classmethod
    def _resolve_inferred_handler_name(
        cls,
        source: str,
        method: str,
        route_path: str,
        file_path: str,
    ) -> str | None:
        for binding in cls._extract_source_endpoint_bindings(source, file_path):
            if (
                str(binding.get("method") or "").strip().upper() == method
                and str(binding.get("path") or "").strip() == route_path
            ):
                handler_name = str(binding.get("handler_name") or "").strip()
                if handler_name:
                    return handler_name
        return None

    @staticmethod
    def _should_include_chain_node(
        context: AnalysisContext,
        node: Any,
        *,
        rel_type: str,
        include_graphql: bool,
    ) -> bool:
        path = (
            get_canonical_relative_path(node.properties, context.runner.repo_path) or ""
        )
        normalized = path.replace("\\", "/").lower()
        path_parts = [part for part in normalized.split("/") if part]
        qualified_name = str(node.properties.get(cs.KEY_QUALIFIED_NAME) or "").lower()
        if (
            any(part in {"tests", "test", "__tests__"} for part in path_parts)
            or ".spec." in normalized
            or ".test." in normalized
        ):
            return False
        if not include_graphql and (
            rel_type == cs.RelationshipType.OWNS_GRAPHQL_OPERATION
            or cs.NodeLabel.GRAPHQL_OPERATION.value in node.labels
            or "graphql" in normalized
            or "graphql" in qualified_name
        ):
            return False
        if context.runner._is_runtime_source_path(path):
            return True
        return any(
            label in node.labels
            for label in {
                cs.NodeLabel.EXTERNAL_PACKAGE.value,
                cs.NodeLabel.DATA_STORE.value,
                cs.NodeLabel.CACHE_STORE.value,
                cs.NodeLabel.QUEUE.value,
                cs.NodeLabel.GRAPHQL_OPERATION.value,
                cs.NodeLabel.SERVICE.value,
            }
        )
