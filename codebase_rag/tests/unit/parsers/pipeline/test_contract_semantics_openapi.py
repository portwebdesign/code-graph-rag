from __future__ import annotations

from codebase_rag.parsers.pipeline.openapi_contracts import (
    extract_openapi_contract_surface,
)


def test_extracts_openapi_schema_contracts_and_endpoint_bindings() -> None:
    source = """{
  "openapi": "3.0.3",
  "paths": {
    "/api/orders": {
      "post": {
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "$ref": "#/components/schemas/CreateOrderRequest"
              }
            }
          }
        },
        "responses": {
          "201": {
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/OrderResponse"
                }
              }
            }
          }
        }
      }
    }
  },
  "components": {
    "schemas": {
      "CreateOrderRequest": {
        "type": "object",
        "required": ["customerId"],
        "properties": {
          "customerId": { "type": "string" },
          "note": { "type": "string" }
        }
      },
      "OrderResponse": {
        "type": "object",
        "required": ["id"],
        "properties": {
          "id": { "type": "string" },
          "warnings": { "type": "array", "items": { "type": "string" } }
        }
      }
    }
  }
}
"""

    contracts, bindings = extract_openapi_contract_surface(source, file_suffix=".json")
    contract_index = {contract.name: contract for contract in contracts}

    assert set(contract_index) == {"CreateOrderRequest", "OrderResponse"}
    assert contract_index["CreateOrderRequest"].kind == "openapi_schema"
    create_fields = {
        field.name: field for field in contract_index["CreateOrderRequest"].fields
    }
    assert create_fields["customerId"].required is True
    assert create_fields["note"].required is False

    response_fields = {
        field.name: field for field in contract_index["OrderResponse"].fields
    }
    assert response_fields["warnings"].type_repr == "string[]"

    assert len(bindings) == 1
    binding = bindings[0]
    assert binding.method == "POST"
    assert binding.path == "/api/orders"
    assert binding.request_contracts == ("CreateOrderRequest",)
    assert binding.response_contracts == ("OrderResponse",)
