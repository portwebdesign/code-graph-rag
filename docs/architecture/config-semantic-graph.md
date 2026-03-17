# Config Semantic Graph

CodeGraphRAG now emits first-wave control-plane config semantics so env readers, feature flags, secret refs, and infra-resource projections are queryable as first-class graph objects.

Current node families:

- `EnvVar`
- `FeatureFlag`
- `SecretRef`

Current relationship families:

- `READS_ENV`
- `SETS_ENV`
- `USES_SECRET`
- `GATES_CODE_PATH`

Current supported sources:

- `.env`
- Docker Compose `environment`
- Kubernetes `env` and `valueFrom.secretKeyRef`
- Python `os.getenv(...)`
- Python `os.environ.get(...)`
- Python `os.environ["..."]`
- bounded settings-class field detection (`BaseSettings`-style inheritance)
- TypeScript/JavaScript `process.env.FOO`
- TypeScript/JavaScript `process.env["FOO"]`

Topology reconciliation:

- `TopologyGraphEnricher` projects `InfraResource.environment` and secret bindings into canonical `EnvVar` / `SecretRef` identities.
- The same QN helpers are used by config parsing and topology projection, so static definitions, code readers, and infra resources converge on the same semantic node identities.
- Projection provenance is written with `source_parser=topology_graph_enricher`.

Secret safety:

- Secret-like values are never persisted as plaintext in semantic nodes.
- `SecretRef` nodes carry names, optional provider/key metadata, and `masked=true`.
- Infra-resource `environment` payloads are retained only in redacted form.
- Hardcoded secret scan reports keep only sanitized `secret_name`, `pattern`, `line`, and `path` metadata.
- `security_report.json` and `secret_scan_report.json` intentionally omit raw secret literals so scan artifacts stay graph-safe.

Current query surface:

- `undefined_env_readers`
- `unbound_secret_refs`
- `orphan_secret_refs`
- `orphan_feature_flags`
- `resource_without_readers`
- `reader_without_resource`
- `unused_feature_flags`

Those presets are exposed through `get_schema_overview(scope="api")` under `semantic_cypher_presets` and can be executed directly via `run_cypher`.

Current MCP workflow surface:

- `get_schema_overview(scope="api")` now suggests a config-drift `query_code_graph(...)` step that asks for infra-resource, env-reader, feature-flag, and secret-ref mismatches.
- `get_schema_overview(scope="api")` also suggests a raw `run_cypher(...)` drill-down for `InfraResource -> EnvVar -> reader` coverage so drift hotspots can be enumerated without writing custom Cypher first.

Validation surface:

- `tests/unit/parsers/pipeline/test_config_semantics_env_readers.py`
- `tests/unit/parsers/pipeline/test_config_semantics_feature_flags.py`
- `tests/unit/parsers/pipeline/test_config_semantics_secret_refs_masked.py`
- `tests/unit/security/test_semantic_graph_never_persists_secret_values.py`
- `tests/unit/security/test_secret_ref_nodes_keep_name_not_value.py`
- `tests/unit/services/test_topology_and_config_semantics_reconcile.py`
- `tests/unit/graph/test_config_runtime_queries.py`
- `tests/unit/mcp/test_mcp_config_semantic_workflows.py`
- `tests/unit/mcp/test_mcp_config_drift_semantic_workflows.py`
- `tests/integration/semantic_fixtures/test_env_flag_secret_fixture.py`
- `tests/integration/semantic_fixtures/test_hardcoded_secret_scan_and_secretref_graph_do_not_conflict.py`
- `tests/integration/semantic_fixtures/test_infraresource_sets_env_edges.py`
- `tests/integration/semantic_fixtures/test_orphan_flag_and_undefined_env_queries.py`
- `tests/integration/semantic_fixtures/test_undefined_env_query.py`

Known limits:

- Python settings detection is intentionally bounded; `Field(env=...)`, alias-heavy models, and framework-specific config wrappers are not exhaustively modeled yet.
- TypeScript support is regex-driven and aimed at env surface discovery rather than full data-flow.
- Kubernetes support covers common `env` / `secretKeyRef` patterns, not the full configMap/templating matrix.
- No value-level drift graph is emitted; semantic graph tracks names, bindings, and gated code paths rather than full runtime config snapshots.
