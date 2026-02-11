from __future__ import annotations

from typing import cast

from codebase_rag.analysis.analysis_runner import (
    AnalysisRunner,
    NodeRecord,
    RelationshipRecord,
)
from codebase_rag.analysis.modules.base_module import AnalysisContext
from codebase_rag.analysis.modules.dead_code_ai import DeadCodeAIModule


class DummyRunner:
    def _dead_code_report(self, nodes, relationships, node_by_id):
        return {
            "dead_functions": [
                {"qualified_name": "a.b", "name": "a", "path": "a.py"},
                {"qualified_name": "c.d", "name": "c", "path": "c.py"},
            ]
        }


def test_dead_code_ai_verification() -> None:
    def verifier(candidate):
        if candidate["qualified_name"] == "a.b":
            return {"is_dead": True, "confidence": 0.9, "reason": "unused"}
        return {"is_dead": False, "confidence": 0.1, "reason": "used"}

    module = DeadCodeAIModule()
    runner = DummyRunner()
    context = AnalysisContext(
        runner=cast(AnalysisRunner, runner),
        nodes=[NodeRecord(1, ["Function"], {})],
        relationships=[RelationshipRecord(1, 2, "CALLS", {})],
        module_path_map={},
        node_by_id={1: NodeRecord(1, ["Function"], {})},
        module_paths=None,
        incremental_paths=None,
        use_db=False,
        summary={},
        dead_code_verifier=verifier,
    )

    result = module.run(context)

    assert result["status"] == "verified"
    assert len(result["verified_dead_code"]) == 1
    assert result["verified_dead_code"][0]["qualified_name"] == "a.b"
