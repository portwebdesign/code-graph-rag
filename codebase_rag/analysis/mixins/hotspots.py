from __future__ import annotations

import json

from codebase_rag.core import constants as cs

from ..protocols import AnalysisRunnerProtocol
from ..types import NodeRecord, RelationshipRecord


class HotspotsMixin:
    def _performance_hotspots(
        self: AnalysisRunnerProtocol,
        nodes: list[NodeRecord],
        relationships: list[RelationshipRecord],
        node_by_id: dict[int, NodeRecord],
    ) -> dict[str, int]:
        fan_in: dict[int, int] = {}
        for rel in relationships:
            if rel.rel_type != cs.RelationshipType.CALLS:
                continue
            fan_in[rel.to_id] = fan_in.get(rel.to_id, 0) + 1

        hotspots: list[dict[str, object]] = []
        for node in nodes:
            if (
                cs.NodeLabel.FUNCTION.value not in node.labels
                and cs.NodeLabel.METHOD.value not in node.labels
            ):
                continue
            complexity = int(str(node.properties.get("complexity") or 0))
            if complexity < 10:
                continue
            calls_in = fan_in.get(node.node_id, 0)
            if calls_in < 5:
                continue
            hotspots.append(
                {
                    "qualified_name": node.properties.get(cs.KEY_QUALIFIED_NAME),
                    "complexity": complexity,
                    "fan_in": calls_in,
                }
            )

        report_payload = {
            "summary": {
                "hotspots": len(hotspots),
                "min_complexity": 10,
                "min_fan_in": 5,
            },
            "reason": (
                "No function/method reached complexity>=10 and fan_in>=5"
                if not hotspots
                else None
            ),
            "hotspots": hotspots,
        }
        output_dir = self.repo_path / "output" / "analysis"
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "performance_hotspots.json"
        report_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
        return {"hotspots": len(hotspots)}
