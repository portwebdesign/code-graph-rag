from __future__ import annotations

from typing import cast

from codebase_rag.analysis.analysis_runner import AnalysisRunner, NodeRecord
from codebase_rag.analysis.modules.base_module import AnalysisContext
from codebase_rag.analysis.modules.ml_insights import MLInsightsModule


class DummyRunner:
    pass


def test_ml_insights_module_runs(monkeypatch) -> None:
    def fake_generate(summary):
        return {"status": "ok", "suggestions": []}

    monkeypatch.setattr(
        "codebase_rag.analysis.modules.ml_insights.generate_ml_insights",
        fake_generate,
    )

    module = MLInsightsModule()
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
        summary={"migration_plan": {"plan": "ok"}},
        dead_code_verifier=None,
    )

    result = module.run(context)

    assert result["status"] == "ok"
