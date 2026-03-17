from __future__ import annotations

from codebase_rag.parsers.pipeline.frontend_operations import (
    extract_frontend_operation_observations,
    extract_openapi_operation_bindings,
)
from codebase_rag.parsers.pipeline.openapi_contracts import (
    extract_openapi_contract_surface,
)


def test_detects_raw_fetch_bypass_even_when_spec_binding_exists() -> None:
    openapi_source = """{
  "openapi": "3.0.3",
  "paths": {
    "/api/orders": {
      "post": {
        "operationId": "createOrder",
        "responses": {"201": {"description": "Created"}}
      }
    }
  }
}
"""
    raw_source = """export async function createOrder() {
  return fetch("/api/orders", { method: "POST" });
}
"""

    _contracts, bindings = extract_openapi_contract_surface(
        openapi_source, file_suffix=".json"
    )
    observations = extract_frontend_operation_observations(
        raw_source,
        relative_path="src/lib/raw/orders.ts",
        operation_bindings=extract_openapi_operation_bindings(bindings),
    )

    assert len(observations) == 1
    observation = observations[0]
    assert observation.operation_name == "createOrder"
    assert observation.operation_id == "createOrder"
    assert observation.client_kind == "fetch"
    assert observation.governance_kind == "bypass"
