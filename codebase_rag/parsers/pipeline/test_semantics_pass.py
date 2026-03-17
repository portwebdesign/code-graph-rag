from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from loguru import logger
from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.parsers.pipeline.openapi_contracts import (
    extract_openapi_contract_surface,
)
from codebase_rag.parsers.pipeline.python_contracts import extract_python_contracts
from codebase_rag.parsers.pipeline.semantic_metadata import (
    build_semantic_metadata,
    build_semantic_qn,
)
from codebase_rag.parsers.pipeline.semantic_pass_registry import (
    is_semantic_pass_enabled,
)
from codebase_rag.parsers.pipeline.test_semantics import (
    EndpointCallObservation,
    TestCaseObservation,
    extract_javascript_test_cases,
    extract_python_test_cases,
)
from codebase_rag.parsers.pipeline.ts_contracts import extract_typescript_contracts


class TestSemanticsPass:
    """Emits first-wave static test graph semantics."""

    def __init__(
        self,
        ingestor,
        repo_path: Path,
        project_name: str,
        function_registry,
    ) -> None:
        self.ingestor = ingestor
        self.repo_path = repo_path
        self.project_name = project_name
        self.function_registry = function_registry
        self.enabled = is_semantic_pass_enabled("CODEGRAPH_TEST_SEMANTICS")
        self._endpoint_qn_cache: dict[tuple[str, str], str | None] = {}

    def process_ast_cache(
        self,
        ast_items: Iterable[tuple[Path, tuple[Node, cs.SupportedLanguage]]],
    ) -> None:
        if not self.enabled:
            return

        ast_cache_items = tuple(ast_items)
        contract_index = self._build_contract_index(ast_cache_items)
        suite_count = 0
        case_count = 0
        edge_count = 0

        for file_path, (_, language) in ast_cache_items:
            if not self._looks_like_test_path(file_path):
                continue
            source = self._read_source(file_path)
            if source is None:
                continue

            default_suite_name = file_path.stem
            if (
                language == cs.SupportedLanguage.PYTHON
                and file_path.suffix == cs.EXT_PY
            ):
                cases = extract_python_test_cases(
                    source, default_suite_name=default_suite_name
                )
            elif language in {cs.SupportedLanguage.JS, cs.SupportedLanguage.TS} and (
                file_path.suffix in {*cs.JS_EXTENSIONS, *cs.TS_EXTENSIONS}
            ):
                cases = extract_javascript_test_cases(
                    source, default_suite_name=default_suite_name
                )
            else:
                continue
            if not cases:
                continue

            relative_path = self._relative_path(file_path)
            suite_qns: dict[str, str] = {}
            for case in cases:
                suite_qn = suite_qns.get(case.suite_name)
                if suite_qn is None:
                    suite_qn = self._ensure_suite_node(
                        suite_name=case.suite_name,
                        framework=case.framework,
                        relative_path=relative_path,
                    )
                    suite_qns[case.suite_name] = suite_qn
                    suite_count += 1

                case_qn = self._ensure_case_node(
                    case=case,
                    suite_qn=suite_qn,
                    relative_path=relative_path,
                )
                case_count += 1

                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.TEST_SUITE, cs.KEY_QUALIFIED_NAME, suite_qn),
                    cs.RelationshipType.CONTAINS,
                    (cs.NodeLabel.TEST_CASE, cs.KEY_QUALIFIED_NAME, case_qn),
                    self._metadata(
                        framework=case.framework,
                        relative_path=relative_path,
                        evidence_kind="test_suite_contains_case",
                        line_start=case.line_start,
                        line_end=case.line_end,
                    ),
                )
                edge_count += 1

                edge_count += self._emit_symbol_edges(
                    case=case,
                    case_qn=case_qn,
                    relative_path=relative_path,
                )
                edge_count += self._emit_endpoint_edges(
                    case=case,
                    case_qn=case_qn,
                    relative_path=relative_path,
                )
                edge_count += self._emit_contract_edges(
                    case=case,
                    case_qn=case_qn,
                    relative_path=relative_path,
                    contract_index=contract_index,
                )

        logger.info(
            "TestSemanticsPass: {} suite node(s), {} case node(s), {} edge(s)",
            suite_count,
            case_count,
            edge_count,
        )

    def _build_contract_index(
        self,
        ast_cache_items: Iterable[tuple[Path, tuple[Node, cs.SupportedLanguage]]],
    ) -> dict[str, list[str]]:
        contract_index: dict[str, list[str]] = defaultdict(list)
        for file_path, (_, language) in ast_cache_items:
            source = self._read_source(file_path)
            if source is None:
                continue
            module_qn = self._module_qn_for_path(file_path)
            if (
                language == cs.SupportedLanguage.PYTHON
                and file_path.suffix == cs.EXT_PY
            ):
                contract_defs = extract_python_contracts(source)
            elif language == cs.SupportedLanguage.TS and file_path.suffix in {
                cs.EXT_TS,
                cs.EXT_TSX,
            }:
                contract_defs = extract_typescript_contracts(source)
            elif language in {
                cs.SupportedLanguage.JSON,
                cs.SupportedLanguage.YAML,
            } and (file_path.suffix in {cs.EXT_JSON, cs.EXT_YAML, cs.EXT_YML}):
                contract_defs, _bindings = extract_openapi_contract_surface(
                    source, file_suffix=file_path.suffix.lower()
                )
            else:
                continue

            for contract_def in contract_defs:
                resolved_qn = self._resolve_symbol_qn(module_qn, contract_def.name)
                contract_identity = (
                    resolved_qn or f"{module_qn}{cs.SEPARATOR_DOT}{contract_def.name}"
                )
                contract_qn = build_semantic_qn(
                    self.project_name, "contract", contract_identity
                )
                if contract_qn not in contract_index[contract_def.name]:
                    contract_index[contract_def.name].append(contract_qn)
        return contract_index

    def _emit_symbol_edges(
        self,
        *,
        case: TestCaseObservation,
        case_qn: str,
        relative_path: str,
    ) -> int:
        edge_count = 0
        for symbol_name in case.symbol_refs:
            source_spec = self._resolve_symbol_spec(symbol_name)
            if source_spec is None:
                continue
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.TEST_CASE, cs.KEY_QUALIFIED_NAME, case_qn),
                cs.RelationshipType.TESTS_SYMBOL,
                source_spec,
                self._metadata(
                    framework=case.framework,
                    relative_path=relative_path,
                    evidence_kind="tests_symbol",
                    line_start=case.line_start,
                    line_end=case.line_end,
                    extra={"symbol_name": symbol_name},
                ),
            )
            edge_count += 1
        return edge_count

    def _emit_endpoint_edges(
        self,
        *,
        case: TestCaseObservation,
        case_qn: str,
        relative_path: str,
    ) -> int:
        edge_count = 0
        seen: set[tuple[str, str]] = set()
        for endpoint_call in case.endpoint_calls:
            key = (endpoint_call.method, endpoint_call.path)
            if key in seen:
                continue
            seen.add(key)
            endpoint_qn = self._ensure_endpoint_node(
                endpoint_call=endpoint_call,
                relative_path=relative_path,
            )
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.TEST_CASE, cs.KEY_QUALIFIED_NAME, case_qn),
                cs.RelationshipType.TESTS_ENDPOINT,
                (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn),
                self._metadata(
                    framework=case.framework,
                    relative_path=relative_path,
                    evidence_kind="tests_endpoint",
                    line_start=case.line_start,
                    line_end=case.line_end,
                    extra={
                        cs.KEY_HTTP_METHOD: endpoint_call.method,
                        cs.KEY_ROUTE_PATH: endpoint_call.path,
                    },
                ),
            )
            edge_count += 1
        return edge_count

    def _emit_contract_edges(
        self,
        *,
        case: TestCaseObservation,
        case_qn: str,
        relative_path: str,
        contract_index: dict[str, list[str]],
    ) -> int:
        edge_count = 0
        seen_contracts: set[str] = set()
        for identifier in case.identifier_refs:
            for contract_qn in contract_index.get(identifier, []):
                if contract_qn in seen_contracts:
                    continue
                seen_contracts.add(contract_qn)
                self.ingestor.ensure_relationship_batch(
                    (cs.NodeLabel.TEST_CASE, cs.KEY_QUALIFIED_NAME, case_qn),
                    cs.RelationshipType.ASSERTS_CONTRACT,
                    (cs.NodeLabel.CONTRACT, cs.KEY_QUALIFIED_NAME, contract_qn),
                    self._metadata(
                        framework=case.framework,
                        relative_path=relative_path,
                        evidence_kind="asserts_contract",
                        line_start=case.line_start,
                        line_end=case.line_end,
                        extra={"contract_name": identifier},
                    ),
                )
                edge_count += 1
        return edge_count

    def _ensure_suite_node(
        self,
        *,
        suite_name: str,
        framework: str,
        relative_path: str,
    ) -> str:
        suite_qn = build_semantic_qn(
            self.project_name,
            "test_suite",
            f"{relative_path}:{suite_name}",
        )
        props = {
            cs.KEY_QUALIFIED_NAME: suite_qn,
            cs.KEY_NAME: suite_name,
            cs.KEY_FRAMEWORK: framework,
            "suite_kind": "test_suite",
        }
        props.update(
            self._metadata(
                framework=framework,
                relative_path=relative_path,
                evidence_kind="test_suite",
            )
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.TEST_SUITE, props)
        return suite_qn

    def _ensure_case_node(
        self,
        *,
        case: TestCaseObservation,
        suite_qn: str,
        relative_path: str,
    ) -> str:
        case_qn = build_semantic_qn(
            self.project_name,
            "test_case",
            f"{relative_path}:{case.suite_name}:{case.case_name}:{case.line_start or 0}",
        )
        props = {
            cs.KEY_QUALIFIED_NAME: case_qn,
            cs.KEY_NAME: case.case_name,
            cs.KEY_FRAMEWORK: case.framework,
            "case_kind": case.case_kind,
            "suite_qn": suite_qn,
        }
        props.update(
            self._metadata(
                framework=case.framework,
                relative_path=relative_path,
                evidence_kind="test_case",
                line_start=case.line_start,
                line_end=case.line_end,
            )
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.TEST_CASE, props)
        return case_qn

    def _ensure_endpoint_node(
        self,
        *,
        endpoint_call: EndpointCallObservation,
        relative_path: str,
    ) -> str:
        endpoint_qn = self._resolve_endpoint_qn(
            method=endpoint_call.method,
            path=endpoint_call.path,
        ) or (
            f"{self.project_name}{cs.SEPARATOR_DOT}"
            f"endpoint.http.{endpoint_call.method}:{endpoint_call.path}"
        )
        self.ingestor.ensure_node_batch(
            cs.NodeLabel.ENDPOINT,
            {
                cs.KEY_QUALIFIED_NAME: endpoint_qn,
                cs.KEY_NAME: f"{endpoint_call.method} {endpoint_call.path}",
                cs.KEY_FRAMEWORK: "http",
                cs.KEY_HTTP_METHOD: endpoint_call.method,
                cs.KEY_ROUTE_PATH: endpoint_call.path,
                **self._metadata(
                    framework="test",
                    relative_path=relative_path,
                    evidence_kind="test_endpoint_reference",
                ),
            },
        )
        return endpoint_qn

    def _resolve_endpoint_qn(self, *, method: str, path: str) -> str | None:
        cache_key = (method.upper(), path)
        if cache_key in self._endpoint_qn_cache:
            return self._endpoint_qn_cache[cache_key]
        if not hasattr(self.ingestor, "fetch_all"):
            self._endpoint_qn_cache[cache_key] = None
            return None
        try:
            rows = self.ingestor.fetch_all(
                """
MATCH (endpoint:Endpoint {project_name: $project_name})
WHERE toUpper(coalesce(endpoint.http_method, '')) = $http_method
  AND coalesce(endpoint.route_path, '') = $route_path
RETURN coalesce(endpoint.qualified_name, '') AS qualified_name,
       coalesce(endpoint.framework, '') AS framework
ORDER BY CASE WHEN toLower(coalesce(endpoint.framework, '')) = 'http' THEN 1 ELSE 0 END,
         qualified_name
LIMIT 1
""",
                {
                    "project_name": self.project_name,
                    "http_method": method.upper(),
                    "route_path": path,
                },
            )
        except Exception:
            rows = []
        resolved_qn: str | None = None
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                candidate = str(row.get("qualified_name", "")).strip()
                if candidate:
                    resolved_qn = candidate
                    break
        self._endpoint_qn_cache[cache_key] = resolved_qn
        return resolved_qn

    def _resolve_symbol_spec(self, symbol_name: str) -> tuple[str, str, str] | None:
        if not symbol_name or symbol_name.startswith("test_"):
            return None
        candidates = list(self.function_registry.find_ending_with(symbol_name))
        for candidate in candidates:
            if ".tests." in candidate.lower():
                continue
            node_type = self.function_registry.get(candidate)
            if node_type is None:
                continue
            return (node_type.value, cs.KEY_QUALIFIED_NAME, candidate)
        return None

    def _resolve_symbol_qn(self, module_qn: str, symbol_name: str) -> str | None:
        preferred_qn = f"{module_qn}{cs.SEPARATOR_DOT}{symbol_name}"
        if self.function_registry.get(preferred_qn) is not None:
            return preferred_qn
        candidates = list(self.function_registry.find_ending_with(symbol_name))
        preferred_prefix = f"{module_qn}{cs.SEPARATOR_DOT}"
        for candidate in candidates:
            if candidate.startswith(preferred_prefix):
                return candidate
        return candidates[0] if candidates else None

    def _metadata(
        self,
        *,
        framework: str,
        relative_path: str,
        evidence_kind: str,
        line_start: int | None = None,
        line_end: int | None = None,
        extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return build_semantic_metadata(
            source_parser="test_semantics_pass",
            evidence_kind=evidence_kind,
            file_path=relative_path,
            confidence=0.84,
            language=framework,
            line_start=line_start,
            line_end=line_end,
            extra=extra,
        )

    def _looks_like_test_path(self, file_path: Path) -> bool:
        normalized = str(file_path).replace("\\", "/").lower()
        if "/tests/" in f"/{normalized}/":
            return True
        name = file_path.name.lower()
        return (
            name.startswith("test_")
            or name.endswith("_test.py")
            or ".spec." in name
            or ".test." in name
        )

    def _module_qn_for_path(self, file_path: Path) -> str:
        relative_path = file_path.relative_to(self.repo_path)
        parts = list(relative_path.with_suffix("").parts)
        if file_path.name == cs.INIT_PY:
            parts = list(relative_path.parent.parts)
        return cs.SEPARATOR_DOT.join([self.project_name, *parts])

    def _relative_path(self, file_path: Path) -> str:
        return str(file_path.relative_to(self.repo_path)).replace("\\", "/")

    @staticmethod
    def _read_source(file_path: Path) -> str | None:
        try:
            return file_path.read_text(encoding=cs.ENCODING_UTF8)
        except Exception:
            return None
