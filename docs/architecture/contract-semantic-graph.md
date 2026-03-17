# Contract Semantic Graph

## Scope

`ContractSemanticsPass` now emits a first-wave contract graph across backend, frontend, and spec sources.

Current supported sources:

- Python `BaseModel`
- Python `@dataclass`
- Python `TypedDict`
- FastAPI handler parameter annotations
- FastAPI `response_model=...`
- TypeScript `interface`
- TypeScript object-literal `type` aliases
- Zod `z.object(...)`
- OpenAPI `components.schemas`
- OpenAPI request/response `$ref` bindings from `paths`

## Nodes

- `Contract`
- `ContractField`

## Relationships

- `Endpoint -[:ACCEPTS_CONTRACT]-> Contract`
- `Endpoint -[:RETURNS_CONTRACT]-> Contract`
- `Function|Method -[:ACCEPTS_CONTRACT]-> Contract`
- `Function|Method -[:RETURNS_CONTRACT]-> Contract`
- `Contract -[:DECLARES_FIELD]-> ContractField`

## Current implementation points

- `codebase_rag/parsers/pipeline/contract_semantics_pass.py`
- `codebase_rag/parsers/pipeline/python_contracts.py`
- `codebase_rag/parsers/pipeline/ts_contracts.py`
- `codebase_rag/parsers/pipeline/openapi_contracts.py`

## Validation

- `codebase_rag/tests/unit/frameworks/test_fastapi_semantic_edges.py`
- `codebase_rag/tests/unit/parsers/pipeline/test_contract_semantics_typescript.py`
- `codebase_rag/tests/unit/parsers/pipeline/test_contract_semantics_openapi.py`
- `codebase_rag/tests/integration/semantic_fixtures/test_fastapi_semantics_fixture.py`
- `codebase_rag/tests/integration/semantic_fixtures/test_frontend_contract_fixture.py`
- `codebase_rag/tests/integration/semantic_fixtures/test_endpoint_contract_edges.py`
- `codebase_rag/tests/integration/semantic_fixtures/test_contract_field_graph_shape.py`

## Known limits

- TypeScript support is intentionally first-wave: object-literal aliases are covered; arbitrary conditional/mapped utility types are not modeled as field graphs yet.
- Zod support is intentionally bounded to `z.object(...)` field surfaces and simple scalar/array/object inference.
- OpenAPI support is intentionally bounded to schema components plus request/response `$ref` bindings; inline anonymous schemas are not elevated to first-class named contracts yet.
- FastAPI request-contract inference still depends on known in-repo contract definitions and does not do full dependency-factory body resolution.
