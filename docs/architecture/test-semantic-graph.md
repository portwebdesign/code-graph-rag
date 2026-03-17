# Test Semantic Graph

## Scope

`TestSemanticsPass` turns static test files into first-class semantic graph coverage evidence.

Current emitted nodes:

- `TestSuite`
- `TestCase`
- `Endpoint` placeholder nodes when no existing endpoint node can be resolved for a test HTTP call

Current emitted edges:

- `CONTAINS` (`TestSuite` -> `TestCase`)
- `TESTS_SYMBOL`
- `TESTS_ENDPOINT`
- `ASSERTS_CONTRACT`

## Supported Framework Families

Current static coverage:

- Python pytest top-level `test_*` functions
- Python unittest-style classes (`Test*`, `*TestCase`) with `test_*` methods
- JavaScript / TypeScript jest-vitest style `it(...)` / `test(...)` blocks
- simple e2e client-call patterns such as `fetch(...)`, `axios.*(...)`, `page.request.post(...)`, and `client.get/post(...)`

Current contract assertion heuristics:

- Python identifier/type reference presence inside a testcase body
- TypeScript identifier/type reference presence inside a testcase body
- endpoint -> contract indirect coverage when an endpoint is linked to request/response contracts and a testcase asserts those contract nodes

## MCP Surface

The test semantic graph is now consumed by MCP workflows:

- `test_bundle` includes `semantic_candidate_testcases`, `semantic_target_summary`, `semantic_gaps`, and `runtime_coverage_matches`
- `test_generate` uses semantic-first candidate selection before filesystem token fallback
- `get_schema_overview(scope="api")` exposes:
  - `untested_public_endpoints`
  - `contract_test_coverage`
- `impact_graph` / `multi_hop_analysis` traverse:
  - `TESTS_SYMBOL`
  - `TESTS_ENDPOINT`
  - `ASSERTS_CONTRACT`

Selection modes:

- `semantic-graph-primary`: semantic graph returned testcase coverage or coverage gaps
- `filesystem-fallback`: no semantic coverage evidence was found, so filename/token heuristics are used
- `goal-only`: no impacted files or symbols are available yet

## Validation Surface

Unit coverage:

- `codebase_rag/tests/unit/parsers/pipeline/test_testcase_symbol_edges.py`
- `codebase_rag/tests/unit/parsers/pipeline/test_testcase_endpoint_edges.py`
- `codebase_rag/tests/unit/graph/test_test_semantics_queries.py`
- `codebase_rag/tests/unit/mcp/test_impacted_test_uses_semantic_test_graph.py`
- `codebase_rag/tests/unit/mcp/test_contract_drift_suggests_testcases.py`

Fixture coverage:

- `codebase_rag/tests/integration/semantic_fixtures/test_test_graph_and_runtime_coverage_coexist.py`
- `codebase_rag/tests/integration/semantic_fixtures/test_untested_public_endpoint_query.py`
- `codebase_rag/tests/integration/semantic_fixtures/test_mcp_semantic_test_selection.py`

## Known Limits

- matcher/assert semantics are still bounded; `ASSERTS_CONTRACT` is identifier/type-reference based, not full assertion-AST aware
- no dedicated xUnit/JUnit/NUnit family support yet
- no framework-specific browser-action graph beyond bounded HTTP request extraction
- runtime `COVERS_MODULE` evidence is preserved and surfaced, but it is still contextual evidence rather than a first-class `TestRun` / `CoverageReport` graph model
- endpoint resolution prefers existing `Endpoint` nodes when they are already queryable; otherwise the pass falls back to deterministic HTTP placeholder endpoint identities
