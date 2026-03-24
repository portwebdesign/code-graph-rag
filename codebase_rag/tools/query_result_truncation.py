from __future__ import annotations

import json
import math

from codebase_rag.data_models.types_defs import ResultRow

DEFAULT_MAX_QUERY_RESULT_ROWS = 50
DEFAULT_MAX_QUERY_RESULT_TOKENS = 3000
DEFAULT_MAX_QUERY_VALUE_CHARS = 800
TOKEN_ESTIMATE_CHARS_PER_TOKEN = 4


def estimate_token_count(value: object) -> int:
    try:
        payload = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    except TypeError:
        payload = str(value)

    normalized = payload.strip()
    if not normalized:
        return 0
    return max(1, math.ceil(len(normalized) / TOKEN_ESTIMATE_CHARS_PER_TOKEN))


def _truncate_string_value(value: str, max_chars: int) -> tuple[str, bool]:
    if len(value) <= max_chars:
        return value, False

    overflow = len(value) - max_chars
    suffix = f"... [truncated {overflow} chars]"
    safe_limit = max(0, max_chars - len(suffix))
    return f"{value[:safe_limit]}{suffix}", True


def truncate_result_row(
    row: ResultRow,
    max_value_chars: int = DEFAULT_MAX_QUERY_VALUE_CHARS,
) -> tuple[ResultRow, bool]:
    truncated = False
    truncated_row: ResultRow = {}
    for key, value in row.items():
        if isinstance(value, str):
            truncated_value, value_truncated = _truncate_string_value(
                value, max_value_chars
            )
            truncated_row[key] = truncated_value
            truncated = truncated or value_truncated
            continue

        truncated_row[key] = value

    return truncated_row, truncated


def truncate_query_results(
    results: list[ResultRow],
    max_rows: int = DEFAULT_MAX_QUERY_RESULT_ROWS,
    max_tokens: int = DEFAULT_MAX_QUERY_RESULT_TOKENS,
    max_value_chars: int = DEFAULT_MAX_QUERY_VALUE_CHARS,
) -> tuple[list[ResultRow], int, bool]:
    if not results:
        return [], 0, False

    total_results = len(results)
    token_budget_used = 0
    truncated_results: list[ResultRow] = []
    truncated = False

    for row in results:
        if len(truncated_results) >= max_rows:
            truncated = True
            break

        safe_row, row_was_truncated = truncate_result_row(row, max_value_chars)
        row_tokens = estimate_token_count(safe_row)
        if truncated_results and token_budget_used + row_tokens > max_tokens:
            truncated = True
            break

        truncated_results.append(safe_row)
        token_budget_used += row_tokens
        truncated = truncated or row_was_truncated

    if len(truncated_results) < total_results:
        truncated = True

    return truncated_results, total_results, truncated
