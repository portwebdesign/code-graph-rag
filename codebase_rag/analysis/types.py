from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NodeRecord:
    node_id: int
    labels: list[str]
    properties: dict[str, object]


@dataclass
class RelationshipRecord:
    from_id: int
    to_id: int
    rel_type: str
    properties: dict[str, object]
