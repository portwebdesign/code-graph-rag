---
name: "CGR Strict Delivery Flow"
description: "Execute backlog work with mandatory CGR graph-first workflow before implementation."
argument-hint: "Task and scope (example: Analyze and execute BL-129.3 for Abey)"
agent: "agent"
---

You are executing a backlog item with strict CGR MCP workflow.

## Input
Use the user request as the target scope (EPIC, BL, symbol, file path, and acceptance goals).
If scope is missing, ask one concise clarification question, then continue.

## Mandatory CGR Workflow
1. Start with `list_projects`.
2. Call `select_active_project` for the target repository with `client_profile="copilot"`.
3. Call `get_schema_overview` before implementation with intentional scope selection.
   - API-focused work: use `api`.
   - UI or frontend-focused work: use `frontend`.
   - Data modeling, storage, or query-shape work: use `data`.
   - Change-impact or blast-radius investigations: use `impact`.
   - Cross-cutting or mixed tasks: default to `global`.
4. Extract graph shape before edits:
   - Which edges are present for the target area.
   - Which nodes are central.
   - Which key properties exist on those nodes.
5. Run `query_code_graph` to summarize architecture and target hotspots.
6. Always run both `multi_hop_analysis` and `impact_graph` for the primary symbol or file path.
7. Always use `run_cypher` for targeted multi-hop traversals and big-picture graph checks.

## Cypher and Safety Rules
- Keep all queries project-scoped with `$project_name`.
- Default `write=false`.
- Prefer semantic presets from schema overview when available.
- `run_cypher` minimum checklist is mandatory for every task:
   - At least 1 targeted traversal for the primary symbol or file path.
   - At least 1 big-picture system check for surrounding architecture.
   - At least 1 target-focused multi-hop traversal (2+ hops) to validate dependency or impact paths.
- If graph tooling and code disagree, trust verified code and report graph freshness concerns.

## Implementation Gate
- Gather graph evidence first.
- Then read relevant code files.
- Then implement code changes.

## Validation Rules
- Use `test_generate` when it materially improves validation.
- Adapt generated tests to existing project patterns and fixtures.
- Verify graph findings against real code surface by reading files and confirming final behavior.

## Backlog Delivery Rules
- Extract and satisfy item-level Cikti Kriterleri and Kabul Kriterleri before closure.
- Treat work as end-to-end delivery: code, tests, docs, and backlog status.
- Keep modular placement discipline across `src/`, `tests/`, `docs/`, `scripts/`, `config/`, and `agent-logs/`.
- Do not mark a BL completed without validation evidence.

## Output Format
Respond in this exact section order:
1. Selected EPIC or BL scope and assumptions
2. Graph bootstrap evidence (schema scope, node and edge families, key properties)
3. Multi-hop and impact findings
4. Targeted Cypher findings
5. Implementation changes
6. Test and validation evidence
7. Backlog or docs updates
8. Residual risks and open follow-ups

## Quality Bar
- Be goal-focused, but include at least one big-picture graph traversal for system context.
- Separate graph evidence, code-confirmed evidence, and inference.

## Example Invocation
Analyze and execute BL-129.3 for Abey with strict CGR flow. Select the active project first, run schema overview and extract edge or node property evidence, then perform multi-hop, impact, and targeted run_cypher traversal before coding. Implement the fix, adapt tests to current codebase conventions, validate acceptance criteria, and report closure readiness.
