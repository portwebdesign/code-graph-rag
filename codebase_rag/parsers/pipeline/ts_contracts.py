from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from codebase_rag.parsers.pipeline.python_contracts import (
    ContractDefinition,
    ContractFieldDefinition,
)


@dataclass(frozen=True)
class FunctionContractSurface:
    function_name: str
    request_contracts: tuple[str, ...]
    response_contracts: tuple[str, ...]
    line_start: int | None = None
    line_end: int | None = None


_IDENTIFIER_RE = r"[A-Za-z_$][A-Za-z0-9_$]*"
_INTERFACE_DECL_RE = re.compile(
    rf"(?:export\s+)?interface\s+(?P<name>{_IDENTIFIER_RE})\b[^\{{]*\{{",
    re.MULTILINE,
)
_TYPE_ALIAS_DECL_RE = re.compile(
    rf"(?:export\s+)?type\s+(?P<name>{_IDENTIFIER_RE})\s*=\s*\{{",
    re.MULTILINE,
)
_ZOD_DECL_RE = re.compile(
    rf"(?:export\s+)?const\s+(?P<name>{_IDENTIFIER_RE})\s*=\s*{_IDENTIFIER_RE}(?:\.{_IDENTIFIER_RE})*\.object\s*\(",
    re.MULTILINE,
)
_FUNCTION_DECL_RE = re.compile(
    rf"(?:export\s+)?(?:async\s+)?function\s+(?P<name>{_IDENTIFIER_RE})\s*\((?P<params>.*?)\)\s*(?::\s*(?P<return_type>[\s\S]{{0,200}}?))?\s*\{{",
    re.MULTILINE | re.DOTALL,
)
_ARROW_FUNCTION_DECL_RE = re.compile(
    rf"(?:export\s+)?const\s+(?P<name>{_IDENTIFIER_RE})\s*=\s*(?:async\s+)?\((?P<params>.*?)\)\s*(?::\s*(?P<return_type>[\s\S]{{0,200}}?))?\s*=>",
    re.MULTILINE | re.DOTALL,
)


def extract_typescript_contracts(source: str) -> list[ContractDefinition]:
    """Extracts first-wave TS interface/type and Zod object contracts."""

    contracts: list[ContractDefinition] = []
    for match in _INTERFACE_DECL_RE.finditer(source):
        block = _extract_brace_block(source, match.end() - 1)
        if block is None:
            continue
        body, end_index = block
        contracts.append(
            ContractDefinition(
                name=match.group("name"),
                kind="typescript_interface",
                fields=tuple(_extract_ts_object_fields(body)),
                line_start=_line_number_for_offset(source, match.start()),
                line_end=_line_number_for_offset(source, end_index),
            )
        )

    for match in _TYPE_ALIAS_DECL_RE.finditer(source):
        block = _extract_brace_block(source, match.end() - 1)
        if block is None:
            continue
        body, end_index = block
        contracts.append(
            ContractDefinition(
                name=match.group("name"),
                kind="typescript_type_alias",
                fields=tuple(_extract_ts_object_fields(body)),
                line_start=_line_number_for_offset(source, match.start()),
                line_end=_line_number_for_offset(source, end_index),
            )
        )

    for match in _ZOD_DECL_RE.finditer(source):
        open_brace = source.find("{", match.end())
        if open_brace < 0:
            continue
        block = _extract_brace_block(source, open_brace)
        if block is None:
            continue
        body, end_index = block
        contracts.append(
            ContractDefinition(
                name=match.group("name"),
                kind="zod",
                fields=tuple(_extract_zod_fields(body)),
                line_start=_line_number_for_offset(source, match.start()),
                line_end=_line_number_for_offset(source, end_index),
            )
        )

    return contracts


def extract_typescript_function_contracts(
    source: str,
    known_contract_names: set[str],
) -> list[FunctionContractSurface]:
    """Maps TypeScript function request/response annotations to known contracts."""

    surfaces: list[FunctionContractSurface] = []
    for match in list(_FUNCTION_DECL_RE.finditer(source)) + list(
        _ARROW_FUNCTION_DECL_RE.finditer(source)
    ):
        params = match.group("params") or ""
        return_type = (match.group("return_type") or "").strip()
        request_contracts = _extract_contract_names_from_params(
            params, known_contract_names
        )
        response_contracts = _extract_contract_names_from_type_expr(
            return_type, known_contract_names
        )
        if not request_contracts and not response_contracts:
            continue
        surfaces.append(
            FunctionContractSurface(
                function_name=match.group("name"),
                request_contracts=tuple(request_contracts),
                response_contracts=tuple(response_contracts),
                line_start=_line_number_for_offset(source, match.start()),
                line_end=_line_number_for_offset(source, match.end()),
            )
        )
    return surfaces


def _extract_ts_object_fields(body: str) -> list[ContractFieldDefinition]:
    fields: list[ContractFieldDefinition] = []
    for member in _split_top_level(body, separators=(";", "\n")):
        candidate = member.strip().rstrip(",")
        if not candidate or candidate.startswith("//"):
            continue
        match = re.match(
            rf"^(?:readonly\s+)?(?P<name>{_IDENTIFIER_RE}|['\"][^'\"]+['\"])(?P<optional>\?)?\s*:\s*(?P<type>.+)$",
            candidate,
            re.DOTALL,
        )
        if not match:
            continue
        raw_name = match.group("name").strip("\"'")
        fields.append(
            ContractFieldDefinition(
                name=raw_name,
                type_repr=_normalize_type_repr(match.group("type")),
                required=match.group("optional") is None,
            )
        )
    return fields


def _extract_zod_fields(body: str) -> list[ContractFieldDefinition]:
    fields: list[ContractFieldDefinition] = []
    for member in _split_top_level(body, separators=(",",)):
        candidate = member.strip()
        if not candidate:
            continue
        match = re.match(
            rf"^(?P<name>{_IDENTIFIER_RE}|['\"][^'\"]+['\"])\s*:\s*(?P<schema>.+)$",
            candidate,
            re.DOTALL,
        )
        if not match:
            continue
        raw_name = match.group("name").strip("\"'")
        schema_expr = match.group("schema").strip()
        fields.append(
            ContractFieldDefinition(
                name=raw_name,
                type_repr=_zod_type_repr(schema_expr),
                required=".optional(" not in schema_expr
                and ".optional()" not in schema_expr
                and ".nullish(" not in schema_expr
                and ".nullish()" not in schema_expr,
            )
        )
    return fields


def _extract_contract_names_from_params(
    params: str,
    known_contract_names: set[str],
) -> list[str]:
    matches: list[str] = []
    for param in _split_top_level(params, separators=(",",)):
        candidate = param.strip()
        if ":" not in candidate:
            continue
        type_expr = candidate.split(":", 1)[1].strip()
        for contract_name in _extract_contract_names_from_type_expr(
            type_expr, known_contract_names
        ):
            if contract_name not in matches:
                matches.append(contract_name)
    return matches


def _extract_contract_names_from_type_expr(
    type_expr: str,
    known_contract_names: set[str],
) -> list[str]:
    if not type_expr:
        return []
    matches: list[str] = []
    candidates = re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", type_expr)
    for contract_name in known_contract_names:
        if contract_name in candidates and contract_name not in matches:
            matches.append(contract_name)
        infer_pattern = rf"typeof\s+{re.escape(contract_name)}\b"
        if re.search(infer_pattern, type_expr) and contract_name not in matches:
            matches.append(contract_name)
    return matches


def _zod_type_repr(schema_expr: str) -> str:
    expr = schema_expr.replace(" ", "")
    if "z.array(" in expr:
        inner = re.search(r"z\.array\((?P<inner>.+)\)", expr)
        if inner:
            return f"{_zod_type_repr(inner.group('inner'))}[]"
        return "array"
    if ".optional(" in expr or ".optional()" in expr:
        expr = expr.replace(".optional()", "")
    if ".nullish(" in expr or ".nullish()" in expr:
        expr = expr.replace(".nullish()", "")
    if "z.string" in expr:
        return "string"
    if "z.number" in expr:
        return "number"
    if "z.boolean" in expr:
        return "boolean"
    if "z.object(" in expr:
        return "object"
    if "z.enum(" in expr:
        return "enum"
    if "z.record(" in expr:
        return "record"
    if "z.literal(" in expr:
        literal_match = re.search(r"z\.literal\((?P<literal>.+)\)", expr)
        return literal_match.group("literal") if literal_match else "literal"
    ref_match = re.search(r"typeof\s+([A-Za-z_$][A-Za-z0-9_$]*)", schema_expr)
    if ref_match:
        return ref_match.group(1)
    return "unknown"


def _normalize_type_repr(type_expr: str) -> str:
    normalized = " ".join(type_expr.replace("\n", " ").split())
    return normalized.rstrip(";").rstrip(",")


def _split_top_level(
    source: str,
    *,
    separators: Iterable[str],
) -> list[str]:
    sep_set = set(separators)
    chunks: list[str] = []
    current: list[str] = []
    brace_depth = 0
    bracket_depth = 0
    paren_depth = 0
    string_delim: str | None = None
    escape = False
    for char in source:
        current.append(char)
        if string_delim:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == string_delim:
                string_delim = None
            continue
        if char in {"'", '"', "`"}:
            string_delim = char
            continue
        if char == "{":
            brace_depth += 1
            continue
        if char == "}":
            brace_depth = max(0, brace_depth - 1)
            continue
        if char == "[":
            bracket_depth += 1
            continue
        if char == "]":
            bracket_depth = max(0, bracket_depth - 1)
            continue
        if char == "(":
            paren_depth += 1
            continue
        if char == ")":
            paren_depth = max(0, paren_depth - 1)
            continue
        if (
            char in sep_set
            and brace_depth == 0
            and bracket_depth == 0
            and paren_depth == 0
        ):
            chunks.append("".join(current[:-1]))
            current = []
    if current:
        chunks.append("".join(current))
    return chunks


def _extract_brace_block(source: str, open_brace_index: int) -> tuple[str, int] | None:
    depth = 0
    string_delim: str | None = None
    escape = False
    start_index = open_brace_index + 1
    for index in range(open_brace_index, len(source)):
        char = source[index]
        if string_delim:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == string_delim:
                string_delim = None
            continue
        if char in {"'", '"', "`"}:
            string_delim = char
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                return source[start_index:index], index
    return None


def _line_number_for_offset(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1
