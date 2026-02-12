"""
This module provides the `SemanticSearchEngine`, a service for performing
semantic searches over the codebase.

The search process is multi-staged:
1.  **Vector Search**: It first uses an embedding model to convert the user's
    natural language query into a vector and finds the most similar code nodes
    from a vector store.
2.  **Graph Ranking**: The initial candidates are then re-ranked based on their
    centrality in the code graph (i.e., their degree), boosting nodes that are
    more connected.
3.  **Textual Reranking**: Finally, the candidates are reranked again based on
    textual overlap with the query and, if enabled, by scoring the source code
    chunks of the top candidates.

This hybrid approach combines the power of semantic vector search with the
structural information from the graph and lexical matching to provide more
relevant and accurate search results.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, cast

from codebase_rag.core import constants as cs
from codebase_rag.data_models.vector_store import search_embeddings
from codebase_rag.graph_db.cypher_queries import (
    CYPHER_GET_FUNCTION_SOURCE_LOCATION,
    build_nodes_by_ids_query,
)
from codebase_rag.services.chunk_indexer import ChunkIndexer
from codebase_rag.services.graph_service import MemgraphIngestor
from codebase_rag.utils.source_extraction import (
    extract_source_lines,
    validate_source_location,
)

from .embeddings_service import EmbeddingsService


class SemanticSearchEngine:
    """
    A search engine that combines vector search with graph-based and textual ranking.

    This class orchestrates a multi-stage search process to find the most relevant
    code nodes for a given natural language query.
    """

    def __init__(
        self,
        embeddings_service: EmbeddingsService,
        graph_host: str,
        graph_port: int,
        batch_size: int = 200,
        chunk_indexer: ChunkIndexer | None = None,
        max_source_rerank: int = 15,
    ) -> None:
        """
        Initializes the SemanticSearchEngine.

        Args:
            embeddings_service (EmbeddingsService): The service for creating text embeddings.
            graph_host (str): The hostname of the graph database.
            graph_port (int): The port of the graph database.
            batch_size (int): The batch size for graph database operations.
            chunk_indexer (ChunkIndexer | None): An optional service for chunking source code.
            max_source_rerank (int): The maximum number of top candidates to fetch source
                                     code for during the reranking phase.
        """
        self.embeddings = embeddings_service
        self.graph_host = graph_host
        self.graph_port = graph_port
        self.batch_size = batch_size
        self.chunk_indexer = chunk_indexer
        self.max_source_rerank = max(0, max_source_rerank)

    def search(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        """
        Performs a search for the given query.

        Args:
            query (str): The natural language search query.
            top_k (int): The number of top results to return.

        Returns:
            A list of dictionaries, where each dictionary represents a ranked search result.
        """
        if not query.strip():
            return []

        query_embedding = self.embeddings.embed_text(query)
        candidates = self._vector_search(query_embedding, top_k=50)
        if not candidates:
            return []

        ranked = self._graph_rank(candidates)
        reranked = self._rerank_by_text(query, ranked)
        return reranked[:top_k]

    def _vector_search(
        self, embedding: list[float], top_k: int
    ) -> list[dict[str, Any]]:
        """
        Performs the initial vector search to find candidate nodes.

        Args:
            embedding (list[float]): The query vector.
            top_k (int): The number of candidates to retrieve.

        Returns:
            A list of candidate nodes with their initial vector similarity scores.
        """
        hits = search_embeddings(embedding, top_k=top_k)
        if not hits:
            return []

        node_ids = [node_id for node_id, _ in hits]
        with self._ingestor() as ingestor:
            cypher_query = build_nodes_by_ids_query(node_ids)
            params = {str(i): node_id for i, node_id in enumerate(node_ids)}
            results = ingestor._execute_query(cypher_query, params)

        results_map = {res[cs.KEY_NODE_ID]: res for res in results}
        formatted: list[dict[str, Any]] = []
        for node_id, score in hits:
            if node_id not in results_map:
                continue
            result = results_map[node_id]
            result_type = result.get("type")
            type_str = (
                result_type[0]
                if isinstance(result_type, list) and result_type
                else cs.SEMANTIC_TYPE_UNKNOWN
            )
            formatted.append(
                {
                    "node_id": node_id,
                    "qualified_name": str(result.get(cs.KEY_QUALIFIED_NAME, "")),
                    "name": str(result.get(cs.KEY_NAME, "")),
                    "type": type_str,
                    "score": float(score),
                }
            )
        return formatted

    def _graph_rank(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Reranks candidates based on their degree in the code graph.

        Nodes with more connections are considered more important and their scores are boosted.

        Args:
            candidates (list[dict[str, Any]]): The list of candidates from the vector search.

        Returns:
            The list of candidates with updated scores.
        """
        if not candidates:
            return []

        node_ids = [item["node_id"] for item in candidates]
        degree_map = self._fetch_degrees(node_ids)
        for item in candidates:
            degree = degree_map.get(item["node_id"], 0)
            item["graph_score"] = degree
            item["score"] = item.get("score", 0.0) + (degree * 0.01)
        return sorted(candidates, key=lambda x: x["score"], reverse=True)

    def _fetch_degrees(self, node_ids: list[int]) -> dict[int, int]:
        """
        Fetches the degree (number of relationships) for a list of nodes from the graph.

        Args:
            node_ids (list[int]): A list of internal node IDs.

        Returns:
            A dictionary mapping each node ID to its degree.
        """
        if not node_ids:
            return {}

        cypher = (
            "MATCH (n) WHERE id(n) IN $ids "
            "OPTIONAL MATCH (n)-[r]-() "
            "RETURN id(n) as node_id, count(r) as degree"
        )
        with self._ingestor() as ingestor:
            results = ingestor._execute_query(cypher, {"ids": node_ids})
        return {
            int(cast(int | str, row["node_id"])): int(cast(int | str, row["degree"]))
            for row in results
        }

    def _rerank_by_text(
        self, query: str, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Reranks candidates based on textual overlap with the query.

        This stage boosts the score of candidates whose names or qualified names
        share tokens with the query. It can also fetch the source code of top
        candidates to score them based on chunk content.

        Args:
            query (str): The original search query.
            candidates (list[dict[str, Any]]): The list of candidates from the graph ranking.

        Returns:
            The final, reranked list of search results.
        """
        tokens = self._tokenize(query)
        if not tokens:
            return candidates

        rerank_limit = (
            self.max_source_rerank if self.max_source_rerank > 0 else len(candidates)
        )
        for index, item in enumerate(candidates):
            haystack = f"{item.get('qualified_name', '')} {item.get('name', '')}"
            overlap = self._token_overlap(tokens, self._tokenize(haystack))
            item["score"] = item.get("score", 0.0) + overlap * 0.05
            if index < rerank_limit and self.chunk_indexer is not None:
                source = self._fetch_source_for_node(item.get("node_id"))
                if source:
                    chunk_score = self._score_chunks(tokens, source)
                    item["score"] += chunk_score
        return sorted(candidates, key=lambda x: x["score"], reverse=True)

    def _tokenize(self, text: str) -> list[str]:
        """A simple tokenizer that splits text by whitespace and converts to lowercase."""
        return [token.lower() for token in text.split() if token.strip()]

    def _token_overlap(self, a: list[str], b: list[str]) -> int:
        """Calculates the number of common tokens between two lists of tokens."""
        if not a or not b:
            return 0
        counter_a = Counter(a)
        counter_b = Counter(b)
        return sum((counter_a & counter_b).values())

    def _score_chunks(self, tokens: list[str], source: str) -> float:
        """
        Scores the source code by finding the chunk with the highest token overlap.

        Args:
            tokens (list[str]): The tokenized query.
            source (str): The source code to score.

        Returns:
            A score based on the best chunk's token overlap.
        """
        if not self.chunk_indexer:
            return 0.0
        best = 0
        for chunk in self.chunk_indexer.create_chunks(source):
            overlap = self._token_overlap(tokens, self._tokenize(chunk.content))
            best = max(best, overlap)
        return best * 0.03

    def _fetch_source_for_node(self, node_id: int | None) -> str | None:
        """
        Fetches the source code for a given node ID from the graph.

        Args:
            node_id (int | None): The internal ID of the node.

        Returns:
            The source code as a string, or None if it cannot be fetched.
        """
        if node_id is None:
            return None
        with self._ingestor() as ingestor:
            results = ingestor._execute_query(
                CYPHER_GET_FUNCTION_SOURCE_LOCATION, {"node_id": node_id}
            )
        if not results:
            return None
        result = results[0]
        is_valid, path_obj = validate_source_location(
            result.get("path"),
            result.get("start_line"),
            result.get("end_line"),
        )
        if not is_valid or path_obj is None:
            return None
        return extract_source_lines(
            path_obj, result.get("start_line"), result.get("end_line")
        )

    def _ingestor(self) -> MemgraphIngestor:
        """A factory method to create a `MemgraphIngestor` instance."""
        return MemgraphIngestor(
            host=self.graph_host,
            port=self.graph_port,
            batch_size=self.batch_size,
        )
