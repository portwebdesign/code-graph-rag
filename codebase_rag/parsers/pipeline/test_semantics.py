from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from codebase_rag.parsers.pipeline.frontend_operations import (
    _extract_request_descriptors,
)
from codebase_rag.parsers.pipeline.ts_contracts import (
    _extract_brace_block,
    _line_number_for_offset,
)

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head"}
_JS_TEST_CASE_RE = re.compile(
    r"\b(?:it|test)\s*\(\s*(['\"`])(?P<name>[^'\"`]+)\1\s*,",
    re.IGNORECASE,
)
_JS_CALL_RE = re.compile(
    r"\b(?P<callee>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(",
    re.IGNORECASE,
)
_JS_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_$][A-Za-z0-9_$]*\b")
_JS_EXCLUDED_CALLS = {
    "describe",
    "it",
    "test",
    "expect",
    "beforeeach",
    "aftereach",
}


@dataclass(frozen=True)
class EndpointCallObservation:
    method: str
    path: str


@dataclass(frozen=True)
class TestCaseObservation:
    suite_name: str
    case_name: str
    case_kind: str
    framework: str
    symbol_refs: tuple[str, ...]
    identifier_refs: tuple[str, ...]
    endpoint_calls: tuple[EndpointCallObservation, ...]
    line_start: int | None = None
    line_end: int | None = None


def extract_python_test_cases(
    source: str, *, default_suite_name: str
) -> list[TestCaseObservation]:
    """Extracts pytest and unittest test cases from Python source."""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    cases: list[TestCaseObservation] = []
    for node in tree.body:
        if isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef
        ) and node.name.startswith("test_"):
            cases.append(
                _build_python_case(
                    node=node,
                    suite_name=default_suite_name,
                    case_kind="pytest",
                )
            )
        if isinstance(node, ast.ClassDef) and _looks_like_python_test_class(node):
            for child in node.body:
                if isinstance(
                    child, ast.FunctionDef | ast.AsyncFunctionDef
                ) and child.name.startswith("test_"):
                    cases.append(
                        _build_python_case(
                            node=child,
                            suite_name=node.name,
                            case_kind="unittest",
                        )
                    )
    return cases


def extract_javascript_test_cases(
    source: str,
    *,
    default_suite_name: str,
) -> list[TestCaseObservation]:
    """Extracts bounded jest/vitest-style test cases from JS/TS source."""

    cases: list[TestCaseObservation] = []
    for match in _JS_TEST_CASE_RE.finditer(source):
        open_brace_index = source.find("{", match.end())
        if open_brace_index < 0:
            continue
        block = _extract_brace_block(source, open_brace_index)
        if block is None:
            continue
        body, end_index = block
        symbol_refs: list[str] = []
        for call_match in _JS_CALL_RE.finditer(body):
            callee = call_match.group("callee")
            last_token = callee.split(".")[-1]
            if last_token.lower() in _JS_EXCLUDED_CALLS:
                continue
            if last_token not in symbol_refs:
                symbol_refs.append(last_token)

        identifier_refs = list(dict.fromkeys(_JS_IDENTIFIER_RE.findall(body)))
        endpoint_calls = [
            EndpointCallObservation(
                method=request["method"],
                path=request["path"],
            )
            for request in _extract_request_descriptors(body)
        ]
        cases.append(
            TestCaseObservation(
                suite_name=default_suite_name,
                case_name=match.group("name"),
                case_kind="jest_vitest",
                framework="javascript",
                symbol_refs=tuple(symbol_refs),
                identifier_refs=tuple(identifier_refs),
                endpoint_calls=tuple(endpoint_calls),
                line_start=_line_number_for_offset(source, match.start()),
                line_end=_line_number_for_offset(source, end_index),
            )
        )
    return cases


def _build_python_case(
    *,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    suite_name: str,
    case_kind: str,
) -> TestCaseObservation:
    collector = _PythonCaseCollector()
    for statement in node.body:
        collector.visit(statement)
    return TestCaseObservation(
        suite_name=suite_name,
        case_name=node.name,
        case_kind=case_kind,
        framework="python",
        symbol_refs=tuple(collector.symbol_refs),
        identifier_refs=tuple(collector.identifier_refs),
        endpoint_calls=tuple(collector.endpoint_calls),
        line_start=getattr(node, "lineno", None),
        line_end=getattr(node, "end_lineno", None),
    )


def _looks_like_python_test_class(node: ast.ClassDef) -> bool:
    if node.name.startswith("Test"):
        return True
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id.endswith("TestCase"):
            return True
        if isinstance(base, ast.Attribute) and base.attr.endswith("TestCase"):
            return True
    return False


class _PythonCaseCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.symbol_refs: list[str] = []
        self.identifier_refs: list[str] = []
        self.endpoint_calls: list[EndpointCallObservation] = []

    def visit_Call(self, node: ast.Call) -> None:
        call_name = _resolve_call_name(node.func)
        last_token = call_name.split(".")[-1]
        if last_token and last_token not in self.symbol_refs:
            self.symbol_refs.append(last_token)
        endpoint_call = _extract_python_endpoint_call(node)
        if endpoint_call is not None and endpoint_call not in self.endpoint_calls:
            self.endpoint_calls.append(endpoint_call)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id not in self.identifier_refs:
            self.identifier_refs.append(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr not in self.identifier_refs:
            self.identifier_refs.append(node.attr)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def _extract_python_endpoint_call(node: ast.Call) -> EndpointCallObservation | None:
    call_name = _resolve_call_name(node.func).lower()
    method = call_name.split(".")[-1]
    if method not in _HTTP_METHODS | {"fetch"}:
        return None
    if not node.args:
        return None
    first_arg = node.args[0]
    if not (isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str)):
        return None
    path = first_arg.value.strip()
    if not path.startswith("/"):
        return None
    http_method = method.upper() if method != "fetch" else _fetch_method(node)
    return EndpointCallObservation(method=http_method, path=path)


def _fetch_method(node: ast.Call) -> str:
    for keyword in node.keywords:
        if keyword.arg != "method":
            continue
        if isinstance(keyword.value, ast.Constant) and isinstance(
            keyword.value.value, str
        ):
            return keyword.value.value.upper()
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Dict):
        for key, value in zip(node.args[1].keys, node.args[1].values, strict=False):
            if (
                isinstance(key, ast.Constant)
                and key.value == "method"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                return value.value.upper()
    return "GET"


def _resolve_call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _resolve_call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""
