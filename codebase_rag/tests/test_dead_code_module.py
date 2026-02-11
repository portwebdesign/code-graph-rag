from __future__ import annotations

from typing import cast

from codebase_rag.analysis.analysis_runner import (
    AnalysisRunner,
    NodeRecord,
    RelationshipRecord,
)
from codebase_rag.analysis.modules.base_module import AnalysisContext
from codebase_rag.analysis.modules.dead_code import DeadCodeModule


class DummyRunner:
    def _dead_code_report_db(self, module_paths):
        return {"dead_functions": [{"qualified_name": "a.b"}]}

    def _dead_code_report(self, nodes, relationships, node_by_id):
        return {"dead_functions": [{"qualified_name": "c.d"}]}


def test_dead_code_module_db() -> None:
    module = DeadCodeModule()
    runner = DummyRunner()
    context = AnalysisContext(
        runner=cast(AnalysisRunner, runner),
        nodes=[],
        relationships=[RelationshipRecord(1, 2, "CALLS", {})],
        module_path_map={},
        node_by_id={1: NodeRecord(1, ["Function"], {})},
        module_paths=["a.py"],
        incremental_paths=None,
        use_db=True,
        summary={},
        dead_code_verifier=None,
    )

    result = module.run(context)

    assert result["dead_functions"][0]["qualified_name"] == "a.b"


def test_dead_code_module_graph() -> None:
    module = DeadCodeModule()
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
        dead_code_verifier=None,
    )

    result = module.run(context)

    assert result["dead_functions"][0]["qualified_name"] == "c.d"
