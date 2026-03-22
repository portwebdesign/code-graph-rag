from __future__ import annotations

from codebase_rag.core import constants as cs

from ..protocols import AnalysisRunnerProtocol
from ..types import NodeRecord, RelationshipRecord


class TopologyMixin:
    _NON_PRODUCTION_PATH_MARKERS = {
        ".git",
        ".venv",
        "__tests__",
        "build",
        "coverage",
        "dist",
        "generated",
        "node_modules",
        "site-packages",
        "spec",
        "test",
        "tests",
        "vendor",
        "venv",
    }
    _SEMANTIC_FAN_RELATION_TYPES = {
        cs.RelationshipType.CALLS,
        cs.RelationshipType.DISPATCHES_TO,
        cs.RelationshipType.USES_DEPENDENCY,
        cs.RelationshipType.SECURED_BY,
        cs.RelationshipType.USES_COMPONENT,
    }

    @staticmethod
    def _is_actionable_symbol_name(name: str) -> bool:
        lowered = str(name or "")
        return not (
            lowered.startswith("anonymous_")
            or lowered.startswith(cs.IIFE_ARROW_PREFIX)
            or lowered.startswith(cs.IIFE_FUNC_PREFIX)
        )

    @classmethod
    def _is_actionable_node(cls, node: NodeRecord | None) -> bool:
        if not node:
            return False
        if not cls._is_actionable_symbol_name(
            str(node.properties.get(cs.KEY_NAME) or "")
        ):
            return False
        path = str(node.properties.get(cs.KEY_PATH) or "")
        if path and cls._is_non_production_path(path):
            return False
        return True

    @staticmethod
    def _top_counter_entries(
        counter: dict[int, int],
        *,
        node_by_id: dict[int, NodeRecord],
        breakdowns: dict[int, dict[str, int]] | None = None,
        k: int = 10,
    ) -> list[dict[str, object]]:
        top = sorted(counter.items(), key=lambda item: item[1], reverse=True)[:k]
        results: list[dict[str, object]] = []
        for node_id, count in top:
            node = node_by_id.get(node_id)
            if not node:
                continue
            label = node.labels[0] if node.labels else None
            payload: dict[str, object] = {
                "qualified_name": node.properties.get(cs.KEY_QUALIFIED_NAME),
                "path": node.properties.get(cs.KEY_PATH),
                "label": label,
                "count": count,
            }
            if breakdowns and node_id in breakdowns:
                payload["relation_breakdown"] = dict(
                    sorted(breakdowns[node_id].items())
                )
            results.append(payload)
        return results

    def _cycle_detection(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
    ) -> dict[str, int]:
        module_ids = {
            node.node_id for node in nodes if cs.NodeLabel.MODULE.value in node.labels
        }
        graph: dict[int, set[int]] = {}
        for rel in relationships:
            if rel.rel_type not in {
                cs.RelationshipType.IMPORTS,
                cs.RelationshipType.RESOLVES_IMPORT,
            }:
                continue
            if rel.from_id in module_ids and rel.to_id in module_ids:
                graph.setdefault(rel.from_id, set()).add(rel.to_id)

        cycles = self._collect_cycles(graph)

        cycles_payload = [
            {
                "cycle": [
                    node.properties.get(cs.KEY_QUALIFIED_NAME)
                    for node_id in cycle
                    if (node := node_by_id.get(node_id))
                ]
            }
            for cycle in cycles
        ]
        report_payload = {
            "summary": {
                "cycles": len(cycles_payload),
                "modules_in_graph": len(module_ids),
            },
            "reason": ("No import cycle detected" if not cycles_payload else None),
            "cycles": cycles_payload,
        }
        self._write_json_report("cycles_report.json", report_payload)
        return {"cycles": len(cycles)}

    def _fan_in_out(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
    ) -> dict[str, int]:
        fan_in: dict[int, int] = {}
        fan_out: dict[int, int] = {}
        production_fan_in: dict[int, int] = {}
        production_fan_out: dict[int, int] = {}
        semantic_fan_in: dict[int, int] = {}
        semantic_fan_out: dict[int, int] = {}
        semantic_in_breakdown: dict[int, dict[str, int]] = {}
        semantic_out_breakdown: dict[int, dict[str, int]] = {}

        for rel in relationships:
            source_node = node_by_id.get(rel.from_id)
            target_node = node_by_id.get(rel.to_id)

            if rel.rel_type == cs.RelationshipType.CALLS:
                fan_out[rel.from_id] = fan_out.get(rel.from_id, 0) + 1
                fan_in[rel.to_id] = fan_in.get(rel.to_id, 0) + 1
                if TopologyMixin._is_actionable_node(
                    source_node
                ) and TopologyMixin._is_actionable_node(target_node):
                    production_fan_out[rel.from_id] = (
                        production_fan_out.get(rel.from_id, 0) + 1
                    )
                    production_fan_in[rel.to_id] = (
                        production_fan_in.get(rel.to_id, 0) + 1
                    )

            if rel.rel_type not in TopologyMixin._SEMANTIC_FAN_RELATION_TYPES:
                continue
            if not TopologyMixin._is_actionable_node(
                source_node
            ) or not TopologyMixin._is_actionable_node(target_node):
                continue

            semantic_fan_out[rel.from_id] = semantic_fan_out.get(rel.from_id, 0) + 1
            semantic_fan_in[rel.to_id] = semantic_fan_in.get(rel.to_id, 0) + 1
            semantic_out_breakdown.setdefault(rel.from_id, {})[rel.rel_type] = (
                semantic_out_breakdown.setdefault(rel.from_id, {}).get(rel.rel_type, 0)
                + 1
            )
            semantic_in_breakdown.setdefault(rel.to_id, {})[rel.rel_type] = (
                semantic_in_breakdown.setdefault(rel.to_id, {}).get(rel.rel_type, 0) + 1
            )

        report_payload = {
            "summary": {
                "raw_fan_in_nodes": len(fan_in),
                "raw_fan_out_nodes": len(fan_out),
                "production_fan_in_nodes": len(production_fan_in),
                "production_fan_out_nodes": len(production_fan_out),
                "semantic_fan_in_nodes": len(semantic_fan_in),
                "semantic_fan_out_nodes": len(semantic_fan_out),
            },
            "reason": (
                "No actionable production fan entries found"
                if not production_fan_in and not production_fan_out
                else None
            ),
            "top_fan_in": TopologyMixin._top_counter_entries(
                fan_in, node_by_id=node_by_id
            ),
            "top_fan_out": TopologyMixin._top_counter_entries(
                fan_out, node_by_id=node_by_id
            ),
            "top_fan_in_production": TopologyMixin._top_counter_entries(
                production_fan_in,
                node_by_id=node_by_id,
            ),
            "top_fan_out_production": TopologyMixin._top_counter_entries(
                production_fan_out,
                node_by_id=node_by_id,
            ),
            "top_semantic_fan_in": TopologyMixin._top_counter_entries(
                semantic_fan_in,
                node_by_id=node_by_id,
                breakdowns=semantic_in_breakdown,
            ),
            "top_semantic_fan_out": TopologyMixin._top_counter_entries(
                semantic_fan_out,
                node_by_id=node_by_id,
                breakdowns=semantic_out_breakdown,
            ),
        }
        self._write_json_report("fan_report.json", report_payload)

        return {
            "fan_in_nodes": len(fan_in),
            "fan_out_nodes": len(fan_out),
            "production_fan_in_nodes": len(production_fan_in),
            "production_fan_out_nodes": len(production_fan_out),
        }

    def _blast_radius(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
    ) -> dict[str, int]:
        call_graph: dict[int, set[int]] = {}
        for rel in relationships:
            if rel.rel_type != cs.RelationshipType.CALLS:
                continue
            call_graph.setdefault(rel.from_id, set()).add(rel.to_id)

        def reachable(start_id: int) -> set[int]:
            seen: set[int] = set()
            stack = [start_id]
            while stack:
                current = stack.pop()
                if current in seen:
                    continue
                seen.add(current)
                stack.extend(call_graph.get(current, set()))
            seen.discard(start_id)
            return seen

        production_results: list[dict[str, object]] = []
        ignored_results: list[dict[str, object]] = []
        for node in nodes:
            if (
                cs.NodeLabel.FUNCTION.value not in node.labels
                and cs.NodeLabel.METHOD.value not in node.labels
            ):
                continue
            impact = len(reachable(node.node_id))
            if impact:
                payload = {
                    "qualified_name": node.properties.get(cs.KEY_QUALIFIED_NAME),
                    "path": node.properties.get(cs.KEY_PATH),
                    "impact": impact,
                }
                path = str(node.properties.get(cs.KEY_PATH) or "")
                if TopologyMixin._is_non_production_path(path):
                    ignored_results.append(payload)
                else:
                    production_results.append(payload)

        top = sorted(
            production_results, key=lambda item: int(item["impact"]), reverse=True
        )[:10]
        ignored_top = sorted(
            ignored_results, key=lambda item: int(item["impact"]), reverse=True
        )[:10]
        self._write_json_report(
            "blast_radius_report.json",
            {
                "summary": {
                    "production_entries": len(production_results),
                    "ignored_entries": len(ignored_results),
                    "top_production_impact": (int(top[0]["impact"]) if top else 0),
                },
                "top_production_impact": top,
                "ignored_non_production": ignored_top,
                "reason": (
                    "No production code blast radius entries found" if not top else None
                ),
            },
        )
        return {
            "entries": len(production_results),
            "ignored_entries": len(ignored_results),
        }

    def _layering_violations(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
    ) -> dict[str, int]:
        module_nodes = {
            node.node_id: node
            for node in nodes
            if cs.NodeLabel.MODULE.value in node.labels
        }
        layers = [
            "domain",
            "application",
            "service",
            "infra",
            "infrastructure",
            "ui",
            "web",
        ]

        def layer_for(path: str) -> str | None:
            parts = path.lower().split("/")
            for layer in layers:
                if layer in parts:
                    return layer
            return None

        violations: list[dict[str, object]] = []
        for rel in relationships:
            if rel.rel_type not in {
                cs.RelationshipType.IMPORTS,
                cs.RelationshipType.RESOLVES_IMPORT,
            }:
                continue
            source = module_nodes.get(rel.from_id)
            target = module_nodes.get(rel.to_id)
            if not source or not target:
                continue
            src_path = str(source.properties.get(cs.KEY_PATH) or "")
            tgt_path = str(target.properties.get(cs.KEY_PATH) or "")
            src_layer = layer_for(src_path)
            tgt_layer = layer_for(tgt_path)
            if (
                src_layer
                and tgt_layer
                and src_layer in {"domain", "application"}
                and tgt_layer in {"infra", "infrastructure", "ui", "web"}
            ):
                violations.append(
                    {
                        "from": source.properties.get(cs.KEY_QUALIFIED_NAME),
                        "to": target.properties.get(cs.KEY_QUALIFIED_NAME),
                        "from_layer": src_layer,
                        "to_layer": tgt_layer,
                    }
                )

        report_payload = {
            "summary": {
                "violations": len(violations),
                "modules_analyzed": len(module_nodes),
            },
            "reason": (
                "No cross-layer violation matched configured layer rules"
                if not violations
                else None
            ),
            "violations": violations,
        }
        self._write_json_report("layering_violations.json", report_payload)
        return {"violations": len(violations)}

    @staticmethod
    def _collect_cycles(graph: dict[int, set[int]]) -> list[list[int]]:
        visited: set[int] = set()
        stack: list[int] = []
        on_stack: set[int] = set()
        cycles: list[list[int]] = []

        def dfs(node_id: int) -> None:
            visited.add(node_id)
            stack.append(node_id)
            on_stack.add(node_id)
            for next_id in graph.get(node_id, set()):
                if next_id not in visited:
                    dfs(next_id)
                elif next_id in on_stack:
                    cycle_start = stack.index(next_id)
                    cycles.append(stack[cycle_start:] + [next_id])
            stack.pop()
            on_stack.discard(node_id)

        for node_id in graph:
            if node_id not in visited:
                dfs(node_id)

        return cycles

    @classmethod
    def _is_non_production_path(cls, path: str) -> bool:
        normalized = str(path or "").replace("\\", "/").lower()
        if not normalized:
            return False
        return not cls._NON_PRODUCTION_PATH_MARKERS.isdisjoint(normalized.split("/"))
