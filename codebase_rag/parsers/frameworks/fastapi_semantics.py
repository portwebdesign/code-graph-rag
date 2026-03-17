from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class FastAPIRouteSemantics:
    router_name: str
    method: str
    path: str
    handler_name: str
    response_model: str | None = None
    dependencies: list[str] = field(default_factory=list)
    security_dependencies: list[str] = field(default_factory=list)
    security_scopes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    line_start: int | None = None
    line_end: int | None = None


_FASTAPI_ROUTE_RE = re.compile(
    r"@(?P<router>[A-Za-z_][A-Za-z0-9_]*)\.(?P<method>get|post|put|delete|patch|api_route)\(",
    re.IGNORECASE,
)
_DEF_RE = re.compile(
    r"\s*(?:async\s+def|def)\s+(?P<handler>[A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_RESPONSE_MODEL_RE = re.compile(r"response_model\s*=\s*([A-Za-z_][\w\.]*)")
_TAGS_RE = re.compile(r"tags\s*=\s*\[([^\]]*)\]", re.IGNORECASE)
_DEPENDS_RE = re.compile(r"Depends\(\s*([A-Za-z_][\w\.]*)?\s*\)")
_SECURITY_RE = re.compile(r"Security\(\s*([A-Za-z_][\w\.]*)?\s*")
_SECURITY_SCOPES_RE = re.compile(
    r"Security\([^)]*?\bscopes\s*=\s*\[([^\]]*)\]", re.IGNORECASE | re.DOTALL
)


def extract_fastapi_route_semantics(source: str) -> list[FastAPIRouteSemantics]:
    routes: list[FastAPIRouteSemantics] = []

    for match in _FASTAPI_ROUTE_RE.finditer(source):
        args_result = _extract_balanced_segment(source, match.end() - 1)
        if args_result is None:
            continue
        args, close_index = args_result
        path = _extract_first_string_arg(args)
        if not path:
            continue

        handler_result = _extract_handler_signature(source, close_index + 1)
        if handler_result is None:
            continue
        handler_name, params, handler_end = handler_result

        method = match.group("method").upper()
        if method == "API_ROUTE":
            method = _extract_api_route_method(args)

        line_start = _line_number_for_index(source, match.start())
        line_end = _line_number_for_index(source, handler_end)
        dependencies = _dedupe_preserve(
            _extract_dep_targets(args) + _extract_dep_targets(params)
        )
        security_dependencies = _dedupe_preserve(
            _extract_security_targets(args) + _extract_security_targets(params)
        )
        security_scopes = _dedupe_preserve(
            _extract_security_scopes(args) + _extract_security_scopes(params)
        )

        routes.append(
            FastAPIRouteSemantics(
                router_name=match.group("router"),
                method=method,
                path=path,
                handler_name=handler_name,
                response_model=_extract_response_model(args),
                dependencies=dependencies,
                security_dependencies=security_dependencies,
                security_scopes=security_scopes,
                tags=_extract_tags(args),
                line_start=line_start,
                line_end=line_end,
            )
        )

    return routes


def _extract_handler_signature(
    source: str, start_index: int
) -> tuple[str, str, int] | None:
    def_match = _DEF_RE.match(source, start_index)
    if not def_match:
        return None
    handler_name = def_match.group("handler")
    open_paren_index = source.find("(", def_match.end())
    if open_paren_index < 0:
        return None
    params_result = _extract_balanced_segment(source, open_paren_index)
    if params_result is None:
        return None
    params, close_index = params_result
    return handler_name, params, close_index


def _extract_balanced_segment(
    source: str, open_index: int, open_char: str = "(", close_char: str = ")"
) -> tuple[str, int] | None:
    if open_index < 0 or open_index >= len(source) or source[open_index] != open_char:
        return None

    depth = 0
    in_string: str | None = None
    escaped = False

    for index in range(open_index, len(source)):
        char = source[index]
        if in_string is not None:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == in_string:
                in_string = None
            continue

        if char in {"'", '"'}:
            in_string = char
            continue
        if char == open_char:
            depth += 1
            continue
        if char == close_char:
            depth -= 1
            if depth == 0:
                return source[open_index + 1 : index], index
    return None


def _extract_first_string_arg(args: str) -> str | None:
    match = re.search(r"['\"]([^'\"]+)['\"]", args)
    if not match:
        return None
    return match.group(1)


def _extract_response_model(args: str) -> str | None:
    match = _RESPONSE_MODEL_RE.search(args)
    if not match:
        return None
    return match.group(1)


def _extract_dep_targets(text: str) -> list[str]:
    return [match.group(1) for match in _DEPENDS_RE.finditer(text) if match.group(1)]


def _extract_security_targets(text: str) -> list[str]:
    return [match.group(1) for match in _SECURITY_RE.finditer(text) if match.group(1)]


def _extract_security_scopes(text: str) -> list[str]:
    scopes: list[str] = []
    for match in _SECURITY_SCOPES_RE.finditer(text):
        raw_scopes = match.group(1)
        for token in raw_scopes.split(","):
            cleaned = token.strip().strip("'\"")
            if cleaned:
                scopes.append(cleaned)
    return scopes


def _extract_tags(args: str) -> list[str]:
    match = _TAGS_RE.search(args)
    if not match:
        return []
    return _dedupe_preserve(
        [
            token.strip().strip("'\"")
            for token in match.group(1).split(",")
            if token.strip().strip("'\"")
        ]
    )


def _extract_api_route_method(args: str) -> str:
    match = re.search(r"methods\s*=\s*\[([^\]]+)\]", args, re.IGNORECASE)
    if not match:
        return "ANY"
    methods_raw = match.group(1)
    methods = [token.strip().strip("'\"") for token in methods_raw.split(",")]
    methods = [method for method in methods if method]
    return methods[0].upper() if methods else "ANY"


def _line_number_for_index(source: str, index: int) -> int:
    return source.count("\n", 0, max(0, index)) + 1


def _dedupe_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
