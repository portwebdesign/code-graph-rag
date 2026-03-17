from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast

import yaml

from codebase_rag.core.config_semantic_identity import (
    is_feature_flag_name,
    is_secret_like_name,
    normalize_env_name,
    parse_env_truthiness,
)
from codebase_rag.parsers.pipeline.typescript_symbol_blocks import (
    extract_typescript_symbol_blocks,
)

_DOTENV_RE = re.compile(
    r"^(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$"
)
_PROCESS_ENV_DOT_RE = re.compile(r"process\.env\.([A-Za-z_][A-Za-z0-9_]*)")
_PROCESS_ENV_BRACKET_RE = re.compile(
    r"process\.env\[['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\]"
)


@dataclass(frozen=True)
class ConfigDefinition:
    env_name: str
    source_kind: str
    source_name: str
    has_definition: bool = True
    default_enabled: bool | None = None
    secret_provider: str | None = None
    secret_key: str | None = None
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True)
class CodeEnvObservation:
    env_name: str
    source_name: str
    source_kind: str
    line_start: int | None = None
    line_end: int | None = None
    reads_env: bool = True
    gates_flag: bool = False
    uses_secret: bool = False
    evidence_kind: str = "env_read"


@dataclass(frozen=True)
class KubernetesEnvBinding:
    resource_kind: str
    resource_name: str
    env_name: str
    literal_value: str | None = None
    secret_name: str | None = None
    secret_key: str | None = None


def extract_dotenv_definitions(source: str) -> list[ConfigDefinition]:
    definitions: list[ConfigDefinition] = []
    for line_number, raw_line in enumerate(source.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _DOTENV_RE.match(stripped)
        if not match:
            continue
        env_name = normalize_env_name(match.group("name"))
        raw_value = match.group("value").strip().strip("'\"")
        definitions.append(
            ConfigDefinition(
                env_name=env_name,
                source_kind="dotenv",
                source_name=".env",
                default_enabled=(
                    parse_env_truthiness(raw_value)
                    if is_feature_flag_name(env_name)
                    else None
                ),
                line_start=line_number,
                line_end=line_number,
            )
        )
    return definitions


def extract_kubernetes_env_bindings(source: str) -> list[KubernetesEnvBinding]:
    bindings: list[KubernetesEnvBinding] = []
    try:
        documents = list(yaml.safe_load_all(source))
    except yaml.YAMLError:
        return bindings

    for document in documents:
        if not isinstance(document, dict):
            continue
        kind = str(document.get("kind", "")).strip()
        metadata = document.get("metadata", {})
        name = (
            str(metadata.get("name", "")).strip() if isinstance(metadata, dict) else ""
        )
        if not kind or not name:
            continue
        for env_entry in _iter_k8s_env_entries(document):
            env_name = normalize_env_name(str(env_entry.get("name", "")).strip())
            if not env_name:
                continue
            raw_value_from = env_entry.get("valueFrom")
            value_from = (
                cast(dict[str, object], raw_value_from)
                if isinstance(raw_value_from, dict)
                else {}
            )
            raw_secret_key_ref = value_from.get("secretKeyRef")
            secret_key_ref = (
                cast(dict[str, object], raw_secret_key_ref)
                if isinstance(raw_secret_key_ref, dict)
                else {}
            )
            bindings.append(
                KubernetesEnvBinding(
                    resource_kind=kind,
                    resource_name=name,
                    env_name=env_name,
                    literal_value=(
                        str(env_entry.get("value", "")).strip()
                        if "value" in env_entry
                        else None
                    ),
                    secret_name=(
                        str(secret_key_ref.get("name", "")).strip()
                        if isinstance(secret_key_ref, dict)
                        else None
                    )
                    or None,
                    secret_key=(
                        str(secret_key_ref.get("key", "")).strip()
                        if isinstance(secret_key_ref, dict)
                        else None
                    )
                    or None,
                )
            )
    return bindings


def extract_python_env_observations(source: str) -> list[CodeEnvObservation]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    visitor = _PythonEnvVisitor()
    visitor.visit(tree)
    return visitor.observations


def extract_typescript_env_observations(
    source: str,
    *,
    relative_path: str,
) -> list[CodeEnvObservation]:
    observations: list[CodeEnvObservation] = []

    for block in extract_typescript_symbol_blocks(source):
        source_kind = (
            "component"
            if relative_path.endswith((".tsx", ".jsx"))
            and block.symbol_name[:1].isupper()
            else "function"
        )
        observations.extend(
            _build_ts_observations(
                block.body,
                source_name=block.symbol_name,
                source_kind=source_kind,
                line_start=block.line_start,
                line_end=block.line_end,
            )
        )

    if observations:
        return _dedupe_observations(observations)

    module_name = relative_path.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "module"
    observations.extend(
        _build_ts_observations(
            source,
            source_name=module_name,
            source_kind="module",
            line_start=1,
            line_end=source.count("\n") + 1,
        )
    )
    return _dedupe_observations(observations)


def _build_ts_observations(
    body: str,
    *,
    source_name: str,
    source_kind: str,
    line_start: int | None,
    line_end: int | None,
) -> list[CodeEnvObservation]:
    observations: list[CodeEnvObservation] = []
    env_names = list(dict.fromkeys(_extract_process_env_names(body)))
    for env_name in env_names:
        uses_secret = is_secret_like_name(env_name)
        observations.append(
            CodeEnvObservation(
                env_name=env_name,
                source_name=source_name,
                source_kind=source_kind,
                line_start=line_start,
                line_end=line_end,
                uses_secret=uses_secret,
            )
        )
        if is_feature_flag_name(env_name):
            observations.append(
                CodeEnvObservation(
                    env_name=env_name,
                    source_name=source_name,
                    source_kind=source_kind,
                    line_start=line_start,
                    line_end=line_end,
                    gates_flag=True,
                    uses_secret=uses_secret,
                    evidence_kind="feature_flag_gate",
                )
            )
    return observations


class _PythonEnvVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope_stack: list[tuple[str, str, int | None, int | None]] = [
            ("module", "__module__", 1, None)
        ]
        self.observations: list[CodeEnvObservation] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope_stack.append(
            (
                "function",
                node.name,
                getattr(node, "lineno", None),
                getattr(node, "end_lineno", None),
            )
        )
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope_stack.append(
            (
                "function",
                node.name,
                getattr(node, "lineno", None),
                getattr(node, "end_lineno", None),
            )
        )
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope_stack.append(
            (
                "class",
                node.name,
                getattr(node, "lineno", None),
                getattr(node, "end_lineno", None),
            )
        )
        if _looks_like_settings_class(node):
            for env_name in _settings_class_env_names(node):
                self._record(
                    env_name=env_name,
                    evidence_kind="settings_class_field",
                    uses_secret=is_secret_like_name(env_name),
                )
                if is_feature_flag_name(env_name):
                    self._record(
                        env_name=env_name,
                        evidence_kind="feature_flag_gate",
                        gates_flag=True,
                    )
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        env_name = _extract_env_name_from_python_call(node)
        if env_name:
            self._record(
                env_name=env_name,
                evidence_kind="python_env_read",
                uses_secret=is_secret_like_name(env_name),
            )
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        env_name = _extract_env_name_from_python_subscript(node)
        if env_name:
            self._record(
                env_name=env_name,
                evidence_kind="python_env_read",
                uses_secret=is_secret_like_name(env_name),
            )
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        for env_name in _extract_env_names_from_python_expr(node.test):
            if is_feature_flag_name(env_name):
                self._record(
                    env_name=env_name,
                    evidence_kind="feature_flag_gate",
                    gates_flag=True,
                    uses_secret=is_secret_like_name(env_name),
                )
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        for env_name in _extract_env_names_from_python_expr(node.test):
            if is_feature_flag_name(env_name):
                self._record(
                    env_name=env_name,
                    evidence_kind="feature_flag_gate",
                    gates_flag=True,
                    uses_secret=is_secret_like_name(env_name),
                )
        self.generic_visit(node)

    def _record(
        self,
        *,
        env_name: str,
        evidence_kind: str,
        gates_flag: bool = False,
        uses_secret: bool = False,
    ) -> None:
        scope_kind, scope_name, line_start, line_end = self.scope_stack[-1]
        self.observations.append(
            CodeEnvObservation(
                env_name=normalize_env_name(env_name),
                source_name=scope_name,
                source_kind=scope_kind,
                line_start=line_start,
                line_end=line_end,
                gates_flag=gates_flag,
                uses_secret=uses_secret,
                evidence_kind=evidence_kind,
            )
        )


def _extract_process_env_names(body: str) -> list[str]:
    names = [match.group(1) for match in _PROCESS_ENV_DOT_RE.finditer(body)]
    names.extend(match.group(1) for match in _PROCESS_ENV_BRACKET_RE.finditer(body))
    return [normalize_env_name(name) for name in names if name]


def _iter_k8s_env_entries(document: dict[str, object]) -> Iterator[dict[str, object]]:
    spec = document.get("spec", {})
    if not isinstance(spec, dict):
        return
    spec_dict = cast(dict[str, object], spec)
    template = spec_dict.get("template")
    pod_spec = (
        cast(dict[str, object], template).get("spec")
        if isinstance(template, dict)
        else spec_dict.get("jobTemplate")
    )
    if isinstance(pod_spec, dict) and "spec" in pod_spec:
        pod_spec = cast(dict[str, object], pod_spec).get("spec", {})
    containers = []
    if isinstance(pod_spec, dict):
        containers = cast(dict[str, object], pod_spec).get("containers", []) or []
    if not isinstance(containers, list):
        return
    for container in containers:
        if not isinstance(container, dict):
            continue
        container_map = cast(dict[str, object], container)
        env_items = container_map.get("env", []) or []
        if not isinstance(env_items, list):
            continue
        for env_item in env_items:
            if isinstance(env_item, dict):
                yield env_item


def _looks_like_settings_class(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Name) and (
            base.id.endswith("BaseSettings") or base.id.endswith("Settings")
        ):
            return True
        if isinstance(base, ast.Attribute) and (
            base.attr.endswith("BaseSettings") or base.attr.endswith("Settings")
        ):
            return True
    return False


def _settings_class_env_names(node: ast.ClassDef) -> list[str]:
    env_names: list[str] = []
    for child in node.body:
        target_names = _assignment_target_names(child)
        for name in target_names:
            normalized = normalize_env_name(name)
            if normalized not in env_names:
                env_names.append(normalized)
    return env_names


def _assignment_target_names(node: ast.stmt) -> list[str]:
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    if isinstance(node, ast.Assign):
        names: list[str] = []
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
        return names
    return []


def _extract_env_name_from_python_call(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        if (
            node.func.attr == "getenv"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return normalize_env_name(node.args[0].value)
        if (
            node.func.attr == "get"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "environ"
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "os"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return normalize_env_name(node.args[0].value)
    return None


def _extract_env_name_from_python_subscript(node: ast.Subscript) -> str | None:
    if not (
        isinstance(node.value, ast.Attribute)
        and node.value.attr == "environ"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "os"
    ):
        return None
    slice_node = node.slice
    if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
        return normalize_env_name(slice_node.value)
    return None


def _extract_env_names_from_python_expr(expr: ast.AST) -> list[str]:
    env_names: list[str] = []
    for node in ast.walk(expr):
        if isinstance(node, ast.Call):
            env_name = _extract_env_name_from_python_call(node)
            if env_name and env_name not in env_names:
                env_names.append(env_name)
        elif isinstance(node, ast.Subscript):
            env_name = _extract_env_name_from_python_subscript(node)
            if env_name and env_name not in env_names:
                env_names.append(env_name)
    return env_names


def _dedupe_observations(
    observations: list[CodeEnvObservation],
) -> list[CodeEnvObservation]:
    unique: dict[
        tuple[str, str, str, bool, bool, str],
        CodeEnvObservation,
    ] = {}
    for observation in observations:
        key = (
            observation.env_name,
            observation.source_name,
            observation.source_kind,
            observation.gates_flag,
            observation.uses_secret,
            observation.evidence_kind,
        )
        unique.setdefault(key, observation)
    return list(unique.values())
