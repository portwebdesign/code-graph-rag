# Graph-Code AI Agent Instructions

## Project Overview

Code-Graph-RAG is a multi-language code intelligence system that parses repositories with Tree-sitter, stores a project-scoped knowledge graph in Memgraph, and serves that graph through CLI + MCP.

Current system priorities:
- MCP-first workflow for coding agents
- project-scoped graph safety (no cross-project data leaks)
- incremental graph sync and drift-aware updates
- analysis + refactor + testing automation via MCP tools

Core runtime areas:
- `codebase_rag/mcp/` → MCP server and tool orchestration
- `codebase_rag/parsers/` → multi-language parsing and extraction
- `codebase_rag/analysis/` → complexity, security, architecture, roadmap outputs
- `codebase_rag/tools/` → query, file ops, graph ops, quality gates, automation tools
- `codebase_rag/core/constants.py` → canonical MCP server/tool names and parameters

## MCP-First Policy (Critical)

Agents MUST use the MCP server named `code-graph-rag` as primary execution interface.

Do not fallback to ad-hoc/manual graph operations when MCP tool exists.

Mandatory behavior:
1. Prefer MCP tools over custom scripts for graph/query/refactor/analysis workflows.
2. Use project-scoped flows first (`select_active_project`, `detect_project_drift`, `sync_graph_updates`).
3. For graph-mutating operations, follow safety requirements (`user_requested`, `reason`, drift checks).
4. Use validation and feedback tools after edits (`execution_feedback`, `validate_done_decision`, `test_quality_gate`).

## MCP Server Configuration

Server name MUST be:
- `code-graph-rag`

Recommended Windows launch via `uv`:
```json
{
  "mcpServers": {
    "code-graph-rag": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "D:/PROGRAMMING/code-graph-rag",
        "cgr",
        "mcp-server"
      ],
      "env": {
        "TARGET_REPO_PATH": "D:/PROGRAMMING/my-code-graph-rag"
      }
    }
  }
}
```

Alternative launch:
- `python -m codebase_rag.cli mcp-server`

Root path resolution order:
- `TARGET_REPO_PATH` → `CLAUDE_PROJECT_ROOT` → `PWD` → current directory

## MCP Toolset (Use All as Needed)

The `code-graph-rag` MCP server exposes this full tool family:

- Project/Scope:
  - `list_projects`, `select_active_project`, `detect_project_drift`, `index_repository`, `sync_graph_updates`, `delete_project`, `wipe_database`

- Query/Retrieval:
  - `query_code_graph`, `semantic_search`, `impact_graph`, `get_function_source`, `get_code_snippet`, `read_file`, `list_directory`

- Edit/Refactor:
  - `write_file`, `surgical_replace_code`, `apply_diff_safe`, `refactor_batch`

- Analysis/Metrics/Artifacts:
  - `run_analysis`, `run_analysis_subset`, `get_analysis_report`, `get_analysis_metric`, `get_analysis_artifact`, `list_analysis_artifacts`, `get_graph_stats`, `get_dependency_stats`, `export_mermaid`, `performance_hotspots`, `security_scan`

- Planning/Quality/Execution Control:
  - `plan_task`, `execution_feedback`, `test_generate`, `test_quality_gate`, `validate_done_decision`, `get_execution_readiness`, `orchestrate_realtime_flow`, `get_tool_usefulness_ranking`

- Memory:
  - `memory_add`, `memory_list`, `memory_query_patterns`

- Advanced Graph Ops:
  - `run_cypher`

## Required MCP Workflow Patterns

### 1) Safe project-scoped start
1. `select_active_project`
2. `detect_project_drift`
3. If needed and explicitly requested: `sync_graph_updates` or `index_repository`

### 2) Change implementation workflow
1. Retrieve context: `query_code_graph` / `semantic_search` / `read_file`
2. Edit safely: `apply_diff_safe` or `surgical_replace_code` / `refactor_batch`
3. Record and validate: `execution_feedback` → `validate_done_decision`
4. Run quality gate if required: `test_quality_gate`

### 3) Realtime graph consistency workflow
- Prefer `orchestrate_realtime_flow` after non-trivial edits to keep graph state and done-decision aligned.

## Safety & Governance Rules

- Never run destructive graph operations unless user explicitly asked.
- `index_repository` and write-mode `run_cypher` require explicit intent and reason.
- Keep Cypher project-scoped to active project.
- Do not bypass safety flags (`user_requested`, `drift_confirmed`, `reason`).

## Practical Commands

```bash
docker-compose up -d
uv run --directory D:/PROGRAMMING/code-graph-rag cgr mcp-server
cgr start --repo-path D:/PROGRAMMING/my-code-graph-rag --update-graph
```

## Coding Conventions

- Use type hints in all new/updated functions.
- Use `loguru` logger consistently.
- Prefer minimal, targeted edits.
- Avoid unrelated refactors while solving user task.
- Keep tests close to changed functionality and run focused checks first.

## Definition of Done

A task is complete only when:
1. Required code/document changes are applied.
2. Relevant checks/tests are executed or explicitly reported as not runnable.
3. MCP feedback/validation path is completed for substantial changes.
4. Final response includes what changed, where, and any remaining risk.
