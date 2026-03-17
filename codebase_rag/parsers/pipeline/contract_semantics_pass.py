from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from loguru import logger
from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.parsers.frameworks.fastapi_semantics import (
    extract_fastapi_route_semantics,
)
from codebase_rag.parsers.pipeline.openapi_contracts import (
    OpenApiEndpointContractBinding,
    extract_openapi_contract_surface,
)
from codebase_rag.parsers.pipeline.python_contracts import (
    ContractDefinition,
    ContractFieldDefinition,
    extract_python_contracts,
    extract_python_handler_contracts,
)
from codebase_rag.parsers.pipeline.semantic_metadata import (
    build_semantic_metadata,
    build_semantic_qn,
)
from codebase_rag.parsers.pipeline.semantic_pass_registry import (
    is_semantic_pass_enabled,
)
from codebase_rag.parsers.pipeline.ts_contracts import (
    extract_typescript_contracts,
    extract_typescript_function_contracts,
)


class ContractSemanticsPass:
    """Emits Python, TypeScript, Zod, and OpenAPI contract semantics."""

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
        self.enabled = is_semantic_pass_enabled("CODEGRAPH_CONTRACT_SEMANTICS")

    def process_ast_cache(
        self,
        ast_items: Iterable[tuple[Path, tuple[Node, cs.SupportedLanguage]]],
    ) -> None:
        if not self.enabled:
            return

        ast_cache_items = tuple(ast_items)
        python_paths = [
            file_path
            for file_path, (_, language) in ast_cache_items
            if language == cs.SupportedLanguage.PYTHON and file_path.suffix == cs.EXT_PY
        ]
        typescript_paths = [
            file_path
            for file_path, (_, language) in ast_cache_items
            if language == cs.SupportedLanguage.TS
            and file_path.suffix in {cs.EXT_TS, cs.EXT_TSX}
        ]
        openapi_paths = [
            file_path
            for file_path, (_, language) in ast_cache_items
            if language in {cs.SupportedLanguage.JSON, cs.SupportedLanguage.YAML}
            and file_path.suffix in {cs.EXT_JSON, cs.EXT_YAML, cs.EXT_YML}
        ]
        if not python_paths and not typescript_paths and not openapi_paths:
            return

        contract_index: dict[str, list[str]] = defaultdict(list)
        contract_count = 0
        field_count = 0
        edge_count = 0
        python_sources: dict[Path, str] = {}
        typescript_sources: dict[Path, str] = {}
        openapi_bindings: list[tuple[Path, str, OpenApiEndpointContractBinding]] = []

        for file_path in python_paths:
            source = self._read_source(file_path)
            if source is None:
                continue
            python_sources[file_path] = source
            module_qn = self._module_qn_for_path(file_path)
            relative_path = self._relative_path(file_path)
            contract_defs = extract_python_contracts(source)
            created_contracts, created_fields = self._ingest_contract_definitions(
                contract_defs=contract_defs,
                contract_index=contract_index,
                module_qn=module_qn,
                relative_path=relative_path,
                framework="python",
                language="python",
            )
            contract_count += created_contracts
            field_count += created_fields

        for file_path in typescript_paths:
            source = self._read_source(file_path)
            if source is None:
                continue
            typescript_sources[file_path] = source
            module_qn = self._module_qn_for_path(file_path)
            relative_path = self._relative_path(file_path)
            contract_defs = extract_typescript_contracts(source)
            created_contracts, created_fields = self._ingest_contract_definitions(
                contract_defs=contract_defs,
                contract_index=contract_index,
                module_qn=module_qn,
                relative_path=relative_path,
                framework="typescript",
                language="typescript",
            )
            contract_count += created_contracts
            field_count += created_fields

        for file_path in openapi_paths:
            if not self._looks_like_openapi_path(file_path):
                continue
            source = self._read_source(file_path)
            if source is None:
                continue
            module_qn = self._module_qn_for_path(file_path)
            relative_path = self._relative_path(file_path)
            contract_defs, bindings = extract_openapi_contract_surface(
                source, file_suffix=file_path.suffix.lower()
            )
            if not contract_defs and not bindings:
                continue
            created_contracts, created_fields = self._ingest_contract_definitions(
                contract_defs=contract_defs,
                contract_index=contract_index,
                module_qn=module_qn,
                relative_path=relative_path,
                framework="openapi",
                language="openapi",
            )
            contract_count += created_contracts
            field_count += created_fields
            openapi_bindings.extend(
                (file_path, module_qn, binding) for binding in bindings
            )

        edge_count += self._emit_python_contract_edges(
            python_sources=python_sources,
            contract_index=contract_index,
        )
        edge_count += self._emit_typescript_contract_edges(
            typescript_sources=typescript_sources,
            contract_index=contract_index,
        )
        edge_count += self._emit_openapi_contract_edges(
            bindings=openapi_bindings,
            contract_index=contract_index,
        )

        logger.info(
            "ContractSemanticsPass: {} contract(s), {} field(s), {} edge(s)",
            contract_count,
            field_count,
            edge_count,
        )

    def _ingest_contract_definitions(
        self,
        *,
        contract_defs: Iterable[ContractDefinition],
        contract_index: dict[str, list[str]],
        module_qn: str,
        relative_path: str,
        framework: str,
        language: str,
    ) -> tuple[int, int]:
        contract_count = 0
        field_count = 0
        for contract_def in contract_defs:
            contract_qn = self._ensure_contract_definition_node(
                contract_def=contract_def,
                module_qn=module_qn,
                relative_path=relative_path,
                framework=framework,
                language=language,
            )
            contract_count += 1
            if contract_qn not in contract_index[contract_def.name]:
                contract_index[contract_def.name].append(contract_qn)
            for field_def in contract_def.fields:
                self._ensure_contract_field(
                    contract_qn=contract_qn,
                    contract_kind=contract_def.kind,
                    field_def=field_def,
                    relative_path=relative_path,
                    framework=framework,
                    language=language,
                )
                field_count += 1
        return contract_count, field_count

    def _emit_python_contract_edges(
        self,
        *,
        python_sources: dict[Path, str],
        contract_index: dict[str, list[str]],
    ) -> int:
        edge_count = 0
        for file_path, source in python_sources.items():
            if "fastapi" not in source.lower():
                continue
            routes = extract_fastapi_route_semantics(source)
            if not routes:
                continue
            module_qn = self._module_qn_for_path(file_path)
            relative_path = self._relative_path(file_path)
            handler_contracts = extract_python_handler_contracts(
                source, set(contract_index)
            )
            for route in routes:
                endpoint_qn = self._endpoint_qn(
                    "fastapi",
                    route.method,
                    self._normalize_endpoint_path(route.path),
                )
                handler_qn = self._find_handler_qn_in_module(
                    module_qn, route.handler_name
                )
                handler_type = (
                    self.function_registry.get(handler_qn) if handler_qn else None
                )
                source_specs: list[tuple[str, str, str]] = [
                    (cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn)
                ]
                if handler_qn and handler_type:
                    source_specs.append(
                        (handler_type.value, cs.KEY_QUALIFIED_NAME, handler_qn)
                    )
                edge_count += self._emit_contract_relationships(
                    source_specs=source_specs,
                    contract_names=handler_contracts.get(route.handler_name, []),
                    relationship_type=cs.RelationshipType.ACCEPTS_CONTRACT,
                    module_qn=module_qn,
                    contract_index=contract_index,
                    relative_path=relative_path,
                    language="python",
                    line_start=route.line_start,
                    line_end=route.line_end,
                    evidence_kind="request_contract",
                )
        return edge_count

    def _emit_typescript_contract_edges(
        self,
        *,
        typescript_sources: dict[Path, str],
        contract_index: dict[str, list[str]],
    ) -> int:
        edge_count = 0
        for file_path, source in typescript_sources.items():
            surfaces = extract_typescript_function_contracts(
                source, set(contract_index)
            )
            if not surfaces:
                continue
            module_qn = self._module_qn_for_path(file_path)
            relative_path = self._relative_path(file_path)
            for surface in surfaces:
                function_qn = self._find_handler_qn_in_module(
                    module_qn, surface.function_name
                )
                function_type = (
                    self.function_registry.get(function_qn) if function_qn else None
                )
                if not function_qn or not function_type:
                    continue
                source_spec = (
                    function_type.value,
                    cs.KEY_QUALIFIED_NAME,
                    function_qn,
                )
                edge_count += self._emit_contract_relationships(
                    source_specs=[source_spec],
                    contract_names=surface.request_contracts,
                    relationship_type=cs.RelationshipType.ACCEPTS_CONTRACT,
                    module_qn=module_qn,
                    contract_index=contract_index,
                    relative_path=relative_path,
                    language="typescript",
                    line_start=surface.line_start,
                    line_end=surface.line_end,
                    evidence_kind="request_contract",
                )
                edge_count += self._emit_contract_relationships(
                    source_specs=[source_spec],
                    contract_names=surface.response_contracts,
                    relationship_type=cs.RelationshipType.RETURNS_CONTRACT,
                    module_qn=module_qn,
                    contract_index=contract_index,
                    relative_path=relative_path,
                    language="typescript",
                    line_start=surface.line_start,
                    line_end=surface.line_end,
                    evidence_kind="response_contract",
                )
        return edge_count

    def _emit_openapi_contract_edges(
        self,
        *,
        bindings: list[tuple[Path, str, OpenApiEndpointContractBinding]],
        contract_index: dict[str, list[str]],
    ) -> int:
        edge_count = 0
        for file_path, module_qn, binding in bindings:
            relative_path = self._relative_path(file_path)
            endpoint_qn = self._ensure_openapi_endpoint_node(
                method=binding.method,
                path=binding.path,
                relative_path=relative_path,
            )
            source_specs = [(cs.NodeLabel.ENDPOINT, cs.KEY_QUALIFIED_NAME, endpoint_qn)]
            edge_count += self._emit_contract_relationships(
                source_specs=source_specs,
                contract_names=binding.request_contracts,
                relationship_type=cs.RelationshipType.ACCEPTS_CONTRACT,
                module_qn=module_qn,
                contract_index=contract_index,
                relative_path=relative_path,
                language="openapi",
                evidence_kind="request_contract",
            )
            edge_count += self._emit_contract_relationships(
                source_specs=source_specs,
                contract_names=binding.response_contracts,
                relationship_type=cs.RelationshipType.RETURNS_CONTRACT,
                module_qn=module_qn,
                contract_index=contract_index,
                relative_path=relative_path,
                language="openapi",
                evidence_kind="response_contract",
            )
        return edge_count

    def _emit_contract_relationships(
        self,
        *,
        source_specs: Iterable[tuple[str, str, str]],
        contract_names: Iterable[str],
        relationship_type: str,
        module_qn: str,
        contract_index: dict[str, list[str]],
        relative_path: str,
        language: str,
        evidence_kind: str,
        line_start: int | None = None,
        line_end: int | None = None,
    ) -> int:
        edge_count = 0
        for contract_name in contract_names:
            contract_qn = self._resolve_contract_qn(
                contract_name=contract_name,
                module_qn=module_qn,
                contract_index=contract_index,
            )
            if not contract_qn:
                continue
            edge_props = build_semantic_metadata(
                source_parser="contract_semantics_pass",
                evidence_kind=evidence_kind,
                file_path=relative_path,
                confidence=0.96,
                language=language,
                line_start=line_start,
                line_end=line_end,
                extra={"contract_name": contract_name},
            )
            for source_spec in source_specs:
                self.ingestor.ensure_relationship_batch(
                    source_spec,
                    relationship_type,
                    (cs.NodeLabel.CONTRACT, cs.KEY_QUALIFIED_NAME, contract_qn),
                    edge_props,
                )
                edge_count += 1
        return edge_count

    def _ensure_contract_definition_node(
        self,
        *,
        contract_def: ContractDefinition,
        module_qn: str,
        relative_path: str,
        framework: str,
        language: str,
    ) -> str:
        resolved_qn = self._resolve_symbol_qn(module_qn, contract_def.name)
        contract_identity = (
            resolved_qn or f"{module_qn}{cs.SEPARATOR_DOT}{contract_def.name}"
        )
        contract_qn = build_semantic_qn(
            self.project_name, "contract", contract_identity
        )
        props = {
            cs.KEY_QUALIFIED_NAME: contract_qn,
            cs.KEY_NAME: contract_def.name,
            cs.KEY_FRAMEWORK: framework,
            "contract_kind": contract_def.kind,
        }
        props.update(
            build_semantic_metadata(
                source_parser="contract_semantics_pass",
                evidence_kind="contract_definition",
                file_path=relative_path,
                confidence=0.98 if resolved_qn else 0.85,
                language=language,
                line_start=contract_def.line_start,
                line_end=contract_def.line_end,
                extra={"symbol_qn": resolved_qn},
            )
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.CONTRACT, props)
        return contract_qn

    def _ensure_contract_field(
        self,
        *,
        contract_qn: str,
        contract_kind: str,
        field_def: ContractFieldDefinition,
        relative_path: str,
        framework: str,
        language: str,
    ) -> None:
        field_qn = build_semantic_qn(
            self.project_name,
            "contract_field",
            f"{contract_qn}:{field_def.name}",
        )
        field_props = {
            cs.KEY_QUALIFIED_NAME: field_qn,
            cs.KEY_NAME: field_def.name,
            cs.KEY_FRAMEWORK: framework,
            "contract_kind": contract_kind,
            "field_type": field_def.type_repr,
            "required": field_def.required,
        }
        field_props.update(
            build_semantic_metadata(
                source_parser="contract_semantics_pass",
                evidence_kind="contract_field_definition",
                file_path=relative_path,
                confidence=0.97,
                language=language,
                line_start=field_def.line_start,
                line_end=field_def.line_end,
            )
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.CONTRACT_FIELD, field_props)
        self.ingestor.ensure_relationship_batch(
            (cs.NodeLabel.CONTRACT, cs.KEY_QUALIFIED_NAME, contract_qn),
            cs.RelationshipType.DECLARES_FIELD,
            (cs.NodeLabel.CONTRACT_FIELD, cs.KEY_QUALIFIED_NAME, field_qn),
            build_semantic_metadata(
                source_parser="contract_semantics_pass",
                evidence_kind="contract_field",
                file_path=relative_path,
                confidence=0.97,
                language=language,
                line_start=field_def.line_start,
                line_end=field_def.line_end,
                extra={"field_name": field_def.name},
            ),
        )

    def _resolve_contract_qn(
        self,
        *,
        contract_name: str,
        module_qn: str,
        contract_index: dict[str, list[str]],
    ) -> str | None:
        existing = contract_index.get(contract_name, [])
        if len(existing) == 1:
            return existing[0]

        resolved_qn = self._resolve_symbol_qn(module_qn, contract_name)
        if not resolved_qn:
            return existing[0] if existing else None

        for contract_qn in existing:
            if contract_qn.endswith(self._sanitize_identity(resolved_qn)):
                return contract_qn
        return existing[0] if existing else None

    def _resolve_symbol_qn(self, module_qn: str, symbol_name: str) -> str | None:
        candidates = self.function_registry.find_ending_with(symbol_name)
        preferred_prefix = f"{module_qn}{cs.SEPARATOR_DOT}"
        for qn in candidates:
            if qn.startswith(preferred_prefix):
                return qn
        return candidates[0] if candidates else None

    def _find_handler_qn_in_module(
        self,
        module_qn: str,
        handler_name: str,
    ) -> str | None:
        candidates = self.function_registry.find_ending_with(handler_name)
        preferred_prefix = f"{module_qn}{cs.SEPARATOR_DOT}"
        for qn in candidates:
            if qn.startswith(preferred_prefix):
                return qn
        return candidates[0] if candidates else None

    def _ensure_openapi_endpoint_node(
        self,
        *,
        method: str,
        path: str,
        relative_path: str,
    ) -> str:
        normalized_path = self._normalize_endpoint_path(path)
        endpoint_qn = self._endpoint_qn("openapi", method, normalized_path)
        self.ingestor.ensure_node_batch(
            cs.NodeLabel.ENDPOINT,
            {
                cs.KEY_QUALIFIED_NAME: endpoint_qn,
                cs.KEY_NAME: f"{method} {normalized_path}",
                cs.KEY_FRAMEWORK: "openapi",
                cs.KEY_HTTP_METHOD: method,
                cs.KEY_ROUTE_PATH: normalized_path,
                cs.KEY_PATH: relative_path,
                **build_semantic_metadata(
                    source_parser="contract_semantics_pass",
                    evidence_kind="openapi_endpoint",
                    file_path=relative_path,
                    confidence=0.94,
                    language="openapi",
                ),
            },
        )
        return endpoint_qn

    def _module_qn_for_path(self, file_path: Path) -> str:
        relative_path = file_path.relative_to(self.repo_path)
        parts = list(relative_path.with_suffix("").parts)
        if file_path.name == cs.INIT_PY:
            parts = list(relative_path.parent.parts)
        return cs.SEPARATOR_DOT.join([self.project_name, *parts])

    def _relative_path(self, file_path: Path) -> str:
        return str(file_path.relative_to(self.repo_path)).replace("\\", "/")

    def _endpoint_qn(self, framework: str, method: str, path: str) -> str:
        return (
            f"{self.project_name}{cs.SEPARATOR_DOT}endpoint.{framework}.{method}:{path}"
        )

    @staticmethod
    def _normalize_endpoint_path(path: str) -> str:
        normalized = path.replace("\\", "/")
        normalized = re.sub(r"\{[^/]+\}", "{param}", normalized)
        normalized = re.sub(r"\[[^/]+\]", "{param}", normalized)
        normalized = re.sub(r":[A-Za-z_][A-Za-z0-9_]*", "{param}", normalized)
        normalized = re.sub(r"\$\{[^}]+\}", "{param}", normalized)
        normalized = re.sub(r"//+", "/", normalized)
        if normalized and not normalized.startswith("/"):
            normalized = f"/{normalized}"
        if len(normalized) > 1 and normalized.endswith("/"):
            normalized = normalized[:-1]
        return normalized or "/"

    @staticmethod
    def _sanitize_identity(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.:\-]+", "_", value.strip()).strip("_")

    @staticmethod
    def _looks_like_openapi_path(file_path: Path) -> bool:
        normalized = file_path.name.lower()
        return "openapi" in normalized or "swagger" in normalized

    @staticmethod
    def _read_source(file_path: Path) -> str | None:
        try:
            return file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            logger.warning(
                "ContractSemanticsPass failed reading {}: {}", file_path, exc
            )
            return None
