# Query Fingerprint Semantic Graph

## Scope

`QueryFingerprintPass` adds first-wave query semantics on top of the existing structural graph.

Current emitted nodes:

- `SqlQuery`
- `CypherQuery`
- `QueryFingerprint`
- semantic `DataStore` placeholders for SQL table references
- semantic `GraphNodeLabel` placeholders for Cypher label references

Current emitted edges:

- `EXECUTES_SQL`
- `EXECUTES_CYPHER`
- `HAS_FINGERPRINT`
- `READS_TABLE`
- `WRITES_TABLE`
- `JOINS_TABLE`
- `READS_LABEL`
- `WRITES_LABEL`

## Current Heuristics

Supported source families:

- Python function and method bodies
- JavaScript / TypeScript function-like symbol bodies

Current matching model:

- Python uses `ast` and inspects direct call arguments plus simple local string bindings.
- JS/TS scans top-level function-like symbol bodies and classifies string literals that look like SQL or Cypher.
- SQL detection is intentionally bounded to `SELECT`, `WITH`, `INSERT`, `UPDATE`, and `DELETE`.
- Cypher detection is intentionally bounded to `MATCH`, `OPTIONAL MATCH`, `MERGE`, `CREATE`, `WITH`, `UNWIND`, and `CALL`.

## Normalization Rules

SQL normalization:

- strip `-- ...` and `/* ... */` comments
- replace single-quoted literals with `?`
- replace numeric literals with `?`
- replace `$1` and `:named` parameters with `?`
- collapse whitespace
- uppercase the canonical form before fingerprinting

Cypher normalization:

- replace string literals with `?`
- replace numeric literals with `?`
- replace `$param` bindings with `?`
- collapse whitespace
- uppercase the canonical form before fingerprinting

Fingerprinting:

- `sha1(normalized_query)[:16]`

## Validation Surface

Unit coverage:

- `codebase_rag/tests/unit/parsers/pipeline/test_sql_query_fingerprint_normalization.py`
- `codebase_rag/tests/unit/parsers/pipeline/test_cypher_query_fingerprint_normalization.py`
- `codebase_rag/tests/unit/data_models/test_semantic_relationship_schemas.py`

Fixture coverage:

- `codebase_rag/tests/integration/semantic_fixtures/test_query_fingerprint_graph_shape.py`
- `codebase_rag/tests/integration/semantic_fixtures/test_query_read_write_edges.py`

## Known Limits

- no full SQL parser for embedded query strings
- no alias-to-table resolution beyond direct `FROM` / `JOIN` / write-clause extraction
- no deep Cypher clause segmentation; labels are clause-presence heuristics
- no stored procedure or ORM DSL semantic lowering yet
- no deduplicated bridge to structural SQL schema nodes yet; semantic table placeholders are used when needed
