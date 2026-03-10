from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
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


def _registry_any(mcp_registry: MCPToolsRegistry) -> Any:
    return cast(Any, mcp_registry)


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
        session_contract = cast(dict[str, object], result.get("session_contract", {}))
        assert session_contract.get("active_project") == project_name
        assert "default_flow" in session_contract
        default_flow = cast(list[str], session_contract.get("default_flow", []))
        assert default_flow[:2] == ["list_projects", "select_active_project"]
        assert "plan_task(for multi-step or backlog-driven work)" in default_flow
        startup_sequence = cast(
            list[str], session_contract.get("mandatory_startup_sequence", [])
        )
        assert startup_sequence == ["list_projects", "select_active_project"]
        tool_preference_policy = cast(
            dict[str, object], session_contract.get("tool_preference_policy", {})
        )
        assert tool_preference_policy.get("graph_rag_first") is True
        prefer_tools = cast(list[str], tool_preference_policy.get("prefer_tools", []))
        defer_tools = cast(list[str], tool_preference_policy.get("defer_tools", []))
        guidance = cast(list[str], tool_preference_policy.get("guidance", []))
        assert "query_code_graph" in prefer_tools
        assert "plan_task" in prefer_tools
        assert "read_file" in defer_tools
        assert any("GraphRAG discovery tools" in item for item in guidance)
        scope_rules = cast(dict[str, object], session_contract.get("scope_rules", {}))
        assert "preferred" in scope_rules
        orchestrator_policy = cast(
            dict[str, object], session_contract.get("orchestrator_policy", {})
        )
        assert (
            orchestrator_policy.get("published_on_first_call")
            == "select_active_project"
        )
        tool_tiering = cast(
            dict[str, object], orchestrator_policy.get("tool_tiering", {})
        )
        visible_tiers = tool_tiering.get("visible_tiers", [])
        assert isinstance(visible_tiers, list)
        assert "tier1" in visible_tiers
        assert "meta" in visible_tiers
        registry_domains = cast(
            dict[str, object], orchestrator_policy.get("registry_domains", {})
        )
        assert "graph" in registry_domains
        assert "workflow" in registry_domains
        guard = cast(dict[str, object], orchestrator_policy.get("tool_chain_guard", {}))
        assert guard.get("max_steps") == 8
        exploration_policy = cast(
            dict[str, object], orchestrator_policy.get("exploration_policy", {})
        )
        assert exploration_policy.get("strategy") == "epsilon_greedy"
        assert "allowed_failure_types" in exploration_policy
        guard_model = cast(
            dict[str, object], orchestrator_policy.get("guard_model", {})
        )
        hard_guards = cast(list[str], guard_model.get("hard_guards", []))
        soft_guards = cast(list[str], guard_model.get("soft_guards", []))
        assert "preflight_gate" in hard_guards
        assert "context_confidence_gate" in soft_guards
        confidence_model = cast(
            dict[str, object], guard_model.get("context_confidence_model", {})
        )
        assert confidence_model.get("name") == "context_confidence_v1"
        initial_broadcast = cast(
            dict[str, object], result.get("initial_llm_policy_broadcast", {})
        )
        assert (
            initial_broadcast.get("published_on_first_call") == "select_active_project"
        )
        policy = cast(dict[str, object], result.get("policy", {}))
        assert policy.get("context_confidence_gate_enabled") is True
        assert policy.get("soft_hard_guard_partition_enabled") is True
        assert policy.get("epsilon_exploration_enabled") is True
        assert policy.get("adaptive_epsilon_enabled") is True
        execution_state = cast(dict[str, object], result.get("execution_state", {}))
        assert execution_state.get("phase") == "retrieval"
        policy_execution_state = cast(
            dict[str, object], orchestrator_policy.get("execution_state", {})
        )
        assert policy_execution_state.get("phase") == "retrieval"
        bootstrap_playbook = cast(
            dict[str, object], result.get("bootstrap_playbook", {})
        )
        assert (
            bootstrap_playbook.get("summary")
            == "Startup sequence complete. Stay graph-first."
        )
        exact_next_calls = cast(
            list[dict[str, object]], result.get("exact_next_calls", [])
        )
        assert len(exact_next_calls) >= 1
        assert exact_next_calls[0].get("tool") == "query_code_graph"
        next_best_action = cast(dict[str, object], result.get("next_best_action", {}))
        assert next_best_action.get("tool") == "query_code_graph"
        response_profiles = cast(
            dict[str, object], session_contract.get("response_profiles", {})
        )
        test_generate_profile = cast(
            dict[str, object], response_profiles.get("test_generate", {})
        )
        assert test_generate_profile.get("default_output_mode") == "code"
        graph_sync_policy = cast(
            dict[str, object], session_contract.get("graph_sync_policy", {})
        )
        assert graph_sync_policy.get("default_mode") == "fast"
        staged_visibility = cast(
            dict[str, object], session_contract.get("staged_tool_visibility", {})
        )
        visible_tools = cast(list[str], staged_visibility.get("visible_tools", []))
        assert "query_code_graph" in visible_tools
        assert "multi_hop_analysis" in visible_tools
        assert "read_file" not in visible_tools
        state_machine = cast(
            dict[str, object], session_contract.get("state_machine", {})
        )
        assert state_machine.get("enabled") is True
        repo_semantics = cast(
            dict[str, object], session_contract.get("repo_semantics", {})
        )
        assert "summary" in repo_semantics

    async def test_select_active_project_applies_ollama_client_profile(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        project_name = Path(mcp_registry.project_root).resolve().name

        ingestor.list_projects.return_value = [project_name]
        ingestor.fetch_all.side_effect = [
            [{"count": 2}],
            [{"count": 1}],
            [{"count": 3}],
            [{"analysis_timestamp": "2026-03-05T10:00:00Z"}],
            [
                {
                    "from_node_type": "Module",
                    "relationship_type": "DEFINES",
                    "to_node_type": "Function",
                }
            ],
        ]

        result = await mcp_registry.select_active_project(client_profile="ollama")

        active_project = cast(dict[str, object], result.get("active_project", {}))
        assert active_project.get("client_profile") == "ollama"

        session_contract = cast(dict[str, object], result.get("session_contract", {}))
        assert session_contract.get("client_profile") == "ollama"

        client_profile_policy = cast(
            dict[str, object], session_contract.get("client_profile_policy", {})
        )
        assert client_profile_policy.get("tool_chain_max_steps") == 5
        assert client_profile_policy.get("planner_contract") == "ultra_compact"

        response_profiles = cast(
            dict[str, object], session_contract.get("response_profiles", {})
        )
        test_generate_profile = cast(
            dict[str, object], response_profiles.get("test_generate", {})
        )
        assert test_generate_profile.get("default_output_mode") == "plan_json"

    async def test_test_generate_uses_impact_aware_test_selection(
        self,
        mcp_registry: MCPToolsRegistry,
        temp_project_root: Path,
    ) -> None:
        src_dir = temp_project_root / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "service.py").write_text(
            "def run_service():\n    return True\n", encoding="utf-8"
        )
        tests_dir = temp_project_root / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test_service.py").write_text(
            "def test_existing_service():\n    assert True\n",
            encoding="utf-8",
        )

        async def fake_run(prompt: str) -> object:
            assert "Impact-aware test selection" in prompt
            return SimpleNamespace(
                status="ok", content="def test_run_service():\n    assert True\n"
            )

        mcp_registry._test_agent = SimpleNamespace(run=fake_run)
        mcp_registry._session_state["last_multi_hop_bundle"] = {
            "affected_files": ["src/service.py"],
            "affected_symbols": ["service.run_service"],
        }

        result = await mcp_registry.test_generate(
            goal="Write tests for service changes"
        )

        assert result["status"] == "ok"
        impact_context = cast(dict[str, object], result.get("impact_context", {}))
        assert impact_context.get("impacted_files") == ["src/service.py"]
        test_selection = cast(dict[str, object], result.get("test_selection", {}))
        existing_tests = cast(
            list[str], test_selection.get("candidate_existing_tests", [])
        )
        assert "tests/test_service.py" in existing_tests
        assert test_selection.get("selection_strategy") == "impact-first"

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

    def test_preflight_gate_guidance_payload_for_new_session(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        error = mcp_registry.get_preflight_gate_error("query_code_graph")
        assert isinstance(error, str)

        payload = mcp_registry.build_gate_guidance_payload(
            tool_name="query_code_graph",
            gate_error=error,
            gate_type="preflight",
        )

        assert payload.get("status") == "blocked"
        assert payload.get("gate") == "preflight"
        assert payload.get("blocked_tool") == "query_code_graph"
        exact_next_calls = cast(
            list[dict[str, object]], payload.get("exact_next_calls")
        )
        assert exact_next_calls[0].get("tool") == "list_projects"
        assert exact_next_calls[1].get("tool") == "select_active_project"
        next_best_action = cast(dict[str, object], payload.get("next_best_action", {}))
        assert next_best_action.get("tool") == "list_projects"

    def test_preflight_gate_guidance_payload_when_schema_missing(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        mcp_registry._session_state["preflight_project_selected"] = True
        mcp_registry._session_state["preflight_schema_summary_loaded"] = False

        error = mcp_registry.get_preflight_gate_error("run_cypher")
        assert isinstance(error, str)

        payload = mcp_registry.build_gate_guidance_payload(
            tool_name="run_cypher",
            gate_error=error,
            gate_type="preflight",
        )

        exact_next_calls = cast(
            list[dict[str, object]], payload.get("exact_next_calls")
        )
        assert len(exact_next_calls) == 1
        assert exact_next_calls[0].get("tool") == "select_active_project"

    def test_phase_gate_blocks_mutation_during_retrieval(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        mcp_registry._session_state["execution_phase"] = "retrieval"

        error = mcp_registry.get_phase_gate_error("write_file")

        assert isinstance(error, str)
        assert "phase_guard_blocked" in error
        assert "retrieval" in error

    def test_phase_gate_allows_mutation_during_execution(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        mcp_registry._session_state["execution_phase"] = "execution"

        error = mcp_registry.get_phase_gate_error("write_file")

        assert error is None

    def test_get_tool_schemas_publishes_full_catalog_with_stage_hints(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        initial_tool_names = [schema.name for schema in mcp_registry.get_tool_schemas()]
        assert "list_projects" in initial_tool_names
        assert "select_active_project" in initial_tool_names
        assert "query_code_graph" in initial_tool_names
        assert "multi_hop_analysis" in initial_tool_names
        assert "read_file" in initial_tool_names
        schema_descriptions = {
            schema.name: schema.description
            for schema in mcp_registry.get_tool_schemas()
        }
        assert "Session stage:" in str(schema_descriptions.get("query_code_graph", ""))
        assert "Session stage:" in str(schema_descriptions.get("read_file", ""))

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
        assert "context_confidence_gate" in result
        assert "pattern_reuse_gate" in result
        assert "completion_gate" in result
        assert "graph_sync_gate" in result
        guard_partition = cast(dict[str, object], result.get("guard_partition", {}))
        assert "hard" in guard_partition
        assert "soft" in guard_partition
        signals = cast(dict[str, object], result.get("signals", {}))
        fallback_exploration = cast(
            dict[str, object], signals.get("fallback_exploration", {})
        )
        assert "calls" in fallback_exploration
        assert "explore_ratio" in fallback_exploration
        assert "execution_state" in result

    async def test_get_execution_readiness_exposes_context_confidence_components(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        mcp_registry._session_state["graph_evidence_count"] = 2
        mcp_registry._session_state["code_evidence_count"] = 2
        mcp_registry._session_state["semantic_similarity_mean"] = 0.85
        mcp_registry._session_state["manual_memory_add_count"] = 1
        mcp_registry._session_state["memory_pattern_query_count"] = 1

        result = await mcp_registry.get_execution_readiness()

        context_gate = cast(
            dict[str, object], result.get("context_confidence_gate", {})
        )
        assert context_gate.get("name") == "context_confidence_v1"
        assert isinstance(context_gate.get("score"), float)
        components = cast(dict[str, object], context_gate.get("components", {}))
        assert "graph_density" in components
        assert "semantic_overlap" in components
        assert "file_depth" in components
        assert "memory_match" in components
        assert "exploration_calibration" in components
        signals = cast(dict[str, object], context_gate.get("signals", {}))
        assert "confidence_calibration" in signals

    async def test_multi_hop_analysis_returns_compressed_bundle(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.side_effect = [
            [
                {
                    "direction": "outbound",
                    "seed_ref": "pkg.Service.run",
                    "seed_path": "pkg/service.py",
                    "node_ref": "pkg.Repository.save",
                    "node_path": "pkg/repository.py",
                    "node_labels": ["Function"],
                    "relation": "CALLS",
                    "hop_count": 1,
                }
            ],
            [
                {
                    "direction": "inbound",
                    "seed_ref": "pkg.Service.run",
                    "seed_path": "pkg/service.py",
                    "node_ref": "pkg.Api.handle",
                    "node_path": "pkg/api.py",
                    "node_labels": ["Function"],
                    "relation": "CALLS",
                    "hop_count": 2,
                }
            ],
        ]

        result = await mcp_registry.multi_hop_analysis(
            qualified_name="pkg.Service.run",
            depth=2,
            limit=20,
        )

        assert result.get("status") == "ok"
        assert "pkg.Repository.save" in cast(
            list[str], result.get("affected_symbols", [])
        )
        assert "pkg/repository.py" in cast(list[str], result.get("affected_files", []))
        hop_summary = cast(dict[str, object], result.get("hop_summary", {}))
        assert hop_summary.get("total_edges") == 2
        next_best_action = cast(dict[str, object], result.get("next_best_action", {}))
        assert next_best_action.get("tool") == "get_code_snippet"
        assert mcp_registry._session_state.get("last_graph_query_digest_id")

    async def test_multi_hop_analysis_unlocks_read_file_followup(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        target_file = Path(mcp_registry.project_root) / "pkg" / "repository.py"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text("value = 1\n", encoding="utf-8")

        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.side_effect = [
            [
                {
                    "direction": "outbound",
                    "seed_ref": "pkg.Service.run",
                    "seed_path": "pkg/service.py",
                    "node_ref": "pkg.Repository.save",
                    "node_path": "pkg/repository.py",
                    "node_labels": ["Function"],
                    "relation": "CALLS",
                    "hop_count": 1,
                }
            ],
            [],
        ]

        _ = await mcp_registry.multi_hop_analysis(qualified_name="pkg.Service.run")
        content = await mcp_registry.read_file("pkg/repository.py")

        assert "value = 1" in content

    async def test_context7_docs_fetches_and_persists_results(
        self, mcp_registry: MCPToolsRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_get_docs(
            library: str,
            query: str,
            version: str | None = None,
        ) -> dict[str, object]:
            return {
                "library_id": f"/docs/{library}/{version or 'latest'}",
                "docs": [
                    {
                        "title": "Routing",
                        "content": f"{library} docs for {query}",
                    }
                ],
            }

        persist_mock = MagicMock()
        monkeypatch.setattr(
            mcp_registry._context7_knowledge_store,
            "lookup",
            lambda library, query: None,
        )
        monkeypatch.setattr(
            mcp_registry._context7_memory_store,
            "lookup",
            lambda library, query: None,
        )
        monkeypatch.setattr(mcp_registry._context7_client, "get_docs", fake_get_docs)
        monkeypatch.setattr(
            mcp_registry._context7_persistence,
            "persist",
            persist_mock,
        )

        result = await mcp_registry.context7_docs(
            library="fastapi",
            query="dependency injection lifecycle",
            version="0.115",
        )

        assert result.get("status") == "ok"
        assert result.get("source") == "context7_api"
        highlights = cast(list[str], result.get("highlights", []))
        assert len(highlights) == 1
        assert "Routing" in highlights[0]
        persist_mock.assert_called_once()

    def test_context7_visibility_gate_emits_only_valid_followups(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        mcp_registry._session_state["preflight_project_selected"] = True
        mcp_registry._session_state["preflight_schema_summary_loaded"] = True
        mcp_registry._session_state["execution_phase"] = "retrieval"

        payload = mcp_registry.get_visibility_gate_payload(
            "context7_docs",
            {"library": "fastapi", "query": "dependency injection"},
        )

        assert isinstance(payload, dict)
        exact_next_calls = cast(
            list[dict[str, object]], payload.get("exact_next_calls", [])
        )
        assert len(exact_next_calls) == 1
        assert exact_next_calls[0].get("tool") == "query_code_graph"

    async def test_get_execution_readiness_requires_graph_sync_after_edits(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        mcp_registry._session_state["preflight_project_selected"] = True
        mcp_registry._session_state["edit_success_count"] = 1
        mcp_registry._session_state["graph_dirty"] = True
        mcp_registry._session_state["last_graph_sync_status"] = "pending"

        result = await mcp_registry.get_execution_readiness()

        gate = cast(dict[str, object], result.get("graph_sync_gate", {}))
        assert gate.get("required") is True
        assert gate.get("pass") is False
        completion = cast(dict[str, object], result.get("completion_gate", {}))
        missing = cast(list[str], completion.get("missing", []))
        assert "graph_sync" in missing

    def test_get_execution_readiness_is_visible_in_orchestrator_tiering(
        self,
        mcp_registry: MCPToolsRegistry,
    ) -> None:
        visible, tier = mcp_registry._is_tool_visible_in_orchestrator(
            "get_execution_readiness"
        )

        assert tier == "meta"
        assert visible is True

    def test_core_project_and_workflow_tools_are_visible_in_orchestrator_tiering(
        self,
        mcp_registry: MCPToolsRegistry,
    ) -> None:
        expected_visible_tools = {
            "list_projects": "tier1",
            "select_active_project": "tier1",
            "query_code_graph": "tier1",
            "semantic_search": "tier1",
            "run_cypher": "tier1",
            "plan_task": "meta",
            "test_generate": "meta",
            "memory_query_patterns": "meta",
            "test_quality_gate": "meta",
            "validate_done_decision": "meta",
            "get_execution_readiness": "meta",
            "orchestrate_realtime_flow": "meta",
            "get_tool_usefulness_ranking": "meta",
        }

        for tool_name, expected_tier in expected_visible_tools.items():
            visible, tier = mcp_registry._is_tool_visible_in_orchestrator(tool_name)
            assert tier == expected_tier
            assert visible is True

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

        _registry_any(mcp_registry).cypher_gen.generate = fake_generate

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
        query_used = str(result.get("query_used", ""))
        assert query_used != bad_query
        assert "RETURN m LIMIT 1" in query_used
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

        _registry_any(mcp_registry).cypher_gen.generate = fake_generate

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

    async def test_query_code_graph_standardized_fallback_uses_run_cypher(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        project_name = Path(mcp_registry.project_root).resolve().name

        async def fake_generate(_: str) -> str:
            return (
                f"MATCH (m:Module {{project_name: '{project_name}'}}) "
                "RETURN m.name AS name LIMIT 5"
            )

        _registry_any(mcp_registry).cypher_gen.generate = fake_generate
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.return_value = []

        async def fake_run_cypher(
            cypher: str,
            params: str | None = None,
            write: bool = False,
            user_requested: bool = False,
            reason: str | None = None,
            advanced_mode: bool = False,
        ) -> dict[str, object]:
            _ = cypher, params, write, user_requested, reason, advanced_mode
            return {"status": "ok", "results": [{"name": "from_run_cypher"}]}

        _registry_any(mcp_registry).run_cypher = fake_run_cypher

        result = await mcp_registry.query_code_graph(
            natural_language_query="show modules",
            output_format="json",
        )

        assert isinstance(result, dict)
        rows = cast(list[dict[str, object]], result.get("results", []))
        assert len(rows) == 1
        assert rows[0].get("name") == "from_run_cypher"
        fallback_chain = cast(list[dict[str, object]], result.get("fallback_chain", []))
        assert len(fallback_chain) >= 1
        assert fallback_chain[0].get("tool") == "run_cypher"
        assert fallback_chain[0].get("success") is True

    async def test_query_code_graph_standardized_fallback_uses_semantic_search(
        self,
        mcp_registry: MCPToolsRegistry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        project_name = Path(mcp_registry.project_root).resolve().name

        async def fake_generate(_: str) -> str:
            return (
                f"MATCH (m:Module {{project_name: '{project_name}'}}) "
                "RETURN m.name AS name LIMIT 5"
            )

        def fake_semantic_search(query: str, top_k: int = 5) -> list[dict[str, object]]:
            _ = query
            _ = top_k
            return [
                {
                    "qualified_name": "pkg.mod.func",
                    "file_path": "src/mod.py",
                    "score": 0.92,
                }
            ]

        _registry_any(mcp_registry).cypher_gen.generate = fake_generate
        monkeypatch.setattr(
            "codebase_rag.mcp.tools.semantic_code_search", fake_semantic_search
        )
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.return_value = []

        result = await mcp_registry.query_code_graph(
            natural_language_query="show semantic fallback",
            output_format="json",
        )

        assert isinstance(result, dict)
        rows = cast(list[dict[str, object]], result.get("results", []))
        assert len(rows) == 1
        assert rows[0].get("source") == "semantic_search"
        fallback_chain = cast(list[dict[str, object]], result.get("fallback_chain", []))
        assert len(fallback_chain) >= 2
        assert fallback_chain[0].get("tool") == "run_cypher"
        assert fallback_chain[1].get("tool") == "semantic_search"

    async def test_query_code_graph_adaptive_fallback_prefers_semantic_first(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        project_name = Path(mcp_registry.project_root).resolve().name

        async def fake_generate(_: str) -> str:
            return (
                f"MATCH (m:Module {{project_name: '{project_name}'}}) "
                "RETURN m.name AS name LIMIT 5"
            )

        call_order: list[str] = []

        async def fake_run_cypher(
            cypher: str,
            params: str | None = None,
            write: bool = False,
            user_requested: bool = False,
            reason: str | None = None,
            advanced_mode: bool = False,
        ) -> dict[str, object]:
            _ = cypher, params, write, user_requested, reason, advanced_mode
            call_order.append("run_cypher")
            return {"status": "ok", "results": []}

        async def fake_semantic(
            query: str,
            top_k: int = 5,
        ) -> dict[str, object]:
            _ = query, top_k
            call_order.append("semantic_search")
            return {
                "count": 1,
                "results": [
                    {
                        "qualified_name": "pkg.grep.hit",
                        "file_path": "src/hit.py",
                        "score": 0.8,
                    }
                ],
            }

        _registry_any(mcp_registry).cypher_gen.generate = fake_generate
        _registry_any(mcp_registry).run_cypher = fake_run_cypher
        _registry_any(mcp_registry).semantic_search = fake_semantic
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.return_value = []

        result = await mcp_registry.query_code_graph(
            natural_language_query="grep text keyword matches",
            output_format="json",
        )

        assert isinstance(result, dict)
        assert len(call_order) >= 1
        assert call_order[0] == "semantic_search"
        diagnostics = cast(dict[str, object], result.get("fallback_diagnostics", {}))
        assert diagnostics.get("failure_type") == "no_data"

    async def test_query_code_graph_fallback_forced_explore_reverses_order(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        project_name = Path(mcp_registry.project_root).resolve().name

        async def fake_generate(_: str) -> str:
            return (
                f"MATCH (m:Module {{project_name: '{project_name}'}}) "
                "RETURN m.name AS name LIMIT 5"
            )

        call_order: list[str] = []

        async def fake_run_cypher(
            cypher: str,
            params: str | None = None,
            write: bool = False,
            user_requested: bool = False,
            reason: str | None = None,
            advanced_mode: bool = False,
        ) -> dict[str, object]:
            _ = cypher, params, write, user_requested, reason, advanced_mode
            call_order.append("run_cypher")
            return {"status": "ok", "results": [{"name": "from_run"}]}

        async def fake_semantic(
            query: str,
            top_k: int = 5,
        ) -> dict[str, object]:
            _ = query, top_k
            call_order.append("semantic_search")
            return {
                "count": 0,
                "results": [],
            }

        mcp_registry._session_state["exploration_force_mode"] = "explore"
        _registry_any(mcp_registry).cypher_gen.generate = fake_generate
        _registry_any(mcp_registry).run_cypher = fake_run_cypher
        _registry_any(mcp_registry).semantic_search = fake_semantic
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.return_value = []

        result = await mcp_registry.query_code_graph(
            natural_language_query="show modules",
            output_format="json",
        )

        assert isinstance(result, dict)
        assert len(call_order) >= 1
        assert call_order[0] == "semantic_search"
        diagnostics = cast(dict[str, object], result.get("fallback_diagnostics", {}))
        exploration = cast(dict[str, object], diagnostics.get("exploration", {}))
        assert exploration.get("mode") == "explore"
        assert isinstance(exploration.get("policy_scores"), list)

    async def test_query_code_graph_fallback_safety_blocks_explore_on_policy_failure(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        call_order: list[str] = []

        async def fake_run_cypher(
            cypher: str,
            params: str | None = None,
            write: bool = False,
            user_requested: bool = False,
            reason: str | None = None,
            advanced_mode: bool = False,
        ) -> dict[str, object]:
            _ = cypher, params, write, user_requested, reason, advanced_mode
            call_order.append("run_cypher")
            return {"status": "ok", "results": [{"name": "fallback_hit"}]}

        async def fake_semantic(
            query: str,
            top_k: int = 5,
        ) -> dict[str, object]:
            _ = query, top_k
            call_order.append("semantic_search")
            return {"count": 0, "results": []}

        mcp_registry._session_state["exploration_force_mode"] = "explore"
        _registry_any(mcp_registry).run_cypher = fake_run_cypher
        _registry_any(mcp_registry).semantic_search = fake_semantic

        result = await mcp_registry._run_standardized_query_fallback_chain(
            natural_language_query="show modules",
            cypher_query="MATCH (m:Module) RETURN m LIMIT 5",
            failure_hint="query_execution_exception",
            error_text="scope policy violation",
            result_rows=0,
        )

        assert result.get("status") == "ok"
        assert len(call_order) >= 1
        assert call_order[0] == "run_cypher"
        diagnostics = cast(dict[str, object], result.get("fallback_diagnostics", {}))
        exploration = cast(dict[str, object], diagnostics.get("exploration", {}))
        assert exploration.get("reason") == "safety_constraint"

    async def test_query_code_graph_fallback_updates_exploration_telemetry(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        project_name = Path(mcp_registry.project_root).resolve().name

        async def fake_generate(_: str) -> str:
            return (
                f"MATCH (m:Module {{project_name: '{project_name}'}}) "
                "RETURN m.name AS name LIMIT 5"
            )

        async def fake_run_cypher(
            cypher: str,
            params: str | None = None,
            write: bool = False,
            user_requested: bool = False,
            reason: str | None = None,
            advanced_mode: bool = False,
        ) -> dict[str, object]:
            _ = cypher, params, write, user_requested, reason, advanced_mode
            return {"status": "ok", "results": [{"name": "fallback_hit"}]}

        async def fake_semantic(
            query: str,
            top_k: int = 5,
        ) -> dict[str, object]:
            _ = query, top_k
            return {"count": 0, "results": []}

        mcp_registry._session_state["exploration_force_mode"] = "explore"
        _registry_any(mcp_registry).cypher_gen.generate = fake_generate
        _registry_any(mcp_registry).run_cypher = fake_run_cypher
        _registry_any(mcp_registry).semantic_search = fake_semantic
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.return_value = []

        _ = await mcp_registry.query_code_graph(
            natural_language_query="show modules",
            output_format="json",
        )

        readiness = await mcp_registry.get_execution_readiness()
        signals = cast(dict[str, object], readiness.get("signals", {}))
        fallback_exploration = cast(
            dict[str, object], signals.get("fallback_exploration", {})
        )
        calls = fallback_exploration.get("calls", 0)
        assert int(calls if isinstance(calls, int | float | str) else 0) >= 1
        assert "avg_reward" in fallback_exploration
        assert "avg_latency_ms" in fallback_exploration

    async def test_query_code_graph_policy_level_optimization_prefers_best_chain(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        project_name = Path(mcp_registry.project_root).resolve().name

        async def fake_generate(_: str) -> str:
            return (
                f"MATCH (m:Module {{project_name: '{project_name}'}}) "
                "RETURN m.name AS name LIMIT 5"
            )

        call_order: list[str] = []

        async def fake_run_cypher(
            cypher: str,
            params: str | None = None,
            write: bool = False,
            user_requested: bool = False,
            reason: str | None = None,
            advanced_mode: bool = False,
        ) -> dict[str, object]:
            _ = cypher, params, write, user_requested, reason, advanced_mode
            call_order.append("run_cypher")
            return {"status": "ok", "results": [{"name": "from_run"}]}

        async def fake_semantic(
            query: str,
            top_k: int = 5,
        ) -> dict[str, object]:
            _ = query, top_k
            call_order.append("semantic_search")
            return {
                "count": 1,
                "results": [
                    {
                        "qualified_name": "pkg.optimal.hit",
                        "file_path": "src/hit.py",
                        "score": 0.95,
                    }
                ],
            }

        mcp_registry._session_state["exploration_force_mode"] = "exploit"
        mcp_registry._session_state["fallback_exploration"] = {
            "calls": 16,
            "explore": 2,
            "exploit": 14,
            "success": 8,
            "failure": 8,
            "consecutive_failures": 1,
            "reward_total": 8.1,
            "latency_ms_total": 7600.0,
            "last_mode": "exploit",
            "last_epsilon": 0.1,
            "last_draw": 0.7,
            "chains": {
                "run_cypher->semantic_search": {
                    "calls": 10,
                    "success": 2,
                    "failure": 8,
                    "rows_total": 3,
                    "latency_ms_total": 8200.0,
                    "reward_total": 2.2,
                    "explore": 1,
                    "exploit": 9,
                },
                "semantic_search->run_cypher": {
                    "calls": 6,
                    "success": 6,
                    "failure": 0,
                    "rows_total": 18,
                    "latency_ms_total": 1600.0,
                    "reward_total": 5.9,
                    "explore": 1,
                    "exploit": 5,
                },
            },
            "recent": [],
        }
        _registry_any(mcp_registry).cypher_gen.generate = fake_generate
        _registry_any(mcp_registry).run_cypher = fake_run_cypher
        _registry_any(mcp_registry).semantic_search = fake_semantic
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.return_value = []

        result = await mcp_registry.query_code_graph(
            natural_language_query="show modules",
            output_format="json",
        )

        assert isinstance(result, dict)
        assert len(call_order) >= 1
        assert call_order[0] == "semantic_search"
        diagnostics = cast(dict[str, object], result.get("fallback_diagnostics", {}))
        exploration = cast(dict[str, object], diagnostics.get("exploration", {}))
        assert exploration.get("reason") == "forced_exploit"
        assert exploration.get("policy_best_chain") == "semantic_search->run_cypher"

    def test_adaptive_epsilon_increases_with_failure_history(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        mcp_registry._session_state["fallback_exploration"] = {
            "calls": 20,
            "explore": 1,
            "exploit": 19,
            "success": 5,
            "failure": 15,
            "consecutive_failures": 4,
            "reward_total": 4.0,
            "latency_ms_total": 28000.0,
            "last_mode": "exploit",
            "last_epsilon": 0.1,
            "last_draw": 0.9,
            "chains": {
                "run_cypher->semantic_search": {
                    "calls": 18,
                    "success": 4,
                    "failure": 14,
                    "rows_total": 5,
                    "latency_ms_total": 25000.0,
                    "reward_total": 3.0,
                    "explore": 1,
                    "exploit": 17,
                }
            },
            "recent": [
                {"reward": 0.1},
                {"reward": 0.15},
                {"reward": 0.12},
                {"reward": 0.14},
            ],
        }

        epsilon = mcp_registry._compute_exploration_epsilon("no_data")

        assert epsilon > mcp_registry._EXPLORATION_BASE_EPSILON
        assert epsilon <= mcp_registry._EXPLORATION_MAX_EPSILON

    async def test_query_code_graph_exception_fallback_reports_failure_type(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        project_name = Path(mcp_registry.project_root).resolve().name

        async def fake_generate(_: str) -> str:
            return (
                f"MATCH (m:Module {{project_name: '{project_name}'}}) "
                "RETURN m.name AS name LIMIT 5"
            )

        async def fake_run_cypher(
            cypher: str,
            params: str | None = None,
            write: bool = False,
            user_requested: bool = False,
            reason: str | None = None,
            advanced_mode: bool = False,
        ) -> dict[str, object]:
            _ = cypher, params, write, user_requested, reason, advanced_mode
            return {"status": "ok", "results": [{"name": "fallback_hit"}]}

        _registry_any(mcp_registry).cypher_gen.generate = fake_generate
        _registry_any(mcp_registry).run_cypher = fake_run_cypher
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.side_effect = RuntimeError("invalid query syntax")

        result = await mcp_registry.query_code_graph(
            natural_language_query="show modules",
            output_format="json",
        )

        assert isinstance(result, dict)
        rows = cast(list[dict[str, object]], result.get("results", []))
        assert len(rows) == 1
        diagnostics = cast(dict[str, object], result.get("fallback_diagnostics", {}))
        assert diagnostics.get("failure_type") in {"bad_query", "policy_block"}

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

            _registry_any(mcp_registry)._planner_agent.plan = fake_plan
            _registry_any(mcp_registry).cypher_gen.generate = fake_generate
            mcp_registry._session_state["plan_task_completed"] = False
            mcp_registry._session_state["auto_plan_attempted"] = False

            ingestor = cast(MagicMock, mcp_registry.ingestor)
            ingestor.fetch_all.return_value = [{"name": "mod1"}]

            result = await mcp_registry.query_code_graph(
                natural_language_query="list dependency chain for parser modules",
                output_format="json",
            )

            assert isinstance(result, dict)
            assert result.get("error") is None
            assert planner_called["count"] == 1
            assert mcp_registry._session_state.get("plan_task_completed") is True
            usage_rate = float(result.get("planner_usage_rate", 0.0))
            assert usage_rate > 0.0
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

            _registry_any(mcp_registry).cypher_gen.generate = fake_generate

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
        captured_context: dict[str, str] = {"value": ""}

        async def fake_plan(goal: str, context: str | None = None) -> object:
            _ = goal
            captured_context["value"] = context or ""
            return SimpleNamespace(
                status="ok",
                content={
                    "summary": "do it",
                    "steps": ["step-1"],
                    "risks": [],
                    "tests": [],
                },
            )

        _registry_any(mcp_registry)._planner_agent.plan = fake_plan

        result = await mcp_registry.plan_task("demo", context="ctx")

        assert result.get("status") == "ok"
        assert result.get("summary") == "do it"
        assert result.get("memory_injection_mandatory") is True
        assert "Memory pattern injection (mandatory):" in captured_context["value"]
        assert "Chain success-rate candidates:" in captured_context["value"]
        assert "Structured evidence packet:" in captured_context["value"]
        evidence_packet = cast(dict[str, object], result.get("evidence_packet", {}))
        assert "bundles" in evidence_packet
        assert mcp_registry._current_execution_phase() == "validation"
        assert mcp_registry.get_phase_gate_error("test_generate") is None
        readiness = await mcp_registry.get_execution_readiness()
        signals = cast(dict[str, object], readiness.get("signals", {}))
        memory_pattern_query_count = signals.get("memory_pattern_query_count", 0)
        assert (
            int(
                memory_pattern_query_count
                if isinstance(memory_pattern_query_count, int | float | str)
                else 0
            )
            >= 1
        )

    async def test_plan_task_keeps_gate_closed_when_planner_output_is_empty(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def fake_plan(goal: str, context: str | None = None) -> object:
            _ = goal, context
            return SimpleNamespace(
                status="empty",
                content={"summary": "", "steps": [], "risks": [], "tests": []},
            )

        _registry_any(mcp_registry)._planner_agent.plan = fake_plan

        result = await mcp_registry.plan_task("demo", context="ctx")

        assert result.get("error") == "planner_empty_output"
        assert mcp_registry._session_state.get("plan_task_completed") is False
        assert "exact_next_calls" in result

    async def test_plan_task_keeps_gate_closed_when_planner_output_has_only_risks(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def fake_plan(goal: str, context: str | None = None) -> object:
            _ = goal, context
            return SimpleNamespace(
                status="ok",
                content={
                    "summary": "",
                    "steps": [],
                    "required_evidence": [],
                    "recommended_tool_chain": [],
                    "copy_paste_calls": [],
                    "risks": ["be careful"],
                    "tests": [],
                },
            )

        _registry_any(mcp_registry)._planner_agent.plan = fake_plan

        result = await mcp_registry.plan_task("demo", context="ctx")

        assert result.get("error") == "planner_empty_output"
        assert mcp_registry._session_state.get("plan_task_completed") is False

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
            return "Successfully applied surgical code replacement in: sample.py"

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
        captured_task: dict[str, str] = {"value": ""}

        async def fake_run(task: str) -> object:
            captured_task["value"] = task
            return SimpleNamespace(status="ok", content="run tests")

        _registry_any(mcp_registry)._test_agent.run = fake_run

        result = await mcp_registry.test_generate("add tests", context="ctx")

        assert result.get("status") == "ok"
        assert result.get("content") == "run tests"
        assert "Structured evidence packet:" in captured_task["value"]
        evidence_packet = cast(dict[str, object], result.get("evidence_packet", {}))
        assert "bundles" in evidence_packet

    async def test_test_generate_extracts_code_from_fenced_payload(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def fake_run(task: str) -> object:
            _ = task
            return SimpleNamespace(
                status="ok",
                content="```python\ndef test_example():\n    assert True\n```",
            )

        _registry_any(mcp_registry)._test_agent.run = fake_run

        result = await mcp_registry.test_generate("add tests")

        assert result.get("format") == "code"
        assert "assert True" in str(result.get("code", ""))

    async def test_test_generate_supports_plan_json_mode(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def fake_run(task: str) -> object:
            _ = task
            return SimpleNamespace(
                status="ok",
                content="```python\ndef test_example():\n    assert True\n```",
            )

        _registry_any(mcp_registry)._test_agent.run = fake_run

        result = await mcp_registry.test_generate("add tests", output_mode="plan_json")

        assert result.get("format") == "json"
        assert '"code": "def test_example()' in str(result.get("content", ""))

    async def test_test_generate_supports_both_mode(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def fake_run(task: str) -> object:
            _ = task
            return SimpleNamespace(
                status="ok",
                content='{"language":"python","code":"def test_example():\\n    assert True\\n","evidence_score":0.9}',
            )

        _registry_any(mcp_registry)._test_agent.run = fake_run

        result = await mcp_registry.test_generate("add tests", output_mode="both")

        assert result.get("format") == "code"
        assert "plan_json" in result

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

    async def test_memory_query_patterns_returns_vector_scores(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        await mcp_registry.memory_add(
            json.dumps(
                {
                    "kind": "successful_tool_chain",
                    "tool_history": ["query_code_graph", "run_cypher"],
                    "note": "graph retrieval success",
                }
            ),
            tags="pattern,success",
        )

        result = await mcp_registry.memory_query_patterns(
            query="graph retrieval chain",
            success_only=True,
            limit=5,
        )

        count = result.get("count", 0)
        assert int(count if isinstance(count, int | float | str) else 0) >= 1
        entries = cast(list[dict[str, object]], result.get("entries", []))
        assert "vector_similarity" in entries[0]
        assert "score" in entries[0]

    async def test_memory_query_patterns_returns_chain_success_rates(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        await mcp_registry.memory_add(
            json.dumps(
                {
                    "kind": "successful_tool_chain",
                    "tool_history": ["query_code_graph", "run_cypher"],
                    "status": "ok",
                }
            ),
            tags="pattern,chain,success",
        )
        await mcp_registry.memory_add(
            json.dumps(
                {
                    "kind": "successful_tool_chain",
                    "tool_history": ["query_code_graph", "run_cypher"],
                    "status": "failed",
                }
            ),
            tags="pattern,chain",
        )

        result = await mcp_registry.memory_query_patterns(
            query="query graph and cypher",
            success_only=False,
            limit=10,
        )

        rates = cast(list[dict[str, object]], result.get("chain_success_rates", []))
        assert len(rates) >= 1
        top = rates[0]
        assert "chain_signature" in top
        assert "success_rate" in top
        assert "total_count" in top

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

    async def test_execution_feedback_collects_structured_failure_reasons(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        result = await mcp_registry.execution_feedback(
            action="test_generate",
            result="partial_success",
            issues="missing cleanup, unverified assertion",
            failure_reasons='["hallucinated_fixture"]',
        )

        reasons = cast(list[str], result.get("structured_reasons", []))
        assert "hallucinated_fixture" in reasons
        assert "missing_cleanup" in reasons
        assert "unverified_assertion" in reasons

    async def test_test_quality_gate_calculates_score(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        result = await mcp_registry.test_quality_gate("0.8", "0.7", "0.6")

        assert result.get("status") == "ok"
        assert result.get("pass") is True
        scores = cast(dict[str, object], result.get("scores", {}))
        total_score = scores.get("total", 0.0)
        assert (
            float(total_score if isinstance(total_score, int | float | str) else 0.0)
            >= 2.0
        )

    async def test_test_quality_gate_uses_extended_dimensions(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        result = await mcp_registry.test_quality_gate(
            "0.8",
            "0.8",
            "0.8",
            repo_evidence="0.9",
            layer_correctness="0.9",
            cleanup_safety="0.8",
            anti_hallucination="0.9",
            implementation_coupling_penalty="0.1",
        )

        assert result.get("pass") is True
        scores = cast(dict[str, object], result.get("scores", {}))
        required_score = scores.get("required", 0.0)
        assert (
            float(
                required_score if isinstance(required_score, int | float | str) else 0.0
            )
            == 4.0
        )
        assert "repo_evidence" in scores

    async def test_test_quality_gate_blocks_low_repo_evidence(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        result = await mcp_registry.test_quality_gate(
            "0.9",
            "0.9",
            "0.9",
            repo_evidence="0.2",
            anti_hallucination="0.9",
        )

        assert result.get("pass") is False
        failures = cast(list[str], result.get("hard_failures", []))
        assert "repo_evidence_below_threshold" in failures

    async def test_get_tool_usefulness_ranking_returns_items(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        await mcp_registry.memory_add("decision", tags="alpha,beta")
        await mcp_registry.memory_query_patterns("decision", success_only=False)

        ranking = await mcp_registry.get_tool_usefulness_ranking(limit=10)

        count = ranking.get("count", 0)
        assert int(count if isinstance(count, int | float | str) else 0) >= 1
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
        guard_partition = cast(dict[str, object], result.get("guard_partition", {}))
        assert "hard" in guard_partition
        assert "soft" in guard_partition
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

        _registry_any(mcp_registry)._validator_agent.validate = fake_validate
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

        _registry_any(mcp_registry)._validator_agent.validate = fake_validate
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

        _registry_any(mcp_registry)._validator_agent.validate = fake_validate

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

    async def test_sync_graph_updates_supports_full_mode(
        self, mcp_registry: MCPToolsRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        class DummyConfig:
            git_delta_enabled = False
            selective_update_enabled = False
            incremental_cache_enabled = False
            analysis_enabled = False

        class DummyUpdater:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)
                self.config = DummyConfig()

            def run(self) -> None:
                return None

        monkeypatch.setattr("codebase_rag.mcp.tools.GraphUpdater", DummyUpdater)

        result = await mcp_registry.sync_graph_updates(
            user_requested=True,
            reason="full refresh after major refactor",
            sync_mode="full",
        )

        assert result.get("status") == "ok"
        assert captured.get("force_full_reparse") is True
        sync_mode = cast(dict[str, object], result.get("sync_mode", {}))
        assert sync_mode.get("requested") == "full"

    async def test_orchestrate_realtime_flow_runs_sequence(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def fake_sync_graph_updates(
            user_requested: bool,
            reason: str,
            sync_mode: str = "fast",
        ) -> dict[str, object]:
            assert user_requested is True
            assert reason == "refresh graph after edit"
            assert sync_mode == "fast"
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

        _registry_any(mcp_registry).sync_graph_updates = fake_sync_graph_updates
        _registry_any(mcp_registry).detect_project_drift = fake_detect_project_drift
        _registry_any(mcp_registry).validate_done_decision = fake_validate_done_decision

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
        assert auto_next.get("executed") is False
        assert auto_next.get("reason") == "tool_not_visible_in_current_session_stage"

    async def test_orchestrate_realtime_flow_handles_sync_error(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def fake_sync_graph_updates(
            user_requested: bool,
            reason: str,
            sync_mode: str = "fast",
        ) -> dict[str, object]:
            _ = user_requested
            _ = reason
            _ = sync_mode
            return {"error": "sync_failed"}

        _registry_any(mcp_registry).sync_graph_updates = fake_sync_graph_updates

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
            sync_mode: str = "fast",
        ) -> dict[str, object]:
            _ = user_requested
            _ = reason
            _ = sync_mode
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

        _registry_any(mcp_registry).sync_graph_updates = flaky_sync_graph_updates
        _registry_any(mcp_registry).validate_done_decision = fake_validate_done_decision

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
            sync_mode: str = "fast",
        ) -> dict[str, object]:
            _ = user_requested
            _ = reason
            _ = sync_mode
            return {"error": "timeout"}

        _registry_any(mcp_registry).sync_graph_updates = always_failing_sync

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

    async def test_orchestrate_realtime_flow_prefers_exact_next_calls_chain(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def fake_sync_graph_updates(
            user_requested: bool,
            reason: str,
            sync_mode: str = "fast",
        ) -> dict[str, object]:
            _ = user_requested
            _ = reason
            _ = sync_mode
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
                "exact_next_calls": [
                    {
                        "tool": "memory_add",
                        "args": {"entry": "from_exact_chain", "tags": "exact"},
                        "priority": 1,
                        "when": "always",
                    },
                    {
                        "tool": "test_quality_gate",
                        "args": {
                            "coverage": "1",
                            "edge_cases": "1",
                            "negative_tests": "1",
                        },
                        "priority": 2,
                        "when": "fallback",
                    },
                ],
                "next_best_action": {
                    "tool": "test_quality_gate",
                    "params_hint": {"coverage": "0"},
                },
            }

        async def fake_memory_add(
            entry: str,
            tags: str | None = None,
        ) -> dict[str, object]:
            return {"status": "ok", "entry": entry, "tags": tags}

        _registry_any(mcp_registry).sync_graph_updates = fake_sync_graph_updates
        _registry_any(mcp_registry).validate_done_decision = fake_validate_done_decision
        _registry_any(mcp_registry).memory_add = fake_memory_add

        result = await mcp_registry.orchestrate_realtime_flow(
            action="write_file",
            result="partial_success",
            user_requested=True,
            sync_reason="refresh graph",
            auto_execute_next=True,
            verify_drift=False,
            debounce_seconds=0,
        )

        assert result.get("status") == "ok"
        auto_next = cast(dict[str, object], result.get("auto_next", {}))
        assert auto_next.get("executed") is False
        assert auto_next.get("mode") == "exact_next_calls"
        attempts = cast(list[dict[str, object]], auto_next.get("attempts", []))
        assert attempts[0].get("tool") == "memory_add"
        assert attempts[0].get("reason") == "tool_not_visible_in_current_session_stage"

    async def test_auto_execute_next_best_action_supports_run_cypher(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        ingestor = cast(MagicMock, mcp_registry.ingestor)
        ingestor.fetch_all.return_value = [{"name": "mod"}]
        project_name = Path(mcp_registry.project_root).resolve().name
        scoped_read = (
            f"MATCH (m:Module {{project_name: '{project_name}'}}) "
            "RETURN m.name AS name LIMIT 1"
        )

        result = await mcp_registry._auto_execute_next_best_action(
            {
                "tool": "run_cypher",
                "params_hint": {
                    "cypher": scoped_read,
                    "params": {},
                    "write": False,
                    "advanced_mode": True,
                },
            }
        )

        assert result.get("executed") is True
        payload = cast(dict[str, object], result.get("result", {}))
        assert payload.get("status") == "ok"

    async def test_auto_execute_exact_next_calls_enforces_tier_visibility(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def fake_memory_add(
            entry: str,
            tags: str | None = None,
        ) -> dict[str, object]:
            return {"status": "ok", "entry": entry, "tags": tags}

        _registry_any(mcp_registry).memory_add = fake_memory_add

        result = await mcp_registry._auto_execute_exact_next_calls(
            [
                {
                    "tool": "read_file",
                    "args": {
                        "file_path": "sample.py",
                        "query_digest_id": "qd_1",
                    },
                    "priority": 1,
                    "when": "first",
                },
                {
                    "tool": "memory_add",
                    "args": {
                        "entry": "from_tiered_chain",
                        "tags": "tiering",
                    },
                    "priority": 2,
                    "when": "fallback",
                },
            ],
            max_candidates=3,
        )

        assert result.get("executed") is False
        attempts = cast(list[dict[str, object]], result.get("attempts", []))
        assert len(attempts) >= 2
        assert attempts[0].get("tool") == "read_file"
        assert attempts[0].get("reason") == "tool_not_visible_in_current_session_stage"
        assert attempts[1].get("tool") == "memory_add"
        assert attempts[1].get("reason") == "tool_not_visible_in_current_session_stage"

    async def test_orchestrate_realtime_flow_applies_max_tool_chain_guard(
        self, mcp_registry: MCPToolsRegistry
    ) -> None:
        async def fake_sync_graph_updates(
            user_requested: bool,
            reason: str,
            sync_mode: str = "fast",
        ) -> dict[str, object]:
            _ = user_requested
            _ = reason
            _ = sync_mode
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
                "exact_next_calls": [
                    {
                        "tool": "read_file",
                        "args": {"file_path": "sample.py"},
                        "priority": index,
                        "when": "loop",
                    }
                    for index in range(1, 11)
                ],
            }

        _registry_any(mcp_registry).sync_graph_updates = fake_sync_graph_updates
        _registry_any(mcp_registry).validate_done_decision = fake_validate_done_decision

        result = await mcp_registry.orchestrate_realtime_flow(
            action="write_file",
            result="partial_success",
            user_requested=True,
            sync_reason="refresh graph",
            auto_execute_next=True,
            verify_drift=False,
            debounce_seconds=0,
        )

        assert result.get("status") == "ok"
        guard = cast(dict[str, object], result.get("tool_chain_guard", {}))
        assert guard.get("max_steps") == 8
        assert guard.get("remaining_for_auto_next") == 5
        auto_next = cast(dict[str, object], result.get("auto_next", {}))
        assert auto_next.get("executed") is False
        assert auto_next.get("candidate_limit") == 5
        assert auto_next.get("total_candidates") == 10
        assert auto_next.get("truncated") is True
        attempts = cast(list[dict[str, object]], auto_next.get("attempts", []))
        assert len(attempts) == 5

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
        mcp_registry._session_state["code_evidence_count"] = 2
        mcp_registry._session_state["graph_evidence_count"] = 2
        mcp_registry._session_state["semantic_success_count"] = 2
        mcp_registry._session_state["semantic_similarity_mean"] = 0.9
        mcp_registry._session_state["test_generate_completed"] = True
        mcp_registry._session_state["test_quality_pass"] = True
        mcp_registry._session_state["test_quality_total"] = 2.4
        mcp_registry._session_state["impact_graph_called"] = True
        mcp_registry._session_state["impact_graph_count"] = 5
        mcp_registry._session_state["manual_memory_add_count"] = 2

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
