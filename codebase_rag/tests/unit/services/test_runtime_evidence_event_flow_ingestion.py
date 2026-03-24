from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from codebase_rag.core import constants as cs
from codebase_rag.services.runtime_evidence import RuntimeEvidenceIngestor


class _FakeRuntimeIngestor:
    def __init__(self, project_name: str) -> None:
        self.project_name = project_name
        self.nodes: list[tuple[str, dict[str, object]]] = []
        self.relationships: list[
            tuple[
                tuple[str, str, str],
                str,
                tuple[str, str, str],
                dict[str, object] | None,
            ]
        ] = []
        self.event_flow_rows = [
            {
                "qualified_name": (
                    f"{project_name}.semantic.event_flow.invoice.created_invoice-events"
                ),
                "canonical_key": "invoice.created@invoice-events",
                "event_name": "invoice.created",
                "channel_name": "invoice-events",
            }
        ]
        self.queue_rows = [
            {
                "qualified_name": f"{project_name}.semantic.queue.invoice-events",
                "queue_name": "invoice-events",
            },
            {
                "qualified_name": f"{project_name}.semantic.queue.invoice-events-dlq",
                "queue_name": "invoice-events-dlq",
            },
        ]
        self.handler_rows = [
            {
                "labels": [str(cs.NodeLabel.METHOD)],
                "qualified_name": (
                    f"{project_name}.main.InvoiceWorker.handle_invoice_created"
                ),
                "name": "handle_invoice_created",
            }
        ]

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
        _ = parameters
        if "MATCH (e:EventFlow" in query:
            return cast(list[object], self.event_flow_rows)
        if "MATCH (q:Queue" in query:
            return cast(list[object], self.queue_rows)
        if "AND (n:Function OR n:Method)" in query:
            return cast(list[object], self.handler_rows)
        return []


def _write_runtime_artifact(repo_path: Path) -> None:
    runtime_dir = repo_path / "output" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "event_name": "invoice.created",
        "queue": "invoice_events",
        "dlq": "invoice-events-dlq",
        "handler": "InvoiceWorker.handle_invoice_created",
        "stage": "consume",
        "retry_count": 2,
    }
    (runtime_dir / "events.ndjson").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )


def test_runtime_evidence_reconciles_runtime_events_to_static_event_graph(
    temp_repo: Path,
) -> None:
    repo_path = temp_repo / "runtime_event_graph"
    repo_path.mkdir()
    _write_runtime_artifact(repo_path)

    ingestor = _FakeRuntimeIngestor(project_name=repo_path.name)
    result = RuntimeEvidenceIngestor(
        repo_path=repo_path,
        project_name=repo_path.name,
        ingestor=ingestor,
    ).ingest_available()

    assert result == {"status": "ok", "artifacts": 1, "events": 1}

    runtime_artifacts = [
        props
        for label, props in ingestor.nodes
        if label == cs.NodeLabel.RUNTIME_ARTIFACT
    ]
    assert len(runtime_artifacts) == 1
    assert runtime_artifacts[0]["path"] == "output/runtime/events.ndjson"
    assert runtime_artifacts[0]["repo_rel_path"] == "output/runtime/events.ndjson"
    assert (
        runtime_artifacts[0]["abs_path"]
        == (repo_path / "output" / "runtime" / "events.ndjson").resolve().as_posix()
    )

    runtime_events = [
        props for label, props in ingestor.nodes if label == cs.NodeLabel.RUNTIME_EVENT
    ]
    assert len(runtime_events) == 1
    event_props = runtime_events[0]
    assert event_props["path"] == "output/runtime/events.ndjson"
    assert event_props["repo_rel_path"] == "output/runtime/events.ndjson"
    assert event_props["file_path"] == "output/runtime/events.ndjson"
    assert (
        event_props["abs_path"]
        == (repo_path / "output" / "runtime" / "events.ndjson").resolve().as_posix()
    )
    assert event_props["normalized_event_name"] == "invoice.created"
    assert event_props["normalized_channel_name"] == "invoice-events"
    assert event_props["normalized_dlq_name"] == "invoice-events-dlq"
    assert event_props["canonical_key"] == "invoice.created@invoice-events"
    assert event_props["has_retry"] is True

    observed_relationships = [
        rel
        for rel in ingestor.relationships
        if rel[1] == cs.RelationshipType.OBSERVED_IN_RUNTIME
    ]
    assert any(rel[2][0] == cs.NodeLabel.EVENT_FLOW for rel in observed_relationships)
    assert any(
        rel[2] == (cs.NodeLabel.FILE, cs.KEY_PATH, "output/runtime/events.ndjson")
        for rel in observed_relationships
    )
    assert any(
        rel[0][0] == cs.NodeLabel.EVENT_FLOW and rel[2][0] == cs.NodeLabel.RUNTIME_EVENT
        for rel in observed_relationships
    )
    assert any(
        rel[2]
        == (
            cs.NodeLabel.QUEUE,
            cs.KEY_QUALIFIED_NAME,
            f"{repo_path.name}.semantic.queue.invoice-events",
        )
        for rel in observed_relationships
    )
    assert any(
        rel[2]
        == (
            cs.NodeLabel.QUEUE,
            cs.KEY_QUALIFIED_NAME,
            f"{repo_path.name}.semantic.queue.invoice-events-dlq",
        )
        for rel in observed_relationships
    )
    assert any(
        rel[2]
        == (
            cs.NodeLabel.METHOD,
            cs.KEY_QUALIFIED_NAME,
            f"{repo_path.name}.main.InvoiceWorker.handle_invoice_created",
        )
        for rel in observed_relationships
    )
    assert any(
        rel[0]
        == (
            cs.NodeLabel.METHOD,
            cs.KEY_QUALIFIED_NAME,
            f"{repo_path.name}.main.InvoiceWorker.handle_invoice_created",
        )
        and rel[2][0] == cs.NodeLabel.RUNTIME_EVENT
        for rel in observed_relationships
    )
