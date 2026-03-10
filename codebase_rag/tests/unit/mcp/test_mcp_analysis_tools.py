from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from codebase_rag.mcp.tools import MCPToolsRegistry

pytestmark = [pytest.mark.anyio]


@pytest.fixture(params=["asyncio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def temp_test_repo(tmp_path: Path) -> Path:
    sample_file = tmp_path / "sample.py"
    sample_file.write_text("def hello():\n    return 1\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def mcp_registry(temp_test_repo: Path) -> MCPToolsRegistry:
    mock_ingestor = MagicMock()
    mock_cypher_gen = MagicMock()

    async def mock_generate(query: str) -> str:
        return "MATCH (n) RETURN n"

    mock_cypher_gen.generate = mock_generate

    return MCPToolsRegistry(
        project_root=str(temp_test_repo),
        ingestor=mock_ingestor,
        cypher_gen=mock_cypher_gen,
    )


class TestMCPAnalysisTools:
    async def test_list_analysis_artifacts_returns_empty_when_missing_dir(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        result = await mcp_registry.list_analysis_artifacts()

        assert result.get("count") == 0
        assert result.get("artifacts") == []

    async def test_list_analysis_artifacts_returns_metadata(
        self, mcp_registry: MCPToolsRegistry, temp_test_repo: Path
    ) -> None:
        report_dir = temp_test_repo / "output" / "analysis"
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "security_report.json").write_text("[]", encoding="utf-8")
        (report_dir / "migration_plan.md").write_text("# plan", encoding="utf-8")

        result = await mcp_registry.list_analysis_artifacts()

        assert result.get("count") == 2
        artifacts = cast(list[dict[str, object]], result.get("artifacts", []))
        assert artifacts[0].get("name") == "migration_plan.md"
        assert artifacts[1].get("name") == "security_report.json"
        assert "size_bytes" in artifacts[0]
        assert "modified_at" in artifacts[0]

    async def test_run_analysis_ok(
        self, mcp_registry: MCPToolsRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = {"value": False}

        class DummyRunner:
            def __init__(self, ingestor: object, repo_path: Path) -> None:
                self.ingestor = ingestor
                self.repo_path = repo_path

            def run_all(self) -> None:
                called["value"] = True

        monkeypatch.setattr("codebase_rag.mcp.tools.AnalysisRunner", DummyRunner)

        result = await mcp_registry.run_analysis()

        assert result.get("status") == "ok"
        assert called["value"] is True

    async def test_run_analysis_retries_on_transient_conflict(
        self, mcp_registry: MCPToolsRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = {"count": 0}

        class DummyRunner:
            def __init__(self, ingestor: object, repo_path: Path) -> None:
                self.ingestor = ingestor
                self.repo_path = repo_path

            def run_all(self) -> None:
                calls["count"] += 1
                if calls["count"] == 1:
                    raise Exception("Cannot resolve conflicting transactions")

        monkeypatch.setattr("codebase_rag.mcp.tools.AnalysisRunner", DummyRunner)

        result = await mcp_registry.run_analysis()

        assert result.get("status") == "ok"
        assert calls["count"] == 2

    async def test_get_analysis_report_parses_json(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.return_value = [
            {
                "run_id": "run-1",
                "analysis_timestamp": "2026-01-25T10:00:00Z",
                "analysis_summary": '{"hotspots": 3}',
            }
        ]

        result = await mcp_registry.get_analysis_report()

        assert result.get("run_id") == "run-1"
        assert result.get("summary") == {"hotspots": 3}

    async def test_get_analysis_metric_parses_json(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.return_value = [
            {
                "analysis_timestamp": "2026-01-25T10:00:00Z",
                "metric_value": '{"value": 42}',
            }
        ]

        result = await mcp_registry.get_analysis_metric("complexity")

        assert result.get("metric_name") == "complexity"
        assert result.get("metric_value") == {"value": 42}

    async def test_get_analysis_artifact_reads_file(
        self, mcp_registry: MCPToolsRegistry, temp_test_repo: Path
    ) -> None:
        report_dir = temp_test_repo / "output" / "analysis"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_file = report_dir / "dead_code_report.json"
        report_file.write_text('{"items": []}', encoding="utf-8")

        result = await mcp_registry.get_analysis_artifact("dead_code_report")

        assert result.get("artifact") == "dead_code_report"
        assert "items" in str(result.get("content", ""))

    async def test_get_analysis_artifact_reads_markdown_file_by_base_name(
        self, mcp_registry: MCPToolsRegistry, temp_test_repo: Path
    ) -> None:
        report_dir = temp_test_repo / "output" / "analysis"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_file = report_dir / "migration_plan.md"
        report_file.write_text("# plan", encoding="utf-8")

        result = await mcp_registry.get_analysis_artifact("migration_plan")

        assert result.get("artifact") == "migration_plan"
        assert result.get("filename") == "migration_plan.md"
        assert "# plan" in str(result.get("content", ""))

    async def test_get_analysis_artifact_not_found_returns_available_list(
        self, mcp_registry: MCPToolsRegistry, temp_test_repo: Path
    ) -> None:
        report_dir = temp_test_repo / "output" / "analysis"
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "security_report.json").write_text("[]", encoding="utf-8")

        result = await mcp_registry.get_analysis_artifact("does_not_exist")

        assert result.get("error") == "artifact_not_found"
        assert "security_report.json" in cast(
            list[str], result.get("available_artifacts", [])
        )

    async def test_mcp_analysis_resources_and_resource_read(
        self, mcp_registry: MCPToolsRegistry, temp_test_repo: Path
    ) -> None:
        report_dir = temp_test_repo / "output" / "analysis"
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "security_report.json").write_text(
            '{"summary":{"issues":1},"violations":[{"path":"src/app.py"}]}',
            encoding="utf-8",
        )

        resources = await mcp_registry.list_mcp_resources()
        overview = await mcp_registry.read_mcp_resource("analysis://overview")

        assert any(
            str(item.get("uri", "")) == "analysis://overview" for item in resources
        )
        assert overview.get("artifact_count") == 1

    async def test_mcp_analysis_prompts_and_bundle_lookup(
        self, mcp_registry: MCPToolsRegistry, temp_test_repo: Path
    ) -> None:
        report_dir = temp_test_repo / "output" / "analysis"
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "api_report.json").write_text(
            '{"summary":{"endpoints":2},"endpoints":[{"path":"src/api.py"}]}',
            encoding="utf-8",
        )

        prompts = await mcp_registry.list_mcp_prompts()
        prompt = await mcp_registry.get_mcp_prompt(
            "architecture_review",
            {"goal": "map services and endpoints"},
        )
        bundle = await mcp_registry.architecture_bundle("map services")

        assert any(
            str(item.get("name", "")) == "architecture_review" for item in prompts
        )
        messages = cast(list[dict[str, object]], prompt.get("messages", []))
        assert messages
        assert "Use normalized bundle findings" in str(messages[0].get("text", ""))
        assert bundle.get("bundle") == "architecture_bundle"
        assert "resource_uris" in bundle
