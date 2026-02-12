from __future__ import annotations

from typing import cast

from codebase_rag.analysis.analysis_runner import (
    AnalysisRunner,
    NodeRecord,
    RelationshipRecord,
)
from codebase_rag.analysis.modules.base_module import AnalysisContext
from codebase_rag.analysis.modules.dependencies import DependenciesModule


class DummyRunner:
    def _dependency_risk(self, nodes, relationships, node_by_id):
        return {"risk": 0}


def test_dependencies_module_runs() -> None:
    module = DependenciesModule()
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

    assert result["risk"] == 0
