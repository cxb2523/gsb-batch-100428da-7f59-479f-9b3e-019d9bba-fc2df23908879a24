from functools import lru_cache

import psycopg
import redis
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

from mcp_server.config import Config


@lru_cache
def get_pg_connection() -> psycopg.Connection:
    conn = psycopg.connect(Config.postgres_dsn())
    conn.autocommit = True
    return conn


@lru_cache
def get_redis_client() -> redis.Redis:
    return redis.from_url(Config.REDIS_URL)


@lru_cache
def get_qdrant_client() -> QdrantClient:
    return QdrantClient(host=Config.QDRANT_HOST, port=Config.QDRANT_PORT)


@lru_cache
def get_embedder() -> SentenceTransformer:
    return SentenceTransformer("all-MiniLM-L6-v2")
