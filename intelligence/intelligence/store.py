import json
import uuid
from urllib.parse import urlparse

import psycopg
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from sentence_transformers import SentenceTransformer

from intelligence.config import Config
from intelligence.models import CrawlMessage, ExtractedEntity, DetectedSchema, MarkdownResult


class KnowledgeStore:
    """Writes crawl results, entities, and schemas to PostgreSQL and Qdrant."""

    EMBEDDING_MODEL = "all-MiniLM-L6-v2"
    EMBEDDING_DIM = 384

    def __init__(self):
        self._conn = psycopg.connect(Config.postgres_dsn())
        self._conn.autocommit = True
        self._qdrant = QdrantClient(host=Config.QDRANT_HOST, port=Config.QDRANT_PORT)
        self._embedder = SentenceTransformer(self.EMBEDDING_MODEL)
        self._ensure_qdrant_collection()

    def close(self):
        self._conn.close()

    def _ensure_qdrant_collection(self):
        collections = [c.name for c in self._qdrant.get_collections().collections]
        if Config.QDRANT_COLLECTION not in collections:
            self._qdrant.create_collection(
                collection_name=Config.QDRANT_COLLECTION,
                vectors_config=VectorParams(
                    size=self.EMBEDDING_DIM,
                    distance=Distance.COSINE,
                ),
            )

    def save_crawled_page(self, msg: CrawlMessage) -> str:
        domain = urlparse(msg.url).netloc
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO crawled_pages (url, domain, status_code, content_type, html, headers, fetched_at, rendered, depth)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (url) DO UPDATE SET
                    status_code = EXCLUDED.status_code,
                    content_type = EXCLUDED.content_type,
                    html = EXCLUDED.html,
                    headers = EXCLUDED.headers,
                    fetched_at = EXCLUDED.fetched_at,
                    rendered = EXCLUDED.rendered,
                    depth = EXCLUDED.depth,
                    updated_at = NOW()
                RETURNING id
                """,
                (
                    msg.url, domain, msg.status_code, msg.content_type,
                    msg.html, json.dumps(msg.headers), msg.fetched_at,
                    msg.rendered, msg.depth,
                ),
            )
            page_id = str(cur.fetchone()[0])
        return page_id

    def save_markdown(self, page_id: str, result: MarkdownResult):
        """Save extracted markdown for a crawled page."""
        meta = {
            "word_count": result.word_count,
            "has_code": result.has_code,
            "has_tables": result.has_tables,
            "has_images": result.has_images,
            "pipeline_version": result.pipeline_version,
            "extraction_method": result.extraction_method,
            "confidence": result.confidence,
        }
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE crawled_pages
                SET markdown = %s, markdown_meta = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (result.markdown, json.dumps(meta), page_id),
            )

    def get_page_markdown(self, page_id: str) -> dict | None:
        """Retrieve markdown and metadata for a crawled page."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, url, markdown, markdown_meta, fetched_at
                FROM crawled_pages
                WHERE id = %s
                """,
                (page_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            columns = [desc[0] for desc in cur.description]
            return dict(zip(columns, row))

    def save_entities(self, page_id: str, entities: list[ExtractedEntity]):
        with self._conn.cursor() as cur:
            for entity in entities:
                entity_id = str(uuid.uuid4())
                cur.execute(
                    """
                    INSERT INTO entities (id, page_id, entity_type, entity_text, start_char, end_char, confidence, pipeline_version)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        entity_id, page_id, entity.entity_type, entity.entity_text,
                        entity.start_char, entity.end_char, entity.confidence,
                        entity.pipeline_version,
                    ),
                )

                # Also index in Qdrant for semantic search
                embedding = self._embedder.encode(
                    f"{entity.entity_type}: {entity.entity_text}"
                ).tolist()
                self._qdrant.upsert(
                    collection_name=Config.QDRANT_COLLECTION,
                    points=[
                        PointStruct(
                            id=entity_id,
                            vector=embedding,
                            payload={
                                "type": "entity",
                                "page_id": page_id,
                                "entity_type": entity.entity_type,
                                "entity_text": entity.entity_text,
                                "confidence": entity.confidence,
                            },
                        )
                    ],
                )

    def save_schemas(self, page_id: str, schemas: list[DetectedSchema]):
        with self._conn.cursor() as cur:
            for schema in schemas:
                schema_id = str(uuid.uuid4())
                cur.execute(
                    """
                    INSERT INTO schemas (id, page_id, schema_type, schema_data, source_url, confidence, pipeline_version)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        schema_id, page_id, schema.schema_type,
                        json.dumps(schema.schema_data), schema.source_url,
                        schema.confidence, schema.pipeline_version,
                    ),
                )

    def query_entities(self, text: str, limit: int = 10) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.entity_text, e.entity_type, e.confidence, e.extracted_at,
                       p.url as source_url, p.fetched_at
                FROM entities e
                JOIN crawled_pages p ON e.page_id = p.id
                WHERE e.entity_text ILIKE %s
                ORDER BY e.confidence DESC
                LIMIT %s
                """,
                (f"%{text}%", limit),
            )
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def search_semantic(self, query: str, limit: int = 10) -> list[dict]:
        embedding = self._embedder.encode(query).tolist()
        results = self._qdrant.query_points(
            collection_name=Config.QDRANT_COLLECTION,
            query=embedding,
            limit=limit,
        )
        return [
            {
                "id": point.id,
                "score": point.score,
                **point.payload,
            }
            for point in results.points
        ]
