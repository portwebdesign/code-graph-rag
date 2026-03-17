from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from loguru import logger
from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.parsers.pipeline.python_transaction_flows import (
    SideEffectObservation,
    TransactionBoundaryObservation,
    extract_python_transaction_flows,
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


class TransactionFlowPass:
    """Emits first-wave Python transaction and side-effect ordering semantics."""

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
        self.enabled = is_semantic_pass_enabled("CODEGRAPH_TRANSACTION_FLOW_SEMANTICS")

    def process_ast_cache(
        self,
        ast_items: Iterable[tuple[Path, tuple[Node, cs.SupportedLanguage]]],
    ) -> None:
        if not self.enabled:
            return

        boundary_count = 0
        side_effect_count = 0
        edge_count = 0

        for file_path, (_, language) in ast_items:
            if language != cs.SupportedLanguage.PYTHON or file_path.suffix != cs.EXT_PY:
                continue
            source = self._read_source(file_path)
            if source is None:
                continue
            boundaries, side_effects = extract_python_transaction_flows(source)
            if not boundaries and not side_effects:
                continue
            relative_path = self._relative_path(file_path)
            boundaries = apply_grouped_guardrail(
                boundaries,
                group_key=lambda boundary: boundary.symbol_name,
                limit_per_group=SEMANTIC_GUARDRAIL_LIMITS[
                    "transaction_boundaries_per_symbol"
                ],
                pass_id="transaction_flow_semantics",
                budget_name="transaction_boundaries_per_symbol",
                scope=relative_path,
            )
            boundaries = apply_sequence_guardrail(
                boundaries,
                limit=SEMANTIC_GUARDRAIL_LIMITS["transaction_boundaries_per_file"],
                pass_id="transaction_flow_semantics",
                budget_name="transaction_boundaries_per_file",
                scope=relative_path,
            )
            side_effects = apply_grouped_guardrail(
                side_effects,
                group_key=lambda side_effect: side_effect.symbol_name,
                limit_per_group=SEMANTIC_GUARDRAIL_LIMITS[
                    "transaction_side_effects_per_symbol"
                ],
                pass_id="transaction_flow_semantics",
                budget_name="transaction_side_effects_per_symbol",
                scope=relative_path,
            )
            side_effects = apply_sequence_guardrail(
                side_effects,
                limit=SEMANTIC_GUARDRAIL_LIMITS["transaction_side_effects_per_file"],
                pass_id="transaction_flow_semantics",
                budget_name="transaction_side_effects_per_file",
                scope=relative_path,
            )

            module_qn = self._module_qn_for_path(file_path)
            boundary_qns: dict[str, str] = {}

            for boundary in boundaries:
                boundary_qn = self._ensure_transaction_boundary_node(
                    observation=boundary,
                    relative_path=relative_path,
                )
                boundary_qns[boundary.boundary_name] = boundary_qn
                boundary_count += 1
                source_spec = self._resolve_source_spec(module_qn, boundary)
                if source_spec is None:
                    continue

                self.ingestor.ensure_relationship_batch(
                    source_spec,
                    cs.RelationshipType.BEGINS_TRANSACTION,
                    (
                        cs.NodeLabel.TRANSACTION_BOUNDARY,
                        cs.KEY_QUALIFIED_NAME,
                        boundary_qn,
                    ),
                    self._boundary_metadata(
                        observation=boundary,
                        relative_path=relative_path,
                        extra={"transition": "begin"},
                    ),
                )
                edge_count += 1

                if boundary.has_commit:
                    self.ingestor.ensure_relationship_batch(
                        source_spec,
                        cs.RelationshipType.COMMITS_TRANSACTION,
                        (
                            cs.NodeLabel.TRANSACTION_BOUNDARY,
                            cs.KEY_QUALIFIED_NAME,
                            boundary_qn,
                        ),
                        self._boundary_metadata(
                            observation=boundary,
                            relative_path=relative_path,
                            extra={"transition": "commit"},
                        ),
                    )
                    edge_count += 1

                if boundary.has_rollback:
                    self.ingestor.ensure_relationship_batch(
                        source_spec,
                        cs.RelationshipType.ROLLBACKS_TRANSACTION,
                        (
                            cs.NodeLabel.TRANSACTION_BOUNDARY,
                            cs.KEY_QUALIFIED_NAME,
                            boundary_qn,
                        ),
                        self._boundary_metadata(
                            observation=boundary,
                            relative_path=relative_path,
                            extra={"transition": "rollback"},
                        ),
                    )
                    edge_count += 1

            side_effect_qns: dict[tuple[str, int, str], str] = {}
            grouped_effects: defaultdict[str | None, list[SideEffectObservation]] = (
                defaultdict(list)
            )

            for side_effect in side_effects:
                side_effect_qn = self._ensure_side_effect_node(
                    observation=side_effect,
                    relative_path=relative_path,
                )
                key = (
                    side_effect.symbol_name,
                    side_effect.line_start or 0,
                    side_effect.effect_kind,
                )
                side_effect_qns[key] = side_effect_qn
                grouped_effects[side_effect.boundary_name].append(side_effect)
                side_effect_count += 1

                source_spec = self._resolve_source_spec(module_qn, side_effect)
                if source_spec is not None:
                    self.ingestor.ensure_relationship_batch(
                        source_spec,
                        cs.RelationshipType.PERFORMS_SIDE_EFFECT,
                        (
                            cs.NodeLabel.SIDE_EFFECT,
                            cs.KEY_QUALIFIED_NAME,
                            side_effect_qn,
                        ),
                        self._side_effect_metadata(
                            observation=side_effect,
                            relative_path=relative_path,
                        ),
                    )
                    edge_count += 1

                boundary_qn = (
                    boundary_qns.get(side_effect.boundary_name)
                    if side_effect.boundary_name
                    else None
                )
                if boundary_qn:
                    self.ingestor.ensure_relationship_batch(
                        (
                            cs.NodeLabel.SIDE_EFFECT,
                            cs.KEY_QUALIFIED_NAME,
                            side_effect_qn,
                        ),
                        cs.RelationshipType.WITHIN_TRANSACTION,
                        (
                            cs.NodeLabel.TRANSACTION_BOUNDARY,
                            cs.KEY_QUALIFIED_NAME,
                            boundary_qn,
                        ),
                        self._side_effect_metadata(
                            observation=side_effect,
                            relative_path=relative_path,
                            extra={"boundary_name": side_effect.boundary_name},
                        ),
                    )
                    edge_count += 1

            for boundary_name, effects in grouped_effects.items():
                if boundary_name is None:
                    continue
                ordered = sorted(
                    effects,
                    key=lambda item: (item.line_start or 0, item.order_index),
                )
                for before_effect, after_effect in zip(
                    ordered, ordered[1:], strict=False
                ):
                    before_qn = side_effect_qns[
                        (
                            before_effect.symbol_name,
                            before_effect.line_start or 0,
                            before_effect.effect_kind,
                        )
                    ]
                    after_qn = side_effect_qns[
                        (
                            after_effect.symbol_name,
                            after_effect.line_start or 0,
                            after_effect.effect_kind,
                        )
                    ]
                    ordering_payload = build_semantic_metadata(
                        source_parser="transaction_flow_pass",
                        evidence_kind="side_effect_ordering",
                        file_path=relative_path,
                        confidence=0.83,
                        language="python",
                        line_start=before_effect.line_start,
                        line_end=after_effect.line_end,
                        extra={
                            "boundary_name": boundary_name,
                            "before_effect_kind": before_effect.effect_kind,
                            "after_effect_kind": after_effect.effect_kind,
                        },
                    )
                    self.ingestor.ensure_relationship_batch(
                        (
                            cs.NodeLabel.SIDE_EFFECT,
                            cs.KEY_QUALIFIED_NAME,
                            before_qn,
                        ),
                        cs.RelationshipType.BEFORE,
                        (
                            cs.NodeLabel.SIDE_EFFECT,
                            cs.KEY_QUALIFIED_NAME,
                            after_qn,
                        ),
                        ordering_payload,
                    )
                    self.ingestor.ensure_relationship_batch(
                        (
                            cs.NodeLabel.SIDE_EFFECT,
                            cs.KEY_QUALIFIED_NAME,
                            after_qn,
                        ),
                        cs.RelationshipType.AFTER,
                        (
                            cs.NodeLabel.SIDE_EFFECT,
                            cs.KEY_QUALIFIED_NAME,
                            before_qn,
                        ),
                        ordering_payload,
                    )
                    edge_count += 2

        logger.info(
            "TransactionFlowPass: {} boundary node(s), {} side-effect node(s), {} edge(s)",
            boundary_count,
            side_effect_count,
            edge_count,
        )

    def _ensure_transaction_boundary_node(
        self,
        *,
        observation: TransactionBoundaryObservation,
        relative_path: str,
    ) -> str:
        boundary_qn = build_semantic_qn(
            self.project_name,
            "transaction",
            observation.boundary_name,
        )
        props = {
            cs.KEY_QUALIFIED_NAME: boundary_qn,
            cs.KEY_NAME: observation.boundary_name,
            "symbol_qn": observation.symbol_name,
            "boundary_kind": observation.boundary_kind,
            "mechanism": observation.mechanism,
            "has_commit": observation.has_commit,
            "has_rollback": observation.has_rollback,
        }
        props.update(
            build_semantic_metadata(
                source_parser="transaction_flow_pass",
                evidence_kind="transaction_boundary",
                file_path=relative_path,
                confidence=0.86,
                language="python",
                line_start=observation.line_start,
                line_end=observation.line_end,
            )
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.TRANSACTION_BOUNDARY, props)
        return boundary_qn

    def _ensure_side_effect_node(
        self,
        *,
        observation: SideEffectObservation,
        relative_path: str,
    ) -> str:
        side_effect_qn = build_semantic_qn(
            self.project_name,
            "side_effect",
            (
                f"{observation.symbol_name}:{observation.effect_kind}:"
                f"{observation.line_start or 0}:{observation.order_index}"
            ),
        )
        props = {
            cs.KEY_QUALIFIED_NAME: side_effect_qn,
            cs.KEY_NAME: observation.effect_kind,
            "symbol_qn": observation.symbol_name,
            "effect_kind": observation.effect_kind,
            "operation_name": observation.operation_name,
            "order_index": observation.order_index,
            "boundary_name": observation.boundary_name,
        }
        props.update(
            build_semantic_metadata(
                source_parser="transaction_flow_pass",
                evidence_kind=f"side_effect_{observation.effect_kind}",
                file_path=relative_path,
                confidence=0.82,
                language="python",
                line_start=observation.line_start,
                line_end=observation.line_end,
            )
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.SIDE_EFFECT, props)
        return side_effect_qn

    def _resolve_source_spec(
        self,
        module_qn: str,
        observation: TransactionBoundaryObservation | SideEffectObservation,
    ) -> tuple[str, str, str] | None:
        candidates = self.function_registry.find_with_prefix_and_suffix(
            module_qn, observation.symbol_name
        )
        if not candidates:
            suffix = observation.symbol_name.split(cs.SEPARATOR_DOT)[-1]
            candidates = self.function_registry.find_ending_with(suffix)
        preferred_prefix = f"{module_qn}{cs.SEPARATOR_DOT}"
        for candidate in candidates:
            if candidate.startswith(preferred_prefix):
                node_type = self.function_registry.get(candidate)
                if node_type is not None:
                    return (node_type.value, cs.KEY_QUALIFIED_NAME, candidate)
        if candidates:
            node_type = self.function_registry.get(candidates[0])
            if node_type is not None:
                return (node_type.value, cs.KEY_QUALIFIED_NAME, candidates[0])
        return None

    def _boundary_metadata(
        self,
        *,
        observation: TransactionBoundaryObservation,
        relative_path: str,
        extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload = {
            "boundary_name": observation.boundary_name,
            "boundary_kind": observation.boundary_kind,
            "mechanism": observation.mechanism,
            "has_commit": observation.has_commit,
            "has_rollback": observation.has_rollback,
        }
        if extra:
            payload.update(extra)
        return build_semantic_metadata(
            source_parser="transaction_flow_pass",
            evidence_kind="transaction_boundary",
            file_path=relative_path,
            confidence=0.86,
            language="python",
            line_start=observation.line_start,
            line_end=observation.line_end,
            extra=payload,
        )

    def _side_effect_metadata(
        self,
        *,
        observation: SideEffectObservation,
        relative_path: str,
        extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload = {
            "effect_kind": observation.effect_kind,
            "operation_name": observation.operation_name,
            "order_index": observation.order_index,
            "boundary_name": observation.boundary_name,
        }
        if extra:
            payload.update(extra)
        return build_semantic_metadata(
            source_parser="transaction_flow_pass",
            evidence_kind=f"side_effect_{observation.effect_kind}",
            file_path=relative_path,
            confidence=0.82,
            language="python",
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
