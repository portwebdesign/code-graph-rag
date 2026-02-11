from __future__ import annotations

import json

from codebase_rag.core import constants as cs

from ..protocols import AnalysisRunnerProtocol
from ..types import NodeRecord, RelationshipRecord


class DependenciesMixin:
    def _dependency_risk(
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

        risky = [
            {
                "qualified_name": node_by_id[node_id].properties.get(
                    cs.KEY_QUALIFIED_NAME
                ),
                "fan_in": count,
            }
            for node_id, count in fan_in.items()
            if node_id in node_by_id and count >= 10
        ]

        output_dir = self.repo_path / "output" / "analysis"
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "dependency_risk.json"
        report_path.write_text(json.dumps(risky, indent=2), encoding="utf-8")
        return {"high_risk": len(risky)}
