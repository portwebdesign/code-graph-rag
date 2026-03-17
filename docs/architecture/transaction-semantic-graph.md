# Transaction Semantic Graph

## Scope

This document describes the first delivery slice of Python transaction-boundary and side-effect-order semantics in `code-graph-rag`.

The current implementation adds first-class semantic coverage for:

- explicit transaction begin / commit / rollback markers
- transaction context-manager boundaries such as `with session.transaction():`
- side-effect nodes attached to the enclosing function or method
- lexical ordering edges between adjacent side effects in the same transaction
- multi-hop traversal from function -> side effect -> ordering -> transaction boundary

## Nodes

The current transaction slice emits:

- `TransactionBoundary`
- `SideEffect`

`TransactionBoundary` represents a bounded transactional region inferred from explicit begin calls or transaction-like context managers.

`SideEffect` represents a write-like action inferred from a call site.

## Relationships

The current transaction slice emits:

- `BEGINS_TRANSACTION`
- `COMMITS_TRANSACTION`
- `ROLLBACKS_TRANSACTION`
- `PERFORMS_SIDE_EFFECT`
- `WITHIN_TRANSACTION`
- `BEFORE`
- `AFTER`

This lets the graph express:

- function -> transaction boundary
- function -> side effect
- side effect -> enclosing transaction boundary
- side effect ordering inside the same transaction region

## Current implementation notes

The current implementation lives in:

- `codebase_rag/parsers/pipeline/python_transaction_flows.py`
- `codebase_rag/parsers/pipeline/transaction_flow_pass.py`

The transaction pass runs as a dedicated post-parse semantic pass through:

- `codebase_rag/services/graph_update_post_services.py`
- `codebase_rag/services/graph_update_orchestrator.py`

The pass is env-gated by:

- `CODEGRAPH_TRANSACTION_FLOW_SEMANTICS`

## Bounded heuristics

The current first wave intentionally uses bounded lexical heuristics instead of full control-flow or dataflow claims.

Supported transaction markers currently include:

- `*.begin()`
- `begin_transaction()`
- `start_transaction()`
- `with *.transaction():`
- `with *.atomic():`
- `with *unit_of_work*`
- `*.commit()`
- `*.rollback()`

Supported side-effect kinds currently include:

- `db_write`
- `cache_write`
- `queue_publish`
- `outbox_write`
- `external_http`
- `graph_write`
- `filesystem_write`

Side-effect classification is driven by call names plus bounded literal inspection.

## Multi-hop support

`impact_graph` / `multi_hop_analysis` now traverse the transaction semantic surface through:

- `BEGINS_TRANSACTION`
- `COMMITS_TRANSACTION`
- `ROLLBACKS_TRANSACTION`
- `PERFORMS_SIDE_EFFECT`
- `WITHIN_TRANSACTION`
- `BEFORE`
- `AFTER`

This enables paths such as:

- function -> side effect -> transaction boundary
- function -> side effect -> BEFORE -> side effect
- function -> side effect -> BEFORE -> side effect -> WITHIN_TRANSACTION -> transaction boundary

## Transaction safety query presets

The event reliability query pack now also includes the transaction-safety preset:

- `external_call_before_commit`

This preset looks for `external_http` side effects that occur inside committing transaction boundaries.

## Cleanup and reparse behavior

Transaction semantic relationships and orphan nodes are cleaned during reparses through:

- `codebase_rag/graph_db/cypher_queries.py`

This cleanup covers:

- transaction semantic edges written per source path
- orphan `TransactionBoundary` nodes
- orphan `SideEffect` nodes

## Current limits

This is intentionally a first wave.

Current limits:

- Python-only
- bounded lexical ordering, not branch-sensitive or interprocedural transaction analysis
- no claim of exact transaction correctness across control-flow merges, retries, or exception semantics
- side-effect classification is heuristic and may miss framework-specific write APIs

## Validation

The current delivery slice is covered by:

- `codebase_rag/tests/unit/parsers/pipeline/test_python_transaction_flows.py`
- `codebase_rag/tests/unit/frameworks/test_transaction_flow_semantic_edges.py`
- `codebase_rag/tests/unit/mcp/test_mcp_transaction_flow_workflows.py`
- `codebase_rag/tests/integration/semantic_fixtures/test_transaction_flow_fixture.py`
- `codebase_rag/tests/unit/graph/test_transaction_safety_queries.py`
- `codebase_rag/tests/integration/semantic_fixtures/test_outbox_without_transaction_query.py`

The semantic fixture harness for this slice lives under:

- `codebase_rag/tests/integration/semantic_fixtures/`
