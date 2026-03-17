from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

from codebase_rag.mcp.tools import MCPToolsRegistry


def test_contract_drift_prefers_contract_linked_testcases(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "contracts.py").write_text(
        "class OrderResponse:\n    pass\n", encoding="utf-8"
    )
    registry = MCPToolsRegistry(
        project_root=str(tmp_path),
        ingestor=MagicMock(),
        cypher_gen=MagicMock(),
    )
    registry._session_state["last_multi_hop_bundle"] = {
        "affected_files": ["contracts.py"],
        "affected_symbols": ["demo.semantic.contract.demo.app.OrderResponse"],
    }

    monkeypatch.setattr(
        registry,
        "_query_semantic_test_candidates",
        lambda **_: (
            [
                {
                    "coverage_kind": "contract",
                    "testcase_qn": "demo.semantic.test_case.tests/test_contracts.py:test_contracts:test_order_response_contract:1",
                    "testcase_name": "test_order_response_contract",
                    "test_file": "tests/test_contracts.py",
                    "framework": "python",
                    "suite_qn": "demo.semantic.test_suite.tests/test_contracts.py:test_contracts",
                    "suite_name": "test_contracts",
                    "matched_target_qn": "demo.semantic.contract.demo.app.OrderResponse",
                    "matched_target_name": "OrderResponse",
                    "matched_target_kind": "Contract",
                }
            ],
            {
                "symbols": [],
                "endpoints": [],
                "contracts": [
                    {
                        "qualified_name": "demo.semantic.contract.demo.app.OrderResponse",
                        "name": "OrderResponse",
                        "path": "contracts.py",
                        "http_method": "",
                        "route_path": "",
                    }
                ],
            },
        ),
    )
    monkeypatch.setattr(
        registry,
        "_query_runtime_coverage_matches",
        lambda **_: [],
    )

    bundle = registry._build_test_selection_bundle()

    assert bundle.get("selection_mode") == "semantic-graph-primary"
    assert bundle.get("candidate_existing_tests") == ["tests/test_contracts.py"]
    semantic_candidates = cast(
        list[dict[str, object]],
        bundle.get("semantic_candidate_testcases", []),
    )
    assert semantic_candidates[0]["coverage_kind"] == "contract"
    target_summary = cast(dict[str, object], bundle.get("semantic_target_summary", {}))
    assert target_summary.get("contract_targets") == 1
    assert bundle.get("semantic_gaps") == []
