from __future__ import annotations

from typing import cast

from codebase_rag.analysis.analysis_runner import AnalysisRunner, NodeRecord
from codebase_rag.analysis.modules.base_module import AnalysisContext
from codebase_rag.analysis.modules.complexity import ComplexityModule


class DummyRunner:
    def _compute_complexity(self, nodes, module_path_map):
        return {"average": 1.0, "max": 1.0, "count": 1.0}


def test_complexity_module_runs() -> None:
    module = ComplexityModule()
    runner = DummyRunner()
    context = AnalysisContext(
        runner=cast(AnalysisRunner, runner),
        nodes=[NodeRecord(1, ["Function"], {"path": "a.py"})],
        relationships=[],
        module_path_map={"a": "a.py"},
        node_by_id={},
        module_paths=None,
        incremental_paths=None,
        use_db=False,
        summary={},
        dead_code_verifier=None,
    )

    result = module.run(context)

    assert result["average"] == 1.0
    assert result["count"] == 1.0
