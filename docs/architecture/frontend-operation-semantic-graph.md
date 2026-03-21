# Frontend Operation Semantic Graph

## Scope

`FrontendOperationPass` turns frontend request surfaces into governed operation nodes.

Current emitted nodes:

- `ClientOperation`
- `Endpoint` placeholder nodes when no existing endpoint node is present

Current emitted edges:

- `USES_OPERATION`
- `REQUESTS_ENDPOINT`
- `GENERATED_FROM_SPEC`
- `BYPASSES_MANIFEST`

`REQUESTS_ENDPOINT` is emitted on two surfaces:

- `ClientOperation -> Endpoint` (canonical governance path)
- source symbol (`Component` / `Function` / `Method`) -> `Endpoint` as a shortcut edge when available

## Current Heuristics

Supported source families:

- JavaScript / TypeScript / JSX / TSX function-like symbol bodies
- OpenAPI JSON/YAML documents that expose `operationId`

Detected request styles:

- raw `fetch(...)`
- template-string `fetch(...)`
- `axios.get/post/...`
- member calls like `apiClient.get(...)`
- `.request({ method, url })`

Governance classification:

- `generated`: source path contains `/generated/`
- `manifest`: OpenAPI binding exists and request uses client-member / request-object patterns
- `governed`: OpenAPI binding exists but generated-file heuristic does not apply
- `bypass`: raw `fetch` / `axios`, or no manifest/spec match

## Operation Binding Rules

- `operationId` comes from OpenAPI when method + normalized path match.
- `ClientOperation.name` prefers `operationId`; otherwise it falls back to a deterministic symbol/method/path identity.
- `GENERATED_FROM_SPEC` is emitted when a governed/generated operation has an OpenAPI `operationId`.
- `BYPASSES_MANIFEST` is emitted for raw-bypass calls even if the endpoint exists in the OpenAPI manifest.

## Validation Surface

Unit coverage:

- `codebase_rag/tests/unit/parsers/pipeline/test_frontend_generated_client_detection.py`
- `codebase_rag/tests/unit/parsers/pipeline/test_frontend_raw_fetch_bypass_detection.py`
- `codebase_rag/tests/unit/graph/test_frontend_operation_queries.py`

Fixture coverage:

- `codebase_rag/tests/integration/semantic_fixtures/test_client_operation_to_endpoint_graph.py`
- `codebase_rag/tests/integration/semantic_fixtures/test_bypasses_manifest_query.py`

## Known Limits

- no first-class support for vendor-specific generated SDK manifests yet
- no AST-level import graph proving which components invoke which generated clients; that still relies on existing `CALLS` / resolver linking
- no dedicated `GeneratedApiClient` node yet; `ClientOperation` is the governed unit
- no GraphQL operation-id manifest support yet
