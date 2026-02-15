from __future__ import annotations

from codebase_rag.tools.graph_algorithms import GraphAlgorithms


class FakeQueryEngine:
    def __init__(self, mage_available: bool = True) -> None:
        self.mage_available = mage_available
        self.writes: list[str] = []
        self.fetches: list[str] = []

    def fetch_all(self, query: str, params: dict | None = None) -> list[dict]:
        self.fetches.append(query)
        if "mg.procedures" in query:
            if self.mage_available:
                return [{"name": "pagerank.get"}]
            raise RuntimeError("mage unavailable")
        return []

    def execute_write(self, query: str, params: dict | None = None) -> None:
        self.writes.append(query)


def test_graph_algorithms_skip_when_no_changes() -> None:
    engine = FakeQueryEngine(mage_available=True)
    GraphAlgorithms(engine).run_all(has_changes=False)
    assert engine.writes == []


def test_graph_algorithms_skip_when_mage_unavailable() -> None:
    engine = FakeQueryEngine(mage_available=False)
    GraphAlgorithms(engine).run_all(has_changes=True)
    assert engine.writes == []


def test_graph_algorithms_sets_properties() -> None:
    engine = FakeQueryEngine(mage_available=True)
    GraphAlgorithms(engine).run_all(has_changes=True)
    joined = "\n".join(engine.fetches + engine.writes)
    assert "pagerank.get" in joined
    assert "SET node.pagerank" in joined
    assert "community_detection.get" in joined
    assert "SET node.community_id" in joined
    assert "cycles.get" in joined
    assert "SET n.has_cycle" in joined
