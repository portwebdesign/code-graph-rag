import time
from uuid import UUID

from loguru import logger

from codebase_rag.core import logs as ls
from codebase_rag.core.config import settings
from codebase_rag.core.constants import PAYLOAD_NODE_ID, PAYLOAD_QUALIFIED_NAME
from codebase_rag.utils.dependencies import has_qdrant_client

_CLIENT = None

if has_qdrant_client():
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointIdsList, PointStruct, VectorParams

    _CLIENT: QdrantClient | None = None

    def get_qdrant_client() -> QdrantClient:
        """
        Initializes and returns a singleton instance of the Qdrant client.

        If the client is not already initialized, it creates a new client instance
        pointing to the database path specified in the settings. It also ensures
        that the required collection exists, creating it if necessary.

        Returns:
            QdrantClient: The singleton Qdrant client instance.
        """
        global _CLIENT
        if _CLIENT is None:
            _CLIENT = QdrantClient(path=settings.QDRANT_DB_PATH)
            if not _CLIENT.collection_exists(settings.QDRANT_COLLECTION_NAME):
                _CLIENT.create_collection(
                    collection_name=settings.QDRANT_COLLECTION_NAME,
                    vectors_config=VectorParams(
                        size=settings.QDRANT_VECTOR_DIM, distance=Distance.COSINE
                    ),
                )
        return _CLIENT

    def store_embedding(
        node_id: int, embedding: list[float], qualified_name: str
    ) -> None:
        """
        Stores a single vector embedding in the Qdrant collection.

        Args:
            node_id (int): The unique ID of the graph node associated with the embedding.
                           This is used as the point ID in Qdrant.
            embedding (list[float]): The vector embedding to store.
            qualified_name (str): The fully qualified name of the code element, stored
                                  in the payload for identification.
        """
        _QDRANT_LOCK_MSG = "already accessed"
        _QDRANT_MAX_RETRIES = 3
        _QDRANT_RETRY_DELAY = 0.5  # seconds

        for attempt in range(_QDRANT_MAX_RETRIES):
            try:
                client = get_qdrant_client()
                client.upsert(
                    collection_name=settings.QDRANT_COLLECTION_NAME,
                    points=[
                        PointStruct(
                            id=node_id,
                            vector=embedding,
                            payload={
                                PAYLOAD_NODE_ID: node_id,
                                PAYLOAD_QUALIFIED_NAME: qualified_name,
                            },
                        )
                    ],
                )
                return
            except Exception as e:
                if _QDRANT_LOCK_MSG in str(e) and attempt < _QDRANT_MAX_RETRIES - 1:
                    logger.debug(
                        f"Qdrant lock contention for '{qualified_name}', "
                        f"retrying in {_QDRANT_RETRY_DELAY}s "
                        f"(attempt {attempt + 1}/{_QDRANT_MAX_RETRIES})"
                    )
                    time.sleep(_QDRANT_RETRY_DELAY)
                    continue
                logger.warning(
                    ls.EMBEDDING_STORE_FAILED.format(name=qualified_name, error=e)
                )
                return

    def search_embeddings(
        query_embedding: list[float], top_k: int | None = None
    ) -> list[tuple[int, float]]:
        """
        Searches for embeddings similar to a query vector.

        Args:
            query_embedding (list[float]): The vector to search with.
            top_k (int | None, optional): The number of top results to return.
                                          Defaults to `settings.QDRANT_TOP_K`.

        Returns:
            list[tuple[int, float]]: A list of tuples, where each tuple contains
                                     the node ID and the similarity score of a match.
                                     Returns an empty list if the search fails.
        """
        effective_top_k = top_k if top_k is not None else settings.QDRANT_TOP_K
        try:
            client = get_qdrant_client()
            result = client.query_points(
                collection_name=settings.QDRANT_COLLECTION_NAME,
                query=query_embedding,
                limit=effective_top_k,
            )
            return [
                (hit.payload[PAYLOAD_NODE_ID], hit.score)
                for hit in result.points
                if hit.payload is not None
            ]
        except Exception as e:
            logger.warning(ls.EMBEDDING_SEARCH_FAILED.format(error=e))
            return []

    def delete_embeddings_by_node_ids(node_ids: list[int]) -> int:
        """Delete embedding points by their graph node IDs."""
        if not node_ids:
            return 0
        try:
            client = get_qdrant_client()
            point_ids: list[int | str | UUID] = list(node_ids)
            client.delete(
                collection_name=settings.QDRANT_COLLECTION_NAME,
                points_selector=PointIdsList(points=point_ids),
                wait=True,
            )
            return len(node_ids)
        except Exception as e:
            logger.warning(ls.EMBEDDING_SEARCH_FAILED.format(error=e))
            return 0

    def wipe_embeddings_collection() -> bool:
        """Delete and recreate the embedding collection."""
        try:
            client = get_qdrant_client()
            if client.collection_exists(settings.QDRANT_COLLECTION_NAME):
                client.delete_collection(settings.QDRANT_COLLECTION_NAME)
            client.create_collection(
                collection_name=settings.QDRANT_COLLECTION_NAME,
                vectors_config=VectorParams(
                    size=settings.QDRANT_VECTOR_DIM,
                    distance=Distance.COSINE,
                ),
            )
            return True
        except Exception as e:
            logger.warning(ls.EMBEDDING_SEARCH_FAILED.format(error=e))
            return False

else:

    def store_embedding(
        node_id: int, embedding: list[float], qualified_name: str
    ) -> None:
        """
        Mock function for storing embeddings. Does nothing if qdrant-client is not installed.
        """
        pass

    def search_embeddings(
        query_embedding: list[float], top_k: int | None = None
    ) -> list[tuple[int, float]]:
        """
        Mock function for searching embeddings. Returns an empty list if qdrant-client
        is not installed.
        """
        return []

    def delete_embeddings_by_node_ids(node_ids: list[int]) -> int:
        """Mock function when qdrant-client is unavailable."""
        return 0

    def wipe_embeddings_collection() -> bool:
        """Mock function when qdrant-client is unavailable."""
        return False
