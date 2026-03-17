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


def test_detects_operation_id_based_query_and_request_helpers() -> None:
    openapi_source = """{
  "openapi": "3.0.3",
  "paths": {
    "/api/internal/health": {
      "get": {
        "operationId": "system_get_get_system_health",
        "responses": {"200": {"description": "OK"}}
      }
    },
    "/api/tasks": {
      "get": {
        "operationId": "tasks_get_list_tasks",
        "responses": {"200": {"description": "OK"}}
      }
    }
  }
}
"""
    source = """export function DashboardScreen(session: unknown) {
  createOperationQueryConfig(session, "system_get_get_system_health", {}, "route");
  return requestOperation(session, "tasks_get_list_tasks", {});
}
"""

    _contracts, bindings = extract_openapi_contract_surface(
        openapi_source, file_suffix=".json"
    )
    observations = extract_frontend_operation_observations(
        source,
        relative_path="frontend/src/features/screens/DashboardScreen.tsx",
        operation_bindings=extract_openapi_operation_bindings(bindings),
    )

    assert len(observations) == 2
    operation_ids = {observation.operation_id for observation in observations}
    assert operation_ids == {
        "system_get_get_system_health",
        "tasks_get_list_tasks",
    }
    assert {observation.client_kind for observation in observations} == {
        "operation_query_config",
        "operation_request",
    }
    assert all(
        observation.governance_kind == "manifest" for observation in observations
    )


def test_detects_get_operation_lookup_as_manifest_governance() -> None:
    openapi_source = """{
  "openapi": "3.0.3",
  "paths": {
    "/api/internal/system/frontend-telemetry": {
      "post": {
        "operationId": "system_post_ingest_frontend_telemetry",
        "responses": {"200": {"description": "OK"}}
      }
    }
  }
}
"""
    source = """export function postTelemetry() {
  const operation = getOperation("system_post_ingest_frontend_telemetry");
  return operation;
}
"""

    _contracts, bindings = extract_openapi_contract_surface(
        openapi_source, file_suffix=".json"
    )
    observations = extract_frontend_operation_observations(
        source,
        relative_path="frontend/src/shared/telemetry/runtime.ts",
        operation_bindings=extract_openapi_operation_bindings(bindings),
    )

    assert len(observations) == 1
    observation = observations[0]
    assert observation.operation_id == "system_post_ingest_frontend_telemetry"
    assert observation.client_kind == "operation_lookup"
    assert observation.governance_kind == "manifest"
    assert observation.path == "/api/internal/system/frontend-telemetry"
