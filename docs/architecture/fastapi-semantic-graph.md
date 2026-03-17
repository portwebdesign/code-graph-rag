# FastAPI Semantic Graph

## Scope

This document describes the first delivery slice of the FastAPI semantic graph in `code-graph-rag`.

The current implementation adds first-class graph entities for:

- FastAPI dependency providers discovered from `Depends(...)`
- FastAPI auth policies discovered from `Security(...)`
- FastAPI auth scopes discovered from `Security(..., scopes=[...])`
- FastAPI response contracts discovered from `response_model=...`
- Python contract definitions discovered from Pydantic `BaseModel`, `@dataclass`, and `TypedDict`
- FastAPI request contracts discovered from handler parameter annotations

## Nodes

The current FastAPI semantic slice emits:

- `DependencyProvider`
- `AuthPolicy`
- `AuthScope`
- `Contract`
- `ContractField`

## Relationships

The current FastAPI semantic slice emits:

- `USES_DEPENDENCY`
- `SECURED_BY`
- `REQUIRES_SCOPE`
- `ACCEPTS_CONTRACT`
- `RETURNS_CONTRACT`
- `DECLARES_FIELD`

These edges are emitted from both:

- endpoint nodes
- resolved handler function or method nodes

This dual emission is intentional. It keeps endpoint-level semantics queryable while also preserving handler-based cleanup and reparse behavior.

## Provenance contract

Every semantic node and edge emitted by this slice carries a bounded provenance payload:

- `source_parser`
- `evidence_kind`
- `path`
- `confidence`
- `start_line`
- `end_line`
- `language`

Unresolved targets are kept as typed placeholders through `is_placeholder=true`.

## Current implementation notes

The previous FastAPI extraction path used duplicated regex parsing in both:

- `python_framework_detector.py`
- `framework_linker.py`

That duplication has been reduced by introducing:

- `codebase_rag/parsers/frameworks/fastapi_semantics.py`

This shared extractor performs balanced parsing of:

- decorator arguments
- handler signatures

This is more resilient than the old single-regex route extraction, especially when nested `Depends(...)` or `Security(...)` calls are present.

## Current limits

This is intentionally the first semantic slice, not the full Task85 backlog.

Current limits:

- Python contract extraction is currently scoped to Pydantic `BaseModel`, `@dataclass`, and `TypedDict`
- request-contract inference currently relies on known in-repo contract definitions and excludes dependency parameters such as `Depends(...)` / `Security(...)`
- FastAPI semantics are currently scoped to route metadata and handler signature patterns, not full dependency graph resolution across arbitrary factories

For the broader cross-source contract graph, see:

- `docs/architecture/contract-semantic-graph.md`

## Example questions now possible

```cypher
MATCH (e:Endpoint)-[:USES_DEPENDENCY]->(d:DependencyProvider)
RETURN e.route_path, d.name
ORDER BY e.route_path;
```

```cypher
MATCH (e:Endpoint)-[:SECURED_BY]->(p:AuthPolicy)-[:REQUIRES_SCOPE]->(s:AuthScope)
RETURN e.route_path, p.name, collect(s.name) AS scopes
ORDER BY e.route_path;
```

```cypher
MATCH (e:Endpoint)-[:RETURNS_CONTRACT]->(c:Contract)
RETURN e.route_path, c.name
ORDER BY e.route_path;
```

```cypher
MATCH (e:Endpoint)-[:ACCEPTS_CONTRACT]->(c:Contract)-[:DECLARES_FIELD]->(f:ContractField)
RETURN e.route_path, c.name, collect(f.name) AS fields
ORDER BY e.route_path;
```

## MCP query surface

`get_schema_overview(scope="api")` now exposes `semantic_cypher_presets` for:

- `endpoint_auth_coverage`
- `endpoint_dependency_visibility`
- `endpoint_contract_gaps`
- `unprotected_endpoints`

These presets are designed for `run_cypher` and stay project-scoped with `$project_name`.

`impact_graph` / `multi_hop_analysis` now also traverse the FastAPI semantic surface through:

- `HAS_ENDPOINT`
- `REQUESTS_ENDPOINT`
- `USES_DEPENDENCY`
- `SECURED_BY`
- `REQUIRES_SCOPE`
- `ACCEPTS_CONTRACT`
- `RETURNS_CONTRACT`
- `DECLARES_FIELD`

## Validation

The current delivery slice is covered by:

- `codebase_rag/tests/unit/data_models/test_semantic_relationship_schemas.py`
- `codebase_rag/tests/unit/frameworks/test_fastapi_semantic_edges.py`
- `codebase_rag/tests/integration/semantic_fixtures/test_fastapi_semantics_fixture.py`
- `codebase_rag/tests/integration/semantic_fixtures/test_unprotected_endpoint_query_pack.py`
- `codebase_rag/tests/unit/mcp/test_mcp_semantic_auth_contract_workflows.py`

The semantic fixture harness lives under:

- `codebase_rag/tests/integration/semantic_fixtures/`

This harness provides:

- tiny repository fixtures that encode semantic behaviors intentionally
- deterministic mock-ingestor subgraph snapshots for regression checks
- project-scoped Memgraph smoke tests for canned semantic Cypher queries

This document should be extended as future Task85 backlog items add:

- test graph bindings
- event and transaction semantics
