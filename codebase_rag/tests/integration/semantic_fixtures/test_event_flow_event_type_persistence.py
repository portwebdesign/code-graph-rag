from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.tests.conftest import get_nodes, get_relationships, run_updater


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_event_flow_nodes_and_edges_include_event_type(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "event_flow_event_type"
    project.mkdir()

    _write(
        project / "main.py",
        """def consumer(event: str, queue: str, dlq: str | None = None):
    def decorator(fn):
        return fn
    return decorator


class Publisher:
    def publish(self, event: str, payload: dict[str, object], queue: str) -> None:
        return None


publisher = Publisher()


def dispatch_invoice_created() -> None:
    publisher.publish("invoice.created", {"ok": True}, queue="invoice-events")


class InvoiceWorker:
    @consumer("invoice.created", queue="invoice-events", dlq="invoice-events-dlq")
    def handle_invoice_created(self, message: dict[str, object]) -> None:
        return None
""",
    )

    run_updater(project, mock_ingestor)

    event_flow_nodes = [
        cast(dict[str, object], call.args[1])
        for call in get_nodes(mock_ingestor, cs.NodeLabel.EVENT_FLOW)
    ]
    assert event_flow_nodes
    assert all(str(node.get("event_type", "")).strip() for node in event_flow_nodes)

    edge_calls = (
        get_relationships(mock_ingestor, cs.RelationshipType.PUBLISHES_EVENT)
        + get_relationships(mock_ingestor, cs.RelationshipType.CONSUMES_EVENT)
        + get_relationships(mock_ingestor, cs.RelationshipType.USES_QUEUE)
    )
    assert edge_calls
    for call in edge_calls:
        metadata = cast(dict[str, object], call.args[3])
        assert str(metadata.get("event_type", "")).strip()
