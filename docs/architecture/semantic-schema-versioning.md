# Semantic Schema Versioning

Current semantic schema version: `1.0.0`

This document defines how CodeGraphRAG versions the semantic graph surface that sits on
top of the structural code graph.

## Why it exists

The semantic graph now exposes first-class edges for auth, contracts, event flows,
transactions, query fingerprints, frontend operations, tests, and config/control-plane
analysis. Consumers need a stable way to understand when that surface changes in a
backward-compatible way and when it does not.

## Signal planes

- `static`: parser-derived semantics grounded in repository source such as Python,
  TypeScript, OpenAPI, env files, and topology manifests.
- `runtime`: runtime artifacts such as event logs and coverage files; these are additive
  observations and do not overwrite static semantic identities.
- `heuristic`: bounded lexical inference used to recover higher-order semantics where a
  framework or schema does not expose an explicit contract.

## Versioning policy

- Major version: bump when node labels, relationship names, or unique identity keys change
  incompatibly.
- Minor version: bump when new semantic capabilities, properties, or query presets are
  added without breaking existing consumers.
- Patch version: use for documentation, validation, or closure updates that do not change
  graph shape expectations.
- Breaking change policy: Bump the semantic schema major version when node labels, relationship names, or unique identity keys change incompatibly.

## Consumer guidance

- Prefer `get_schema_overview(...).semantic_schema` as the machine-readable source of truth.
- Treat runtime evidence as observational, not authoritative.
- Treat heuristic edges as bounded semantic hints, not proof of full dataflow.
- Check the validation matrix before depending on a new capability in automation.
- Consumer guidance: Prefer semantic_schema metadata from get_schema_overview(...) and the versioning document before relying on newly added semantic node families.

## Capability index

- `fastapi_auth_contract`: `docs/architecture/fastapi-semantic-graph.md`
- `event_flow`: `docs/architecture/event-flow-semantic-graph.md`
- `runtime_reconciliation`: `docs/architecture/event-flow-semantic-graph.md`
- `transaction_flow`: `docs/architecture/transaction-semantic-graph.md`
- `query_fingerprint`: `docs/architecture/query-fingerprint-semantic-graph.md`
- `frontend_operation_governance`: `docs/architecture/frontend-operation-semantic-graph.md`
- `test_semantics`: `docs/architecture/test-semantic-graph.md`
- `config_control_plane`: `docs/architecture/config-semantic-graph.md`

## Compatibility references

- Validation matrix: `docs/architecture/semantic-validation-matrix.md`
- Release closure: `docs/architecture/semantic-release-closure.md`
