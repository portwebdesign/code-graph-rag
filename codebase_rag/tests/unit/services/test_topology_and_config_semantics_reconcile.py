from __future__ import annotations

from pathlib import Path

from codebase_rag.core import constants as cs
from codebase_rag.services.topology_graph_enricher import TopologyGraphEnricher


class _RecordingIngestor:
    def __init__(self) -> None:
        self.nodes: list[tuple[str, dict[str, object]]] = []
        self.relationships: list[
            tuple[
                tuple[str, str, str],
                str,
                tuple[str, str, str],
                dict[str, object] | None,
            ]
        ] = []

    def ensure_node_batch(self, label: str, payload: dict[str, object]) -> None:
        self.nodes.append((label, payload))

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
        del query, parameters
        return []


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_topology_fixture(repo_path: Path) -> None:
    _write(
        repo_path / "docker-compose.yml",
        """services:
  api:
    image: demo/api:latest
    environment:
      APP_SECRET: ${APP_SECRET}
      FEATURE_BILLING: "1"
""",
    )
    _write(
        repo_path / "deployment.yaml",
        """apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  template:
    spec:
      containers:
        - name: api
          image: demo/api:latest
          env:
            - name: APP_SECRET
              valueFrom:
                secretKeyRef:
                  name: api-secrets
                  key: app-secret
            - name: NEXT_PUBLIC_API_URL
              value: "https://api.example.test"
""",
    )


def test_topology_enricher_projects_config_edges_with_canonical_identities(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CODEGRAPH_CONFIG_SEMANTICS", "1")
    _write_topology_fixture(tmp_path)
    ingestor = _RecordingIngestor()

    TopologyGraphEnricher(tmp_path, "demo", ingestor).enrich()

    sets_env = [
        rel for rel in ingestor.relationships if rel[1] == cs.RelationshipType.SETS_ENV
    ]
    uses_secret = [
        rel
        for rel in ingestor.relationships
        if rel[1] == cs.RelationshipType.USES_SECRET
    ]

    assert any(
        rel[0][2] == "demo.infra.compose_service.api"
        and rel[2][2] == "demo.semantic.env_var.APP_SECRET"
        for rel in sets_env
    )
    assert any(
        rel[0][2] == "demo.infra.k8s.deployment.api"
        and rel[2][2] == "demo.semantic.env_var.NEXT_PUBLIC_API_URL"
        for rel in sets_env
    )
    assert any(
        rel[0][2] == "demo.infra.k8s.deployment.api"
        and rel[2][2] == "demo.semantic.secret_ref.API_SECRETS"
        for rel in uses_secret
    )

    env_nodes = [
        payload for label, payload in ingestor.nodes if label == cs.NodeLabel.ENV_VAR
    ]
    assert any(
        payload.get(cs.KEY_SOURCE_PARSER) == "topology_graph_enricher"
        and payload.get(cs.KEY_PATH) == "docker-compose.yml"
        for payload in env_nodes
    )


def test_topology_enricher_respects_config_semantics_env_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CODEGRAPH_CONFIG_SEMANTICS", "0")
    _write_topology_fixture(tmp_path)
    ingestor = _RecordingIngestor()

    TopologyGraphEnricher(tmp_path, "demo", ingestor).enrich()

    assert not any(
        rel[1] in {cs.RelationshipType.SETS_ENV, cs.RelationshipType.USES_SECRET}
        for rel in ingestor.relationships
    )
    assert not any(
        label in {cs.NodeLabel.ENV_VAR, cs.NodeLabel.SECRET_REF}
        for label, _payload in ingestor.nodes
    )
