from __future__ import annotations

from typing import cast

from codebase_rag.analysis.analysis_runner import AnalysisRunner
from codebase_rag.analysis.modules.base_module import AnalysisContext
from codebase_rag.analysis.modules.framework_matcher import FrameworkMatcherModule


class DummyIngestor:
    def fetch_all(self, query, params):
        text = query.lower()
        if "models.py" in text:
            return [{"qualified_name": "app.models.User"}]
        if "views.py" in text:
            return [{"qualified_name": "app.views.index"}]
        if "urls.py" in text:
            return [{"qualified_name": "app.urls"}]
        if "middleware" in text and "class" in text:
            return [{"qualified_name": "app.middleware.Auth"}]
        if "@app.route" in text:
            return [{"qualified_name": "app.routes.home"}]
        if "blueprint" in text:
            return [{"qualified_name": "app.routes.bp"}]
        if "before_request" in text:
            return [{"qualified_name": "app.hooks.before_request"}]
        if "@app.get" in text:
            return [{"qualified_name": "app.api.get"}]
        if "depends" in text:
            return [{"qualified_name": "app.api.dep"}]
        return []


class DummyRunner:
    def __init__(self):
        self.ingestor = DummyIngestor()


def test_framework_matcher_runs() -> None:
    module = FrameworkMatcherModule()
    runner = DummyRunner()
    context = AnalysisContext(
        runner=cast(AnalysisRunner, runner),
        nodes=[],
        relationships=[],
        module_path_map={},
        node_by_id={},
        module_paths=None,
        incremental_paths=None,
        use_db=True,
        summary={},
        dead_code_verifier=None,
    )

    result = module.run(context)

    assert result["django"]["total_components"] == 4
    assert result["flask"]["total_components"] == 3
    assert result["fastapi"]["total_components"] == 2
