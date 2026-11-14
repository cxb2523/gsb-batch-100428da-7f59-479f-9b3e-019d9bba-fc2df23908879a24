# rag/retriever.py
"""Qdrant vector search for retrieving relevant knowledge chunks."""

import logging
from qdrant_client import QdrantClient
from qdrant_client.models import ScoredPoint  # type: ignore

logger = logging.getLogger(__name__)

COLLECTION_NAME = "knowledge_base"
VECTOR_SIZE = 512  # bge-small-zh-v1.5 embedding dimension


def ensure_collection(client: QdrantClient,
                      collection_name: str = COLLECTION_NAME) -> None:
    """Create the collection if it doesn't exist."""
    from qdrant_client.models import Distance, VectorParams
    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        logger.info(f"Created collection: {collection_name}")


def search(query_vector: list[float], client: QdrantClient,
           collection_name: str = COLLECTION_NAME,
           top_k: int = 5) -> list[ScoredPoint]:
    """Search Qdrant for the most relevant knowledge chunks."""
    return client.query_points(
        collection_name=collection_name,
        query=query_vector,
        limit=top_k,
    ).points
