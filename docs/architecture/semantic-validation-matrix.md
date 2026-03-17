# Semantic Validation Matrix

This matrix defines the minimum graph acceptance surface for each semantic family.

Each row maps:

- semantic capability
- canonical fixture repository
- canonical validation query
- minimum expected rows
- acceptance coverage hook

All canonical queries are defined in `codebase_rag/graph_db/cypher_queries.py` through `build_semantic_validation_query_pack()`.

## Matrix

| Capability | Fixture | Validation Query | Minimum Rows | Acceptance Coverage |
| --- | --- | --- | ---: | --- |
| FastAPI dependency/auth/contract | `fastapi_semantic_fixture` | `fastapi_auth_contract_minimum` | 1 | `test_validation_matrix_queries.py`, `test_semantic_acceptance_ci_suite.py` |
| Event flow / outbox / consumer / replay | `event_flow_semantic_fixture` | `event_flow_minimum` | 1 | `test_validation_matrix_queries.py` |
| Transaction boundary / side-effect ordering | `transaction_flow_semantic_fixture` | `transaction_flow_minimum` | 1 | `test_validation_matrix_queries.py`, `test_semantic_acceptance_ci_suite.py` |
| SQL/Cypher query fingerprint | `query_fingerprint_semantic_fixture` | `query_fingerprint_minimum` | 1 | `test_validation_matrix_queries.py` |
| Frontend governed operation / raw bypass | `frontend_operation_semantic_fixture` | `frontend_operation_minimum` | 1 | `test_validation_matrix_queries.py` |
| Test graph coverage | `test_semantics_fixture` | `test_semantics_minimum` | 1 | `test_validation_matrix_queries.py` |
| Config / env / flag / secret control plane | `env_flag_secret_semantic_fixture` | `config_control_plane_minimum` | 1 | `test_validation_matrix_queries.py` |

## Canonical Acceptance Rules

- Every validation query must be project-scoped with `$project_name`.
- Every validation query must return `matched_rows`.
- Every fixture must parse through the normal graph updater path, not a test-only shortcut.
- `multi_hop_analysis` must prove at least one semantic-first traversal in CI.
- `impact_graph` must prove at least one semantic-edge blast-radius traversal in CI.

## Guardrail Context

The validation matrix assumes semantic guardrails stay active:

- per-symbol and per-file caps for query observations
- per-symbol caps for event observations
- per-file and per-source caps for config/env observations
- per-symbol and per-file caps for transaction boundaries and side effects

Those runtime thresholds live in `codebase_rag/parsers/pipeline/semantic_guardrails.py`.

## CI Hook

The GitHub Actions integration workflow runs:

- `codebase_rag/tests/integration/semantic_fixtures/test_validation_matrix_queries.py`
- `codebase_rag/tests/integration/test_semantic_acceptance_ci_suite.py`

This keeps the matrix executable instead of becoming a stale document.
