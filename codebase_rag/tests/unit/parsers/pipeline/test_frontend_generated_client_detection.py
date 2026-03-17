from __future__ import annotations

from codebase_rag.parsers.pipeline.frontend_operations import (
    extract_frontend_operation_observations,
    extract_openapi_operation_bindings,
)
from codebase_rag.parsers.pipeline.openapi_contracts import (
    extract_openapi_contract_surface,
)


def test_detects_generated_client_operation_and_binds_operation_id() -> None:
    openapi_source = """{
  "openapi": "3.0.3",
  "paths": {
    "/api/customers": {
      "get": {
        "operationId": "listCustomers",
        "responses": {"200": {"description": "OK"}}
      }
    }
  }
}
"""
    generated_source = """const apiClient = {
  get: async (path: string) => fetch(path),
};

export async function listCustomers() {
  return apiClient.get("/api/customers");
}
"""

    _contracts, bindings = extract_openapi_contract_surface(
        openapi_source, file_suffix=".json"
    )
    observations = extract_frontend_operation_observations(
        generated_source,
        relative_path="src/lib/generated/client.ts",
        operation_bindings=extract_openapi_operation_bindings(bindings),
    )

    assert len(observations) == 1
    observation = observations[0]
    assert observation.operation_name == "listCustomers"
    assert observation.operation_id == "listCustomers"
    assert observation.client_kind == "http_client_member"
    assert observation.governance_kind == "generated"
    assert observation.manifest_source == "openapi"
