from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from codebase_rag.mcp.tools import MCPToolsRegistry

pytestmark = [pytest.mark.anyio]


@pytest.fixture(params=["asyncio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def temp_project_root(tmp_path: Path) -> Path:
    sample_file = tmp_path / "sample.py"
    sample_file.write_text("value = 1\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def mcp_registry(temp_project_root: Path) -> MCPToolsRegistry:
    mock_ingestor = MagicMock()
    mock_cypher_gen = MagicMock()

    async def mock_generate(query: str) -> str:
        return "MATCH (n) RETURN n"

    mock_cypher_gen.generate = mock_generate

    return MCPToolsRegistry(
        project_root=str(temp_project_root),
        ingestor=mock_ingestor,
        cypher_gen=mock_cypher_gen,
    )


class TestMCPNewTools:
    async def test_plan_task_returns_payload(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def fake_plan(goal: str, context: str | None = None) -> object:
            return SimpleNamespace(
                status="ok",
                content={
                    "summary": "do it",
                    "steps": ["step-1"],
                    "risks": [],
                    "tests": [],
                },
            )

        mcp_registry._planner_agent.plan = fake_plan

        result = await mcp_registry.plan_task("demo", context="ctx")

        assert result.get("status") == "ok"
        assert result.get("summary") == "do it"

    async def test_impact_graph_requires_target(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        result = await mcp_registry.impact_graph()

        assert result.get("error") == "missing_target"

    async def test_impact_graph_returns_results(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.return_value = [{"source": "a", "target": "b", "depth": 1}]

        result = await mcp_registry.impact_graph(qualified_name="demo.fn")

        assert result.get("count") == 1

    async def test_apply_diff_safe_blocks_sensitive_path(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        payload = json.dumps([{"target_code": "a", "replacement_code": "b"}])

        result = await mcp_registry.apply_diff_safe(".env", payload)

        assert result.get("error") == "sensitive_path"

    async def test_apply_diff_safe_rejects_large_diff(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        large_text = "a\n" * 400
        payload = json.dumps([{"target_code": large_text, "replacement_code": "b"}])

        result = await mcp_registry.apply_diff_safe("sample.py", payload)

        assert result.get("error") == "diff_limit_exceeded"

    async def test_apply_diff_safe_ok(self, mcp_registry: MCPToolsRegistry) -> None:
        payload = json.dumps(
            [{"target_code": "value = 1", "replacement_code": "value = 2"}]
        )

        async def fake_replace(**_: object) -> str:
            return "ok"

        mcp_registry._file_editor_tool.function = fake_replace

        result = await mcp_registry.apply_diff_safe("sample.py", payload)

        assert result.get("status") == "ok"

    async def test_run_analysis_subset_parses_modules(
        self, mcp_registry: MCPToolsRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = {"modules": None}

        class DummyRunner:
            def __init__(self, ingestor: object, repo_path: Path) -> None:
                self.ingestor = ingestor
                self.repo_path = repo_path

            def run_modules(self, modules: set[str] | None = None) -> None:
                called["modules"] = modules

        monkeypatch.setattr("codebase_rag.mcp.tools.AnalysisRunner", DummyRunner)

        result = await mcp_registry.run_analysis_subset('["security", "usage"]')

        assert result.get("status") == "ok"
        assert called["modules"] == {"security", "usage"}

    async def test_security_scan_returns_summary(
        self, mcp_registry: MCPToolsRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class DummyRunner:
            def __init__(self, ingestor: object, repo_path: Path) -> None:
                self.ingestor = ingestor
                self.repo_path = repo_path

            def run_modules(self, modules: set[str] | None = None) -> None:
                return None

        monkeypatch.setattr("codebase_rag.mcp.tools.AnalysisRunner", DummyRunner)

        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.return_value = [
            {
                "analysis_timestamp": "2026-01-28T12:00:00Z",
                "analysis_summary": json.dumps(
                    {
                        "security": {"issues": 1},
                        "secret_scan": {"findings": []},
                        "sast_taint_tracking": {"taints": 0},
                    }
                ),
            }
        ]

        result = await mcp_registry.security_scan()

        assert result.get("security") == {"issues": 1}
        assert result.get("secret_scan") == {"findings": []}
        assert result.get("sast_taint_tracking") == {"taints": 0}

    async def test_test_generate_returns_content(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def fake_run(task: str) -> object:
            return SimpleNamespace(status="ok", content="run tests")

        mcp_registry._test_agent.run = fake_run

        result = await mcp_registry.test_generate("add tests", context="ctx")

        assert result.get("status") == "ok"
        assert result.get("content") == "run tests"

    async def test_memory_add_and_list(self, mcp_registry: MCPToolsRegistry) -> None:
        result_add = await mcp_registry.memory_add("decision", tags="alpha,beta")
        result_list = await mcp_registry.memory_list(limit=10)

        assert result_add.get("status") == "ok"
        count = cast(int, result_list.get("count", 0))
        assert count >= 1

    async def test_refactor_batch_ok(self, mcp_registry: MCPToolsRegistry) -> None:
        payload = json.dumps(
            [
                {
                    "file_path": "sample.py",
                    "chunks": [
                        {"target_code": "value = 1", "replacement_code": "value = 2"}
                    ],
                }
            ]
        )

        async def fake_replace(**_: object) -> str:
            return "ok"

        mcp_registry._file_editor_tool.function = fake_replace

        result = await mcp_registry.refactor_batch(payload)

        assert result.get("status") == "ok"

    async def test_performance_hotspots_returns_summary(
        self, mcp_registry: MCPToolsRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class DummyRunner:
            def __init__(self, ingestor: object, repo_path: Path) -> None:
                self.ingestor = ingestor
                self.repo_path = repo_path

            def run_modules(self, modules: set[str] | None = None) -> None:
                return None

        monkeypatch.setattr("codebase_rag.mcp.tools.AnalysisRunner", DummyRunner)

        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.return_value = [
            {
                "analysis_timestamp": "2026-01-28T12:00:00Z",
                "analysis_summary": json.dumps({"performance_hotspots": ["a"]}),
            }
        ]

        result = await mcp_registry.performance_hotspots()

        assert result.get("performance_hotspots") == ["a"]
