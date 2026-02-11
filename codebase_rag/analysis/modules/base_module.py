from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..analysis_runner import AnalysisRunner, NodeRecord, RelationshipRecord


@dataclass
class AnalysisContext:
    runner: AnalysisRunner
    nodes: list[NodeRecord]
    relationships: list[RelationshipRecord]
    module_path_map: dict[str, str]
    node_by_id: dict[int, NodeRecord]
    module_paths: list[str] | None
    incremental_paths: list[str] | None
    use_db: bool
    summary: dict[str, Any]
    dead_code_verifier: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None


class AnalysisModule(ABC):
    @abstractmethod
    def get_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def run(self, context: AnalysisContext) -> dict[str, Any]:
        raise NotImplementedError
