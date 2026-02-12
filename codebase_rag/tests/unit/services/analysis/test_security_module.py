from __future__ import annotations

from typing import cast

from codebase_rag.analysis.analysis_runner import AnalysisRunner, NodeRecord
from codebase_rag.analysis.modules.base_module import AnalysisContext
from codebase_rag.analysis.modules.security import SecurityModule


class DummyRunner:
    def _security_scan(self, nodes):
        return {"issues": 0}


def test_security_module_runs() -> None:
    module = SecurityModule()
    runner = DummyRunner()
    context = AnalysisContext(
        runner=cast(AnalysisRunner, runner),
        nodes=[NodeRecord(1, ["Function"], {})],
        relationships=[],
        module_path_map={},
        node_by_id={},
        module_paths=None,
        incremental_paths=None,
        use_db=False,
        summary={},
        dead_code_verifier=None,
    )

    result = module.run(context)

    assert result["issues"] == 0
