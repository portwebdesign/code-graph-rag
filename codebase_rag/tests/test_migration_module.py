from __future__ import annotations

from typing import cast

from codebase_rag.analysis.analysis_runner import (
    AnalysisRunner,
    NodeRecord,
    RelationshipRecord,
)
from codebase_rag.analysis.modules.base_module import AnalysisContext
from codebase_rag.analysis.modules.migration import MigrationModule


class DummyRunner:
    def _migration_plan(
        self,
        nodes,
        relationships,
        node_by_id,
        test_coverage_proxy,
        dependency_risk,
        performance_hotspots,
        layering_violations,
    ):
        return {"plan": "ok"}


def test_migration_module_runs() -> None:
    module = MigrationModule()
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
        summary={
            "test_coverage_proxy": {"coverage": 0},
            "dependency_risk": {"risk": 0},
            "performance_hotspots": {"hotspots": []},
            "layering_violations": {"violations": []},
        },
        dead_code_verifier=None,
    )

    result = module.run(context)

    assert result["plan"] == "ok"
