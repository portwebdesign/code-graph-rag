from __future__ import annotations

import ast
import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

from codebase_rag.parsers.pipeline.typescript_symbol_blocks import (
    extract_typescript_symbol_blocks,
)

_QUERYISH_CALLEE_TOKENS = (
    "execute",
    "executemany",
    "query",
    "run",
    "cypher",
    "sql",
    "cursor",
    "session",
    "transaction",
    "write_transaction",
    "read_transaction",
)
_SQL_START_RE = re.compile(r"^\s*(SELECT|WITH|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
_CYPHER_START_RE = re.compile(
    r"^\s*(MATCH|OPTIONAL MATCH|MERGE|CREATE|WITH|UNWIND|CALL)\b",
    re.IGNORECASE,
)
_SQL_SINGLE_QUOTED_RE = re.compile(r"'(?:''|[^'])*'")
_SQL_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
_SQL_POSITIONAL_PARAM_RE = re.compile(r"\$\d+\b")
_SQL_NAMED_PARAM_RE = re.compile(r":[A-Za-z_][A-Za-z0-9_]*\b")
_CYPHER_STRING_RE = re.compile(r"'(?:\\'|[^'])*'|\"(?:\\\"|[^\"])*\"")
_CYPHER_PARAM_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\b")
_TABLE_TOKEN_RE = r"[A-Za-z_][A-Za-z0-9_.$]*"
_SQL_FROM_RE = re.compile(rf"\bFROM\s+(?P<table>{_TABLE_TOKEN_RE})", re.IGNORECASE)
_SQL_JOIN_RE = re.compile(rf"\bJOIN\s+(?P<table>{_TABLE_TOKEN_RE})", re.IGNORECASE)
_SQL_INSERT_RE = re.compile(
    rf"\bINSERT\s+INTO\s+(?P<table>{_TABLE_TOKEN_RE})", re.IGNORECASE
)
_SQL_UPDATE_RE = re.compile(rf"\bUPDATE\s+(?P<table>{_TABLE_TOKEN_RE})", re.IGNORECASE)
_SQL_DELETE_RE = re.compile(
    rf"\bDELETE\s+FROM\s+(?P<table>{_TABLE_TOKEN_RE})", re.IGNORECASE
)
_CYPHER_NODE_LABEL_RE = re.compile(r"\((?P<body>[^)]*)\)", re.IGNORECASE)
_TS_STRING_RE = re.compile(r"(?P<quote>`|'|\")(?P<body>(?:\\.|(?!\1).)*)\1", re.DOTALL)


@dataclass(frozen=True)
class QueryObservation:
    symbol_name: str
    symbol_kind: str
    query_kind: str
    raw_query: str
    normalized_query: str
    fingerprint: str
    query_intent: str
    read_targets: tuple[str, ...]
    write_targets: tuple[str, ...]
    join_targets: tuple[str, ...]
    line_start: int | None = None
    line_end: int | None = None


def extract_python_query_observations(source: str) -> list[QueryObservation]:
    """Extracts query fingerprints from Python source."""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    collector = _PythonQueryCollector()
    collector.visit(tree)
    return _dedupe_observations(collector.observations)


def extract_typescript_query_observations(source: str) -> list[QueryObservation]:
    """Extracts query fingerprints from TS/TSX source."""

    observations: list[QueryObservation] = []
    for block in extract_typescript_symbol_blocks(source):
        for match in _TS_STRING_RE.finditer(block.body):
            raw_query = match.group("body")
            observation = build_query_observation(
                symbol_name=block.symbol_name,
                symbol_kind="function",
                raw_query=raw_query,
                line_start=block.line_start,
                line_end=block.line_end,
            )
            if observation is not None:
                observations.append(observation)
    return _dedupe_observations(observations)


def build_query_observation(
    *,
    symbol_name: str,
    symbol_kind: str,
    raw_query: str,
    line_start: int | None = None,
    line_end: int | None = None,
) -> QueryObservation | None:
    query_kind = classify_query_kind(raw_query)
    if query_kind is None:
        return None

    normalized_query = normalize_query_text(raw_query, query_kind=query_kind)
    fingerprint = fingerprint_query(normalized_query)

    if query_kind == "sql":
        read_targets = tuple(_extract_sql_read_targets(raw_query))
        write_targets = tuple(_extract_sql_write_targets(raw_query))
        join_targets = tuple(_extract_sql_join_targets(raw_query))
    else:
        labels = _extract_cypher_labels(raw_query)
        read_targets = tuple(labels if _contains_cypher_read_clause(raw_query) else [])
        write_targets = tuple(
            labels if _contains_cypher_write_clause(raw_query) else []
        )
        join_targets = ()

    query_intent = classify_query_intent(
        read_targets=read_targets,
        write_targets=write_targets,
    )

    return QueryObservation(
        symbol_name=symbol_name,
        symbol_kind=symbol_kind,
        query_kind=query_kind,
        raw_query=" ".join(raw_query.strip().split()),
        normalized_query=normalized_query,
        fingerprint=fingerprint,
        query_intent=query_intent,
        read_targets=read_targets,
        write_targets=write_targets,
        join_targets=join_targets,
        line_start=line_start,
        line_end=line_end,
    )


def classify_query_kind(raw_query: str) -> str | None:
    query = raw_query.strip()
    if not query:
        return None
    if _SQL_START_RE.match(query) and re.search(
        r"\b(FROM|INTO|SET)\b", query, re.IGNORECASE
    ):
        return "sql"
    if _CYPHER_START_RE.match(query) and (
        ":" in query or "->" in query or "<-" in query
    ):
        return "cypher"
    return None


def normalize_query_text(raw_query: str, *, query_kind: str) -> str:
    query = raw_query.strip()
    if query_kind == "sql":
        query = re.sub(r"--.*?$", "", query, flags=re.MULTILINE)
        query = re.sub(r"/\*.*?\*/", "", query, flags=re.DOTALL)
        query = _SQL_SINGLE_QUOTED_RE.sub("?", query)
        query = _SQL_POSITIONAL_PARAM_RE.sub("?", query)
        query = _SQL_NAMED_PARAM_RE.sub("?", query)
        query = _SQL_NUMBER_RE.sub("?", query)
    else:
        query = _CYPHER_STRING_RE.sub("?", query)
        query = _CYPHER_PARAM_RE.sub("?", query)
        query = _SQL_NUMBER_RE.sub("?", query)
    query = " ".join(query.split())
    return query.upper()


def fingerprint_query(normalized_query: str) -> str:
    return hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()[:16]


def classify_query_intent(
    *,
    read_targets: Iterable[str],
    write_targets: Iterable[str],
) -> str:
    has_reads = bool(tuple(read_targets))
    has_writes = bool(tuple(write_targets))
    if has_reads and has_writes:
        return "READ_WRITE"
    if has_writes:
        return "WRITE"
    return "READ"


class _PythonQueryCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.class_stack: list[str] = []
        self.observations: list[QueryObservation] = []

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
        local_strings = _collect_local_string_bindings(node)
        seen: set[tuple[str, str, int | None]] = set()

        for call in _collect_function_calls(node):
            callee_name = _resolve_call_name(call.func).lower()
            call_is_queryish = any(
                token in callee_name for token in _QUERYISH_CALLEE_TOKENS
            )
            for candidate in _extract_call_string_candidates(call, local_strings):
                if not call_is_queryish and classify_query_kind(candidate) is None:
                    continue
                observation = build_query_observation(
                    symbol_name=symbol_name,
                    symbol_kind=symbol_kind,
                    raw_query=candidate,
                    line_start=getattr(call, "lineno", None),
                    line_end=getattr(call, "end_lineno", None),
                )
                if observation is None:
                    continue
                key = (
                    observation.query_kind,
                    observation.fingerprint,
                    observation.line_start,
                )
                if key in seen:
                    continue
                seen.add(key)
                self.observations.append(observation)


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


class _LocalStringCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.bindings: dict[str, str] = {}

    def visit_Assign(self, node: ast.Assign) -> None:
        value = _extract_string_expr(node.value, self.bindings)
        if value is None:
            self.generic_visit(node)
            return
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.bindings[target.id] = value
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        value = _extract_string_expr(node.value, self.bindings)
        if value is not None and isinstance(node.target, ast.Name):
            self.bindings[node.target.id] = value
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


def _collect_local_string_bindings(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, str]:
    collector = _LocalStringCollector()
    for statement in node.body:
        collector.visit(statement)
    return collector.bindings


def _extract_call_string_candidates(
    call: ast.Call,
    local_strings: dict[str, str],
) -> list[str]:
    candidates: list[str] = []
    for expr in [*call.args, *(keyword.value for keyword in call.keywords)]:
        value = _extract_string_expr(expr, local_strings)
        if value is None:
            continue
        if value not in candidates:
            candidates.append(value)
    return candidates


def _extract_string_expr(
    expr: ast.expr | None,
    local_strings: dict[str, str],
) -> str | None:
    if expr is None:
        return None
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.Name):
        return local_strings.get(expr.id)
    if isinstance(expr, ast.JoinedStr):
        parts: list[str] = []
        for value in expr.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("{param}")
        return "".join(parts)
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        left = _extract_string_expr(expr.left, local_strings)
        right = _extract_string_expr(expr.right, local_strings)
        if left is not None and right is not None:
            return f"{left}{right}"
    return None


def _resolve_call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _resolve_call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _extract_sql_read_targets(raw_query: str) -> list[str]:
    tables: list[str] = []
    for pattern in (_SQL_FROM_RE, _SQL_JOIN_RE):
        for match in pattern.finditer(raw_query):
            table = _normalize_identifier(match.group("table"))
            if table and table not in tables:
                tables.append(table)
    return tables


def _extract_sql_write_targets(raw_query: str) -> list[str]:
    tables: list[str] = []
    for pattern in (_SQL_INSERT_RE, _SQL_UPDATE_RE, _SQL_DELETE_RE):
        for match in pattern.finditer(raw_query):
            table = _normalize_identifier(match.group("table"))
            if table and table not in tables:
                tables.append(table)
    return tables


def _extract_sql_join_targets(raw_query: str) -> list[str]:
    tables: list[str] = []
    for match in _SQL_JOIN_RE.finditer(raw_query):
        table = _normalize_identifier(match.group("table"))
        if table and table not in tables:
            tables.append(table)
    return tables


def _extract_cypher_labels(raw_query: str) -> list[str]:
    labels: list[str] = []
    for match in _CYPHER_NODE_LABEL_RE.finditer(raw_query):
        body = match.group("body")
        for label in re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", body):
            if label not in labels:
                labels.append(label)
    return labels


def _contains_cypher_read_clause(raw_query: str) -> bool:
    return bool(re.search(r"\b(MATCH|OPTIONAL MATCH)\b", raw_query, re.IGNORECASE))


def _contains_cypher_write_clause(raw_query: str) -> bool:
    return bool(re.search(r"\b(CREATE|MERGE|DELETE|SET)\b", raw_query, re.IGNORECASE))


def _normalize_identifier(identifier: str | None) -> str | None:
    if identifier is None:
        return None
    value = identifier.strip().strip('`"')
    return value or None


def _dedupe_observations(
    observations: Iterable[QueryObservation],
) -> list[QueryObservation]:
    unique: dict[tuple[str, str, str, int | None], QueryObservation] = {}
    for observation in observations:
        key = (
            observation.symbol_name,
            observation.query_kind,
            observation.fingerprint,
            observation.line_start,
        )
        unique.setdefault(key, observation)
    return list(unique.values())
