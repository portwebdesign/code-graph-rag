from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from codebase_rag.core import constants as cs


@dataclass
class PolicyResult:
    allowed: bool
    error: str | None = None
    details: dict[str, object] | None = None


class MCPPolicyEngine:
    _WRITE_DENY_KEYWORDS = (
        "detach delete",
        " delete ",
        " drop ",
        " remove ",
        " truncate ",
        " call db.",
        " call apoc.",
    )
    _WRITE_REQUIRED_KEYWORDS = (" create ", " merge ", " set ")
    _NODE_LABEL_PATTERN = re.compile(r"(?<!\[):\s*`?([A-Za-z_][A-Za-z0-9_]*)`?")
    _REL_TYPE_PATTERN = re.compile(r"\[:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?")
    _WRITE_SPLIT_PATTERN = re.compile(
        r"\b(SET|CREATE|MERGE|DELETE|DETACH\s+DELETE|REMOVE)\b",
        re.IGNORECASE,
    )
    _GENERIC_REASONS = {
        "fix",
        "change",
        "update",
        "write",
        "do it",
        "needed",
        "n/a",
        "na",
        "test",
    }
    _INTENT_PATTERNS = (
        "fix",
        "refactor",
        "add test",
        "update dependency",
        "migrate",
        "maintenance",
        "bug",
    )

    def __init__(
        self,
        *,
        active_project_name_getter: Callable[[], str],
        max_write_impact: int = 50,
    ) -> None:
        self._active_project_name_getter = active_project_name_getter
        self._max_write_impact = max_write_impact

    def validate_operation(
        self,
        tool_name: str,
        params: dict[str, object],
        context: dict[str, object] | None = None,
    ) -> PolicyResult:
        context_data = context or {}
        if tool_name == cs.MCPToolName.RUN_CYPHER:
            return self._validate_run_cypher(params, context_data)
        if tool_name == cs.MCPToolName.INDEX_REPOSITORY:
            return self._validate_index_repository(params, context_data)
        if tool_name == cs.MCPToolName.SYNC_GRAPH_UPDATES:
            return self._validate_sync_graph_updates(params)
        if tool_name == cs.MCPToolName.REFACTOR_BATCH:
            return self._validate_refactor_batch(params, context_data)
        return PolicyResult(allowed=True)

    def _validate_run_cypher(
        self,
        params: dict[str, object],
        context: dict[str, object],
    ) -> PolicyResult:
        cypher = str(params.get("cypher", ""))
        parsed_params = params.get("parsed_params", {})
        write = bool(params.get("write", False))
        user_requested = bool(params.get("user_requested", False))
        reason = params.get("reason")

        parsed_params_dict: dict[str, object] = {}
        if isinstance(parsed_params, dict):
            parsed_params_dict = {
                str(key): value for key, value in parsed_params.items()
            }

        scope_error = self.validate_project_scope_policy(cypher, parsed_params_dict)
        if scope_error is not None:
            return PolicyResult(allowed=False, error=scope_error)

        if not write:
            return PolicyResult(allowed=True)

        if not user_requested:
            return PolicyResult(
                allowed=False, error=cs.MCP_RUN_CYPHER_WRITE_REQUIRES_USER_REQUEST
            )

        if not isinstance(reason, str) or not reason.strip():
            return PolicyResult(allowed=False, error=cs.MCP_RUN_CYPHER_REASON_REQUIRED)

        intent_error = self.validate_intent_quality(reason)
        if intent_error is not None:
            return PolicyResult(allowed=False, error=intent_error)

        write_allowlist_error = self.validate_write_allowlist_policy(cypher)
        if write_allowlist_error is not None:
            return PolicyResult(allowed=False, error=write_allowlist_error)

        write_impact = context.get("write_impact")
        if write_impact is None:
            return PolicyResult(
                allowed=False, error=cs.MCP_RUN_CYPHER_DRY_RUN_UNAVAILABLE
            )
        write_impact_value = self._coerce_int(write_impact)

        risk_factor_raw = context.get("risk_factor", 1.0)
        risk_factor = self._coerce_float(risk_factor_raw, default=1.0)
        risk_factor = max(0.2, min(1.5, risk_factor))
        adaptive_limit = max(5, int(self._max_write_impact * risk_factor))

        if write_impact_value > adaptive_limit:
            return PolicyResult(
                allowed=False,
                error=cs.MCP_RUN_CYPHER_WRITE_IMPACT_EXCEEDED.format(
                    impact=write_impact_value,
                    max_impact=adaptive_limit,
                ),
                details={
                    "impact": write_impact_value,
                    "max_impact": adaptive_limit,
                    "risk_factor": risk_factor,
                },
            )

        return PolicyResult(
            allowed=True,
            details={
                "impact": write_impact_value,
                "max_impact": adaptive_limit,
                "risk_factor": risk_factor,
            },
        )

    def _validate_index_repository(
        self,
        params: dict[str, object],
        context: dict[str, object],
    ) -> PolicyResult:
        user_requested = bool(params.get("user_requested", False))
        drift_confirmed = bool(params.get("drift_confirmed", False))
        reason = params.get("reason")
        project_already_indexed = bool(context.get("project_already_indexed", False))

        if not user_requested:
            return PolicyResult(allowed=False, error=cs.MCP_INDEX_REQUIRES_USER_REQUEST)

        if not isinstance(reason, str) or not reason.strip():
            return PolicyResult(allowed=False, error=cs.MCP_INDEX_REASON_REQUIRED)

        if project_already_indexed and not drift_confirmed:
            return PolicyResult(
                allowed=False,
                error=cs.MCP_INDEX_DRIFT_CONFIRMATION_REQUIRED,
            )

        return PolicyResult(allowed=True)

    def _validate_refactor_batch(
        self,
        params: dict[str, object],
        context: dict[str, object],
    ) -> PolicyResult:
        readiness = context.get("readiness", {})
        if not isinstance(readiness, dict):
            return PolicyResult(allowed=False, error=cs.MCP_COMPLETION_GATE_BLOCKED)
        readiness_dict = cast(dict[str, object], readiness)

        completion_gate = readiness_dict.get("completion_gate", {})
        if isinstance(completion_gate, dict) and not bool(
            cast(dict[str, object], completion_gate).get("pass", False)
        ):
            completion_gate_dict = cast(dict[str, object], completion_gate)
            missing_raw = completion_gate_dict.get("missing", [])
            missing = missing_raw if isinstance(missing_raw, list) else []
            missing_text = ", ".join(str(item) for item in missing)
            return PolicyResult(
                allowed=False,
                error=cs.MCP_COMPLETION_GATE_BLOCKED.format(missing=missing_text),
            )

        impact_gate = readiness_dict.get("impact_graph_gate", {})
        if isinstance(impact_gate, dict) and not bool(
            cast(dict[str, object], impact_gate).get("pass", False)
        ):
            return PolicyResult(
                allowed=False,
                error=cs.MCP_IMPACT_GATE_BLOCKED,
            )

        if isinstance(impact_gate, dict) and bool(
            cast(dict[str, object], impact_gate).get("require_plan", False)
        ):
            signals = readiness_dict.get("signals", {})
            signals_dict = (
                cast(dict[str, object], signals) if isinstance(signals, dict) else {}
            )
            plan_done = bool(signals_dict.get("plan_task_completed", False))
            if not plan_done:
                return PolicyResult(
                    allowed=False,
                    error=cs.MCP_PLAN_GATE_BLOCKED,
                )

        test_quality_gate = readiness_dict.get("test_quality_gate", {})
        if isinstance(test_quality_gate, dict) and not bool(
            cast(dict[str, object], test_quality_gate).get("pass", False)
        ):
            test_quality_gate_dict = cast(dict[str, object], test_quality_gate)
            return PolicyResult(
                allowed=False,
                error=cs.MCP_TEST_QUALITY_GATE_BLOCKED.format(
                    score=test_quality_gate_dict.get("score", 0),
                    required=test_quality_gate_dict.get("required", 2),
                ),
            )

        replan_gate = readiness_dict.get("replan_gate", {})
        if isinstance(replan_gate, dict) and not bool(
            cast(dict[str, object], replan_gate).get("pass", True)
        ):
            replan_gate_dict = cast(dict[str, object], replan_gate)
            reasons_raw = replan_gate_dict.get("reasons", [])
            reasons = reasons_raw if isinstance(reasons_raw, list) else []
            reasons_text = ", ".join(str(item) for item in reasons)
            return PolicyResult(
                allowed=False,
                error=cs.MCP_REPLAN_REQUIRED.format(reasons=reasons_text),
            )

        confidence_gate = readiness_dict.get("confidence_gate", {})
        if isinstance(confidence_gate, dict) and not bool(
            cast(dict[str, object], confidence_gate).get("pass", False)
        ):
            confidence_gate_dict = cast(dict[str, object], confidence_gate)
            return PolicyResult(
                allowed=False,
                error=cs.MCP_CONFIDENCE_GATE_BLOCKED.format(
                    score=confidence_gate_dict.get("score", 0),
                    required=confidence_gate_dict.get("required", 2),
                ),
            )

        pattern_gate = readiness_dict.get("pattern_reuse_gate", {})
        if isinstance(pattern_gate, dict) and not bool(
            cast(dict[str, object], pattern_gate).get("pass", False)
        ):
            pattern_gate_dict = cast(dict[str, object], pattern_gate)
            return PolicyResult(
                allowed=False,
                error=cs.MCP_PATTERN_REUSE_BLOCKED.format(
                    score=pattern_gate_dict.get("score", 0),
                    required=pattern_gate_dict.get("required", 70),
                ),
            )

        return PolicyResult(allowed=True)

    def _validate_sync_graph_updates(self, params: dict[str, object]) -> PolicyResult:
        user_requested = bool(params.get("user_requested", False))
        reason = params.get("reason")

        if not user_requested:
            return PolicyResult(
                allowed=False,
                error=cs.MCP_SYNC_GRAPH_REQUIRES_USER_REQUEST,
            )

        if not isinstance(reason, str) or not reason.strip():
            return PolicyResult(
                allowed=False,
                error=cs.MCP_SYNC_GRAPH_REASON_REQUIRED,
            )

        return PolicyResult(allowed=True)

    def validate_project_scope_policy(
        self, cypher_query: str, parsed_params: dict[str, object] | None = None
    ) -> str | None:
        project_name = self._active_project_name_getter()
        is_scoped = self.is_project_scoped_cypher(cypher_query, project_name)
        params = parsed_params or {}
        uses_project_param = "$project_name" in cypher_query.lower()

        if uses_project_param:
            project_param = params.get(cs.KEY_PROJECT_NAME)
            if (
                not isinstance(project_param, str)
                or project_param.strip() != project_name
            ):
                return cs.MCP_RUN_CYPHER_PROJECT_PARAM_MISMATCH.format(
                    project_name=project_name
                )
            is_scoped = True

        if not is_scoped:
            return cs.MCP_RUN_CYPHER_SCOPE_ERROR.format(project_name=project_name)
        return None

    def validate_write_allowlist_policy(self, cypher_query: str) -> str | None:
        normalized = f" {re.sub(r'\\s+', ' ', cypher_query.lower())} "

        for keyword in self._WRITE_DENY_KEYWORDS:
            if keyword in normalized:
                return cs.MCP_RUN_CYPHER_WRITE_FORBIDDEN_KEYWORD.format(
                    keyword=keyword.strip()
                )

        if not any(keyword in normalized for keyword in self._WRITE_REQUIRED_KEYWORDS):
            return cs.MCP_RUN_CYPHER_WRITE_NO_MUTATION

        allowed_labels = {label.value for label in cs.NodeLabel}
        allowed_rel_types = {rel_type.value for rel_type in cs.RelationshipType}

        labels = set(self._NODE_LABEL_PATTERN.findall(cypher_query))
        unknown_labels = sorted(
            label for label in labels if label not in allowed_labels
        )
        if unknown_labels:
            return cs.MCP_RUN_CYPHER_WRITE_UNKNOWN_LABELS.format(
                labels=", ".join(unknown_labels)
            )

        rel_types = set(self._REL_TYPE_PATTERN.findall(cypher_query))
        unknown_rel_types = sorted(
            rel_type for rel_type in rel_types if rel_type not in allowed_rel_types
        )
        if unknown_rel_types:
            return cs.MCP_RUN_CYPHER_WRITE_UNKNOWN_REL_TYPES.format(
                rel_types=", ".join(unknown_rel_types)
            )

        return None

    def validate_intent_quality(self, reason: str) -> str | None:
        normalized = reason.strip().lower()
        if len(normalized) < 12:
            return cs.MCP_RUN_CYPHER_LOW_INTENT_QUALITY

        if normalized in self._GENERIC_REASONS:
            return cs.MCP_RUN_CYPHER_LOW_INTENT_QUALITY

        has_signal = any(pattern in normalized for pattern in self._INTENT_PATTERNS)
        if not has_signal:
            return cs.MCP_RUN_CYPHER_LOW_INTENT_QUALITY

        return None

    @staticmethod
    def _coerce_int(value: object, default: int = 0) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return default
            try:
                return int(float(candidate))
            except ValueError:
                return default
        return default

    @staticmethod
    def _coerce_float(value: object, default: float = 0.0) -> float:
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return default
            try:
                return float(candidate)
            except ValueError:
                return default
        return default

    def estimate_write_impact_query(self, cypher_query: str) -> str | None:
        if "match" not in cypher_query.lower():
            return None
        match = self._WRITE_SPLIT_PATTERN.search(cypher_query)
        if match is None:
            return None

        prefix = cypher_query[: match.start()].strip()
        if not prefix:
            return None
        return f"{prefix} RETURN count(*) AS affected"

    @staticmethod
    def is_project_scoped_cypher(cypher_query: str, project_name: str) -> bool:
        normalized_query = cypher_query.lower()
        normalized_project = project_name.lower()
        scoped_patterns = (
            f"project {{name: '{normalized_project}'}}",
            f'project {{name: "{normalized_project}"}}',
            f"project_name: '{normalized_project}'",
            f'project_name: "{normalized_project}"',
            f"project_name='{normalized_project}'",
            f'project_name="{normalized_project}"',
            f"project_name = '{normalized_project}'",
            f'project_name = "{normalized_project}"',
        )
        if any(pattern in normalized_query for pattern in scoped_patterns):
            return True
        return (
            "project_name" in normalized_query and "$project_name" in normalized_query
        )
