from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, cast

from loguru import logger

from ..utils.git_delta import filter_existing, get_git_delta
from .analysis_runner import AnalysisRunner


def _normalize_modules(modules: list[str] | None) -> list[str] | None:
    if not modules:
        return None
    normalized: list[str] = []
    for item in modules:
        parts = [part.strip() for part in item.split(",") if part.strip()]
        normalized.extend(parts)
    return normalized or None


def _timed_run(
    runner: AnalysisRunner,
    modules: set[str] | None,
    incremental_paths: list[str] | None = None,
) -> float:
    start = time.perf_counter()
    runner.run_modules(modules, incremental_paths=incremental_paths)
    return time.perf_counter() - start


def _summarize_durations(durations: list[float]) -> dict[str, float]:
    if not durations:
        return {"avg_seconds": 0.0, "min_seconds": 0.0, "max_seconds": 0.0}
    return {
        "avg_seconds": round(sum(durations) / len(durations), 4),
        "min_seconds": round(min(durations), 4),
        "max_seconds": round(max(durations), 4),
    }


def run_analysis_benchmarks(
    ingestor: Any,
    repo_path: Path,
    base_rev: str | None = None,
    runs: int = 1,
    modules: list[str] | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    original_cache = os.getenv("CODEGRAPH_ANALYSIS_CACHE")
    original_fast = os.getenv("CODEGRAPH_ANALYSIS_INCREMENTAL_FAST")
    if base_rev and original_cache is None:
        os.environ["CODEGRAPH_ANALYSIS_CACHE"] = "1"
    if base_rev and original_fast is None:
        os.environ["CODEGRAPH_ANALYSIS_INCREMENTAL_FAST"] = "1"
    runner = AnalysisRunner(ingestor, repo_path)
    modules_list = _normalize_modules(modules)
    modules_set = set(modules_list) if modules_list else None

    full_durations: list[float] = []
    for _ in range(max(runs, 1)):
        full_durations.append(_timed_run(runner, modules_set))

    full_result = {
        "runs": len(full_durations),
        **_summarize_durations(full_durations),
    }

    incremental_result: dict[str, Any]
    incremental_paths: list[str] | None = None
    if base_rev:
        changed, _ = get_git_delta(repo_path, base_rev)
        changed = filter_existing(changed)
        incremental_paths = [str(path.relative_to(repo_path)) for path in changed]
    if incremental_paths is None:
        incremental_result = {
            "status": "skipped",
            "reason": "base_rev_missing",
        }
    else:
        incremental_durations: list[float] = []
        for _ in range(max(runs, 1)):
            incremental_durations.append(
                _timed_run(runner, modules_set, incremental_paths=incremental_paths)
            )
        incremental_result = {
            "runs": len(incremental_durations),
            "changed_files": len(incremental_paths),
            "base_rev": base_rev,
            **_summarize_durations(incremental_durations),
        }

    dead_code_result = runner.run_modules({"dead_code_ai"})
    dead_code_ai = cast(dict[str, Any], dead_code_result.get("dead_code_ai", {}))
    candidates = cast(list[Any], dead_code_ai.get("candidates", []))
    verified = cast(list[Any], dead_code_ai.get("verified_dead_code", []))
    false_positive_rate = None
    if candidates:
        false_positive_rate = round(1 - (len(verified) / len(candidates)), 4)

    dead_code_summary = {
        "status": dead_code_ai.get("status"),
        "candidates": len(candidates),
        "verified": len(verified),
        "false_positive_rate": false_positive_rate,
    }

    results = {
        "full": full_result,
        "incremental": incremental_result,
        "dead_code_ai": dead_code_summary,
    }

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        logger.info("Benchmark report written to %s", output_path)

    if base_rev and original_cache is None:
        os.environ.pop("CODEGRAPH_ANALYSIS_CACHE", None)
    if base_rev and original_fast is None:
        os.environ.pop("CODEGRAPH_ANALYSIS_INCREMENTAL_FAST", None)

    return results
