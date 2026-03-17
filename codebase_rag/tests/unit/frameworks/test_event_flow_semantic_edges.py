from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

from codebase_rag.core import constants as cs
from codebase_rag.tests.conftest import get_nodes, get_relationships, run_updater


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _node_props(mock_ingestor: MagicMock, node_type: str) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], call[0][1])
        for call in get_nodes(mock_ingestor, node_type)
    ]


def test_materializes_event_flow_outbox_consumer_dlq_and_replay_edges(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    project = temp_repo / "event_flow_semantics"
    project.mkdir()

    _write(
        project / "main.py",
        """def consumer(event: str, queue: str, dlq: str | None = None):
    def decorator(fn):
        return fn
    return decorator


class RedisStreamOutbox:
    def publish(self, event: str, payload: dict[str, object], stream: str) -> None:
        return None


class BrokerPublisher:
    def publish(self, event: str, payload: dict[str, object], queue: str) -> None:
        return None


redis_stream_outbox = RedisStreamOutbox()
publisher = BrokerPublisher()


def replay_events(event: str, queue: str, dlq: str) -> None:
    return None


def persist_invoice_outbox(invoice_id: str) -> None:
    redis_stream_outbox.publish("invoice.created", {"invoice_id": invoice_id}, stream="invoice-events")


def dispatch_invoice_created(invoice_id: str) -> None:
    publisher.publish("invoice.created", {"invoice_id": invoice_id}, queue="invoice-events")


class InvoiceWorker:
    @consumer("invoice.created", queue="invoice-events", dlq="invoice-events-dlq")
    def handle_invoice_created(self, message: dict[str, object]) -> None:
        return None


def replay_invoice_created() -> None:
    replay_events("invoice.created", queue="invoice-events", dlq="invoice-events-dlq")
""",
    )

    run_updater(project, mock_ingestor)

    flow_nodes = _node_props(mock_ingestor, cs.NodeLabel.EVENT_FLOW)
    assert any(
        props.get(cs.KEY_NAME) == "invoice.created"
        and props.get("canonical_key") == "invoice.created@invoice-events"
        for props in flow_nodes
    )

    queue_nodes = _node_props(mock_ingestor, cs.NodeLabel.QUEUE)
    assert any(
        props.get(cs.KEY_NAME) == "invoice-events"
        and props.get("engine") == "redis-streams"
        for props in queue_nodes
    )
    assert any(props.get(cs.KEY_NAME) == "invoice-events-dlq" for props in queue_nodes)

    flow_qn = "event_flow_semantics.semantic.event_flow.invoice.created_invoice-events"
    queue_qn = "event_flow_semantics.semantic.queue.invoice-events"
    dlq_qn = "event_flow_semantics.semantic.queue.invoice-events-dlq"

    writes_outbox = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.WRITES_OUTBOX)
    ]
    assert any(
        rel[0]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "event_flow_semantics.main.persist_invoice_outbox",
        )
        and rel[2] == (cs.NodeLabel.EVENT_FLOW, cs.KEY_QUALIFIED_NAME, flow_qn)
        for rel in writes_outbox
    )

    publishes = [
        call.args
        for call in get_relationships(
            mock_ingestor, cs.RelationshipType.PUBLISHES_EVENT
        )
    ]
    assert any(
        rel[0]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "event_flow_semantics.main.dispatch_invoice_created",
        )
        and rel[2] == (cs.NodeLabel.EVENT_FLOW, cs.KEY_QUALIFIED_NAME, flow_qn)
        for rel in publishes
    )

    consumes = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.CONSUMES_EVENT)
    ]
    assert any(
        rel[0]
        == (
            cs.NodeLabel.METHOD,
            cs.KEY_QUALIFIED_NAME,
            "event_flow_semantics.main.InvoiceWorker.handle_invoice_created",
        )
        and rel[2] == (cs.NodeLabel.EVENT_FLOW, cs.KEY_QUALIFIED_NAME, flow_qn)
        for rel in consumes
    )

    writes_dlq = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.WRITES_DLQ)
    ]
    assert any(
        rel[0]
        == (
            cs.NodeLabel.METHOD,
            cs.KEY_QUALIFIED_NAME,
            "event_flow_semantics.main.InvoiceWorker.handle_invoice_created",
        )
        and rel[2] == (cs.NodeLabel.QUEUE, cs.KEY_QUALIFIED_NAME, dlq_qn)
        for rel in writes_dlq
    )

    replays = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.REPLAYS_EVENT)
    ]
    assert any(
        rel[0]
        == (
            cs.NodeLabel.FUNCTION,
            cs.KEY_QUALIFIED_NAME,
            "event_flow_semantics.main.replay_invoice_created",
        )
        and rel[2] == (cs.NodeLabel.EVENT_FLOW, cs.KEY_QUALIFIED_NAME, flow_qn)
        for rel in replays
    )

    uses_handler = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.USES_HANDLER)
    ]
    assert any(
        rel[0] == (cs.NodeLabel.EVENT_FLOW, cs.KEY_QUALIFIED_NAME, flow_qn)
        and rel[2]
        == (
            cs.NodeLabel.METHOD,
            cs.KEY_QUALIFIED_NAME,
            "event_flow_semantics.main.InvoiceWorker.handle_invoice_created",
        )
        for rel in uses_handler
    )

    uses_queue = [
        call.args
        for call in get_relationships(mock_ingestor, cs.RelationshipType.USES_QUEUE)
    ]
    assert any(
        rel[0] == (cs.NodeLabel.EVENT_FLOW, cs.KEY_QUALIFIED_NAME, flow_qn)
        and rel[2] == (cs.NodeLabel.QUEUE, cs.KEY_QUALIFIED_NAME, queue_qn)
        and cast(dict[str, object], rel[3]).get("queue_role") == "primary"
        for rel in uses_queue
    )
    assert any(
        rel[0] == (cs.NodeLabel.EVENT_FLOW, cs.KEY_QUALIFIED_NAME, flow_qn)
        and rel[2] == (cs.NodeLabel.QUEUE, cs.KEY_QUALIFIED_NAME, dlq_qn)
        and cast(dict[str, object], rel[3]).get("queue_role") == "dlq"
        for rel in uses_queue
    )
