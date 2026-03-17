from codebase_rag.core.event_flow_identity import (
    build_event_flow_canonical_key,
    normalize_channel_name,
    normalize_event_name,
)


def test_normalize_event_name_collapses_common_separators() -> None:
    assert normalize_event_name("Invoice/Created") == "invoice.created"
    assert normalize_event_name("invoice_created") == "invoice.created"
    assert normalize_event_name("invoice-created") == "invoice.created"


def test_normalize_channel_name_collapses_common_separators() -> None:
    assert normalize_channel_name("invoice/events") == "invoice-events"
    assert normalize_channel_name("invoice_events") == "invoice-events"
    assert normalize_channel_name("invoice.events") == "invoice-events"


def test_build_event_flow_canonical_key_prefers_event_and_channel_pair() -> None:
    assert (
        build_event_flow_canonical_key(
            event_name="invoice.created",
            channel_name="invoice_events",
            fallback_name="handle_invoice_created",
        )
        == "invoice.created@invoice-events"
    )
    assert (
        build_event_flow_canonical_key(
            event_name="",
            channel_name="invoice-events",
            fallback_name="handle_invoice_created",
        )
        == "invoice-events"
    )
