from __future__ import annotations

from codebase_rag.parsers.pipeline.python_event_flows import extract_python_event_flows


def test_extract_python_event_flows_detects_outbox_publish_consume_and_replay() -> None:
    observations = extract_python_event_flows(
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


def persist_invoice_outbox() -> None:
    redis_stream_outbox.publish("invoice.created", {"ok": True}, stream="invoice-events")


def dispatch_invoice_created() -> None:
    publisher.publish("invoice.created", {"ok": True}, queue="invoice-events")


class InvoiceWorker:
    @consumer("invoice.created", queue="invoice-events", dlq="invoice-events-dlq")
    def handle_invoice_created(self, message: dict[str, object]) -> None:
        return None


def replay_invoice_created() -> None:
    replay_events("invoice.created", queue="invoice-events", dlq="invoice-events-dlq")
"""
    )

    summary = {
        (
            item.symbol_name,
            item.stage,
            item.event_name,
            item.channel_name,
            item.dlq_name,
        )
        for item in observations
    }
    assert (
        "persist_invoice_outbox",
        "outbox",
        "invoice.created",
        "invoice-events",
        None,
    ) in summary
    assert (
        "dispatch_invoice_created",
        "publish",
        "invoice.created",
        "invoice-events",
        None,
    ) in summary
    assert (
        "InvoiceWorker.handle_invoice_created",
        "consume",
        "invoice.created",
        "invoice-events",
        "invoice-events-dlq",
    ) in summary
    assert (
        "replay_invoice_created",
        "replay",
        "invoice.created",
        "invoice-events",
        "invoice-events-dlq",
    ) in summary
