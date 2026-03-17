from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class TransactionBoundaryObservation:
    symbol_name: str
    symbol_kind: str
    boundary_name: str
    boundary_kind: str
    mechanism: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    has_commit: bool = False
    has_rollback: bool = False


@dataclass(frozen=True)
class SideEffectObservation:
    symbol_name: str
    symbol_kind: str
    effect_kind: str
    operation_name: str
    boundary_name: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    order_index: int = 0


@dataclass
class _MutableBoundary:
    boundary_name: str
    symbol_name: str
    symbol_kind: str
    boundary_kind: str
    mechanism: str | None
    line_start: int | None
    line_end: int | None
    has_commit: bool = False
    has_rollback: bool = False


def extract_python_transaction_flows(
    source: str,
) -> tuple[list[TransactionBoundaryObservation], list[SideEffectObservation]]:
    """Extracts first-wave Python transaction boundaries and side-effect order."""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ([], [])

    aliases = _build_aliases(tree)
    collector = _TransactionFlowCollector(aliases)
    collector.visit(tree)

    boundary_unique: dict[str, TransactionBoundaryObservation] = {}
    for boundary in collector.boundaries:
        boundary_unique.setdefault(boundary.boundary_name, boundary)

    effect_unique: dict[
        tuple[str, str, str, int | None, str | None],
        SideEffectObservation,
    ] = {}
    for effect in collector.side_effects:
        key = (
            effect.symbol_name,
            effect.effect_kind,
            effect.operation_name,
            effect.line_start,
            effect.boundary_name,
        )
        effect_unique.setdefault(key, effect)

    return (list(boundary_unique.values()), list(effect_unique.values()))


class _TransactionFlowCollector(ast.NodeVisitor):
    def __init__(self, aliases: dict[str, str]) -> None:
        self.aliases = aliases
        self.class_stack: list[str] = []
        self.boundaries: list[TransactionBoundaryObservation] = []
        self.side_effects: list[SideEffectObservation] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._process_function(node)
        for child in node.body:
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                self.visit(child)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._process_function(node)
        for child in node.body:
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                self.visit(child)

    def _process_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        symbol_name = (
            ".".join([*self.class_stack, node.name]) if self.class_stack else node.name
        )
        symbol_kind = "method" if self.class_stack else "function"

        mutable_boundaries = self._collect_boundaries(
            node=node,
            symbol_name=symbol_name,
            symbol_kind=symbol_kind,
        )
        side_effects = self._collect_side_effects(
            node=node,
            symbol_name=symbol_name,
            symbol_kind=symbol_kind,
            boundaries=mutable_boundaries,
        )

        for boundary in mutable_boundaries.values():
            if (
                boundary.boundary_kind == "context_manager"
                and not boundary.has_commit
                and not boundary.has_rollback
            ):
                boundary.has_commit = True
            self.boundaries.append(
                TransactionBoundaryObservation(
                    symbol_name=boundary.symbol_name,
                    symbol_kind=boundary.symbol_kind,
                    boundary_name=boundary.boundary_name,
                    boundary_kind=boundary.boundary_kind,
                    mechanism=boundary.mechanism,
                    line_start=boundary.line_start,
                    line_end=boundary.line_end,
                    has_commit=boundary.has_commit,
                    has_rollback=boundary.has_rollback,
                )
            )

        self.side_effects.extend(side_effects)

    def _collect_boundaries(
        self,
        *,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        symbol_name: str,
        symbol_kind: str,
    ) -> dict[str, _MutableBoundary]:
        boundaries: dict[str, _MutableBoundary] = {}
        boundary_index = 0
        context_call_sites = _transaction_context_call_sites(node, self.aliases)

        for with_node in _collect_with_nodes(node):
            mechanism = _transaction_context_name(with_node, self.aliases)
            if not mechanism:
                continue
            boundary_index += 1
            boundary_name = _build_boundary_name(
                symbol_name=symbol_name,
                line_start=getattr(with_node, "lineno", None),
                index=boundary_index,
                boundary_kind="context",
            )
            boundaries[boundary_name] = _MutableBoundary(
                boundary_name=boundary_name,
                symbol_name=symbol_name,
                symbol_kind=symbol_kind,
                boundary_kind="context_manager",
                mechanism=mechanism,
                line_start=getattr(with_node, "lineno", None),
                line_end=getattr(with_node, "end_lineno", None),
            )

        open_stack: list[str] = []
        for call in _sort_calls(_collect_function_calls(node)):
            if _call_site_key(call) in context_call_sites:
                continue
            operation_kind = _classify_transaction_operation(
                _resolve_name(call.func, self.aliases)
            )
            if operation_kind == "begin":
                boundary_index += 1
                boundary_name = _build_boundary_name(
                    symbol_name=symbol_name,
                    line_start=getattr(call, "lineno", None),
                    index=boundary_index,
                    boundary_kind="explicit",
                )
                boundaries[boundary_name] = _MutableBoundary(
                    boundary_name=boundary_name,
                    symbol_name=symbol_name,
                    symbol_kind=symbol_kind,
                    boundary_kind="explicit",
                    mechanism=_resolve_name(call.func, self.aliases),
                    line_start=getattr(call, "lineno", None),
                    line_end=getattr(node, "end_lineno", None),
                )
                open_stack.append(boundary_name)
                continue

            if operation_kind in {"commit", "rollback"}:
                if open_stack:
                    boundary = boundaries[open_stack.pop()]
                else:
                    boundary = self._innermost_context_boundary(
                        boundaries=boundaries,
                        line_number=getattr(call, "lineno", None),
                    )
                    if boundary is None:
                        continue

                boundary.line_end = getattr(call, "lineno", boundary.line_end)
                if operation_kind == "commit":
                    boundary.has_commit = True
                else:
                    boundary.has_rollback = True

        return boundaries

    def _collect_side_effects(
        self,
        *,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        symbol_name: str,
        symbol_kind: str,
        boundaries: dict[str, _MutableBoundary],
    ) -> list[SideEffectObservation]:
        side_effects: list[SideEffectObservation] = []
        effect_index = 0
        open_stack: list[str] = []
        context_call_sites = _transaction_context_call_sites(node, self.aliases)

        for call in _sort_calls(_collect_function_calls(node)):
            line_start = getattr(call, "lineno", None)
            operation_name = _resolve_name(call.func, self.aliases)
            operation_kind = _classify_transaction_operation(operation_name)

            if _call_site_key(call) in context_call_sites:
                continue

            if operation_kind == "begin":
                boundary = self._boundary_started_at(
                    boundaries=boundaries,
                    line_number=line_start,
                    boundary_kind="explicit",
                )
                if boundary is not None:
                    open_stack.append(boundary.boundary_name)
                continue

            if operation_kind in {"commit", "rollback"}:
                if open_stack:
                    open_stack.pop()
                continue

            effect_kind = _classify_side_effect(call, operation_name)
            if effect_kind is None:
                continue

            context_boundary = self._innermost_context_boundary(
                boundaries=boundaries,
                line_number=line_start,
            )
            boundary_name = (
                open_stack[-1]
                if open_stack
                else (
                    context_boundary.boundary_name
                    if context_boundary is not None
                    else None
                )
            )

            effect_index += 1
            side_effects.append(
                SideEffectObservation(
                    symbol_name=symbol_name,
                    symbol_kind=symbol_kind,
                    effect_kind=effect_kind,
                    operation_name=operation_name,
                    boundary_name=boundary_name,
                    line_start=line_start,
                    line_end=getattr(call, "end_lineno", None),
                    order_index=effect_index,
                )
            )

        return side_effects

    @staticmethod
    def _boundary_started_at(
        *,
        boundaries: dict[str, _MutableBoundary],
        line_number: int | None,
        boundary_kind: str,
    ) -> _MutableBoundary | None:
        for boundary in boundaries.values():
            if boundary.boundary_kind != boundary_kind:
                continue
            if boundary.line_start == line_number:
                return boundary
        return None

    @staticmethod
    def _innermost_context_boundary(
        *,
        boundaries: dict[str, _MutableBoundary],
        line_number: int | None,
    ) -> _MutableBoundary | None:
        if line_number is None:
            return None

        matches = [
            boundary
            for boundary in boundaries.values()
            if boundary.boundary_kind == "context_manager"
            and boundary.line_start is not None
            and boundary.line_end is not None
            and boundary.line_start <= line_number <= boundary.line_end
        ]
        if not matches:
            return None
        matches.sort(
            key=lambda item: (
                (item.line_end or 0) - (item.line_start or 0),
                item.line_start or 0,
            )
        )
        return matches[0]


class _CallCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[ast.Call] = []

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


class _WithCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.with_nodes: list[ast.With | ast.AsyncWith] = []

    def visit_With(self, node: ast.With) -> None:
        self.with_nodes.append(node)
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.with_nodes.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def _collect_function_calls(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Iterable[ast.Call]:
    collector = _CallCollector()
    for statement in node.body:
        collector.visit(statement)
    return collector.calls


def _collect_with_nodes(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Iterable[ast.With | ast.AsyncWith]:
    collector = _WithCollector()
    for statement in node.body:
        collector.visit(statement)
    return collector.with_nodes


def _sort_calls(calls: Iterable[ast.Call]) -> list[ast.Call]:
    return sorted(
        calls,
        key=lambda item: (
            getattr(item, "lineno", 0),
            getattr(item, "col_offset", 0),
        ),
    )


def _build_boundary_name(
    *,
    symbol_name: str,
    line_start: int | None,
    index: int,
    boundary_kind: str,
) -> str:
    return f"{symbol_name}:{boundary_kind}:{line_start or 0}:{index}"


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


def _resolve_name(node: ast.expr, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        left = _resolve_name(node.value, aliases)
        return f"{left}.{node.attr}"
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _transaction_context_name(
    node: ast.With | ast.AsyncWith,
    aliases: dict[str, str],
) -> str | None:
    for item in node.items:
        context_name = _resolve_name(item.context_expr, aliases)
        if _looks_like_transaction_context(context_name):
            return context_name
    return None


def _transaction_context_call_sites(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    aliases: dict[str, str],
) -> set[tuple[int, int, int | None, int | None]]:
    call_sites: set[tuple[int, int, int | None, int | None]] = set()
    for with_node in _collect_with_nodes(node):
        for item in with_node.items:
            context_expr = item.context_expr
            if not isinstance(context_expr, ast.Call):
                continue
            context_name = _resolve_name(context_expr.func, aliases)
            if _looks_like_transaction_context(context_name):
                call_sites.add(_call_site_key(context_expr))
    return call_sites


def _call_site_key(node: ast.Call) -> tuple[int, int, int | None, int | None]:
    return (
        getattr(node, "lineno", 0),
        getattr(node, "col_offset", 0),
        getattr(node, "end_lineno", None),
        getattr(node, "end_col_offset", None),
    )


def _looks_like_transaction_context(value: str) -> bool:
    lowered = value.strip().lower()
    if not lowered:
        return False
    return any(
        token in lowered
        for token in (
            "transaction",
            "atomic",
            "unit_of_work",
            "unitofwork",
            "uow",
        )
    )


def _classify_transaction_operation(operation_name: str) -> str | None:
    lowered = operation_name.strip().lower()
    if not lowered:
        return None
    base_name = lowered.split(".")[-1]
    if base_name in {"commit"} or lowered.endswith(".commit"):
        return "commit"
    if base_name in {"rollback"} or lowered.endswith(".rollback"):
        return "rollback"
    if (
        base_name in {"begin", "begin_transaction", "start_transaction", "transaction"}
        or "begin_transaction" in lowered
        or "start_transaction" in lowered
        or lowered.endswith(".begin")
        or lowered.endswith(".transaction")
        or "atomic" in lowered
    ):
        return "begin"
    return None


def _classify_side_effect(call: ast.Call, operation_name: str) -> str | None:
    lowered = operation_name.lower()
    base_name = lowered.split(".")[-1]
    literals = [value.lower() for value in _iter_string_literals(call)]

    if _classify_transaction_operation(operation_name) is not None:
        return None

    if any(
        token in lowered for token in ("requests.", "httpx.", "aiohttp.", "client.")
    ):
        if base_name in {"post", "put", "patch", "delete", "send"}:
            return "external_http"

    if any(
        token in lowered for token in ("cache.", "redis.", "memcache.", "memcached.")
    ):
        if base_name in {"set", "delete", "expire", "incr", "decr"}:
            return "cache_write"

    if "outbox" in lowered or any("outbox" in literal for literal in literals):
        return "outbox_write"

    if base_name in {
        "publish",
        "dispatch",
        "enqueue",
        "send",
        "emit",
        "produce",
        "xadd",
    }:
        if any(
            token in lowered
            for token in (
                "publisher",
                "producer",
                "broker",
                "stream",
                "queue",
                "topic",
                "event",
                "kafka",
                "rabbit",
                "redis",
                "bus",
            )
        ) or any("queue" in literal or "topic" in literal for literal in literals):
            return "queue_publish"

    if any(token in lowered for token in ("memgraph", "neo4j", "cypher")) or any(
        token in literal
        for literal in literals
        for token in ("create ", "merge ", "delete ", "set ")
    ):
        if base_name in {"run", "execute", "query", "write_transaction"}:
            return "graph_write"

    if base_name in {"write_text", "write_bytes", "write", "append"} or (
        base_name in {"dump", "save"}
        and any(token in lowered for token in ("file", "path", "json", "yaml"))
    ):
        return "filesystem_write"

    if base_name in {
        "insert",
        "update",
        "upsert",
        "delete",
        "save",
        "create",
        "add",
        "bulk_create",
        "executemany",
    }:
        return "db_write"

    return None


def _iter_string_literals(call: ast.Call) -> Iterable[str]:
    for argument in call.args:
        literal = _extract_string_literal(argument)
        if literal:
            yield literal
    for keyword in call.keywords:
        literal = _extract_string_literal(keyword.value)
        if literal:
            yield literal


def _extract_string_literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        value = node.value.strip()
        return value or None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
        joined = "".join(parts).strip()
        return joined or None
    return None
