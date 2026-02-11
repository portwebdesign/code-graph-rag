from __future__ import annotations

from codebase_rag.core import constants as cs
from codebase_rag.services.chunk_indexer import ChunkIndexer
from codebase_rag.services.embeddings_service import EmbeddingsService
from codebase_rag.services.semantic_search import SemanticSearchEngine


def test_semantic_search_chunk_rerank(monkeypatch) -> None:
    monkeypatch.setattr(
        "codebase_rag.services.embeddings_service.embed_code",
        lambda _: [0.0, 0.0, 0.0],
    )

    def fake_search_embeddings(_embedding, top_k=50):
        return [(1, 0.7), (2, 0.6)]

    monkeypatch.setattr(
        "codebase_rag.services.semantic_search.search_embeddings",
        fake_search_embeddings,
    )

    class DummyIngestor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def _execute_query(self, query, params):
            if "RETURN id(n) AS node_id" in query:
                return [
                    {
                        cs.KEY_NODE_ID: 1,
                        cs.KEY_QUALIFIED_NAME: "pkg.auth.login",
                        cs.KEY_NAME: "login",
                        "type": ["Function"],
                    },
                    {
                        cs.KEY_NODE_ID: 2,
                        cs.KEY_QUALIFIED_NAME: "pkg.util.helper",
                        cs.KEY_NAME: "helper",
                        "type": ["Function"],
                    },
                ]
            if "count(r) as degree" in query:
                return [
                    {"node_id": 1, "degree": 3},
                    {"node_id": 2, "degree": 1},
                ]
            return []

    service = EmbeddingsService(max_cache_size=4)
    engine = SemanticSearchEngine(
        embeddings_service=service,
        graph_host="localhost",
        graph_port=7687,
        chunk_indexer=ChunkIndexer(max_lines=2, overlap=0),
        max_source_rerank=2,
    )

    monkeypatch.setattr(engine, "_ingestor", lambda: DummyIngestor())
    monkeypatch.setattr(
        engine,
        "_fetch_source_for_node",
        lambda node_id: "auth login token" if node_id == 1 else "utility",
    )

    results = engine.search("auth login", top_k=2)

    assert results[0]["node_id"] == 1
    assert len(results) == 2
