from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from loguru import logger
from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.parsers.pipeline.query_fingerprints import (
    QueryObservation,
    extract_python_query_observations,
    extract_typescript_query_observations,
)
from codebase_rag.parsers.pipeline.semantic_guardrails import (
    SEMANTIC_GUARDRAIL_LIMITS,
    apply_grouped_guardrail,
    apply_sequence_guardrail,
)
from codebase_rag.parsers.pipeline.semantic_metadata import (
    build_semantic_metadata,
    build_semantic_qn,
)
from codebase_rag.parsers.pipeline.semantic_pass_registry import (
    is_semantic_pass_enabled,
)


class QueryFingerprintPass:
    """Emits SQL/Cypher query nodes, fingerprints, and read/write edges."""

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
        self.enabled = is_semantic_pass_enabled("CODEGRAPH_QUERY_FINGERPRINT_SEMANTICS")

    def process_ast_cache(
        self,
        ast_items: Iterable[tuple[Path, tuple[Node, cs.SupportedLanguage]]],
    ) -> None:
        if not self.enabled:
            return

        query_count = 0
        fingerprint_count = 0
        edge_count = 0
        seen_fingerprints: set[tuple[str, str]] = set()

        for file_path, (_, language) in ast_items:
            source = self._read_source(file_path)
            if source is None:
                continue

            observations: list[QueryObservation] = []
            if (
                language == cs.SupportedLanguage.PYTHON
                and file_path.suffix == cs.EXT_PY
            ):
                observations = extract_python_query_observations(source)
            elif language in {cs.SupportedLanguage.JS, cs.SupportedLanguage.TS} and (
                file_path.suffix in {*cs.JS_EXTENSIONS, *cs.TS_EXTENSIONS}
            ):
                observations = extract_typescript_query_observations(source)

            if not observations:
                continue

            module_qn = self._module_qn_for_path(file_path)
            relative_path = self._relative_path(file_path)
            language_name = (
                "python" if language == cs.SupportedLanguage.PYTHON else "typescript"
            )
            observations = apply_grouped_guardrail(
                observations,
                group_key=lambda observation: observation.symbol_name,
                limit_per_group=SEMANTIC_GUARDRAIL_LIMITS[
                    "query_observations_per_symbol"
                ],
                pass_id="query_fingerprint_semantics",
                budget_name="query_observations_per_symbol",
                scope=relative_path,
            )
            observations = apply_sequence_guardrail(
                observations,
                limit=SEMANTIC_GUARDRAIL_LIMITS["query_observations_per_file"],
                pass_id="query_fingerprint_semantics",
                budget_name="query_observations_per_file",
                scope=relative_path,
            )

            for observation in observations:
                query_qn = self._ensure_query_node(
                    observation=observation,
                    relative_path=relative_path,
                    module_qn=module_qn,
                    language=language_name,
                )
                query_count += 1

                fingerprint_key = (observation.query_kind, observation.fingerprint)
                fingerprint_qn = self._ensure_fingerprint_node(
                    observation=observation,
                    relative_path=relative_path,
                    language=language_name,
                )
                if fingerprint_key not in seen_fingerprints:
                    fingerprint_count += 1
                    seen_fingerprints.add(fingerprint_key)

                self.ingestor.ensure_relationship_batch(
                    self._resolve_source_spec(module_qn, observation.symbol_name),
                    (
                        cs.RelationshipType.EXECUTES_SQL
                        if observation.query_kind == "sql"
                        else cs.RelationshipType.EXECUTES_CYPHER
                    ),
                    (
                        cs.NodeLabel.SQL_QUERY
                        if observation.query_kind == "sql"
                        else cs.NodeLabel.CYPHER_QUERY,
                        cs.KEY_QUALIFIED_NAME,
                        query_qn,
                    ),
                    self._relationship_metadata(
                        observation=observation,
                        relative_path=relative_path,
                        language=language_name,
                        evidence_kind="query_execution",
                    ),
                )
                edge_count += 1

                self.ingestor.ensure_relationship_batch(
                    (
                        cs.NodeLabel.SQL_QUERY
                        if observation.query_kind == "sql"
                        else cs.NodeLabel.CYPHER_QUERY,
                        cs.KEY_QUALIFIED_NAME,
                        query_qn,
                    ),
                    cs.RelationshipType.HAS_FINGERPRINT,
                    (
                        cs.NodeLabel.QUERY_FINGERPRINT,
                        cs.KEY_QUALIFIED_NAME,
                        fingerprint_qn,
                    ),
                    self._relationship_metadata(
                        observation=observation,
                        relative_path=relative_path,
                        language=language_name,
                        evidence_kind="query_fingerprint",
                    ),
                )
                edge_count += 1

                if observation.query_kind == "sql":
                    edge_count += self._emit_sql_target_edges(
                        query_qn=query_qn,
                        observation=observation,
                        relative_path=relative_path,
                        language=language_name,
                    )
                else:
                    edge_count += self._emit_cypher_target_edges(
                        query_qn=query_qn,
                        observation=observation,
                        relative_path=relative_path,
                        language=language_name,
                    )

        logger.info(
            "QueryFingerprintPass: {} query node(s), {} fingerprint node(s), {} edge(s)",
            query_count,
            fingerprint_count,
            edge_count,
        )

    def _ensure_query_node(
        self,
        *,
        observation: QueryObservation,
        relative_path: str,
        module_qn: str,
        language: str,
    ) -> str:
        query_identity = (
            f"{module_qn}:{observation.symbol_name}:{observation.query_kind}:"
            f"{observation.fingerprint}:{observation.line_start or 0}"
        )
        query_qn = build_semantic_qn(
            self.project_name,
            f"{observation.query_kind}_query",
            query_identity,
        )
        query_label = (
            cs.NodeLabel.SQL_QUERY
            if observation.query_kind == "sql"
            else cs.NodeLabel.CYPHER_QUERY
        )
        props = {
            cs.KEY_QUALIFIED_NAME: query_qn,
            cs.KEY_NAME: f"{observation.query_kind}:{observation.fingerprint}",
            "query_kind": observation.query_kind,
            "fingerprint": observation.fingerprint,
            "query_intent": observation.query_intent,
            "normalized_query": observation.normalized_query,
            "raw_query": observation.raw_query,
            "symbol_qn": f"{module_qn}{cs.SEPARATOR_DOT}{observation.symbol_name}",
        }
        props.update(
            build_semantic_metadata(
                source_parser="query_fingerprint_pass",
                evidence_kind=f"{observation.query_kind}_query",
                file_path=relative_path,
                confidence=0.9,
                language=language,
                line_start=observation.line_start,
                line_end=observation.line_end,
            )
        )
        self.ingestor.ensure_node_batch(query_label, props)
        return query_qn

    def _ensure_fingerprint_node(
        self,
        *,
        observation: QueryObservation,
        relative_path: str,
        language: str,
    ) -> str:
        fingerprint_qn = build_semantic_qn(
            self.project_name,
            "query_fingerprint",
            f"{observation.query_kind}:{observation.fingerprint}",
        )
        props = {
            cs.KEY_QUALIFIED_NAME: fingerprint_qn,
            cs.KEY_NAME: observation.fingerprint,
            "query_kind": observation.query_kind,
            "fingerprint": observation.fingerprint,
            "normalized_query": observation.normalized_query,
        }
        props.update(
            build_semantic_metadata(
                source_parser="query_fingerprint_pass",
                evidence_kind="query_fingerprint",
                file_path=relative_path,
                confidence=0.97,
                language=language,
                line_start=observation.line_start,
                line_end=observation.line_end,
            )
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.QUERY_FINGERPRINT, props)
        return fingerprint_qn

    def _emit_sql_target_edges(
        self,
        *,
        query_qn: str,
        observation: QueryObservation,
        relative_path: str,
        language: str,
    ) -> int:
        edge_count = 0
        target_pairs = apply_sequence_guardrail(
            [
                *[
                    (name, cs.RelationshipType.READS_TABLE)
                    for name in observation.read_targets
                ],
                *[
                    (name, cs.RelationshipType.WRITES_TABLE)
                    for name in observation.write_targets
                ],
                *[
                    (name, cs.RelationshipType.JOINS_TABLE)
                    for name in observation.join_targets
                ],
            ],
            limit=SEMANTIC_GUARDRAIL_LIMITS["query_targets_per_query"],
            pass_id="query_fingerprint_semantics",
            budget_name="query_targets_per_query",
            scope=f"{relative_path}:{observation.symbol_name}:{observation.fingerprint}",
        )
        for table_name, rel_type in target_pairs:
            table_qn = self._ensure_table_node(
                table_name=table_name,
                relative_path=relative_path,
                language=language,
            )
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.SQL_QUERY, cs.KEY_QUALIFIED_NAME, query_qn),
                rel_type,
                (cs.NodeLabel.DATA_STORE, cs.KEY_QUALIFIED_NAME, table_qn),
                self._relationship_metadata(
                    observation=observation,
                    relative_path=relative_path,
                    language=language,
                    evidence_kind=rel_type.lower(),
                    extra={"table_name": table_name},
                ),
            )
            edge_count += 1
        return edge_count

    def _emit_cypher_target_edges(
        self,
        *,
        query_qn: str,
        observation: QueryObservation,
        relative_path: str,
        language: str,
    ) -> int:
        edge_count = 0
        target_pairs = apply_sequence_guardrail(
            [
                *[
                    (name, cs.RelationshipType.READS_LABEL)
                    for name in observation.read_targets
                ],
                *[
                    (name, cs.RelationshipType.WRITES_LABEL)
                    for name in observation.write_targets
                ],
            ],
            limit=SEMANTIC_GUARDRAIL_LIMITS["query_targets_per_query"],
            pass_id="query_fingerprint_semantics",
            budget_name="query_targets_per_query",
            scope=f"{relative_path}:{observation.symbol_name}:{observation.fingerprint}",
        )
        for label_name, rel_type in target_pairs:
            label_qn = self._ensure_graph_label_node(
                label_name=label_name,
                relative_path=relative_path,
                language=language,
            )
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.CYPHER_QUERY, cs.KEY_QUALIFIED_NAME, query_qn),
                rel_type,
                (cs.NodeLabel.GRAPH_NODE_LABEL, cs.KEY_QUALIFIED_NAME, label_qn),
                self._relationship_metadata(
                    observation=observation,
                    relative_path=relative_path,
                    language=language,
                    evidence_kind=rel_type.lower(),
                    extra={"label_name": label_name},
                ),
            )
            edge_count += 1
        return edge_count

    def _ensure_table_node(
        self,
        *,
        table_name: str,
        relative_path: str,
        language: str,
    ) -> str:
        table_qn = build_semantic_qn(
            self.project_name,
            "sql_table",
            table_name,
        )
        props = {
            cs.KEY_QUALIFIED_NAME: table_qn,
            cs.KEY_NAME: table_name,
            "store_kind": "sql_table",
            "table_name": table_name,
        }
        props.update(
            build_semantic_metadata(
                source_parser="query_fingerprint_pass",
                evidence_kind="sql_table_reference",
                file_path=relative_path,
                confidence=0.82,
                language=language,
            )
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.DATA_STORE, props)
        return table_qn

    def _ensure_graph_label_node(
        self,
        *,
        label_name: str,
        relative_path: str,
        language: str,
    ) -> str:
        label_qn = build_semantic_qn(
            self.project_name,
            "graph_label",
            label_name,
        )
        props = {
            cs.KEY_QUALIFIED_NAME: label_qn,
            cs.KEY_NAME: label_name,
        }
        props.update(
            build_semantic_metadata(
                source_parser="query_fingerprint_pass",
                evidence_kind="graph_label_reference",
                file_path=relative_path,
                confidence=0.82,
                language=language,
            )
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.GRAPH_NODE_LABEL, props)
        return label_qn

    def _resolve_source_spec(
        self,
        module_qn: str,
        symbol_name: str,
    ) -> tuple[str, str, str]:
        preferred_qn = f"{module_qn}{cs.SEPARATOR_DOT}{symbol_name}"
        node_type = self.function_registry.get(preferred_qn)
        if node_type is not None:
            return (node_type.value, cs.KEY_QUALIFIED_NAME, preferred_qn)

        candidates = []
        find_with_prefix_and_suffix = getattr(
            self.function_registry, "find_with_prefix_and_suffix", None
        )
        if callable(find_with_prefix_and_suffix):
            candidates = list(find_with_prefix_and_suffix(module_qn, symbol_name))
        if not candidates:
            candidates = list(self.function_registry.find_ending_with(symbol_name))
        for candidate in candidates:
            candidate_type = self.function_registry.get(candidate)
            if candidate_type is not None:
                return (candidate_type.value, cs.KEY_QUALIFIED_NAME, candidate)
        return (cs.NodeLabel.MODULE, cs.KEY_QUALIFIED_NAME, module_qn)

    def _relationship_metadata(
        self,
        *,
        observation: QueryObservation,
        relative_path: str,
        language: str,
        evidence_kind: str,
        extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload = {
            "query_kind": observation.query_kind,
            "query_intent": observation.query_intent,
            "fingerprint": observation.fingerprint,
        }
        if extra:
            payload.update(extra)
        return build_semantic_metadata(
            source_parser="query_fingerprint_pass",
            evidence_kind=evidence_kind,
            file_path=relative_path,
            confidence=0.86,
            language=language,
            line_start=observation.line_start,
            line_end=observation.line_end,
            extra=payload,
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
