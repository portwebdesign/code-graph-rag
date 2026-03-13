from __future__ import annotations

from pathlib import Path

from codebase_rag.core import constants as cs
from codebase_rag.services.topology_graph_enricher import TopologyGraphEnricher


class _FakeIngestor:
    def __init__(
        self, endpoints: list[dict[str, object]], requesters: list[dict[str, object]]
    ) -> None:
        self._endpoints = endpoints
        self._requesters = requesters
        self.relationships: list[
            tuple[
                tuple[str, str, str],
                str,
                tuple[str, str, str],
                dict[str, object] | None,
            ]
        ] = []

    def ensure_node_batch(self, label: str, payload: dict[str, object]) -> None:
        del label, payload

    def ensure_relationship_batch(
        self,
        source: tuple[str, str, str],
        relationship_type: str,
        target: tuple[str, str, str],
        payload: dict[str, object] | None = None,
    ) -> None:
        self.relationships.append((source, relationship_type, target, payload))

    def flush_all(self) -> None:
        return None

    def fetch_all(
        self,
        query: str,
        parameters: dict[str, object] | None = None,
    ) -> list[object]:
        del parameters
        if "MATCH (source)-[:REQUESTS_ENDPOINT]" in query:
            return list(self._requesters)
        if "MATCH (e:Endpoint" in query:
            return list(self._endpoints)
        return []


class _StaticTopologyGraphEnricher(TopologyGraphEnricher):
    def _collect_services(self) -> dict[str, dict[str, object]]:
        return {
            "frontend": {
                "qualified_name": f"{self.project_name}.service.frontend",
                "name": "frontend",
                "role": "frontend",
                "root_path": "frontend",
            },
            "backend": {
                "qualified_name": f"{self.project_name}.service.backend",
                "name": "backend",
                "role": "backend",
                "root_path": "src",
            },
        }

    def _collect_infra_resources(self) -> list[dict[str, object]]:
        return []

    def _collect_runtime_systems(
        self, resources: list[dict[str, object]]
    ) -> tuple[
        list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]
    ]:
        del resources
        return [], [], []

    def _collect_graphql_operations(self) -> list[dict[str, object]]:
        return []


def test_bridges_frontend_requesters_to_backend_services_by_route_path(
    tmp_path: Path,
) -> None:
    ingestor = _FakeIngestor(
        endpoints=[
            {
                "qualified_name": "demo.endpoint.http.POST:/api/customers",
                "file_path": "frontend/src/app/page.tsx",
                "route_path": "/api/customers",
                "framework": "http",
                "http_method": "POST",
            },
            {
                "qualified_name": "demo.endpoint.fastapi.POST:/api/customers",
                "file_path": "src/api/customers.py",
                "route_path": "/api/customers",
                "framework": "fastapi",
                "http_method": "POST",
            },
        ],
        requesters=[
            {
                "source_path": "frontend/src/app/page.tsx",
                "endpoint_file_path": "frontend/src/app/page.tsx",
                "endpoint_qn": "demo.endpoint.http.POST:/api/customers",
                "route_path": "/api/customers",
                "http_method": "POST",
                "framework": "http",
            }
        ],
    )
    enricher = _StaticTopologyGraphEnricher(tmp_path, "demo", ingestor)

    enricher.enrich()

    calls_service = [
        rel
        for rel in ingestor.relationships
        if rel[1] == cs.RelationshipType.CALLS_SERVICE
    ]
    assert any(
        rel[0] == (cs.NodeLabel.SERVICE, cs.KEY_QUALIFIED_NAME, "demo.service.frontend")
        and rel[2]
        == (cs.NodeLabel.SERVICE, cs.KEY_QUALIFIED_NAME, "demo.service.backend")
        and (rel[3] or {}).get("endpoint_qn")
        == "demo.endpoint.fastapi.POST:/api/customers"
        for rel in calls_service
    )
