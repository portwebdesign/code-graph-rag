from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from loguru import logger
from tree_sitter import Node

from codebase_rag.core import constants as cs
from codebase_rag.core.event_flow_identity import build_event_flow_canonical_key
from codebase_rag.parsers.pipeline.python_event_flows import (
    EventFlowObservation,
    extract_python_event_flows,
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


class EventFlowPass:
    """Emits first-wave Python event/outbox/consumer semantic graph edges."""

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
        self.enabled = is_semantic_pass_enabled("CODEGRAPH_EVENT_FLOW_SEMANTICS")

    def process_ast_cache(
        self,
        ast_items: Iterable[tuple[Path, tuple[Node, cs.SupportedLanguage]]],
    ) -> None:
        if not self.enabled:
            return

        flow_count = 0
        queue_count = 0
        edge_count = 0

        for file_path, (_, language) in ast_items:
            if language != cs.SupportedLanguage.PYTHON or file_path.suffix != cs.EXT_PY:
                continue
            source = self._read_source(file_path)
            if source is None:
                continue
            observations = extract_python_event_flows(source)
            if not observations:
                continue
            module_qn = self._module_qn_for_path(file_path)
            relative_path = self._relative_path(file_path)
            observations = apply_grouped_guardrail(
                observations,
                group_key=lambda observation: observation.symbol_name,
                limit_per_group=SEMANTIC_GUARDRAIL_LIMITS[
                    "event_observations_per_symbol"
                ],
                pass_id="event_flow_semantics",
                budget_name="event_observations_per_symbol",
                scope=relative_path,
            )
            observations = apply_sequence_guardrail(
                observations,
                limit=SEMANTIC_GUARDRAIL_LIMITS["event_observations_per_file"],
                pass_id="event_flow_semantics",
                budget_name="event_observations_per_file",
                scope=relative_path,
            )
            for observation in observations:
                flow_qn = self._ensure_event_flow_node(
                    observation=observation,
                    relative_path=relative_path,
                )
                flow_count += 1
                source_spec = self._resolve_source_spec(module_qn, observation)
                stage_rel = self._relationship_for_stage(observation.stage)
                if stage_rel and source_spec is not None:
                    self.ingestor.ensure_relationship_batch(
                        source_spec,
                        stage_rel,
                        (cs.NodeLabel.EVENT_FLOW, cs.KEY_QUALIFIED_NAME, flow_qn),
                        self._edge_metadata(
                            observation=observation,
                            relative_path=relative_path,
                        ),
                    )
                    edge_count += 1
                if observation.channel_name:
                    queue_qn = self._ensure_queue_node(
                        queue_name=observation.channel_name,
                        queue_role="primary",
                        mechanism=observation.mechanism,
                        relative_path=relative_path,
                        observation=observation,
                    )
                    queue_count += 1
                    self.ingestor.ensure_relationship_batch(
                        (cs.NodeLabel.EVENT_FLOW, cs.KEY_QUALIFIED_NAME, flow_qn),
                        cs.RelationshipType.USES_QUEUE,
                        (cs.NodeLabel.QUEUE, cs.KEY_QUALIFIED_NAME, queue_qn),
                        self._edge_metadata(
                            observation=observation,
                            relative_path=relative_path,
                            extra={"queue_role": "primary"},
                        ),
                    )
                    edge_count += 1
                if observation.stage == "consume" and source_spec is not None:
                    self.ingestor.ensure_relationship_batch(
                        (cs.NodeLabel.EVENT_FLOW, cs.KEY_QUALIFIED_NAME, flow_qn),
                        cs.RelationshipType.USES_HANDLER,
                        source_spec,
                        self._edge_metadata(
                            observation=observation,
                            relative_path=relative_path,
                            extra={"handler_name": observation.symbol_name},
                        ),
                    )
                    edge_count += 1
                if observation.dlq_name:
                    dlq_qn = self._ensure_queue_node(
                        queue_name=observation.dlq_name,
                        queue_role="dlq",
                        mechanism=observation.mechanism,
                        relative_path=relative_path,
                        observation=observation,
                    )
                    queue_count += 1
                    self.ingestor.ensure_relationship_batch(
                        (cs.NodeLabel.EVENT_FLOW, cs.KEY_QUALIFIED_NAME, flow_qn),
                        cs.RelationshipType.USES_QUEUE,
                        (cs.NodeLabel.QUEUE, cs.KEY_QUALIFIED_NAME, dlq_qn),
                        self._edge_metadata(
                            observation=observation,
                            relative_path=relative_path,
                            extra={"queue_role": "dlq"},
                        ),
                    )
                    edge_count += 1
                    if source_spec is not None:
                        self.ingestor.ensure_relationship_batch(
                            source_spec,
                            cs.RelationshipType.WRITES_DLQ,
                            (cs.NodeLabel.QUEUE, cs.KEY_QUALIFIED_NAME, dlq_qn),
                            self._edge_metadata(
                                observation=observation,
                                relative_path=relative_path,
                                extra={"queue_role": "dlq"},
                            ),
                        )
                        edge_count += 1

        logger.info(
            "EventFlowPass: {} flow(s), {} queue(s), {} edge(s)",
            flow_count,
            queue_count,
            edge_count,
        )

    def _ensure_event_flow_node(
        self,
        *,
        observation: EventFlowObservation,
        relative_path: str,
    ) -> str:
        canonical_key = self._canonical_key(observation)
        flow_qn = build_semantic_qn(self.project_name, "event_flow", canonical_key)
        display_name = (
            observation.event_name or observation.channel_name or canonical_key
        )
        props = {
            cs.KEY_QUALIFIED_NAME: flow_qn,
            cs.KEY_NAME: display_name,
            cs.KEY_FRAMEWORK: "python",
            "canonical_key": canonical_key,
            "event_type": observation.event_type or observation.event_name or "event",
            "event_name": observation.event_name or display_name,
            "channel_name": observation.channel_name,
            "dlq_name": observation.dlq_name,
            "has_outbox": observation.stage == "outbox",
            "has_publish": observation.stage == "publish",
            "has_consumer": observation.stage == "consume",
            "has_replay": observation.stage == "replay",
        }
        props.update(
            build_semantic_metadata(
                source_parser="event_flow_pass",
                evidence_kind=f"event_{observation.stage}",
                file_path=relative_path,
                confidence=0.9,
                language="python",
                line_start=observation.line_start,
                line_end=observation.line_end,
                extra={"mechanism": observation.mechanism},
            )
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.EVENT_FLOW, props)
        return flow_qn

    def _ensure_queue_node(
        self,
        *,
        queue_name: str,
        queue_role: str,
        mechanism: str | None,
        relative_path: str,
        observation: EventFlowObservation,
    ) -> str:
        queue_qn = build_semantic_qn(self.project_name, "queue", queue_name)
        props = {
            cs.KEY_QUALIFIED_NAME: queue_qn,
            cs.KEY_NAME: queue_name,
            cs.KEY_FRAMEWORK: "python",
            "queue_name": queue_name,
            "queue_role_hint": queue_role,
            "engine": self._infer_queue_engine(queue_name, mechanism),
        }
        props.update(
            build_semantic_metadata(
                source_parser="event_flow_pass",
                evidence_kind="event_queue",
                file_path=relative_path,
                confidence=0.88,
                language="python",
                line_start=observation.line_start,
                line_end=observation.line_end,
                extra={"mechanism": mechanism},
            )
        )
        self.ingestor.ensure_node_batch(cs.NodeLabel.QUEUE, props)
        return queue_qn

    def _resolve_source_spec(
        self,
        module_qn: str,
        observation: EventFlowObservation,
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

    def _edge_metadata(
        self,
        *,
        observation: EventFlowObservation,
        relative_path: str,
        extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload = {
            "canonical_key": self._canonical_key(observation),
            "event_type": observation.event_type or observation.event_name or "event",
            "event_name": observation.event_name,
            "channel_name": observation.channel_name,
            "dlq_name": observation.dlq_name,
            "mechanism": observation.mechanism,
            "stage": observation.stage,
        }
        if extra:
            payload.update(extra)
        return build_semantic_metadata(
            source_parser="event_flow_pass",
            evidence_kind=f"event_{observation.stage}",
            file_path=relative_path,
            confidence=0.9,
            language="python",
            line_start=observation.line_start,
            line_end=observation.line_end,
            extra=payload,
        )

    @staticmethod
    def _relationship_for_stage(stage: str) -> str | None:
        return {
            "outbox": cs.RelationshipType.WRITES_OUTBOX,
            "publish": cs.RelationshipType.PUBLISHES_EVENT,
            "consume": cs.RelationshipType.CONSUMES_EVENT,
            "replay": cs.RelationshipType.REPLAYS_EVENT,
        }.get(stage)

    @staticmethod
    def _infer_queue_engine(queue_name: str, mechanism: str | None) -> str | None:
        haystack = " ".join(filter(None, (queue_name, mechanism or ""))).lower()
        if any(token in haystack for token in ("redis", "stream", "xadd")):
            return "redis-streams"
        if "kafka" in haystack:
            return "kafka"
        if any(token in haystack for token in ("rabbit", "amqp")):
            return "rabbitmq"
        if "bull" in haystack:
            return "bullmq"
        if "sqs" in haystack:
            return "sqs"
        return None

    @staticmethod
    def _canonical_key(observation: EventFlowObservation) -> str:
        return build_event_flow_canonical_key(
            event_name=observation.event_name,
            channel_name=observation.channel_name,
            fallback_name=observation.symbol_name,
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
