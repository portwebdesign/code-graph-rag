from __future__ import annotations

import ast
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.parsers.core.utils import safe_decode_text

type ResolveCallableReference = Callable[[str, str, str | None], tuple[str, str] | None]


@dataclass(frozen=True, slots=True)
class DispatchRegistry:
    name: str
    entries: dict[str, tuple[str, str]]


@dataclass(frozen=True, slots=True)
class DispatchTarget:
    registry_name: str
    dispatch_key: str
    dispatch_key_kind: str
    callee_type: str
    callee_qn: str
    confidence: float


class PythonMapDispatchAnalyzer:
    _SCOPE_BARRIER_TYPES = {
        cs.TS_PY_CLASS_DEFINITION,
        cs.TS_PY_DECORATED_DEFINITION,
        cs.TS_PY_FUNCTION_DEFINITION,
        "lambda",
    }

    def __init__(self, resolve_callable_reference: ResolveCallableReference) -> None:
        self._resolve_callable_reference = resolve_callable_reference

    def resolve_dispatch_targets(
        self,
        scope_node: Node,
        call_node: Node,
        module_qn: str,
        *,
        class_context: str | None = None,
    ) -> list[DispatchTarget]:
        parsed_dispatch = self._parse_dispatch_call(call_node)
        if parsed_dispatch is None:
            return []
        registry_name, dispatch_key, dispatch_key_kind = parsed_dispatch

        registries = self._collect_registries_before_call(
            scope_node,
            call_node,
            module_qn,
            class_context=class_context,
        )
        registry = registries.get(registry_name)
        if registry is None:
            return []

        if dispatch_key_kind == "literal":
            target = registry.entries.get(dispatch_key)
            if target is None:
                return []
            callee_type, callee_qn = target
            return [
                DispatchTarget(
                    registry_name=registry_name,
                    dispatch_key=dispatch_key,
                    dispatch_key_kind=dispatch_key_kind,
                    callee_type=callee_type,
                    callee_qn=callee_qn,
                    confidence=0.98,
                )
            ]

        confidence = 0.72 if dispatch_key_kind in {"identifier", "attribute"} else 0.55
        seen: set[tuple[str, str]] = set()
        targets: list[DispatchTarget] = []
        for callee_type, callee_qn in sorted(set(registry.entries.values())):
            dedupe_key = (callee_type, callee_qn)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            targets.append(
                DispatchTarget(
                    registry_name=registry_name,
                    dispatch_key=dispatch_key,
                    dispatch_key_kind=dispatch_key_kind,
                    callee_type=callee_type,
                    callee_qn=callee_qn,
                    confidence=confidence,
                )
            )
        return targets

    def _collect_registries_before_call(
        self,
        scope_node: Node,
        call_node: Node,
        module_qn: str,
        *,
        class_context: str | None,
    ) -> dict[str, DispatchRegistry]:
        registries: dict[str, DispatchRegistry] = {}
        for node in self._iter_scope_nodes(scope_node):
            if node.start_byte >= call_node.start_byte:
                break
            if node.type != cs.TS_PY_ASSIGNMENT:
                continue

            registry_name = self._get_assignment_target_name(node)
            if not registry_name:
                continue

            dictionary_node = node.child_by_field_name(cs.TS_FIELD_RIGHT)
            if (
                not isinstance(dictionary_node, Node)
                or dictionary_node.type != "dictionary"
            ):
                registries.pop(registry_name, None)
                continue

            registry = self._build_registry(
                registry_name,
                dictionary_node,
                module_qn,
                class_context=class_context,
            )
            if registry.entries:
                registries[registry_name] = registry
            else:
                registries.pop(registry_name, None)
        return registries

    def _build_registry(
        self,
        registry_name: str,
        dictionary_node: Node,
        module_qn: str,
        *,
        class_context: str | None,
    ) -> DispatchRegistry:
        entries: dict[str, tuple[str, str]] = {}
        for pair_node in dictionary_node.children:
            if pair_node.type != "pair":
                continue
            key_node = pair_node.child_by_field_name("key")
            value_node = pair_node.child_by_field_name("value")
            if not isinstance(key_node, Node) or not isinstance(value_node, Node):
                continue

            key_name = self._extract_registry_key(key_node)
            if not key_name:
                continue

            value_text = safe_decode_text(value_node)
            if not value_text:
                continue

            resolved = self._resolve_callable_reference(
                value_text,
                module_qn,
                class_context,
            )
            if resolved is None:
                continue
            entries[key_name] = resolved
        return DispatchRegistry(name=registry_name, entries=entries)

    def _parse_dispatch_call(
        self,
        call_node: Node,
    ) -> tuple[str, str, str] | None:
        function_node = call_node.child_by_field_name(cs.TS_FIELD_FUNCTION)
        if not isinstance(function_node, Node) or function_node.type != "subscript":
            return None

        registry_node = function_node.child_by_field_name("value")
        key_node = function_node.child_by_field_name("subscript")
        if not isinstance(registry_node, Node) or not isinstance(key_node, Node):
            return None
        if registry_node.type != cs.TS_IDENTIFIER:
            return None

        registry_name = safe_decode_text(registry_node)
        if not registry_name:
            return None

        dispatch_key, dispatch_key_kind = self._extract_dispatch_key(key_node)
        if not dispatch_key:
            return None
        return registry_name, dispatch_key, dispatch_key_kind

    @staticmethod
    def _get_assignment_target_name(assignment_node: Node) -> str | None:
        target_node = assignment_node.child_by_field_name(cs.TS_FIELD_LEFT)
        if not isinstance(target_node, Node) or target_node.type != cs.TS_IDENTIFIER:
            return None
        return safe_decode_text(target_node)

    @staticmethod
    def _extract_registry_key(key_node: Node) -> str | None:
        if key_node.type == cs.TS_STRING:
            return PythonMapDispatchAnalyzer._parse_string_literal(key_node)
        if key_node.type == cs.TS_IDENTIFIER:
            return safe_decode_text(key_node)
        return None

    @staticmethod
    def _extract_dispatch_key(key_node: Node) -> tuple[str | None, str]:
        if key_node.type == cs.TS_STRING:
            return PythonMapDispatchAnalyzer._parse_string_literal(key_node), "literal"
        if key_node.type == cs.TS_IDENTIFIER:
            return safe_decode_text(key_node), "identifier"
        if key_node.type == cs.TS_ATTRIBUTE:
            return safe_decode_text(key_node), "attribute"
        return safe_decode_text(key_node), key_node.type

    @staticmethod
    def _parse_string_literal(node: Node) -> str | None:
        raw_text = safe_decode_text(node)
        if not raw_text:
            return None
        try:
            value = ast.literal_eval(raw_text)
        except (SyntaxError, ValueError):
            stripped = raw_text.strip()
            if (
                len(stripped) >= 2
                and stripped[0] == stripped[-1]
                and stripped[0] in {"'", '"'}
            ):
                return stripped[1:-1]
            return stripped
        return value if isinstance(value, str) else None

    def _iter_scope_nodes(self, scope_node: Node) -> Iterable[Node]:
        for child in scope_node.children:
            if not isinstance(child, Node):
                continue
            yield child
            if child.type in self._SCOPE_BARRIER_TYPES:
                continue
            yield from self._iter_scope_nodes(child)
