from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from codebase_rag.parsers.pipeline.openapi_contracts import (
    OpenApiEndpointContractBinding,
)
from codebase_rag.parsers.pipeline.typescript_symbol_blocks import (
    TypeScriptSymbolBlock,
    extract_typescript_symbol_blocks,
)

_METHOD_PATTERN = re.compile(
    r"method\s*:\s*['\"](GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)['\"]",
    re.IGNORECASE,
)
_FETCH_PATTERN = re.compile(
    r"fetch\s*\(\s*['\"](?P<path>[^'\"]+)['\"](\s*,\s*\{(?P<options>[^}]*)\})?",
    re.IGNORECASE,
)
_FETCH_TEMPLATE_PATTERN = re.compile(
    r"fetch\s*\(\s*`(?P<path>[^`]+)`(\s*,\s*\{(?P<options>[^}]*)\})?",
    re.IGNORECASE,
)
_AXIOS_PATTERN = re.compile(
    r"axios\.(?P<method>get|post|put|delete|patch)\s*\(\s*(['\"`])(?P<path>[^'\"`]+)\2",
    re.IGNORECASE,
)
_MEMBER_HTTP_PATTERN = re.compile(
    r"\b(?P<receiver>(?:[A-Za-z_$][\w$]*)(?:\.[A-Za-z_$][\w$]*)*)\."
    r"(?P<method>get|post|put|delete|patch)\s*\(\s*(['\"`])(?P<path>[^'\"`]+)\3",
    re.IGNORECASE,
)
_REQUEST_BLOCK_PATTERN = re.compile(
    r"\b(?:[A-Za-z_$][\w$]*)(?:\.[A-Za-z_$][\w$]*)*\.request\s*\(\s*\{"
    r"(?P<body>[\s\S]{0,500}?)\}\s*\)",
    re.IGNORECASE,
)
_REQUEST_URL_PATTERN = re.compile(
    r"\b(?:url|path)\s*:\s*['\"`]([^'\"`]+)['\"`]",
    re.IGNORECASE,
)
_REQUEST_OPERATION_PATTERN = re.compile(
    r"\brequestOperation(?:Record)?(?:<[^>]+>)?\s*\(\s*[^,]+,\s*['\"`](?P<operation_id>[^'\"`]+)['\"`]",
    re.IGNORECASE,
)
_CREATE_QUERY_CONFIG_PATTERN = re.compile(
    r"\bcreateOperationQueryConfig(?:<[^>]+>)?\s*\(\s*[^,]+,\s*['\"`](?P<operation_id>[^'\"`]+)['\"`]",
    re.IGNORECASE,
)
_USE_OPERATION_QUERY_PATTERN = re.compile(
    r"\buseOperationQuery(?:<[^>]+>)?\s*\(\s*['\"`](?P<operation_id>[^'\"`]+)['\"`]",
    re.IGNORECASE,
)
_GET_OPERATION_PATTERN = re.compile(
    r"\bgetOperation\s*\(\s*['\"`](?P<operation_id>[^'\"`]+)['\"`]\s*\)",
    re.IGNORECASE,
)
_GET_OPERATION_BY_PATH_PATTERN = re.compile(
    r"\bgetOperationByPath\s*\(\s*['\"`](?P<method>get|post|put|delete|patch|options|head)['\"`]\s*,\s*['\"`](?P<path>[^'\"`]+)['\"`]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FrontendOperationObservation:
    symbol_name: str
    symbol_kind: str
    operation_name: str
    method: str
    path: str
    client_kind: str
    governance_kind: str
    operation_id: str | None = None
    manifest_source: str | None = None
    line_start: int | None = None
    line_end: int | None = None


def extract_frontend_operation_observations(
    source: str,
    *,
    relative_path: str,
    operation_bindings: dict[tuple[str, str], OpenApiEndpointContractBinding],
) -> list[FrontendOperationObservation]:
    """Extracts generated-client and raw-bypass operation usage from TS/TSX."""

    observations: list[FrontendOperationObservation] = []
    bindings_by_operation_id = {
        binding.operation_id: binding
        for binding in operation_bindings.values()
        if binding.operation_id
    }
    for block in extract_typescript_symbol_blocks(source):
        for request in _extract_request_descriptors(
            block.body,
            operation_bindings=operation_bindings,
            operation_bindings_by_id=bindings_by_operation_id,
        ):
            method = request["method"]
            path = normalize_http_path(request["path"])
            binding = operation_bindings.get((method, path))
            governance_kind = _classify_governance_kind(
                relative_path=relative_path,
                client_kind=request["client_kind"],
                binding=binding,
            )
            operation_name = _build_operation_name(
                symbol_name=block.symbol_name,
                method=method,
                path=path,
                binding=binding,
            )
            observations.append(
                FrontendOperationObservation(
                    symbol_name=block.symbol_name,
                    symbol_kind=_symbol_kind_for_block(block, relative_path),
                    operation_name=operation_name,
                    operation_id=(
                        request.get("operation_id")
                        or (binding.operation_id if binding else None)
                    ),
                    method=method,
                    path=path,
                    client_kind=request["client_kind"],
                    governance_kind=governance_kind,
                    manifest_source=(
                        request.get("manifest_source")
                        or ("openapi" if binding else None)
                    ),
                    line_start=block.line_start,
                    line_end=block.line_end,
                )
            )
    return _dedupe_operations(observations)


def extract_openapi_operation_bindings(
    bindings: Iterable[OpenApiEndpointContractBinding],
) -> dict[tuple[str, str], OpenApiEndpointContractBinding]:
    return {
        (binding.method.upper(), normalize_http_path(binding.path)): binding
        for binding in bindings
    }


def normalize_http_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    normalized = re.sub(r"\{[^/]+\}", "{param}", normalized)
    normalized = re.sub(r"\[[^/]+\]", "{param}", normalized)
    normalized = re.sub(r":[A-Za-z_][A-Za-z0-9_]*", "{param}", normalized)
    normalized = re.sub(r"\$\{[^}]+\}", "{param}", normalized)
    normalized = re.sub(r"//+", "/", normalized)
    if normalized and not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if len(normalized) > 1 and normalized.endswith("/"):
        normalized = normalized[:-1]
    return normalized or "/"


def _extract_request_descriptors(
    body: str,
    *,
    operation_bindings: (
        dict[tuple[str, str], OpenApiEndpointContractBinding] | None
    ) = None,
    operation_bindings_by_id: dict[str, OpenApiEndpointContractBinding] | None = None,
) -> list[dict[str, str]]:
    operation_bindings = operation_bindings or {}
    operation_bindings_by_id = operation_bindings_by_id or {}
    requests: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def _append(
        method: str,
        raw_path: str,
        *,
        client_kind: str,
        operation_id: str | None = None,
        manifest_source: str | None = None,
    ) -> None:
        path = normalize_http_path(raw_path)
        if not _looks_like_route_like_path(raw_path, path):
            return
        key = (method.upper(), path, client_kind)
        if key in seen:
            return
        seen.add(key)
        request: dict[str, str] = {
            "method": method.upper(),
            "path": path,
            "client_kind": client_kind,
        }
        if operation_id:
            request["operation_id"] = operation_id
        if manifest_source:
            request["manifest_source"] = manifest_source
        requests.append(request)

    for match in _FETCH_PATTERN.finditer(body):
        options = match.group("options") or ""
        method_match = _METHOD_PATTERN.search(options)
        _append(
            method_match.group(1) if method_match else "GET",
            match.group("path"),
            client_kind="fetch",
        )
    for match in _FETCH_TEMPLATE_PATTERN.finditer(body):
        options = match.group("options") or ""
        method_match = _METHOD_PATTERN.search(options)
        _append(
            method_match.group(1) if method_match else "GET",
            match.group("path"),
            client_kind="fetch_template",
        )
    for match in _AXIOS_PATTERN.finditer(body):
        _append(
            match.group("method"),
            match.group("path"),
            client_kind="axios",
        )
    for match in _MEMBER_HTTP_PATTERN.finditer(body):
        receiver = match.group("receiver") or ""
        raw_path = match.group("path")
        if not _looks_like_member_http_request(receiver, raw_path):
            continue
        _append(
            match.group("method"),
            raw_path,
            client_kind="http_client_member",
        )
    for match in _REQUEST_BLOCK_PATTERN.finditer(body):
        request_body = match.group("body") or ""
        url_match = _REQUEST_URL_PATTERN.search(request_body)
        if not url_match:
            continue
        method_match = _METHOD_PATTERN.search(request_body)
        _append(
            method_match.group(1) if method_match else "GET",
            url_match.group(1),
            client_kind="http_client_request",
        )

    _append_operation_id_descriptors(
        requests,
        seen,
        body,
        operation_bindings=operation_bindings,
        operation_bindings_by_id=operation_bindings_by_id,
    )

    return requests


def _append_operation_id_descriptors(
    requests: list[dict[str, str]],
    seen: set[tuple[str, str, str]],
    body: str,
    *,
    operation_bindings: dict[tuple[str, str], OpenApiEndpointContractBinding],
    operation_bindings_by_id: dict[str, OpenApiEndpointContractBinding],
) -> None:
    helper_patterns = (
        (_REQUEST_OPERATION_PATTERN, "operation_request"),
        (_CREATE_QUERY_CONFIG_PATTERN, "operation_query_config"),
        (_USE_OPERATION_QUERY_PATTERN, "operation_query_hook"),
        (_GET_OPERATION_PATTERN, "operation_lookup"),
    )
    for pattern, client_kind in helper_patterns:
        for match in pattern.finditer(body):
            operation_id = match.group("operation_id")
            binding = operation_bindings_by_id.get(operation_id)
            if binding is None:
                continue
            _append_bound_operation_descriptor(
                requests,
                seen,
                binding,
                client_kind=client_kind,
                operation_id=operation_id,
            )

    for match in _GET_OPERATION_BY_PATH_PATTERN.finditer(body):
        method = match.group("method").upper()
        path = normalize_http_path(match.group("path"))
        binding = operation_bindings.get((method, path))
        if binding is None:
            continue
        _append_bound_operation_descriptor(
            requests,
            seen,
            binding,
            client_kind="operation_path_lookup",
            operation_id=binding.operation_id,
        )


def _append_bound_operation_descriptor(
    requests: list[dict[str, str]],
    seen: set[tuple[str, str, str]],
    binding: OpenApiEndpointContractBinding,
    *,
    client_kind: str,
    operation_id: str | None,
) -> None:
    method = binding.method.upper()
    path = normalize_http_path(binding.path)
    key = (method, path, client_kind)
    if key in seen:
        return
    seen.add(key)
    request: dict[str, str] = {
        "method": method,
        "path": path,
        "client_kind": client_kind,
        "manifest_source": "openapi",
    }
    if operation_id:
        request["operation_id"] = operation_id
    requests.append(request)


def _classify_governance_kind(
    *,
    relative_path: str,
    client_kind: str,
    binding: OpenApiEndpointContractBinding | None,
) -> str:
    normalized_path = relative_path.replace("\\", "/").lower()
    raw_clients = {"fetch", "fetch_template", "axios"}
    manifest_clients = {
        "operation_lookup",
        "operation_path_lookup",
        "operation_query_config",
        "operation_query_hook",
        "operation_request",
    }
    if client_kind in raw_clients:
        return "bypass"
    if client_kind in manifest_clients and binding is not None:
        return "manifest"
    if "/generated/" in f"/{normalized_path}/":
        return "generated"
    if binding is not None and client_kind.startswith("http_client"):
        return "manifest"
    if binding is not None:
        return "governed"
    return "bypass"


def _build_operation_name(
    *,
    symbol_name: str,
    method: str,
    path: str,
    binding: OpenApiEndpointContractBinding | None,
) -> str:
    if binding and binding.operation_id:
        return binding.operation_id
    safe_path = path.strip("/").replace("/", ".").replace("{param}", "param") or "root"
    return f"{symbol_name}.{method.lower()}.{safe_path}"


def _symbol_kind_for_block(block: TypeScriptSymbolBlock, relative_path: str) -> str:
    if relative_path.endswith((".tsx", ".jsx")) and block.symbol_name[:1].isupper():
        return "component"
    return "function"


def _looks_like_member_http_request(receiver: str, raw_path: str) -> bool:
    normalized_receiver = receiver.strip().lower()
    if not normalized_receiver:
        return False

    non_request_suffixes = (
        ".headers",
        ".searchparams",
        ".params",
        ".query",
        ".queries",
        ".cookies",
        ".headersmap",
    )
    if normalized_receiver.endswith(non_request_suffixes):
        return False
    if _looks_like_route_like_path(raw_path, normalize_http_path(raw_path)):
        return True

    clientish_tokens = {
        "api",
        "client",
        "http",
        "https",
        "axios",
        "request",
        "requester",
        "fetcher",
        "gateway",
        "sdk",
        "service",
        "agent",
    }
    receiver_tokens = {
        token for token in re.split(r"[^a-z0-9]+", normalized_receiver) if token
    }
    return not clientish_tokens.isdisjoint(receiver_tokens)


def _looks_like_route_like_path(raw_path: str, normalized_path: str) -> bool:
    if normalized_path == "/":
        return False
    if normalized_path.startswith(("/api/", "/graphql", "/v1/", "/v2/")):
        return True
    return raw_path.startswith(("/", "http://", "https://"))


def _dedupe_operations(
    observations: Iterable[FrontendOperationObservation],
) -> list[FrontendOperationObservation]:
    unique: dict[tuple[str, str, str, str], FrontendOperationObservation] = {}
    for observation in observations:
        key = (
            observation.symbol_name,
            observation.method,
            observation.path,
            observation.client_kind,
        )
        unique.setdefault(key, observation)
    return list(unique.values())
