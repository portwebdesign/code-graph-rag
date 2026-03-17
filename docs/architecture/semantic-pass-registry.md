# Semantic Pass Registry

`SemanticPassRegistry` is the central ordering and env-gating layer for post-parse semantic passes.

Current shipped order:

1. `contract_semantics`
2. `event_flow_semantics`
3. `query_fingerprint_semantics`
4. `transaction_flow_semantics`
5. `frontend_operation_semantics`
6. `config_semantics`
7. `test_semantics`

Why this order:

- contract semantics should land before downstream semantic query packs consume request/response surface
- event flow semantics should materialize producer/consumer topology before reliability workflows traverse it
- query fingerprint semantics should run before transaction/test workflows consume table-label evidence
- transaction semantics should run after structural/event linking so side-effect ordering can attach to already-ingested symbols
- frontend operation semantics should materialize governed/bypass request surfaces before semantic test selection traverses API consumers
- config semantics should land before test/MCP coverage workflows so env, secret, feature-flag, and infra control-plane edges are queryable through the same semantic preset surface
- test semantics should run after contract and frontend semantic passes so endpoint/contract coverage edges can bind to existing semantic nodes

Current implementation points:

- `codebase_rag/parsers/pipeline/semantic_pass_registry.py`
- `codebase_rag/services/graph_update_post_services.py`
- `codebase_rag/services/graph_update_orchestrator.py`

Current env flags:

- `CODEGRAPH_CONTRACT_SEMANTICS`
- `CODEGRAPH_EVENT_FLOW_SEMANTICS`
- `CODEGRAPH_QUERY_FINGERPRINT_SEMANTICS`
- `CODEGRAPH_TRANSACTION_FLOW_SEMANTICS`
- `CODEGRAPH_FRONTEND_OPERATION_SEMANTICS`
- `CODEGRAPH_CONFIG_SEMANTICS`
- `CODEGRAPH_TEST_SEMANTICS`

Validation surface:

- `tests/unit/parsers/pipeline/test_semantic_pass_registration.py`
- `tests/unit/parsers/pipeline/test_semantic_pass_env_flags.py`
- `tests/integration/parsers/test_semantic_pass_order_is_deterministic.py`
- `tests/perf/test_semantic_pass_parse_time_budget.py`
- `tests/perf/test_semantic_graph_cardinality_budget.py`
- `tests/unit/parsers/passes/test_query_fingerprint_dedup_prevents_explosion.py`
- `tests/unit/parsers/pipeline/test_config_semantics_env_readers.py`
- `tests/unit/parsers/pipeline/test_config_semantics_feature_flags.py`
- `tests/unit/parsers/pipeline/test_config_semantics_secret_refs_masked.py`
- `tests/unit/services/test_topology_and_config_semantics_reconcile.py`

Fixture packs used by semantic regressions:

- `FASTAPI_AUTH_CONTRACT_FIXTURE`
- `EVENT_FLOW_FIXTURE`
- `EVENT_FLOW_RUNTIME_FIXTURE`
- `TRANSACTION_FLOW_FIXTURE`
- `FRONTEND_CONTRACT_FIXTURE`
- `TEST_SEMANTICS_FIXTURE`
- `ENV_FLAG_SECRET_FIXTURE`

Guardrail layer:

- runtime thresholds live in `codebase_rag/parsers/pipeline/semantic_guardrails.py`
- truncation is deterministic and warning-backed rather than best-effort
- the synthetic `semantic_guardrail_stress_fixture` is used to keep node and edge growth inside tested budgets

`config_semantics` is now part of the shipped registry rather than a reserved slot. Future semantic families should keep using the same registry contract instead of hard-coding bespoke pass calls into the orchestrator.
