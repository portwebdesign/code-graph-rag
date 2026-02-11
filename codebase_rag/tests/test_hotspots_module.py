from __future__ import annotations

from typing import cast

from codebase_rag.analysis.analysis_runner import (
    AnalysisRunner,
    NodeRecord,
    RelationshipRecord,
)
from codebase_rag.analysis.modules.base_module import AnalysisContext
from codebase_rag.analysis.modules.hotspots import HotspotsModule


class DummyRunner:
    def _performance_hotspots(self, nodes, relationships, node_by_id):
        return {"hotspots": []}


def test_hotspots_module_runs() -> None:
    module = HotspotsModule()
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

    assert result["hotspots"] == []
