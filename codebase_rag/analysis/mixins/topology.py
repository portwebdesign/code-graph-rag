from __future__ import annotations

from codebase_rag.core import constants as cs

from ..protocols import AnalysisRunnerProtocol
from ..types import NodeRecord, RelationshipRecord


class TopologyMixin:
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

        report_payload = [
            {
                "cycle": [
                    node.properties.get(cs.KEY_QUALIFIED_NAME)
                    for node_id in cycle
                    if (node := node_by_id.get(node_id))
                ]
            }
            for cycle in cycles
        ]
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
        for rel in relationships:
            if rel.rel_type != cs.RelationshipType.CALLS:
                continue
            fan_out[rel.from_id] = fan_out.get(rel.from_id, 0) + 1
            fan_in[rel.to_id] = fan_in.get(rel.to_id, 0) + 1

        def top_k(counter: dict[int, int], k: int = 10) -> list[dict[str, object]]:
            top = sorted(counter.items(), key=lambda item: item[1], reverse=True)[:k]
            results: list[dict[str, object]] = []
            for node_id, count in top:
                node = node_by_id.get(node_id)
                if not node:
                    continue
                results.append(
                    {
                        "qualified_name": node.properties.get(cs.KEY_QUALIFIED_NAME),
                        "count": count,
                    }
                )
            return results

        report_payload = {
            "top_fan_in": top_k(fan_in),
            "top_fan_out": top_k(fan_out),
        }
        self._write_json_report("fan_report.json", report_payload)

        return {"fan_in_nodes": len(fan_in), "fan_out_nodes": len(fan_out)}

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

        results: list[dict[str, object]] = []
        for node in nodes:
            if (
                cs.NodeLabel.FUNCTION.value not in node.labels
                and cs.NodeLabel.METHOD.value not in node.labels
            ):
                continue
            impact = len(reachable(node.node_id))
            if impact:
                results.append(
                    {
                        "qualified_name": node.properties.get(cs.KEY_QUALIFIED_NAME),
                        "impact": impact,
                    }
                )

        top = sorted(results, key=lambda item: item["impact"], reverse=True)[:10]
        self._write_json_report("blast_radius_report.json", top)
        return {"entries": len(results)}

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

        self._write_json_report("layering_violations.json", violations)
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
