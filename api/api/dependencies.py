import json
from functools import lru_cache

import psycopg
import redis
from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

from api.config import Config

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(key: str | None = Security(api_key_header)):
    if not Config.API_KEY:
        return  # Auth disabled
    if key != Config.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


@lru_cache
def get_redis_client() -> redis.Redis:
    return redis.from_url(Config.REDIS_URL)


@lru_cache
def get_pg_connection() -> psycopg.Connection:
    conn = psycopg.connect(Config.postgres_dsn())
    conn.autocommit = True
    return conn


@lru_cache
def get_qdrant_client() -> QdrantClient:
    return QdrantClient(host=Config.QDRANT_HOST, port=Config.QDRANT_PORT)


@lru_cache
def get_embedder() -> SentenceTransformer:
    return SentenceTransformer("all-MiniLM-L6-v2")
