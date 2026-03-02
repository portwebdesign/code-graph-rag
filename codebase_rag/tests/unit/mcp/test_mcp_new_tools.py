from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from codebase_rag.agents import MCP_SYSTEM_PROMPT
from codebase_rag.core.config import settings
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
    def test_registry_defaults_to_central_system_prompt(
        self,
        mcp_registry: MCPToolsRegistry,
    ) -> None:
        assert mcp_registry._orchestrator_prompt == MCP_SYSTEM_PROMPT.strip()

    def test_registry_rejects_non_canonical_system_prompt(
        self,
        temp_project_root: Path,
    ) -> None:
        with pytest.raises(ValueError):
            MCPToolsRegistry(
                project_root=str(temp_project_root),
                ingestor=MagicMock(),
                cypher_gen=MagicMock(),
                orchestrator_prompt="custom prompt",
            )

    async def test_select_active_project_returns_preflight_context(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        project_name = Path(mcp_registry.project_root).resolve().name

        ingestor.list_projects.return_value = [project_name, "other-project"]
        ingestor.fetch_all.side_effect = [
            [{"count": 3}],
            [{"count": 2}],
            [{"count": 5}],
            [{"analysis_timestamp": "2026-03-01T10:00:00Z"}],
            [
                {
                    "from_node_type": "Module",
                    "relationship_type": "DEFINES",
                    "to_node_type": "Class",
                }
            ],
        ]

        result = await mcp_registry.select_active_project()

        assert result.get("status") == "ok"
        active = cast(dict[str, object], result.get("active_project", {}))
        assert active.get("name") == project_name
        assert active.get("indexed") is True
        policy = cast(dict[str, object], result.get("policy", {}))
        assert policy.get("run_cypher_write_allowlist_enforced") is True
        preflight = cast(dict[str, object], result.get("session_preflight", {}))
        assert preflight.get("status") == "ok"
        rows = preflight.get("rows", 0)
        assert isinstance(rows, int)
        assert rows >= 1
        schema_json = cast(dict[str, object], preflight.get("schema_summary_json", {}))
        summary_rows = schema_json.get("schema_summary", [])
        assert isinstance(summary_rows, list)
        preview_rows = preflight.get("schema_summary_preview", [])
        assert isinstance(preview_rows, list)
        preview_row_count = preflight.get("preview_row_count", 0)
        assert isinstance(preview_row_count, int)
        assert preview_row_count >= 1
        schema_md = preflight.get("schema_summary_markdown", "")
        assert isinstance(schema_md, str)
        assert "| from_node_type | relationship_type | to_node_type |" in schema_md

    def test_preflight_gate_blocks_non_exempt_tools_before_selection(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        error = mcp_registry.get_preflight_gate_error("query_code_graph")
        assert isinstance(error, str)
        assert "session_preflight_required" in error

    def test_preflight_gate_allows_exempt_tools(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        assert mcp_registry.get_preflight_gate_error("list_projects") is None
        assert mcp_registry.get_preflight_gate_error("select_active_project") is None

    async def test_detect_project_drift_returns_payload(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.side_effect = [[{"count": 0}], [{"count": 0}]]

        result = await mcp_registry.detect_project_drift()

        assert result.get("status") == "ok"
        drift = cast(dict[str, object], result.get("drift", {}))
        assert "drift_detected" in drift

    async def test_get_execution_readiness_returns_gates(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        result = await mcp_registry.get_execution_readiness()

        assert "confidence_gate" in result
        assert "pattern_reuse_gate" in result
        assert "completion_gate" in result

    def test_next_best_action_prefers_graph_before_read_file(self) -> None:
        readiness = {
            "completion_gate": {
                "missing": ["code_source", "graph_read"],
            }
        }

        action = MCPToolsRegistry._build_next_best_action(
            blockers=["completion_gate_blocked"],
            readiness=readiness,
        )

        assert action.get("tool") == "query_code_graph"
        assert action.get("action") == "collect_graph_evidence"

    async def test_semantic_search_returns_results(
        self, mcp_registry: MCPToolsRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_semantic_search(query: str, top_k: int = 5) -> list[dict[str, object]]:
            assert query == "auth flow"
            assert top_k == 3
            return [{"node_id": 11, "qualified_name": "app.auth.login", "score": 0.91}]

        monkeypatch.setattr(
            "codebase_rag.mcp.tools.semantic_code_search", fake_semantic_search
        )

        result = await mcp_registry.semantic_search("auth flow", top_k=3)

        assert result.get("count") == 1
        assert isinstance(result.get("results"), list)

    async def test_query_code_graph_repairs_invalid_generated_query(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        project_name = Path(mcp_registry.project_root).resolve().name
        bad_query = (
            f"MATCH (m:Module {{project_name: '{project_name}'}}) RETURN m "
            "MATCH (x) RETURN x"
        )
        good_query = (
            f"MATCH (m:Module {{project_name: '{project_name}'}}) RETURN m LIMIT 1"
        )

        call_count = {"count": 0}

        async def fake_generate(_: str) -> str:
            call_count["count"] += 1
            return bad_query if call_count["count"] == 1 else good_query

        mcp_registry.cypher_gen.generate = fake_generate

        def fake_fetch_all(
            query: str, params: dict[str, object] | None = None
        ) -> list[dict[str, object]]:
            _ = params
            if "RETURN m MATCH" in query:
                raise RuntimeError(
                    "MATCH can't be put after RETURN clause or after an update."
                )
            return [{"name": "ParserModule"}]

        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.side_effect = fake_fetch_all

        result = await mcp_registry.query_code_graph(
            natural_language_query="show parser modules",
            output_format="json",
        )

        assert isinstance(result, dict)
        assert result.get("error") is None
        assert result.get("query_used") == good_query
        rows = result.get("results", [])
        assert isinstance(rows, list)
        assert len(rows) == 1

    async def test_query_code_graph_uses_parser_fallback_on_empty_results(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        project_name = Path(mcp_registry.project_root).resolve().name

        async def fake_generate(_: str) -> str:
            return (
                f"MATCH (m:Module {{project_name: '{project_name}'}}) RETURN m LIMIT 25"
            )

        mcp_registry.cypher_gen.generate = fake_generate

        def fake_fetch_all(
            query: str, params: dict[str, object] | None = None
        ) -> list[dict[str, object]]:
            _ = params
            if "CONTAINS '/codebase_rag/parsers'" in query:
                return [
                    {
                        "name": "BaseParser",
                        "qualified_name": "codebase_rag.parsers.base.BaseParser",
                        "type": ["Class"],
                        "path": "codebase_rag/parsers/base.py",
                    }
                ]
            return []

        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.side_effect = fake_fetch_all

        result = await mcp_registry.query_code_graph(
            natural_language_query=(
                "Show me parser-related modules and classes in codebase_rag.parsers"
            ),
            output_format="json",
        )

        assert isinstance(result, dict)
        rows = result.get("results", [])
        assert isinstance(rows, list)
        assert len(rows) == 1
        query_used = str(result.get("query_used", ""))
        assert "CONTAINS '/codebase_rag/parsers'" in query_used

    async def test_query_code_graph_auto_plans_on_first_query(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        project_name = Path(mcp_registry.project_root).resolve().name
        previous_auto_plan = settings.MCP_AUTO_PLAN_ON_FIRST_QUERY
        settings.MCP_AUTO_PLAN_ON_FIRST_QUERY = True
        try:
            planner_called = {"count": 0}

            async def fake_plan(goal: str, context: str | None = None) -> object:
                planner_called["count"] += 1
                _ = goal, context
                return SimpleNamespace(
                    status="ok",
                    content={
                        "summary": "plan ok",
                        "steps": ["step-1"],
                        "risks": [],
                        "tests": [],
                    },
                )

            async def fake_generate(_: str) -> str:
                return (
                    f"MATCH (m:Module {{project_name: '{project_name}'}}) "
                    "RETURN m.name AS name LIMIT 5"
                )

            mcp_registry._planner_agent.plan = fake_plan
            mcp_registry.cypher_gen.generate = fake_generate
            mcp_registry._session_state["plan_task_completed"] = False
            mcp_registry._session_state["auto_plan_attempted"] = False

            ingestor = cast(MagicMock, mcp_registry.ingestor)
            ingestor.fetch_all.return_value = [{"name": "mod1"}]

            result = await mcp_registry.query_code_graph(
                natural_language_query="list parser modules",
                output_format="json",
            )

            assert isinstance(result, dict)
            assert result.get("error") is None
            assert planner_called["count"] == 1
            assert mcp_registry._session_state.get("plan_task_completed") is True
        finally:
            settings.MCP_AUTO_PLAN_ON_FIRST_QUERY = previous_auto_plan

    async def test_query_code_graph_caps_large_results_for_context_safety(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        project_name = Path(mcp_registry.project_root).resolve().name
        previous_max_rows = settings.MCP_QUERY_RESULT_MAX_ROWS
        settings.MCP_QUERY_RESULT_MAX_ROWS = 3
        try:

            async def fake_generate(_: str) -> str:
                return (
                    f"MATCH (m:Module {{project_name: '{project_name}'}}) "
                    "RETURN m.name AS name LIMIT 50"
                )

            mcp_registry.cypher_gen.generate = fake_generate

            ingestor = cast(MagicMock, mcp_registry.ingestor)
            ingestor.fetch_all.return_value = [
                {"name": f"module_{idx}", "qualified_name": f"pkg.module_{idx}"}
                for idx in range(10)
            ]

            result = await mcp_registry.query_code_graph(
                natural_language_query="show all modules",
                output_format="json",
            )

            assert isinstance(result, dict)
            rows = result.get("results", [])
            assert isinstance(rows, list)
            assert len(rows) == 3
            summary = str(result.get("summary", ""))
            assert "truncated" in summary.lower()
            chunk_state = mcp_registry._session_state.get("query_result_chunks", [])
            assert isinstance(chunk_state, list)
            assert len(chunk_state) >= 1
        finally:
            settings.MCP_QUERY_RESULT_MAX_ROWS = previous_max_rows

    async def test_get_function_source_returns_source(
        self, mcp_registry: MCPToolsRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_get_source(node_id: int) -> str | None:
            assert node_id == 11
            return "def login():\n    return True\n"

        monkeypatch.setattr(
            "codebase_rag.mcp.tools.get_function_source_code", fake_get_source
        )

        result = await mcp_registry.get_function_source(11)

        assert result.get("status") == "ok"
        assert "def login" in str(result.get("source_code", ""))

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
        readiness = await mcp_registry.get_execution_readiness()
        signals = cast(dict[str, object], readiness.get("signals", {}))
        assert int(signals.get("memory_pattern_query_count", 0)) >= 1

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

    async def test_memory_query_patterns_filters_success(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        await mcp_registry.memory_add(
            "refactor api design", tags="refactor,api,success"
        )
        await mcp_registry.memory_add("failed migration trial", tags="migration")

        result = await mcp_registry.memory_query_patterns(
            "refactor api",
            filter_tags="refactor",
            success_only=True,
            limit=10,
        )

        assert result.get("count") == 1

    async def test_execution_feedback_sets_replan_required(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        result = await mcp_registry.execution_feedback(
            action="refactor_batch",
            result="partial_success",
            issues="test failure,low coverage",
        )

        assert result.get("status") == "ok"
        assert result.get("replan_required") is True

    async def test_test_quality_gate_calculates_score(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        result = await mcp_registry.test_quality_gate("0.8", "0.7", "0.6")

        assert result.get("status") == "ok"
        assert result.get("pass") is True
        scores = cast(dict[str, object], result.get("scores", {}))
        assert float(scores.get("total", 0.0)) >= 2.0

    async def test_get_tool_usefulness_ranking_returns_items(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        await mcp_registry.memory_add("decision", tags="alpha,beta")
        await mcp_registry.memory_query_patterns("decision", success_only=False)

        ranking = await mcp_registry.get_tool_usefulness_ranking(limit=10)

        assert ranking.get("count", 0) >= 1
        rows = cast(list[dict[str, object]], ranking.get("ranking", []))
        assert any(row.get("tool") == "memory_query_patterns" for row in rows)

    async def test_validate_done_decision_blocks_when_gates_fail(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        result = await mcp_registry.validate_done_decision(goal="finalize task")

        assert result.get("status") == "ok"
        assert result.get("decision") == "not_done"
        blockers = cast(list[str], result.get("blockers", []))
        assert len(blockers) > 0
        assert "confidence_summary" in result
        assert "next_best_action" in result
        assert "ui_summary" in result

    async def test_validate_done_decision_returns_done_when_all_gates_pass(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def fake_validate(payload: dict[str, object]) -> object:
            _ = payload
            return SimpleNamespace(
                status="ok",
                content={
                    "decision": "done",
                    "rationale": "all_checks_green",
                    "required_actions": [],
                },
            )

        mcp_registry._validator_agent.validate = fake_validate
        mcp_registry._session_state["code_evidence_count"] = 2
        mcp_registry._session_state["graph_evidence_count"] = 2
        mcp_registry._session_state["semantic_success_count"] = 1
        mcp_registry._session_state["semantic_similarity_mean"] = 0.9
        mcp_registry._session_state["test_generate_completed"] = True
        mcp_registry._session_state["test_quality_pass"] = True
        mcp_registry._session_state["test_quality_total"] = 2.6
        mcp_registry._session_state["impact_graph_called"] = True
        mcp_registry._session_state["impact_graph_count"] = 3
        mcp_registry._session_state["manual_memory_add_count"] = 1
        mcp_registry._session_state["pattern_reuse_score"] = 85.0
        mcp_registry._session_state["replan_required"] = False
        mcp_registry._session_state["replan_reasons"] = []

        result = await mcp_registry.validate_done_decision(goal="finalize task")

        assert result.get("status") == "ok"
        assert result.get("decision") == "done"
        protocol = cast(dict[str, object], result.get("protocol", {}))
        assert protocol.get("pass") is True
        confidence_summary = cast(
            dict[str, object], result.get("confidence_summary", {})
        )
        assert confidence_summary.get("pass") is True
        next_best_action = cast(dict[str, object], result.get("next_best_action", {}))
        assert next_best_action.get("action") == "proceed_to_apply_or_finalize"

    async def test_validate_done_decision_enforces_required_actions_on_not_done(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def fake_validate(payload: dict[str, object]) -> object:
            _ = payload
            return SimpleNamespace(
                status="ok",
                content={
                    "decision": "not_done",
                    "rationale": "needs_more_work",
                    "required_actions": [],
                },
            )

        mcp_registry._validator_agent.validate = fake_validate
        mcp_registry._session_state["code_evidence_count"] = 2
        mcp_registry._session_state["graph_evidence_count"] = 2
        mcp_registry._session_state["semantic_success_count"] = 1
        mcp_registry._session_state["semantic_similarity_mean"] = 0.95
        mcp_registry._session_state["test_generate_completed"] = True
        mcp_registry._session_state["test_quality_pass"] = True
        mcp_registry._session_state["test_quality_total"] = 2.7
        mcp_registry._session_state["impact_graph_called"] = True
        mcp_registry._session_state["impact_graph_count"] = 2
        mcp_registry._session_state["manual_memory_add_count"] = 1
        mcp_registry._session_state["pattern_reuse_score"] = 90.0
        mcp_registry._session_state["replan_required"] = False
        mcp_registry._session_state["replan_reasons"] = []

        result = await mcp_registry.validate_done_decision(goal="strict hardening")

        assert result.get("decision") == "not_done"
        validator = cast(dict[str, object], result.get("validator", {}))
        required_actions = cast(list[str], validator.get("required_actions", []))
        assert len(required_actions) > 0

    async def test_validate_done_decision_blockers_override_validator_done(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def fake_validate(payload: dict[str, object]) -> object:
            _ = payload
            return SimpleNamespace(
                status="ok",
                content={
                    "decision": "done",
                    "rationale": "looks_good",
                    "required_actions": [],
                },
            )

        mcp_registry._validator_agent.validate = fake_validate

        result = await mcp_registry.validate_done_decision(goal="strict hardening")

        assert result.get("decision") == "not_done"
        blockers = cast(list[str], result.get("blockers", []))
        assert len(blockers) > 0
        validator = cast(dict[str, object], result.get("validator", {}))
        required_actions = cast(list[str], validator.get("required_actions", []))
        assert len(required_actions) > 0

    async def test_sync_graph_updates_requires_user_requested(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        result = await mcp_registry.sync_graph_updates(
            user_requested=False,
            reason="sync graph after edits",
        )

        assert "error" in result

    async def test_sync_graph_updates_runs_graph_updater(
        self, mcp_registry: MCPToolsRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = {"run": 0}

        class DummyConfig:
            git_delta_enabled = True
            selective_update_enabled = True
            incremental_cache_enabled = True
            analysis_enabled = False

        class DummyUpdater:
            def __init__(self, **_: object) -> None:
                self.config = DummyConfig()

            def run(self) -> None:
                called["run"] += 1

        monkeypatch.setattr("codebase_rag.mcp.tools.GraphUpdater", DummyUpdater)

        result = await mcp_registry.sync_graph_updates(
            user_requested=True,
            reason="sync graph after edits",
        )

        assert result.get("status") == "ok"
        assert called["run"] == 1
        sync_mode = cast(dict[str, object], result.get("sync_mode", {}))
        assert sync_mode.get("git_delta_enabled") is True

    async def test_orchestrate_realtime_flow_runs_sequence(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def fake_sync_graph_updates(
            user_requested: bool,
            reason: str,
        ) -> dict[str, object]:
            assert user_requested is True
            assert reason == "refresh graph after edit"
            return {"status": "ok"}

        async def fake_detect_project_drift(
            repo_path: str | None = None,
        ) -> dict[str, object]:
            _ = repo_path
            return {"status": "ok", "drift": {"drift_detected": False}}

        async def fake_validate_done_decision(
            goal: str | None = None,
            context: str | None = None,
        ) -> dict[str, object]:
            _ = goal
            _ = context
            return {
                "status": "ok",
                "decision": "not_done",
                "next_best_action": {
                    "tool": "memory_add",
                    "params_hint": {
                        "entry": "store decision",
                        "tags": "decision,success",
                    },
                },
            }

        mcp_registry.sync_graph_updates = fake_sync_graph_updates
        mcp_registry.detect_project_drift = fake_detect_project_drift
        mcp_registry.validate_done_decision = fake_validate_done_decision

        result = await mcp_registry.orchestrate_realtime_flow(
            action="refactor_batch",
            result="partial_success",
            issues="low coverage",
            user_requested=True,
            sync_reason="refresh graph after edit",
            goal="finalize change",
            context="ctx",
            auto_execute_next=True,
            verify_drift=True,
            debounce_seconds=0,
        )

        assert result.get("status") == "ok"
        flow = cast(list[str], result.get("flow", []))
        assert "execution_feedback" in flow
        assert "sync_graph_updates" in flow
        assert "detect_project_drift" in flow
        assert "validate_done_decision" in flow
        auto_next = cast(dict[str, object], result.get("auto_next", {}))
        assert auto_next.get("executed") is True

    async def test_orchestrate_realtime_flow_handles_sync_error(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def fake_sync_graph_updates(
            user_requested: bool,
            reason: str,
        ) -> dict[str, object]:
            _ = user_requested
            _ = reason
            return {"error": "sync_failed"}

        mcp_registry.sync_graph_updates = fake_sync_graph_updates

        result = await mcp_registry.orchestrate_realtime_flow(
            action="write_file",
            result="success",
            user_requested=True,
            sync_reason="refresh graph",
            debounce_seconds=0,
        )

        assert result.get("status") == "error"
        assert result.get("stage") == "sync_graph_updates"

    async def test_orchestrate_realtime_flow_retries_sync_then_succeeds(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        call_state = {"sync": 0}

        async def flaky_sync_graph_updates(
            user_requested: bool,
            reason: str,
        ) -> dict[str, object]:
            _ = user_requested
            _ = reason
            call_state["sync"] += 1
            if call_state["sync"] < 2:
                return {"error": "temporarily unavailable"}
            return {"status": "ok"}

        async def fake_validate_done_decision(
            goal: str | None = None,
            context: str | None = None,
        ) -> dict[str, object]:
            _ = goal
            _ = context
            return {
                "status": "ok",
                "decision": "not_done",
                "ui_summary": "Decision: not_done",
                "next_best_action": {
                    "tool": "memory_add",
                    "params_hint": {"entry": "x"},
                },
            }

        mcp_registry.sync_graph_updates = flaky_sync_graph_updates
        mcp_registry.validate_done_decision = fake_validate_done_decision

        result = await mcp_registry.orchestrate_realtime_flow(
            action="write_file",
            result="success",
            user_requested=True,
            sync_reason="refresh graph",
            auto_execute_next=False,
            verify_drift=False,
            debounce_seconds=0,
        )

        assert result.get("status") == "ok"
        assert call_state["sync"] == 2
        circuit = cast(dict[str, object], result.get("circuit_breaker", {}))
        assert circuit.get("state") == "closed"

    async def test_orchestrate_realtime_flow_circuit_breaker_blocks_after_repeated_failures(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def always_failing_sync(
            user_requested: bool,
            reason: str,
        ) -> dict[str, object]:
            _ = user_requested
            _ = reason
            return {"error": "timeout"}

        mcp_registry.sync_graph_updates = always_failing_sync

        for _ in range(3):
            failure_result = await mcp_registry.orchestrate_realtime_flow(
                action="write_file",
                result="success",
                user_requested=True,
                sync_reason="refresh graph",
                auto_execute_next=False,
                verify_drift=False,
                debounce_seconds=0,
            )
            assert failure_result.get("status") == "error"
            assert failure_result.get("stage") == "sync_graph_updates"

        blocked_result = await mcp_registry.orchestrate_realtime_flow(
            action="write_file",
            result="success",
            user_requested=True,
            sync_reason="refresh graph",
            auto_execute_next=False,
            verify_drift=False,
            debounce_seconds=0,
        )

        assert blocked_result.get("status") == "error"
        assert blocked_result.get("stage") == "circuit_breaker_open"

    async def test_refactor_batch_ok(
        self, mcp_registry: MCPToolsRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        def fake_semantic_search(query: str, top_k: int = 3) -> list[dict[str, object]]:
            _ = query
            _ = top_k
            return [{"score": 0.95}]

        monkeypatch.setattr(
            "codebase_rag.mcp.tools.semantic_code_search", fake_semantic_search
        )

        mcp_registry._file_editor_tool.function = fake_replace
        mcp_registry._session_state["code_evidence_count"] = 1
        mcp_registry._session_state["graph_evidence_count"] = 1
        mcp_registry._session_state["semantic_success_count"] = 1
        mcp_registry._session_state["semantic_similarity_mean"] = 0.9
        mcp_registry._session_state["test_generate_completed"] = True
        mcp_registry._session_state["test_quality_pass"] = True
        mcp_registry._session_state["test_quality_total"] = 2.4
        mcp_registry._session_state["impact_graph_called"] = True
        mcp_registry._session_state["impact_graph_count"] = 5
        mcp_registry._session_state["manual_memory_add_count"] = 1

        result = await mcp_registry.refactor_batch(payload)

        assert result.get("status") == "ok"

    async def test_refactor_batch_requires_evidence(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
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

        result = await mcp_registry.refactor_batch(payload)

        assert "error" in result

    async def test_refactor_batch_requires_impact_graph(
        self, mcp_registry: MCPToolsRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        def fake_semantic_search(query: str, top_k: int = 3) -> list[dict[str, object]]:
            _ = query
            _ = top_k
            return [{"score": 0.95}]

        monkeypatch.setattr(
            "codebase_rag.mcp.tools.semantic_code_search", fake_semantic_search
        )

        mcp_registry._session_state["code_evidence_count"] = 1
        mcp_registry._session_state["graph_evidence_count"] = 1
        mcp_registry._session_state["semantic_success_count"] = 1
        mcp_registry._session_state["semantic_similarity_mean"] = 0.9
        mcp_registry._session_state["test_generate_completed"] = True
        mcp_registry._session_state["test_quality_pass"] = True
        mcp_registry._session_state["test_quality_total"] = 2.1
        mcp_registry._session_state["manual_memory_add_count"] = 1

        result = await mcp_registry.refactor_batch(payload)

        assert "error" in result

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

        assert result.get("status") == "ok"
