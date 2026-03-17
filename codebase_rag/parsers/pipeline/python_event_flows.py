from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class EventFlowObservation:
    symbol_name: str
    symbol_kind: str
    stage: str
    event_name: str | None = None
    channel_name: str | None = None
    dlq_name: str | None = None
    mechanism: str | None = None
    line_start: int | None = None
    line_end: int | None = None


def extract_python_event_flows(source: str) -> list[EventFlowObservation]:
    """Extracts first-wave event/outbox/replay observations from Python source."""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    aliases = _build_aliases(tree)
    collector = _EventFlowCollector(aliases)
    collector.visit(tree)

    unique: dict[
        tuple[str, str, str, str | None, str | None, str | None, str | None],
        EventFlowObservation,
    ] = {}
    for observation in collector.observations:
        key = (
            observation.symbol_name,
            observation.symbol_kind,
            observation.stage,
            observation.event_name,
            observation.channel_name,
            observation.dlq_name,
            observation.mechanism,
        )
        unique.setdefault(key, observation)
    return list(unique.values())


class _EventFlowCollector(ast.NodeVisitor):
    def __init__(self, aliases: dict[str, str]) -> None:
        self.aliases = aliases
        self.class_stack: list[str] = []
        self.observations: list[EventFlowObservation] = []

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

        for decorator in node.decorator_list:
            observation = _observation_from_decorator(
                decorator,
                symbol_name=symbol_name,
                symbol_kind=symbol_kind,
                aliases=self.aliases,
            )
            if observation is not None:
                self.observations.append(observation)

        for call in _collect_function_calls(node):
            observation = _observation_from_call(
                call,
                symbol_name=symbol_name,
                symbol_kind=symbol_kind,
                function_name=node.name,
                aliases=self.aliases,
            )
            if observation is not None:
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


def _collect_function_calls(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Iterable[ast.Call]:
    collector = _CallCollector()
    for statement in node.body:
        collector.visit(statement)
    return collector.calls


def _observation_from_decorator(
    decorator: ast.expr,
    *,
    symbol_name: str,
    symbol_kind: str,
    aliases: dict[str, str],
) -> EventFlowObservation | None:
    decorator_expr = decorator.func if isinstance(decorator, ast.Call) else decorator
    decorator_name = _resolve_name(decorator_expr, aliases).lower()
    if not any(
        token in decorator_name
        for token in ("consumer", "subscriber", "worker", "handler", "job")
    ):
        return None

    call = decorator if isinstance(decorator, ast.Call) else None
    event_name = _extract_event_name(call)
    channel_name = _extract_channel_name(call)
    dlq_name = _extract_dlq_name(call)
    if not any((event_name, channel_name, dlq_name)):
        return None

    return EventFlowObservation(
        symbol_name=symbol_name,
        symbol_kind=symbol_kind,
        stage="consume",
        event_name=event_name,
        channel_name=channel_name,
        dlq_name=dlq_name,
        mechanism=decorator_name,
        line_start=getattr(decorator, "lineno", None),
        line_end=getattr(decorator, "end_lineno", None),
    )


def _observation_from_call(
    call: ast.Call,
    *,
    symbol_name: str,
    symbol_kind: str,
    function_name: str,
    aliases: dict[str, str],
) -> EventFlowObservation | None:
    mechanism = _resolve_name(call.func, aliases)
    stage = _classify_call_stage(call, mechanism, function_name=function_name)
    if stage is None:
        return None

    event_name = _extract_event_name(call)
    channel_name = _extract_channel_name(call)
    dlq_name = _extract_dlq_name(call)
    if not any((event_name, channel_name, dlq_name)):
        return None

    return EventFlowObservation(
        symbol_name=symbol_name,
        symbol_kind=symbol_kind,
        stage=stage,
        event_name=event_name,
        channel_name=channel_name,
        dlq_name=dlq_name,
        mechanism=mechanism,
        line_start=getattr(call, "lineno", None),
        line_end=getattr(call, "end_lineno", None),
    )


def _classify_call_stage(
    call: ast.Call,
    mechanism: str,
    *,
    function_name: str,
) -> str | None:
    lower_mechanism = mechanism.lower()
    lower_function = function_name.lower()
    string_literals = [value.lower() for value in _iter_string_literals(call)]

    if any(token in lower_mechanism for token in ("replay", "redrive", "requeue")):
        return "replay"
    if any(token in lower_mechanism for token in ("consume", "subscribe", "listen")):
        return "consume"
    if "outbox" in lower_mechanism or any(
        "outbox" in value for value in string_literals
    ):
        if any(
            token in lower_mechanism
            for token in ("publish", "enqueue", "insert", "save", "write", "create")
        ) or any("outbox" in value for value in string_literals):
            return "outbox"
    if any(token in lower_function for token in ("replay", "redrive", "requeue")) and (
        any(
            token in lower_mechanism
            for token in ("publish", "emit", "dispatch", "send", "enqueue")
        )
        or any("dlq" in value or "dead-letter" in value for value in string_literals)
    ):
        return "replay"
    if any(
        token in lower_mechanism
        for token in ("publish", "emit", "dispatch", "send", "enqueue", "produce")
    ) and (
        any(
            token in lower_mechanism
            for token in (
                "publisher",
                "producer",
                "broker",
                "bus",
                "stream",
                "queue",
                "topic",
                "kafka",
                "rabbit",
                "redis",
                "event",
            )
        )
        or bool(_extract_event_name(call) or _extract_channel_name(call))
    ):
        return "publish"
    return None


def _extract_event_name(call: ast.Call | None) -> str | None:
    if call is None:
        return None
    for keyword in call.keywords:
        if keyword.arg in {
            "event",
            "event_name",
            "message_type",
            "kind",
            "subject",
            "routing_key",
            "name",
        }:
            literal = _extract_string_literal(keyword.value)
            if literal:
                return literal
    positional = list(_iter_string_literals(call))
    if positional:
        first = positional[0]
        if "dlq" not in first.lower() and "dead-letter" not in first.lower():
            return first
    return None


def _extract_channel_name(call: ast.Call | None) -> str | None:
    if call is None:
        return None
    for keyword in call.keywords:
        if keyword.arg in {
            "queue",
            "queue_name",
            "stream",
            "stream_name",
            "channel",
            "channel_name",
            "topic",
            "topic_name",
        }:
            literal = _extract_string_literal(keyword.value)
            if literal:
                return literal
    positional = list(_iter_string_literals(call))
    if len(positional) >= 2:
        candidate = positional[1]
        if "dlq" not in candidate.lower() and "dead-letter" not in candidate.lower():
            return candidate
    return None


def _extract_dlq_name(call: ast.Call | None) -> str | None:
    if call is None:
        return None
    for keyword in call.keywords:
        if keyword.arg in {
            "dlq",
            "dlq_name",
            "dead_letter",
            "dead_letter_queue",
            "dead_letter_topic",
            "retry_queue",
        }:
            literal = _extract_string_literal(keyword.value)
            if literal:
                return literal
    for literal in _iter_string_literals(call):
        lowered = literal.lower()
        if "dlq" in lowered or "dead-letter" in lowered or "dead_letter" in lowered:
            return literal
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
