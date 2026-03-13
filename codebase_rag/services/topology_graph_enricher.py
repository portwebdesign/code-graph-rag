from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol, cast

from codebase_rag.core import constants as cs
from codebase_rag.parsers.config.config_parser import ConfigParserMixin


class TopologyGraphIngestorProtocol(Protocol):
    def ensure_node_batch(self, label: str, payload: dict[str, object]) -> None: ...

    def ensure_relationship_batch(
        self,
        source: tuple[str, str, str],
        relationship_type: str,
        target: tuple[str, str, str],
        payload: dict[str, object] | None = None,
    ) -> None: ...

    def flush_all(self) -> None: ...

    def fetch_all(
        self,
        query: str,
        parameters: dict[str, object] | None = None,
    ) -> list[object]: ...


class TopologyGraphEnricher(ConfigParserMixin):
    _SKIP_DIRS = {
        ".git",
        ".idea",
        ".next",
        ".venv",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "venv",
    }
    _FRONTEND_MARKERS = {"frontend", "web", "ui", "client", "apps"}
    _BACKEND_MARKERS = {"backend", "api", "server", "services", "src"}
    _WORKER_MARKERS = {"worker", "workers", "jobs", "queue", "queues", "tasks"}
    _DATASTORE_HINTS = {
        "postgres": ("database", "postgres"),
        "postgresql": ("database", "postgres"),
        "mysql": ("database", "mysql"),
        "mariadb": ("database", "mariadb"),
        "sqlite": ("database", "sqlite"),
        "mongodb": ("database", "mongodb"),
        "mongo": ("database", "mongodb"),
        "memgraph": ("graph", "memgraph"),
        "neo4j": ("graph", "neo4j"),
    }
    _CACHE_HINTS = {"redis": "redis", "memcached": "memcached"}
    _QUEUE_HINTS = {
        "rabbitmq": "rabbitmq",
        "kafka": "kafka",
        "nats": "nats",
        "sqs": "sqs",
        "celery": "celery",
        "bullmq": "bullmq",
        "bull": "bull",
    }

    def __init__(self, repo_path: Path, project_name: str, ingestor: object) -> None:
        self.repo_path = repo_path.resolve()
        self.project_name = project_name
        self.ingestor = ingestor

    def enrich(self) -> dict[str, object]:
        if not all(
            hasattr(self.ingestor, attr)
            for attr in ("ensure_node_batch", "ensure_relationship_batch", "fetch_all")
        ):
            return {
                "status": "skipped",
                "reason": "ingestor_missing_query_or_batch_api",
            }
        ingestor = self._ingestor_api()
        if ingestor is None:
            return {
                "status": "skipped",
                "reason": "ingestor_missing_query_or_batch_api",
            }

        services = self._collect_services()
        resources = self._collect_infra_resources()
        datastores, caches, queues = self._collect_runtime_systems(resources)
        graphql_ops = self._collect_graphql_operations()
        endpoints = self._fetch_endpoints()
        requester_links = self._fetch_requesters()

        project_spec = (
            cs.NodeLabel.PROJECT,
            cs.KEY_NAME,
            self.project_name,
        )

        for service in services.values():
            service_qn = str(service.get("qualified_name", "")).strip()
            self._ensure_named_node(cs.NodeLabel.SERVICE, service_qn, service)
            ingestor.ensure_relationship_batch(
                project_spec,
                cs.RelationshipType.CONTAINS,
                (cs.NodeLabel.SERVICE, cs.KEY_QUALIFIED_NAME, service_qn),
            )

        for resource in resources:
            resource_qn = str(resource.get("qualified_name", "")).strip()
            self._ensure_named_node(
                cs.NodeLabel.INFRA_RESOURCE,
                resource_qn,
                resource,
            )
            ingestor.ensure_relationship_batch(
                project_spec,
                cs.RelationshipType.CONTAINS,
                (
                    cs.NodeLabel.INFRA_RESOURCE,
                    cs.KEY_QUALIFIED_NAME,
                    resource_qn,
                ),
            )

        for store in datastores:
            store_qn = str(store.get("qualified_name", "")).strip()
            self._ensure_named_node(cs.NodeLabel.DATA_STORE, store_qn, store)
            ingestor.ensure_relationship_batch(
                project_spec,
                cs.RelationshipType.CONTAINS,
                (
                    cs.NodeLabel.DATA_STORE,
                    cs.KEY_QUALIFIED_NAME,
                    store_qn,
                ),
            )

        for cache in caches:
            cache_qn = str(cache.get("qualified_name", "")).strip()
            self._ensure_named_node(
                cs.NodeLabel.CACHE_STORE,
                cache_qn,
                cache,
            )
            ingestor.ensure_relationship_batch(
                project_spec,
                cs.RelationshipType.CONTAINS,
                (
                    cs.NodeLabel.CACHE_STORE,
                    cs.KEY_QUALIFIED_NAME,
                    cache_qn,
                ),
            )

        for queue in queues:
            queue_qn = str(queue.get("qualified_name", "")).strip()
            self._ensure_named_node(cs.NodeLabel.QUEUE, queue_qn, queue)
            ingestor.ensure_relationship_batch(
                project_spec,
                cs.RelationshipType.CONTAINS,
                (cs.NodeLabel.QUEUE, cs.KEY_QUALIFIED_NAME, queue_qn),
            )

        for operation in graphql_ops:
            operation_qn = str(operation.get("qualified_name", "")).strip()
            self._ensure_named_node(
                cs.NodeLabel.GRAPHQL_OPERATION,
                operation_qn,
                operation,
            )
            owner_qn = self._service_qn(
                self._match_service_for_path(str(operation.get("path", "")), services)
            )
            ingestor.ensure_relationship_batch(
                (cs.NodeLabel.SERVICE, cs.KEY_QUALIFIED_NAME, owner_qn),
                cs.RelationshipType.OWNS_GRAPHQL_OPERATION,
                (
                    cs.NodeLabel.GRAPHQL_OPERATION,
                    cs.KEY_QUALIFIED_NAME,
                    operation_qn,
                ),
            )

        for resource in resources:
            resource_service_name = str(resource.get("service_name", "")).strip()
            if not resource_service_name:
                continue
            service_qn = self._service_qn(resource_service_name)
            resource_qn = str(resource.get("qualified_name", "")).strip()
            if not resource_qn:
                continue
            ingestor.ensure_relationship_batch(
                (cs.NodeLabel.INFRA_RESOURCE, cs.KEY_QUALIFIED_NAME, resource_qn),
                cs.RelationshipType.DEPLOYS_SERVICE,
                (cs.NodeLabel.SERVICE, cs.KEY_QUALIFIED_NAME, service_qn),
            )

        for endpoint in endpoints:
            endpoint_qn = str(endpoint.get("qualified_name", "")).strip()
            service_name = self._match_service_for_path(
                str(endpoint.get("file_path", "")),
                services,
            )
            if not endpoint_qn:
                continue
            ingestor.ensure_relationship_batch(
                (
                    cs.NodeLabel.SERVICE,
                    cs.KEY_QUALIFIED_NAME,
                    self._service_qn(service_name),
                ),
                cs.RelationshipType.EXPOSES_ENDPOINT,
                (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn),
                {
                    "framework": str(endpoint.get("framework", "")).strip(),
                    "route_path": str(endpoint.get("route_path", "")).strip(),
                },
            )

        for requester in requester_links:
            source_service_name = self._match_service_for_path(
                str(requester.get("source_path", "")),
                services,
            )
            target_service_name = self._match_service_for_path(
                str(requester.get("endpoint_file_path", "")),
                services,
            )
            target_endpoint_qn = str(requester.get("endpoint_qn", "")).strip()
            if source_service_name == target_service_name:
                resolved_target = self._resolve_request_target_service(
                    requester,
                    endpoints,
                    services,
                    source_service_name,
                )
                if resolved_target is None:
                    continue
                target_service_name, target_endpoint_qn = resolved_target
            if source_service_name == target_service_name:
                continue
            ingestor.ensure_relationship_batch(
                (
                    cs.NodeLabel.SERVICE,
                    cs.KEY_QUALIFIED_NAME,
                    self._service_qn(source_service_name),
                ),
                cs.RelationshipType.CALLS_SERVICE,
                (
                    cs.NodeLabel.SERVICE,
                    cs.KEY_QUALIFIED_NAME,
                    self._service_qn(target_service_name),
                ),
                {
                    "endpoint_qn": target_endpoint_qn,
                    "route_path": str(requester.get("route_path", "")).strip(),
                    "source_path": str(requester.get("source_path", "")).strip(),
                },
            )

        for service in services.values():
            service_qn = str(service.get("qualified_name", "")).strip()
            role = str(service.get("role", "")).strip()
            service_name = str(service.get("name", "")).strip()
            for store in datastores:
                if self._service_uses_system(service_name, service, store):
                    ingestor.ensure_relationship_batch(
                        (cs.NodeLabel.SERVICE, cs.KEY_QUALIFIED_NAME, service_qn),
                        cs.RelationshipType.CONNECTS_TO_DATASTORE,
                        (
                            cs.NodeLabel.DATA_STORE,
                            cs.KEY_QUALIFIED_NAME,
                            str(store.get("qualified_name", "")).strip(),
                        ),
                    )
            for resource in resources:
                resource_service_name = str(resource.get("service_name", "")).strip()
                if resource_service_name != service_name:
                    continue
                depends_on = resource.get("depends_on", [])
                if not isinstance(depends_on, list):
                    continue
                for dependency in depends_on:
                    dependency_name = str(dependency).strip()
                    if not dependency_name:
                        continue
                    ingestor.ensure_relationship_batch(
                        (
                            cs.NodeLabel.SERVICE,
                            cs.KEY_QUALIFIED_NAME,
                            service_qn,
                        ),
                        cs.RelationshipType.CALLS_SERVICE,
                        (
                            cs.NodeLabel.SERVICE,
                            cs.KEY_QUALIFIED_NAME,
                            self._service_qn(dependency_name),
                        ),
                        {
                            "source": "infra_dependency",
                            "resource_path": str(resource.get("path", "")).strip(),
                        },
                    )
            for cache in caches:
                if role in {"backend", "worker"} or self._service_uses_system(
                    service_name, service, cache
                ):
                    ingestor.ensure_relationship_batch(
                        (cs.NodeLabel.SERVICE, cs.KEY_QUALIFIED_NAME, service_qn),
                        cs.RelationshipType.USES_CACHE,
                        (
                            cs.NodeLabel.CACHE_STORE,
                            cs.KEY_QUALIFIED_NAME,
                            str(cache.get("qualified_name", "")).strip(),
                        ),
                    )
            for queue in queues:
                if role == "worker" or self._service_uses_system(
                    service_name, service, queue
                ):
                    ingestor.ensure_relationship_batch(
                        (cs.NodeLabel.SERVICE, cs.KEY_QUALIFIED_NAME, service_qn),
                        cs.RelationshipType.USES_QUEUE,
                        (
                            cs.NodeLabel.QUEUE,
                            cs.KEY_QUALIFIED_NAME,
                            str(queue.get("qualified_name", "")).strip(),
                        ),
                    )

        if hasattr(ingestor, "flush_all"):
            ingestor.flush_all()

        return {
            "status": "ok",
            "services": len(services),
            "infra_resources": len(resources),
            "datastores": len(datastores),
            "caches": len(caches),
            "queues": len(queues),
            "graphql_operations": len(graphql_ops),
            "endpoints": len(endpoints),
        }

    def _collect_services(self) -> dict[str, dict[str, object]]:
        services: dict[str, dict[str, object]] = {}
        services["core"] = self._service_payload("core", role="backend", root_path=".")

        for resource in self._collect_infra_resources():
            service_name = str(resource.get("service_name", "")).strip()
            if service_name:
                services.setdefault(
                    service_name,
                    self._service_payload(
                        service_name,
                        role=self._role_for_name(service_name),
                        root_path=str(resource.get("path", ".")),
                    ),
                )

        for file_path in self._iter_repo_files(limit=1000):
            relative = file_path.relative_to(self.repo_path).as_posix()
            parts = relative.split("/")
            markers = set(part.lower() for part in parts)
            if not self._FRONTEND_MARKERS.isdisjoint(markers):
                services.setdefault(
                    "frontend",
                    self._service_payload(
                        "frontend", role="frontend", root_path=parts[0]
                    ),
                )
            if not self._BACKEND_MARKERS.isdisjoint(markers):
                services.setdefault(
                    "backend",
                    self._service_payload(
                        "backend", role="backend", root_path=parts[0]
                    ),
                )
            if not self._WORKER_MARKERS.isdisjoint(markers):
                services.setdefault(
                    "worker",
                    self._service_payload("worker", role="worker", root_path=parts[0]),
                )

        return services

    def _collect_infra_resources(self) -> list[dict[str, object]]:
        resources: list[dict[str, object]] = []
        for file_path in self._iter_repo_files(limit=300):
            relative = file_path.relative_to(self.repo_path).as_posix()
            config_type = self.detect_config_type(relative)
            if not config_type:
                continue
            try:
                parsed = self.parse_config_file(str(file_path))
            except Exception:
                continue

            if config_type == "docker-compose":
                services = parsed.get("services", [])
                if isinstance(services, list):
                    for item in services:
                        service_name = str(
                            getattr(item, "name", "") or item.get("name", "")
                        ).strip()
                        if not service_name:
                            continue
                        resources.append(
                            {
                                "qualified_name": self._infra_qn(
                                    "compose_service", service_name
                                ),
                                "name": service_name,
                                "service_name": service_name,
                                "kind": "docker-compose",
                                "path": relative,
                                "image": str(
                                    getattr(item, "image", "") or item.get("image", "")
                                ).strip(),
                                "build": str(
                                    getattr(item, "build", "") or item.get("build", "")
                                ).strip(),
                                "environment": (
                                    getattr(item, "environment", {})
                                    if not isinstance(item, dict)
                                    else item.get("environment", {})
                                ),
                                "depends_on": (
                                    getattr(item, "depends_on", [])
                                    if not isinstance(item, dict)
                                    else item.get("depends_on", [])
                                ),
                            }
                        )
            elif config_type == "kubernetes":
                k8s_resources = parsed.get("resources", [])
                if isinstance(k8s_resources, list):
                    for item in k8s_resources:
                        kind = str(
                            getattr(item, "kind", "")
                            if not isinstance(item, dict)
                            else item.get("kind", "")
                        ).strip()
                        name = str(
                            getattr(item, "name", "")
                            if not isinstance(item, dict)
                            else item.get("name", "")
                        ).strip()
                        if not kind or not name:
                            continue
                        resources.append(
                            {
                                "qualified_name": self._infra_qn(
                                    "k8s", f"{kind}:{name}"
                                ),
                                "name": f"{kind}:{name}",
                                "service_name": name,
                                "kind": "kubernetes",
                                "path": relative,
                            }
                        )
        return resources

    def _collect_runtime_systems(
        self,
        resources: list[dict[str, object]],
    ) -> tuple[
        list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]
    ]:
        datastores: dict[str, dict[str, object]] = {}
        caches: dict[str, dict[str, object]] = {}
        queues: dict[str, dict[str, object]] = {}

        for resource in resources:
            haystack = " ".join(
                [
                    str(resource.get("name", "")),
                    str(resource.get("image", "")),
                    json.dumps(resource.get("environment", {}), ensure_ascii=False),
                    json.dumps(resource.get("depends_on", []), ensure_ascii=False),
                ]
            ).lower()
            for hint, (kind, engine) in self._DATASTORE_HINTS.items():
                if hint in haystack:
                    qualified_name = self._system_qn(kind, engine)
                    datastores.setdefault(
                        qualified_name,
                        {
                            "qualified_name": qualified_name,
                            "name": engine,
                            "kind": kind,
                            "engine": engine,
                        },
                    )
            for hint, engine in self._CACHE_HINTS.items():
                if hint in haystack:
                    qualified_name = self._system_qn("cache", engine)
                    caches.setdefault(
                        qualified_name,
                        {
                            "qualified_name": qualified_name,
                            "name": engine,
                            "kind": "cache",
                            "engine": engine,
                        },
                    )
            for hint, engine in self._QUEUE_HINTS.items():
                if hint in haystack:
                    qualified_name = self._system_qn("queue", engine)
                    queues.setdefault(
                        qualified_name,
                        {
                            "qualified_name": qualified_name,
                            "name": engine,
                            "kind": "queue",
                            "engine": engine,
                        },
                    )
        return list(datastores.values()), list(caches.values()), list(queues.values())

    def _collect_graphql_operations(self) -> list[dict[str, object]]:
        operations: list[dict[str, object]] = []
        pattern = re.compile(r"\b(query|mutation|subscription)\s+([A-Za-z0-9_]+)")
        for file_path in self._iter_repo_files(limit=200):
            if file_path.suffix.lower() not in cs.GRAPHQL_EXTENSIONS:
                continue
            try:
                content = file_path.read_text(
                    encoding=cs.ENCODING_UTF8, errors="ignore"
                )
            except Exception:
                continue
            relative = file_path.relative_to(self.repo_path).as_posix()
            for match in pattern.finditer(content):
                op_type = match.group(1)
                name = match.group(2)
                operations.append(
                    {
                        "qualified_name": (
                            f"{self.project_name}.graphql.{op_type}.{name}"
                        ),
                        "name": name,
                        "operation_type": op_type,
                        "path": relative,
                    }
                )
        return operations

    def _fetch_endpoints(self) -> list[dict[str, object]]:
        ingestor = self._ingestor_api()
        if ingestor is None:
            return []
        rows = ingestor.fetch_all(
            """
            MATCH (e:Endpoint {project_name: $project_name})
            RETURN
              coalesce(e.qualified_name, '') AS qualified_name,
              coalesce(e.path, '') AS file_path,
              coalesce(e.route_path, '') AS route_path,
              coalesce(e.framework, '') AS framework
            """,
            {cs.KEY_PROJECT_NAME: self.project_name},
        )
        return [cast(dict[str, object], row) for row in rows if isinstance(row, dict)]

    def _fetch_requesters(self) -> list[dict[str, object]]:
        ingestor = self._ingestor_api()
        if ingestor is None:
            return []
        rows = ingestor.fetch_all(
            """
            MATCH (source)-[:REQUESTS_ENDPOINT]->(e:Endpoint {project_name: $project_name})
            RETURN
              coalesce(source.path, '') AS source_path,
              coalesce(e.path, '') AS endpoint_file_path,
              coalesce(e.qualified_name, '') AS endpoint_qn,
              coalesce(e.route_path, '') AS route_path,
              coalesce(e.http_method, '') AS http_method,
              coalesce(e.framework, '') AS framework
            """,
            {cs.KEY_PROJECT_NAME: self.project_name},
        )
        return [cast(dict[str, object], row) for row in rows if isinstance(row, dict)]

    def _resolve_request_target_service(
        self,
        requester: dict[str, object],
        endpoints: list[dict[str, object]],
        services: dict[str, dict[str, object]],
        source_service_name: str,
    ) -> tuple[str, str] | None:
        route_path = str(requester.get("route_path", "")).strip()
        http_method = str(requester.get("http_method", "")).strip().upper()
        request_endpoint_qn = str(requester.get("endpoint_qn", "")).strip()
        source_path = str(requester.get("source_path", "")).strip()
        if not route_path:
            return None

        for endpoint in endpoints:
            candidate_qn = str(endpoint.get("qualified_name", "")).strip()
            candidate_path = str(endpoint.get("file_path", "")).strip()
            candidate_route = str(endpoint.get("route_path", "")).strip()
            candidate_framework = str(endpoint.get("framework", "")).strip().lower()
            candidate_method = str(endpoint.get("http_method", "")).strip().upper()
            if not candidate_qn or candidate_qn == request_endpoint_qn:
                continue
            if candidate_route != route_path:
                continue
            if http_method and candidate_method and http_method != candidate_method:
                continue
            if candidate_framework in {"http", "graphql"}:
                continue
            if candidate_path == source_path:
                continue

            target_service_name = self._match_service_for_path(candidate_path, services)
            if not target_service_name or target_service_name == source_service_name:
                continue
            return target_service_name, candidate_qn

        return None

    def _iter_repo_files(self, *, limit: int) -> list[Path]:
        files: list[Path] = []
        for path in self.repo_path.rglob("*"):
            if len(files) >= limit:
                break
            if not path.is_file():
                continue
            if any(part in self._SKIP_DIRS for part in path.parts):
                continue
            files.append(path)
        return files

    def _match_service_for_path(
        self,
        file_path: str,
        services: dict[str, dict[str, object]],
    ) -> str:
        normalized = file_path.replace("\\", "/").lower()
        if any(marker in normalized.split("/") for marker in self._FRONTEND_MARKERS):
            return "frontend" if "frontend" in services else "core"
        if any(marker in normalized.split("/") for marker in self._WORKER_MARKERS):
            return "worker" if "worker" in services else "core"
        if any(marker in normalized.split("/") for marker in self._BACKEND_MARKERS):
            return "backend" if "backend" in services else "core"
        for service_name, payload in services.items():
            root_path = str(payload.get("root_path", "")).strip().lower()
            if root_path and root_path in normalized:
                return service_name
        return "core"

    def _service_uses_system(
        self,
        service_name: str,
        service_payload: dict[str, object],
        system_payload: dict[str, object],
    ) -> bool:
        service_haystack = " ".join(
            [
                service_name.lower(),
                str(service_payload.get("role", "")).lower(),
                str(service_payload.get("root_path", "")).lower(),
            ]
        )
        system_engine = str(system_payload.get("engine", "")).lower()
        if system_engine and system_engine in service_haystack:
            return True
        if str(service_payload.get("role", "")).lower() in {"backend", "worker"}:
            return True
        return False

    def _ensure_named_node(
        self,
        label: str,
        qualified_name: str,
        payload: dict[str, object],
    ) -> None:
        node_payload = {
            cs.KEY_QUALIFIED_NAME: qualified_name,
            cs.KEY_NAME: str(payload.get("name", "")).strip() or qualified_name,
            cs.KEY_PROJECT_NAME: self.project_name,
            **{
                key: value
                for key, value in payload.items()
                if key not in {cs.KEY_QUALIFIED_NAME, cs.KEY_NAME}
            },
        }
        ingestor = self._ingestor_api()
        if ingestor is None:
            return
        ingestor.ensure_node_batch(label, node_payload)

    def _service_payload(
        self, name: str, *, role: str, root_path: str
    ) -> dict[str, object]:
        return {
            "qualified_name": self._service_qn(name),
            "name": name,
            "role": role,
            "root_path": root_path,
        }

    def _service_qn(self, service_name: str) -> str:
        normalized = service_name.strip().lower() or "core"
        return f"{self.project_name}.service.{normalized}"

    def _infra_qn(self, kind: str, name: str) -> str:
        normalized = name.replace("/", ".").replace(":", ".").strip().lower()
        return f"{self.project_name}.infra.{kind}.{normalized}"

    def _system_qn(self, kind: str, name: str) -> str:
        normalized = name.strip().lower()
        return f"{self.project_name}.{kind}.{normalized}"

    def _role_for_name(self, service_name: str) -> str:
        lowered = service_name.lower()
        if lowered in {"frontend", "web", "ui", "client"}:
            return "frontend"
        if lowered in {"worker", "jobs", "queue"}:
            return "worker"
        return "backend"

    def _ingestor_api(self) -> TopologyGraphIngestorProtocol | None:
        required = ("ensure_node_batch", "ensure_relationship_batch", "fetch_all")
        if not all(hasattr(self.ingestor, attr) for attr in required):
            return None
        return cast(TopologyGraphIngestorProtocol, self.ingestor)
