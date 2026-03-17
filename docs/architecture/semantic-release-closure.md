# Semantic Release Closure

This note records the shipped semantic capability set, release gates, and known limits for
the current semantic schema release.

## Shipped capability set

- FastAPI dependency, auth, scope, and contract graph
- Event flow, outbox, replay, DLQ, and queue semantics
- Runtime artifact and static event reconciliation
- Transaction boundary and side-effect ordering graph
- SQL and Cypher query fingerprint graph
- Frontend operation governance graph
- Testcase, endpoint, contract, and runtime coverage graph
- Env, flag, secret, and infra control-plane graph

## Release gates

- Validation matrix queries in `docs/architecture/semantic-validation-matrix.md`
- Semantic acceptance suite in `.github/workflows/ci.yml`
- Full semantic reparse smoke over canonical fixture families
- Query-pack smoke for auth, event, frontend, test, config, and validation packs
- Runtime/static coexist smoke for runtime events and runtime coverage

## Known limits

- Runtime evidence is additive and does not replace static semantic graph identities.
- Transaction, event, and config semantics remain intentionally bounded by lexical or
  schema heuristics; they are not full program analysis.
- Frontend semantic slices require parser availability and may be skipped in constrained
  environments.
- Guardrails preserve representative semantic evidence rather than every possible edge in
  high-cardinality fixture scenarios.

## Upgrade checklist

- Re-run semantic acceptance and smoke suites after parser/schema changes.
- Review `get_schema_overview(...).semantic_schema` before consuming new relation families.
- Update the versioning document when labels, identities, or relationship names change.
