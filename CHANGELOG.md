# Changelog

## Unreleased

### Added

- Added actionable duplicate-code and fan-report views with structured categories, production filtering, and semantic fan-in/out summaries.
- Added FastAPI function-truthfulness bridging so dependency providers and auth policies now resolve to concrete function symbols and callback registrations can keep dead-code analysis honest.
- Added first-wave FastAPI semantic graph support for dependency providers, auth policies, auth scopes, and response contracts.
- Added shared FastAPI semantic extraction logic to avoid duplicated, fragile route parsing between detector and linker.
- Added semantic metadata helpers for provenance, confidence, and placeholder handling.
- Added `ContractSemanticsPass` for Python `BaseModel` / `@dataclass` / `TypedDict` definitions, `ContractField` nodes, and FastAPI `ACCEPTS_CONTRACT` request edges.
- Added semantic auth/contract canned Cypher query presets and surfaced them through `get_schema_overview(scope="api")`.
- Added a reusable semantic fixture harness with deterministic graph snapshots and Memgraph canned-query smoke coverage.
- Added semantic edge traversal to `impact_graph` / `multi_hop_analysis` so auth, endpoint, and contract relations participate in blast-radius analysis.
- Added first-wave Python event-flow semantics for outbox, publish, consume, replay, queue, and DLQ graph edges.
- Added event-flow fixture coverage and MCP multi-hop regression tests for producer -> flow -> handler -> DLQ paths.
- Added runtime event-flow reconciliation so `RuntimeArtifact` / `RuntimeEvent` nodes can attach to static `EventFlow`, `Queue`, and handler nodes through `OBSERVED_IN_RUNTIME`.
- Added runtime reconciliation fixture coverage, normalization tests, and MCP regressions for producer -> event flow -> runtime event -> handler paths.
- Added first-wave Python transaction semantics for `TransactionBoundary` / `SideEffect` nodes plus begin/commit/rollback, side-effect, and ordering edges.
- Added transaction fixture coverage and MCP multi-hop regression tests for function -> side effect -> ordering -> transaction boundary paths.
- Added a central `SemanticPassRegistry` for deterministic semantic pass ordering and shared env-flag gating.
- Added frontend generated-client vs raw fetch fixture coverage and env/flag/secret control-plane fixture coverage.
- Added TypeScript interface/type-alias, Zod, and OpenAPI contract extraction to `ContractSemanticsPass`.
- Added frontend/function and OpenAPI endpoint contract-edge coverage plus contract field graph regressions.
- Added event reliability and transaction safety Cypher presets for outbox-without-transaction, consumer-without-dlq, replay paths, external-call-before-commit, and duplicate publishers.
- Added `QueryFingerprintPass` with `SqlQuery`, `CypherQuery`, `QueryFingerprint`, SQL table, and Cypher label semantics.
- Added `FrontendOperationPass` with `ClientOperation`, generated-from-spec, and raw bypass semantics plus frontend governance Cypher presets.
- Added `TestSemanticsPass` with `TestSuite` / `TestCase`, `TESTS_SYMBOL`, `TESTS_ENDPOINT`, and `ASSERTS_CONTRACT` semantics plus runtime coverage coexist validation.
- Added semantic test-coverage Cypher presets for `untested_public_endpoints` and `contract_test_coverage`.
- Added MCP semantic-first impacted-test selection with contract-drift suggestions and runtime coverage context.
- Added first-wave config semantics for `.env`, Docker Compose, Kubernetes env blocks, Python `os.getenv`/settings classes, and TypeScript `process.env` readers.
- Added config/runtime Cypher presets for `undefined_env_readers`, `orphan_secret_refs`, and `unused_feature_flags`.
- Added config-drift Cypher presets for `unbound_secret_refs`, `orphan_feature_flags`, `resource_without_readers`, and `reader_without_resource`.
- Added secret-scan guardrails so security findings keep only sanitized secret identifiers and never persist raw secret literals.
- Added semantic guardrail thresholds plus stress-fixture perf/cardinality regression coverage for query, event, config, and transaction semantic families.
- Added canonical semantic validation queries, a validation matrix document, and CI acceptance coverage for semantic graph minimums.
- Added semantic schema metadata, versioning guidance, and release-closure documentation for semantic graph consumers.

### Changed

- Aligned `pyproject.toml` package version to upstream `0.0.159` after the selective upstream-adoption closure work; this is a release-metadata sync, not a parity claim.
- Updated `duplicate_code_report.json` to separate actionable cross-file duplicates from ignored same-file, synthetic, and low-value utility noise.
- Updated `fan_report.json` to keep raw `CALLS` hotspots while adding production-only and semantic hotspot views.
- Updated dead-code liveness heuristics to treat FastAPI dependency/auth registrations and app callback registrations as framework registration evidence.
- Extended graph schema with `Contract`, `ContractField`, `DependencyProvider`, `AuthPolicy`, and `AuthScope`.
- Extended relationship schema with `USES_DEPENDENCY`, `SECURED_BY`, `REQUIRES_SCOPE`, `ACCEPTS_CONTRACT`, `RETURNS_CONTRACT`, and `DECLARES_FIELD`.
- Updated graph update orchestration so contract semantics run as a dedicated post-parse pass.
- Updated dynamic edge cleanup queries so FastAPI and contract semantic relationships are cleared during reparses and orphan semantic nodes are removed.
- Updated MCP tool descriptions to explicitly steer auth/contract semantic discovery through `query_code_graph`, `get_schema_overview`, and `run_cypher`.
- Updated graph schema with `EventFlow` / `Queue` node schemas and event reliability edge families.
- Updated cleanup and impact-graph traversal to include event/outbox/DLQ semantic relationships.
- Updated runtime evidence ingest, cleanup queries, and impact-graph traversal to include runtime observation edges without overwriting static semantics.
- Updated graph update orchestration, cleanup queries, and impact traversal to include transaction-boundary and side-effect-order semantics.
- Updated semantic post-pass orchestration to run through the registry instead of hard-coded pass calls.
- Updated Windows pytest collection rules so semantic registry/order tests run on Windows while the broader parser skip guard remains intact.
- Updated contract semantics docs and README to describe the broader backend/frontend/spec contract surface.
- Updated schema overview guidance and event/transaction docs to surface reliability query presets.
- Updated semantic pass ordering, cleanup queries, and impact traversal to include query fingerprint and frontend operation semantics.
- Updated OpenAPI extraction to retain `operationId` for downstream client-operation binding.
- Updated semantic pass ordering, env gating, and deterministic validation to include `test_semantics`.
- Updated `impact_graph`, `multi_hop_analysis`, `test_bundle`, and `test_generate` to consume semantic testcase graph edges before filesystem fallback.
- Updated `test_generate` to default to `output_mode="plan_json"`, add compact output safety caps, and return a structured fallback plan when filesystem access is blocked by project-root guards.
- Updated semantic pass ordering, env gating, cleanup queries, and schema overview presets to include `config_semantics`.
- Updated topology enrichment to project infra-resource env/secret payloads into canonical semantic config edges with provenance metadata.
- Updated `secret_scan_report.json` and `security_report.json` serialization to emit masked secret metadata instead of raw matches.
- Updated API schema-overview guidance to include config/runtime drift workflows and raw `InfraResource -> EnvVar -> reader` drill-down queries.
- Updated semantic passes to apply deterministic truncation guardrails before high-cardinality semantic node families can explode.
- Updated GitHub Actions integration workflow to run an explicit semantic acceptance suite before the broader integration suite.
- Updated `get_schema_overview(...)` to expose machine-readable semantic schema metadata alongside query presets.

### Documentation

- Documented the FastAPI semantic graph slice in `docs/architecture/fastapi-semantic-graph.md`.
- Updated `README.md` with the new FastAPI semantic graph relationships and configuration flag.
- Documented the semantic fixture validation strategy for FastAPI auth/contract semantics.
- Documented the first-wave event-flow semantic graph in `docs/architecture/event-flow-semantic-graph.md`.
- Updated `docs/architecture/config-semantic-graph.md` with secret-safety guardrails, config-drift presets, and MCP workflow guidance.
- Added `docs/architecture/semantic-validation-matrix.md` and updated semantic registry docs with guardrail/perf validation coverage.
- Expanded the event-flow semantic graph docs to cover runtime reconciliation, runtime artifact formats, and `OBSERVED_IN_RUNTIME` traversal.
- Documented the first-wave transaction semantic graph in `docs/architecture/transaction-semantic-graph.md`.
- Documented semantic pass ordering and env-gated orchestration in `docs/architecture/semantic-pass-registry.md`.
- Documented the cross-source contract graph in `docs/architecture/contract-semantic-graph.md`.
- Documented the query fingerprint semantic graph in `docs/architecture/query-fingerprint-semantic-graph.md`.
- Documented the frontend operation semantic graph in `docs/architecture/frontend-operation-semantic-graph.md`.
- Documented the test semantic graph in `docs/architecture/test-semantic-graph.md`.
- Documented the config semantic graph in `docs/architecture/config-semantic-graph.md`.
- Documented semantic schema versioning and release closure in `docs/architecture/semantic-schema-versioning.md` and `docs/architecture/semantic-release-closure.md`.
