from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from loguru import logger
from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.parsers.pipeline.frontend_operations import (
    FrontendOperationObservation,
    extract_frontend_operation_observations,
    extract_openapi_operation_bindings,
    normalize_http_path,
)
from codebase_rag.parsers.pipeline.openapi_contracts import (
    extract_openapi_contract_surface,
)
from codebase_rag.parsers.pipeline.semantic_metadata import (
    build_semantic_metadata,
    build_semantic_qn,
)
from codebase_rag.parsers.pipeline.semantic_pass_registry import (
    is_semantic_pass_enabled,
)


class FrontendOperationPass:
    """Emits generated-client and raw bypass client operation semantics."""

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
        self.enabled = is_semantic_pass_enabled(
            "CODEGRAPH_FRONTEND_OPERATION_SEMANTICS"
        )

    def process_ast_cache(
        self,
        ast_items: Iterable[tuple[Path, tuple[Node, cs.SupportedLanguage]]],
    ) -> None:
        if not self.enabled:
            return

        operation_bindings = {}
        sources: list[tuple[Path, cs.SupportedLanguage]] = []

        for file_path, (_, language) in ast_items:
            sources.append((file_path, language))
            if language not in {cs.SupportedLanguage.JSON, cs.SupportedLanguage.YAML}:
                continue
            if file_path.suffix not in {cs.EXT_JSON, cs.EXT_YAML, cs.EXT_YML}:
                continue
            if not self._looks_like_openapi_path(file_path):
                continue
            source = self._read_source(file_path)
            if source is None:
                continue
            _contracts, bindings = extract_openapi_contract_surface(
                source, file_suffix=file_path.suffix.lower()
            )
            operation_bindings.update(extract_openapi_operation_bindings(bindings))

        operation_count = 0
        edge_count = 0
        for file_path, language in sources:
            if language not in {cs.SupportedLanguage.JS, cs.SupportedLanguage.TS}:
                continue
            if file_path.suffix not in {*cs.JS_EXTENSIONS, *cs.TS_EXTENSIONS}:
                continue
            source = self._read_source(file_path)
            if source is None:
                continue
            relative_path = self._relative_path(file_path)
            observations = extract_frontend_operation_observations(
                source,
                relative_path=relative_path,
                operation_bindings=operation_bindings,
            )
            if not observations:
                continue

            module_qn = self._module_qn_for_path(file_path)
            for observation in observations:
                operation_qn = self._ensure_operation_node(
                    observation=observation,
                    relative_path=relative_path,
                )
                endpoint_qn = self._ensure_endpoint_node(
                    method=observation.method,
                    path=observation.path,
                    relative_path=relative_path,
                )
                source_spec = self._resolve_source_spec(module_qn, observation)

                self.ingestor.ensure_relationship_batch(
                    source_spec,
                    cs.RelationshipType.USES_OPERATION,
                    (
                        cs.NodeLabel.CLIENT_OPERATION,
                        cs.KEY_QUALIFIED_NAME,
                        operation_qn,
                    ),
                    self._metadata(
                        observation=observation,
                        relative_path=relative_path,
                        evidence_kind="uses_operation",
                    ),
                )
                self.ingestor.ensure_relationship_batch(
                    (
                        cs.NodeLabel.CLIENT_OPERATION,
                        cs.KEY_QUALIFIED_NAME,
                        operation_qn,
                    ),
                    cs.RelationshipType.REQUESTS_ENDPOINT,
                    (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn),
                    self._metadata(
                        observation=observation,
                        relative_path=relative_path,
                        evidence_kind="client_operation_request",
                    ),
                )
                edge_count += 2

                if observation.governance_kind != "bypass" and observation.operation_id:
                    self.ingestor.ensure_relationship_batch(
                        (
                            cs.NodeLabel.CLIENT_OPERATION,
                            cs.KEY_QUALIFIED_NAME,
                            operation_qn,
                        ),
                        cs.RelationshipType.GENERATED_FROM_SPEC,
                        (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn),
                        self._metadata(
                            observation=observation,
                            relative_path=relative_path,
                            evidence_kind="generated_from_spec",
                        ),
                    )
                    edge_count += 1
                if observation.governance_kind == "bypass":
                    self.ingestor.ensure_relationship_batch(
                        (
                            cs.NodeLabel.CLIENT_OPERATION,
                            cs.KEY_QUALIFIED_NAME,
                            operation_qn,
                        ),
                        cs.RelationshipType.BYPASSES_MANIFEST,
                        (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn),
                        self._metadata(
                            observation=observation,
                            relative_path=relative_path,
                            evidence_kind="bypasses_manifest",
                        ),
                    )
                    edge_count += 1
                operation_count += 1

        logger.info(
            "FrontendOperationPass: {} client operation node(s), {} edge(s)",
            operation_count,
            edge_count,
        )

    def _ensure_operation_node(
        self,
        *,
        observation: FrontendOperationObservation,
        relative_path: str,
    ) -> str:
        identity = (
            f"{relative_path}:{observation.symbol_name}:"
            f"{observation.method}:{observation.path}:{observation.client_kind}"
        )
        operation_qn = build_semantic_qn(
            self.project_name,
            "client_operation",
            identity,
        )
        props = {
            cs.KEY_QUALIFIED_NAME: operation_qn,
            cs.KEY_NAME: observation.operation_name,
            cs.KEY_HTTP_METHOD: observation.method,
            cs.KEY_ROUTE_PATH: observation.path,
            "operation_id": observation.operation_id,
            "client_kind": observation.client_kind,
            "governance_kind": observation.governance_kind,
            "manifest_source": observation.manifest_source,
        }
        props.update(
            build_semantic_metadata(
                source_parser="frontend_operation_pass",
                evidence_kind="client_operation",
                file_path=relative_path,
                confidence=0.9,
                language="typescript",
                line_start=observation.line_start,
                line_end=observation.line_end,
            )
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.CLIENT_OPERATION, props)
        return operation_qn

    def _ensure_endpoint_node(
        self,
        *,
        method: str,
        path: str,
        relative_path: str,
    ) -> str:
        endpoint_qn = self._endpoint_qn(method, path)
        self.ingestor.ensure_node_batch(
            cs.NodeLabel.ENDPOINT,
            {
                cs.KEY_QUALIFIED_NAME: endpoint_qn,
                cs.KEY_NAME: f"{method} {path}",
                cs.KEY_FRAMEWORK: "http",
                cs.KEY_HTTP_METHOD: method,
                cs.KEY_ROUTE_PATH: path,
                **build_semantic_metadata(
                    source_parser="frontend_operation_pass",
                    evidence_kind="client_endpoint",
                    file_path=relative_path,
                    confidence=0.82,
                    language="typescript",
                ),
            },
        )
        return endpoint_qn

    def _resolve_source_spec(
        self,
        module_qn: str,
        observation: FrontendOperationObservation,
    ) -> tuple[str, str, str]:
        preferred_qn = f"{module_qn}{cs.SEPARATOR_DOT}{observation.symbol_name}"
        node_type = self.function_registry.get(preferred_qn)
        if node_type is not None:
            return (node_type.value, cs.KEY_QUALIFIED_NAME, preferred_qn)

        candidates = []
        find_with_prefix_and_suffix = getattr(
            self.function_registry, "find_with_prefix_and_suffix", None
        )
        if callable(find_with_prefix_and_suffix):
            candidates = list(
                find_with_prefix_and_suffix(module_qn, observation.symbol_name)
            )
        if not candidates:
            candidates = list(
                self.function_registry.find_ending_with(observation.symbol_name)
            )
        for candidate in candidates:
            candidate_type = self.function_registry.get(candidate)
            if candidate_type is not None:
                return (candidate_type.value, cs.KEY_QUALIFIED_NAME, candidate)
        return (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn)

    def _metadata(
        self,
        *,
        observation: FrontendOperationObservation,
        relative_path: str,
        evidence_kind: str,
    ) -> dict[str, object]:
        return build_semantic_metadata(
            source_parser="frontend_operation_pass",
            evidence_kind=evidence_kind,
            file_path=relative_path,
            confidence=0.86,
            language="typescript",
            line_start=observation.line_start,
            line_end=observation.line_end,
            extra={
                "client_kind": observation.client_kind,
                "governance_kind": observation.governance_kind,
                "operation_id": observation.operation_id,
            },
        )

    def _looks_like_openapi_path(self, file_path: Path) -> bool:
        normalized = str(file_path).replace("\\", "/").lower()
        return "openapi" in normalized or normalized.endswith(
            ("swagger.json", "swagger.yaml", "swagger.yml")
        )

    def _module_qn_for_path(self, file_path: Path) -> str:
        relative_path = file_path.relative_to(self.repo_path)
        parts = list(relative_path.with_suffix("").parts)
        if file_path.name == cs.INIT_PY:
            parts = list(relative_path.parent.parts)
        return cs.SEPARATOR_DOT.join([self.project_name, *parts])

    def _relative_path(self, file_path: Path) -> str:
        return str(file_path.relative_to(self.repo_path)).replace("\\", "/")

    def _endpoint_qn(self, method: str, path: str) -> str:
        normalized_path = normalize_http_path(path)
        return (
            f"{self.project_name}{cs.SEPARATOR_DOT}"
            f"endpoint.http.{method.upper()}:{normalized_path}"
        )

    @staticmethod
    def _read_source(file_path: Path) -> str | None:
        try:
            return file_path.read_text(encoding=cs.ENCODING_UTF8)
        except Exception:
            return None
