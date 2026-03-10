from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from codebase_rag.core import constants as cs
from codebase_rag.mcp.tools import MCPToolsRegistry
from codebase_rag.services.cypher_guard import CypherGuard
from codebase_rag.services.cypher_templates import CypherTemplateBank
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.services.llm import CypherGenerator


@dataclass(frozen=True)
class ClientProfileBenchmarkCase:
    profile: str
    expected_max_steps: int
    expected_output_mode: str


@dataclass(frozen=True)
class CypherBenchmarkCase:
    prompt: str
    expected_strategy: str
    expected_template: str
    expected_contains: tuple[str, ...]


_CLIENT_PROFILE_CASES = (
    ClientProfileBenchmarkCase("balanced", 8, "code"),
    ClientProfileBenchmarkCase("vscode", 8, "code"),
    ClientProfileBenchmarkCase("cline", 9, "code"),
    ClientProfileBenchmarkCase("copilot", 7, "code"),
    ClientProfileBenchmarkCase("ollama", 5, "plan_json"),
    ClientProfileBenchmarkCase("http", 8, "code"),
)

_CYPHER_CASES = (
    CypherBenchmarkCase(
        prompt="List modules in the active project",
        expected_strategy="direct_template",
        expected_template="module_inventory",
        expected_contains=("MATCH", ":Module", "RETURN"),
    ),
    CypherBenchmarkCase(
        prompt="Show classes in the active project",
        expected_strategy="direct_template",
        expected_template="class_inventory",
        expected_contains=("MATCH", ":Class", "RETURN"),
    ),
    CypherBenchmarkCase(
        prompt="List functions in the active project",
        expected_strategy="direct_template",
        expected_template="function_inventory",
        expected_contains=("MATCH", ":Function", "RETURN"),
    ),
    CypherBenchmarkCase(
        prompt="Show dependency hotspots in the active project",
        expected_strategy="direct_template",
        expected_template="dependency_hotspots",
        expected_contains=("MATCH", "CALLS", "RETURN"),
    ),
)


async def _noop_generate(_query: str) -> str:
    return "MATCH (n) RETURN n;"


def _build_structural_registry(
    repo_path: Path,
    ingestor: MemgraphIngestor,
) -> MCPToolsRegistry:
    return MCPToolsRegistry(
        project_root=str(repo_path),
        ingestor=ingestor,
        cypher_gen=cast(CypherGenerator, SimpleNamespace(generate=_noop_generate)),
    )


def _score_checks(checks: dict[str, bool]) -> float:
    if not checks:
        return 0.0
    passed = sum(1 for value in checks.values() if value)
    return round((passed / len(checks)) * 100.0, 2)


def _average_score(rows: list[dict[str, object]]) -> float:
    if not rows:
        return 0.0
    total = sum(cast(float, row.get("score", 0.0)) for row in rows)
    return round(total / len(rows), 2)


async def run_mcp_benchmarks(
    repo_path: str | Path,
    ingestor: MemgraphIngestor,
    *,
    output_path: str | Path | None = None,
    live_llm: bool = False,
) -> dict[str, object]:
    repo_root = Path(repo_path).resolve()
    template_bank = CypherTemplateBank()
    cypher_guard = CypherGuard()
    live_generator = CypherGenerator() if live_llm else None
    report: dict[str, object] = {
        "status": "ok",
        "generated_at": int(time.time()),
        "repo_path": str(repo_root),
        "project_name": repo_root.name,
        "live_llm": live_llm,
        "client_profiles": [],
        "cypher_generation": [],
    }

    client_results: list[dict[str, object]] = []
    for case in _CLIENT_PROFILE_CASES:
        registry = _build_structural_registry(repo_root, ingestor)
        result = await registry.select_active_project(
            repo_path=str(repo_root),
            client_profile=case.profile,
        )
        session_contract = cast(dict, result.get("session_contract", {}))
        response_profiles = cast(dict, session_contract.get("response_profiles", {}))
        test_generate = cast(dict, response_profiles.get("test_generate", {}))
        client_profile_policy = cast(
            dict, session_contract.get("client_profile_policy", {})
        )
        state_machine = cast(dict, session_contract.get("state_machine", {}))
        repo_semantics = cast(dict, session_contract.get("repo_semantics", {}))

        checks = {
            "select_active_project_ok": result.get("status") == "ok",
            "profile_echoed": (
                isinstance(result.get("active_project"), dict)
                and cast(dict[str, object], result["active_project"]).get(
                    "client_profile"
                )
                == case.profile
            ),
            "session_profile_echoed": session_contract.get("client_profile")
            == case.profile,
            "tool_chain_limit_matches": client_profile_policy.get(
                "tool_chain_max_steps"
            )
            == case.expected_max_steps,
            "test_output_mode_matches": test_generate.get("default_output_mode")
            == case.expected_output_mode,
            "state_machine_present": bool(state_machine.get("enabled", False)),
            "repo_semantics_present": "summary" in repo_semantics,
        }
        client_results.append(
            {
                "profile": case.profile,
                "checks": checks,
                "score": _score_checks(checks),
                "client_profile_policy": client_profile_policy,
                "response_profile": test_generate,
                "state_machine": state_machine,
                "repo_semantics": repo_semantics,
                "active_project": result.get("active_project", {}),
            }
        )

    cypher_results: list[dict[str, object]] = []
    for case in _CYPHER_CASES:
        strategy = template_bank.inspect(case.prompt)
        direct_query = (strategy.query or "") if strategy is not None else ""
        checks = {
            "strategy_matches": strategy is not None
            and strategy.strategy == case.expected_strategy,
            "template_matches": strategy is not None
            and strategy.name == case.expected_template,
            "query_contains_expected_terms": all(
                fragment in direct_query for fragment in case.expected_contains
            ),
        }
        row: dict[str, object] = {
            "prompt": case.prompt,
            "checks": checks,
            "strategy": strategy.strategy if strategy is not None else "llm",
            "template_name": strategy.name if strategy is not None else "",
            "direct_query": direct_query,
        }
        combined_checks = dict(checks)
        guard_result = cypher_guard.rewrite_and_validate(
            direct_query,
            require_project_scope=True,
        )
        row["guard"] = {
            "valid": guard_result.valid,
            "warnings": guard_result.warnings,
            "errors": guard_result.errors,
            "metadata": guard_result.metadata,
            "query": guard_result.query,
        }
        combined_checks["guard_valid"] = guard_result.valid
        if live_generator is not None:
            generated_query = await live_generator.generate(case.prompt)
            row["generated_query"] = generated_query
            live_checks = {
                "generated_query_present": bool(generated_query.strip()),
                "generated_contains_expected_terms": all(
                    fragment in generated_query for fragment in case.expected_contains
                ),
            }
            row["live_llm_checks"] = live_checks
            combined_checks.update(live_checks)
        row["score"] = _score_checks(combined_checks)
        cypher_results.append(row)

    report["client_profiles"] = client_results
    report["cypher_generation"] = cypher_results
    report["summary"] = {
        "client_profile_average": _average_score(client_results),
        "cypher_average": _average_score(cypher_results),
    }
    report["regression_suite"] = {
        "checks": {
            "client_profiles_all_green": all(
                cast(float, row.get("score", 0.0)) >= 100.0 for row in client_results
            ),
            "cypher_guard_all_green": all(
                bool(cast(dict[str, object], row.get("guard", {})).get("valid", False))
                for row in cypher_results
            ),
            "state_machine_published": all(
                bool(
                    cast(dict[str, object], row.get("state_machine", {})).get(
                        "enabled", False
                    )
                )
                for row in client_results
            ),
        }
    }

    if output_path is not None:
        output = Path(output_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding=cs.ENCODING_UTF8,
        )
        report["output_path"] = str(output)

    return report
