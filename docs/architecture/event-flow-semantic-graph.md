# Event Flow Semantic Graph

## Scope

This document describes the current Python event-flow semantic slice in `code-graph-rag`.

The current implementation adds first-class semantic coverage for:

- outbox writes detected from Python call sites with `outbox`-family heuristics
- event publish calls detected from broker / publisher / stream / queue APIs
- consumer handlers detected from decorator and subscription-style patterns
- replay / redrive / requeue handlers detected from replay-style APIs
- primary queue and DLQ bindings attached to the same canonical event flow
- runtime event artifacts reconciled back onto static event-flow, queue, and handler nodes

## Nodes

The current event slice emits:

- `EventFlow`
- `Queue`
- `RuntimeArtifact`
- `RuntimeEvent`

`EventFlow` uses a deterministic `canonical_key` derived from normalized event name and channel name. The current first wave keeps this model intentionally compact instead of introducing separate `EventChannel` and `EventMessage` labels.

## Relationships

The current event slice emits:

- `WRITES_OUTBOX`
- `PUBLISHES_EVENT`
- `CONSUMES_EVENT`
- `WRITES_DLQ`
- `REPLAYS_EVENT`
- `USES_HANDLER`
- `USES_QUEUE`
- `OBSERVED_IN_RUNTIME`

This lets the graph express:

- producer -> event flow
- consumer -> event flow
- event flow -> queue
- event flow -> handler
- consumer / replay handler -> DLQ
- runtime artifact -> runtime event -> event flow / queue / handler
- event flow / queue / handler -> runtime event reverse evidence links

## Canonical identity

`EventFlow` nodes are keyed by a normalized identity:

- event name when available
- otherwise channel / queue name
- channel name appended when it adds disambiguation

Examples:

- `invoice.created@invoice-events`
- `payment.failed@payment-events`

This identity is then converted into a graph-safe qualified name via the shared semantic identity helper.

## Runtime reconciliation

Runtime evidence is ingested from repository-local runtime output directories such as:

- `output/runtime`
- `output/dynamic`
- `output/profiler`
- `coverage`
- `logs`

Supported runtime payload forms currently include:

- JSON
- NDJSON
- plain text log lines
- LCOV coverage files

For event-oriented artifacts, the runtime ingestor normalizes:

- `event_name`
- `queue` / `queue_name`
- `stream` / `stream_name`
- `topic` / `topic_name`
- `channel` / `channel_name`
- `dlq` / `dead_letter_queue` / `retry_queue`
- `handler` / `consumer` / `worker`
- `retry_count`
- `stage`

The same canonical identity helper used by the static event-flow pass is reused during runtime ingest. This is what lets runtime channel variants such as `invoice_events` reconcile back to static queue/event-flow nodes keyed as `invoice-events`.

Static and runtime planes are intentionally kept separate:

- static semantics stay on `EventFlow` / `Queue` / handler nodes
- dynamic observations are stored as `RuntimeArtifact` / `RuntimeEvent`
- reconciliation is represented with `OBSERVED_IN_RUNTIME` edges instead of mutating static nodes

## Current implementation notes

The current implementation lives in:

- `codebase_rag/parsers/pipeline/event_flow_pass.py`
- `codebase_rag/parsers/pipeline/python_event_flows.py`
- `codebase_rag/services/runtime_evidence.py`
- `codebase_rag/core/event_flow_identity.py`

Supported heuristic families currently include:

- `outbox.*publish(...)`, `*outbox*` call names, and string-literal outbox hints
- `publisher.publish(...)`, `broker.publish(...)`, `producer.send(...)`, `*.enqueue(...)`
- consumer decorators such as `@consumer(...)`, `@subscriber(...)`, `@worker(...)`, `@job(...)`
- replay / redrive / requeue call names such as `replay_*`, `redrive_*`, `requeue_*`
- queue binding keywords such as `queue=`, `stream=`, `topic=`, `channel=`
- DLQ binding keywords such as `dlq=`, `dead_letter_queue=`, `retry_queue=`

Queue engine inference is heuristic. The first wave recognizes names or mechanisms that hint:

- Redis Streams
- Kafka
- RabbitMQ
- BullMQ
- SQS

## Multi-hop support

`impact_graph` / `multi_hop_analysis` now traverse the event semantic surface through:

- `WRITES_OUTBOX`
- `PUBLISHES_EVENT`
- `CONSUMES_EVENT`
- `WRITES_DLQ`
- `REPLAYS_EVENT`
- `USES_HANDLER`
- `USES_QUEUE`
- `OBSERVED_IN_RUNTIME`

This enables paths such as:

- producer -> event flow -> consumer handler
- producer -> event flow -> consumer handler -> DLQ
- replay handler -> event flow -> queue
- producer -> event flow -> runtime event -> observed handler
- producer -> event flow -> runtime event -> observed queue / DLQ

## Reliability query presets

`build_event_reliability_query_pack()` now exposes first-wave reliability and safety queries for:

- `outbox_without_transaction`
- `consumer_without_dlq`
- `replay_paths`
- `external_call_before_commit`
- `duplicate_publishers`

These presets are surfaced through `get_schema_overview(scope="api")` and are intended for `run_cypher` or graph-first MCP workflows.

## Current limits

This is intentionally a first wave, not the full event-reliability backlog.

Current limits:

- the extractor is currently Python-only
- event semantics are heuristic and lexical, not full dataflow
- SQL outbox table inserts are only covered when outbox naming is visible at the call / literal level
- transaction ordering and side-effect boundaries are not modeled yet; that belongs to BL-254
- runtime reconciliation is name-based and bounded; it does not claim exact delivery guarantees or broker offset correctness

## Validation

The current delivery slice is covered by:

- `codebase_rag/tests/unit/parsers/pipeline/test_python_event_flows.py`
- `codebase_rag/tests/unit/frameworks/test_event_flow_semantic_edges.py`
- `codebase_rag/tests/integration/semantic_fixtures/test_event_flow_fixture.py`
- `codebase_rag/tests/unit/mcp/test_mcp_event_flow_workflows.py`
- `codebase_rag/tests/unit/services/test_event_channel_name_normalization.py`
- `codebase_rag/tests/unit/services/test_runtime_evidence_event_flow_ingestion.py`
- `codebase_rag/tests/unit/mcp/test_mcp_runtime_event_flow_workflows.py`
- `codebase_rag/tests/integration/semantic_fixtures/test_runtime_and_static_event_edges_reconcile.py`
- `codebase_rag/tests/unit/graph/test_event_reliability_queries.py`
- `codebase_rag/tests/integration/semantic_fixtures/test_outbox_without_transaction_query.py`

The semantic fixture harness for this slice lives under:

- `codebase_rag/tests/integration/semantic_fixtures/`

This document should be extended as future Task85 backlog items add:

- transaction boundaries
