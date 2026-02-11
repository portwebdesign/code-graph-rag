from __future__ import annotations

from typing import Any

from codebase_rag.core import constants as cs

from ..protocols import AnalysisRunnerProtocol
from ..types import NodeRecord, RelationshipRecord


class MigrationPlanMixin:
    def _migration_plan(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
        coverage_stats: dict[str, Any],
        risk_stats: dict[str, Any],
        hotspot_stats: dict[str, Any],
        layer_stats: dict[str, Any],
    ) -> dict[str, Any]:
        module_nodes = {
            node.node_id: node
            for node in nodes
            if cs.NodeLabel.MODULE.value in node.labels
        }
        external_nodes = [
            node for node in nodes if cs.NodeLabel.EXTERNAL_PACKAGE.value in node.labels
        ]

        graph: dict[int, set[int]] = {}
        fan_in: dict[int, int] = {}
        fan_out: dict[int, int] = {}
        for rel in relationships:
            if rel.rel_type not in {
                cs.RelationshipType.IMPORTS,
                cs.RelationshipType.RESOLVES_IMPORT,
            }:
                continue
            if rel.from_id not in module_nodes or rel.to_id not in module_nodes:
                continue
            graph.setdefault(rel.from_id, set()).add(rel.to_id)
            fan_out[rel.from_id] = fan_out.get(rel.from_id, 0) + 1
            fan_in[rel.to_id] = fan_in.get(rel.to_id, 0) + 1

        cycles = self._collect_cycles(graph)
        cyclic_nodes = {node_id for cycle in cycles for node_id in cycle}

        module_scores: list[dict[str, object]] = []
        for node_id, node in module_nodes.items():
            qn = str(node.properties.get(cs.KEY_QUALIFIED_NAME) or "")
            path = str(node.properties.get(cs.KEY_PATH) or "")
            score = fan_in.get(node_id, 0) + fan_out.get(node_id, 0)
            if node_id in cyclic_nodes:
                score += 5
            if any(
                part in path.lower()
                for part in ["core", "domain", "infra", "infrastructure"]
            ):
                score += 2
            module_scores.append(
                {
                    "qualified_name": qn,
                    "path": path,
                    "fan_in": fan_in.get(node_id, 0),
                    "fan_out": fan_out.get(node_id, 0),
                    "in_cycle": node_id in cyclic_nodes,
                    "score": score,
                }
            )

        module_scores.sort(key=lambda item: item["score"], reverse=True)
        if not module_scores:
            return {"phases": [], "summary": "No modules available"}

        chunk_size = max(1, len(module_scores) // 3)
        phase1 = module_scores[-chunk_size:]
        phase2 = (
            module_scores[-2 * chunk_size : -chunk_size]
            if len(module_scores) >= 2 * chunk_size
            else []
        )
        phase3 = module_scores[: len(module_scores) - 2 * chunk_size]

        phases = [
            {
                "name": "Phase 0 — Preparation",
                "goals": [
                    "Inventory dependencies and entrypoints",
                    "Add missing tests around low-risk modules",
                    "Freeze external API contracts",
                ],
            },
            {
                "name": "Phase 1 — Low-risk modules",
                "modules": [item["qualified_name"] for item in phase1],
            },
            {
                "name": "Phase 2 — Medium-risk modules",
                "modules": [item["qualified_name"] for item in phase2],
            },
            {
                "name": "Phase 3 — High-risk / cyclic core",
                "modules": [item["qualified_name"] for item in phase3],
            },
        ]

        summary = {
            "module_count": len(module_nodes),
            "cycle_count": len(cycles),
            "external_dependencies": len(external_nodes),
            "coverage_proxy": coverage_stats,
            "dependency_risk": risk_stats,
            "hotspots": hotspot_stats,
            "layering_violations": layer_stats,
        }

        prompt = self._format_migration_prompt(summary, phases, module_scores)

        self._write_json_report(
            "migration_plan.json",
            {"summary": summary, "phases": phases, "modules": module_scores},
        )
        md_path = self._write_text_report("migration_plan.md", prompt)

        return {
            "summary": summary,
            "phases": phases,
            "llm_prompt_path": str(md_path.relative_to(self.repo_path)),
        }

    def _format_migration_prompt(
        self,
        summary: dict[str, object],
        phases: list[dict[str, object]],
        modules: list[dict[str, object]],
    ) -> str:
        lines = [
            "# Migration Roadmap (LLM Input)",
            "",
            "## Summary",
            f"- Module count: {summary.get('module_count')}",
            f"- Cycles detected: {summary.get('cycle_count')}",
            f"- External dependencies: {summary.get('external_dependencies')}",
            f"- Coverage proxy: {summary.get('coverage_proxy')}",
            f"- Dependency risk: {summary.get('dependency_risk')}",
            f"- Hotspots: {summary.get('hotspots')}",
            f"- Layering violations: {summary.get('layering_violations')}",
            "",
            "## Phases",
        ]
        for phase in phases:
            lines.append(f"### {phase.get('name')}")
            goals = phase.get("goals")
            if isinstance(goals, list):
                for goal in goals:
                    lines.append(f"- {goal}")
            phase_modules = phase.get("modules")
            if isinstance(phase_modules, list):
                for module in phase_modules:
                    lines.append(f"- {module}")
            lines.append("")

        lines.append("## Module Risk Ranking (Top 20)")
        for entry in modules[:20]:
            lines.append(
                f"- {entry['qualified_name']} (score={entry['score']}, fan_in={entry['fan_in']}, fan_out={entry['fan_out']}, in_cycle={entry['in_cycle']})"
            )

        lines.append("")
        lines.append("## Instructions for the LLM")
        lines.append(
            "Use the phases and module ranking above to produce a migration plan with milestones, test strategy, and rollback steps."
        )
        lines.append(
            "Prioritize low-risk modules first, isolate cyclic core modules, and address external dependency risks."
        )
        lines.append(
            "Output should include: timeline, ownership, risk mitigation, and validation checklist."
        )

        return "\n".join(lines) + "\n"
