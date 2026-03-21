from __future__ import annotations

from codebase_rag.parsers.pipeline.python_event_flows import extract_python_event_flows


def test_extract_event_type_from_message_type_keyword() -> None:
    observations = extract_python_event_flows(
        """class Publisher:
    def publish(self, payload: dict[str, object], queue: str, message_type: str) -> None:
        return None


publisher = Publisher()


def emit_invoice() -> None:
    publisher.publish({"ok": True}, queue="invoice-events", message_type="invoice.created")
"""
    )

    publish_obs = [item for item in observations if item.stage == "publish"]
    assert publish_obs
    assert publish_obs[0].event_type == "invoice.created"


def test_extract_event_type_fallback_for_stage_when_name_missing() -> None:
    observations = extract_python_event_flows(
        """def consumer(queue: str):
    def decorator(fn):
        return fn
    return decorator


class InvoiceWorker:
    @consumer(queue="invoice-events")
    def handle_invoice_created(self, message: dict[str, object]) -> None:
        return None
"""
    )

    consume_obs = [item for item in observations if item.stage == "consume"]
    assert consume_obs
    assert consume_obs[0].event_type == "consumed_event"
