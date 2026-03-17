from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

import yaml

from codebase_rag.parsers.pipeline.python_contracts import (
    ContractDefinition,
    ContractFieldDefinition,
)

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


@dataclass(frozen=True)
class OpenApiEndpointContractBinding:
    method: str
    path: str
    operation_id: str | None
    request_contracts: tuple[str, ...]
    response_contracts: tuple[str, ...]


def extract_openapi_contract_surface(
    source: str,
    *,
    file_suffix: str,
) -> tuple[list[ContractDefinition], list[OpenApiEndpointContractBinding]]:
    """Extracts first-wave OpenAPI schema contracts and endpoint bindings."""

    document = _load_openapi_document(source, file_suffix=file_suffix)
    if not isinstance(document, dict):
        return [], []
    document_map = cast(dict[str, object], document)
    if "openapi" not in document_map and "swagger" not in document_map:
        return [], []

    components = document_map.get("components")
    schemas = (
        cast(dict[str, object], components).get("schemas", {})
        if isinstance(components, dict)
        else {}
    )
    if not isinstance(schemas, dict):
        schemas = {}
    contracts: list[ContractDefinition] = []
    for schema_name, schema in cast(dict[str, object], schemas).items():
        if not isinstance(schema_name, str):
            continue
        if not isinstance(schema, dict):
            continue
        schema_map = cast(dict[str, object], schema)
        contracts.append(
            ContractDefinition(
                name=schema_name,
                kind="openapi_schema",
                fields=tuple(_extract_openapi_fields(schema_map)),
            )
        )

    bindings: list[OpenApiEndpointContractBinding] = []
    paths = document_map.get("paths", {})
    if isinstance(paths, dict):
        for raw_path, path_item in cast(dict[str, object], paths).items():
            if not isinstance(raw_path, str) or not isinstance(path_item, dict):
                continue
            path_item_map = cast(dict[str, object], path_item)
            for method, operation in path_item_map.items():
                normalized_method = str(method).lower()
                if normalized_method not in HTTP_METHODS:
                    continue
                if not isinstance(operation, dict):
                    continue
                operation_map = cast(dict[str, object], operation)
                bindings.append(
                    OpenApiEndpointContractBinding(
                        method=normalized_method.upper(),
                        path=raw_path,
                        operation_id=_normalize_operation_id(
                            operation_map.get("operationId")
                        ),
                        request_contracts=tuple(
                            _extract_openapi_request_contracts(operation_map)
                        ),
                        response_contracts=tuple(
                            _extract_openapi_response_contracts(operation_map)
                        ),
                    )
                )

    return contracts, bindings


def _extract_openapi_fields(schema: dict[str, object]) -> list[ContractFieldDefinition]:
    properties = schema.get("properties", {})
    raw_required_names = schema.get("required")
    required_names = (
        {item for item in raw_required_names if isinstance(item, str)}
        if isinstance(raw_required_names, list)
        else set()
    )
    if not isinstance(properties, dict):
        return []

    fields: list[ContractFieldDefinition] = []
    for field_name, payload in cast(dict[str, object], properties).items():
        if not isinstance(field_name, str):
            continue
        if not isinstance(payload, dict):
            continue
        payload_map = cast(dict[str, object], payload)
        fields.append(
            ContractFieldDefinition(
                name=field_name,
                type_repr=_openapi_type_repr(payload_map),
                required=field_name in required_names,
            )
        )
    return fields


def _extract_openapi_request_contracts(operation: dict[str, object]) -> list[str]:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return []
    request_body_map = cast(dict[str, object], request_body)
    content = request_body_map.get("content", {})
    if not isinstance(content, dict):
        return []
    return _extract_contract_refs_from_content(cast(dict[str, object], content))


def _extract_openapi_response_contracts(operation: dict[str, object]) -> list[str]:
    responses = operation.get("responses", {})
    if not isinstance(responses, dict):
        return []
    contract_names: list[str] = []
    for status, response in responses.items():
        if not isinstance(status, str) or not isinstance(response, dict):
            continue
        if not (status.startswith("2") or status == "default"):
            continue
        response_map = cast(dict[str, object], response)
        content = response_map.get("content", {})
        if not isinstance(content, dict):
            continue
        for contract_name in _extract_contract_refs_from_content(
            cast(dict[str, object], content)
        ):
            if contract_name not in contract_names:
                contract_names.append(contract_name)
    return contract_names


def _extract_contract_refs_from_content(content: dict[str, object]) -> list[str]:
    contract_names: list[str] = []
    for media in content.values():
        if not isinstance(media, dict):
            continue
        media_map = cast(dict[str, object], media)
        schema = media_map.get("schema")
        if not isinstance(schema, dict):
            continue
        contract_name = _schema_ref_name(cast(dict[str, object], schema))
        if contract_name and contract_name not in contract_names:
            contract_names.append(contract_name)
    return contract_names


def _schema_ref_name(schema: dict[str, object]) -> str | None:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref:
        return ref.rsplit("/", 1)[-1]
    return None


def _openapi_type_repr(schema: dict[str, object]) -> str:
    ref_name = _schema_ref_name(schema)
    if ref_name:
        return ref_name

    schema_type = schema.get("type")
    if isinstance(schema_type, str) and schema_type == "array":
        items = schema.get("items", {})
        if isinstance(items, dict):
            return f"{_openapi_type_repr(cast(dict[str, object], items))}[]"
        return "array"
    if isinstance(schema_type, str):
        return schema_type
    if "enum" in schema:
        return "enum"
    if "properties" in schema:
        return "object"
    return "unknown"


def _load_openapi_document(source: str, *, file_suffix: str) -> object:
    try:
        if file_suffix == ".json":
            return json.loads(source)
        if file_suffix in {".yaml", ".yml"}:
            return yaml.safe_load(source) if source.strip() else {}
    except Exception:
        return None
    return None


def _normalize_operation_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
