from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable

from loguru import logger

SEMANTIC_GUARDRAIL_LIMITS: dict[str, int] = {
    "query_observations_per_file": 160,
    "query_observations_per_symbol": 32,
    "query_targets_per_query": 12,
    "event_observations_per_file": 96,
    "event_observations_per_symbol": 24,
    "config_definitions_per_file": 160,
    "config_observations_per_file": 192,
    "config_observations_per_source": 48,
    "transaction_boundaries_per_file": 64,
    "transaction_boundaries_per_symbol": 8,
    "transaction_side_effects_per_file": 160,
    "transaction_side_effects_per_symbol": 32,
}


SEMANTIC_PERFORMANCE_BUDGETS: dict[str, float | int] = {
    "parse_time_seconds": 20.0,
    "rss_growth_kib": 65_536,
    "stress_fixture_total_nodes": 320,
    "stress_fixture_total_relationships": 640,
    "stress_fixture_sql_queries": 32,
    "stress_fixture_cypher_queries": 32,
    "stress_fixture_event_flows": 24,
    "stress_fixture_env_vars": 96,
    "stress_fixture_side_effects": 96,
}


def apply_sequence_guardrail[T](
    items: Iterable[T],
    *,
    limit: int,
    pass_id: str,
    budget_name: str,
    scope: str,
) -> list[T]:
    sequence = list(items)
    observed = len(sequence)
    if observed <= limit:
        return sequence
    dropped = observed - limit
    logger.warning(
        "{} guardrail '{}' trimmed {} item(s) at {} (limit={}, observed={})",
        pass_id,
        budget_name,
        dropped,
        scope,
        limit,
        observed,
    )
    return sequence[:limit]


def apply_grouped_guardrail[T](
    items: Iterable[T],
    *,
    group_key: Callable[[T], str],
    limit_per_group: int,
    pass_id: str,
    budget_name: str,
    scope: str,
) -> list[T]:
    grouped_counts: defaultdict[str, int] = defaultdict(int)
    kept: list[T] = []
    dropped = 0
    for item in items:
        group = group_key(item)
        if grouped_counts[group] >= limit_per_group:
            dropped += 1
            continue
        grouped_counts[group] += 1
        kept.append(item)
    if dropped:
        logger.warning(
            "{} guardrail '{}' trimmed {} item(s) at {} (limit_per_group={})",
            pass_id,
            budget_name,
            dropped,
            scope,
            limit_per_group,
        )
    return kept
