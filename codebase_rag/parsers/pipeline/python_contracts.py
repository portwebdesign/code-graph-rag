from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ContractFieldDefinition:
    name: str
    type_repr: str | None
    required: bool
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True)
class ContractDefinition:
    name: str
    kind: str
    fields: tuple[ContractFieldDefinition, ...] = field(default_factory=tuple)
    line_start: int | None = None
    line_end: int | None = None
    total: bool = True


def extract_python_contracts(source: str) -> list[ContractDefinition]:
    """Extracts Python contract definitions for Pydantic, dataclass, and TypedDict."""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    aliases = _build_aliases(tree)
    contracts: list[ContractDefinition] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        contract_kind = _get_contract_kind(node, aliases)
        if contract_kind is None:
            continue
        total = _typed_dict_total(node) if contract_kind == "typeddict" else True
        contracts.append(
            ContractDefinition(
                name=node.name,
                kind=contract_kind,
                fields=tuple(_extract_contract_fields(node, contract_kind, total)),
                line_start=getattr(node, "lineno", None),
                line_end=getattr(node, "end_lineno", None),
                total=total,
            )
        )
    return contracts


def extract_python_handler_contracts(
    source: str,
    known_contract_names: set[str],
) -> dict[str, list[str]]:
    """Maps handler function names to referenced request contract names."""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    aliases = _build_aliases(tree)
    handler_contracts: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        contracts = _extract_handler_contract_names(node, known_contract_names, aliases)
        if contracts:
            handler_contracts[node.name] = contracts
    return handler_contracts


def _build_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                bound_name = alias.asname or alias.name
                aliases[bound_name] = f"{module}.{alias.name}" if module else alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound_name = alias.asname or alias.name
                aliases[bound_name] = alias.name
    return aliases


def _get_contract_kind(node: ast.ClassDef, aliases: dict[str, str]) -> str | None:
    if any(_looks_like_dataclass(dec, aliases) for dec in node.decorator_list):
        return "dataclass"

    base_names = {_resolve_name(base, aliases) for base in node.bases}
    if any(base.endswith("BaseModel") for base in base_names):
        return "pydantic"
    if any(base.endswith("TypedDict") for base in base_names):
        return "typeddict"
    return None


def _typed_dict_total(node: ast.ClassDef) -> bool:
    for keyword in node.keywords:
        if keyword.arg != "total":
            continue
        return not isinstance(keyword.value, ast.Constant) or bool(keyword.value.value)
    return True


def _extract_contract_fields(
    node: ast.ClassDef,
    contract_kind: str,
    total: bool,
) -> list[ContractFieldDefinition]:
    fields: list[ContractFieldDefinition] = []
    for stmt in node.body:
        if not isinstance(stmt, ast.AnnAssign):
            continue
        if not isinstance(stmt.target, ast.Name):
            continue
        annotation = _safe_unparse(stmt.annotation)
        required = _is_required_field(stmt, contract_kind, total)
        fields.append(
            ContractFieldDefinition(
                name=stmt.target.id,
                type_repr=annotation,
                required=required,
                line_start=getattr(stmt, "lineno", None),
                line_end=getattr(stmt, "end_lineno", None),
            )
        )
    return fields


def _extract_handler_contract_names(
    function_node: ast.FunctionDef | ast.AsyncFunctionDef,
    known_contract_names: set[str],
    aliases: dict[str, str],
) -> list[str]:
    matches: list[str] = []
    for arg, default in _iter_function_parameters(function_node):
        if arg.annotation is None:
            continue
        if _looks_like_dependency_default(default, aliases):
            continue
        for name in _extract_annotation_candidate_names(arg.annotation, aliases):
            if name in known_contract_names and name not in matches:
                matches.append(name)
    return matches


def _iter_function_parameters(
    function_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Iterable[tuple[ast.arg, ast.expr | None]]:
    positional_args = [
        *function_node.args.posonlyargs,
        *function_node.args.args,
    ]
    defaults = list(function_node.args.defaults)
    default_padding = [None] * max(0, len(positional_args) - len(defaults))
    for arg, default in zip(
        positional_args, [*default_padding, *defaults], strict=False
    ):
        yield arg, default
    for arg, default in zip(
        function_node.args.kwonlyargs,
        function_node.args.kw_defaults,
        strict=False,
    ):
        yield arg, default


def _looks_like_dependency_default(
    default: ast.expr | None,
    aliases: dict[str, str],
) -> bool:
    if not isinstance(default, ast.Call):
        return False
    call_name = _resolve_name(default.func, aliases)
    return call_name.endswith("Depends") or call_name.endswith("Security")


def _extract_annotation_candidate_names(
    annotation: ast.expr,
    aliases: dict[str, str],
) -> list[str]:
    candidates: list[str] = []
    for node in ast.walk(annotation):
        if not isinstance(node, ast.Name | ast.Attribute):
            continue
        resolved = _resolve_name(node, aliases)
        simple_name = resolved.rsplit(".", 1)[-1]
        if simple_name not in candidates:
            candidates.append(simple_name)
    return candidates


def _looks_like_dataclass(node: ast.expr, aliases: dict[str, str]) -> bool:
    expr = node.func if isinstance(node, ast.Call) else node
    return _resolve_name(expr, aliases).endswith("dataclass")


def _is_required_field(
    stmt: ast.AnnAssign,
    contract_kind: str,
    total: bool,
) -> bool:
    if contract_kind == "typeddict" and not total:
        return False
    return stmt.value is None


def _resolve_name(node: ast.expr, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        left = _resolve_name(node.value, aliases)
        return f"{left}.{node.attr}"
    return _safe_unparse(node) or ""


def _safe_unparse(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None
