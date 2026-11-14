import json
import logging
import time
from datetime import datetime

import redis

from intelligence.config import Config
from intelligence.models import CrawlMessage
from intelligence.pipelines.entity import EntityPipeline
from intelligence.pipelines.entity_resolver import EntityResolver
from intelligence.pipelines.markdown import MarkdownPipeline
from intelligence.pipelines.schema import SchemaPipeline
from intelligence.store import KnowledgeStore

logger = logging.getLogger(__name__)


class Worker:
    """Consumes crawled pages from Redis Streams, runs pipelines, stores results."""

    def __init__(self):
        self._redis = redis.from_url(Config.REDIS_URL, decode_responses=True)
        self._store = KnowledgeStore()
        self._entity_pipeline = EntityPipeline()
        self._entity_resolver = EntityResolver()
        self._schema_pipeline = SchemaPipeline()
        self._markdown_pipeline = MarkdownPipeline()
        self._stream = Config.REDIS_STREAM_NAME
        self._group = Config.REDIS_CONSUMER_GROUP
        self._consumer = f"worker-{id(self)}"

    def setup(self):
        """Create consumer group if it doesn't exist."""
        try:
            self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except redis.exceptions.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    @staticmethod
    def parse_message(raw: dict) -> CrawlMessage:
        # Normalize bytes keys/values to strings (Redis without decode_responses)
        decoded = {}
        for k, v in raw.items():
            key = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            decoded[key] = val

        headers = decoded.get("headers", "{}")
        if isinstance(headers, str):
            headers = json.loads(headers)

        return CrawlMessage(
            url=decoded["url"],
            status_code=int(decoded["status_code"]),
            content_type=decoded.get("content_type", ""),
            html=decoded.get("html", ""),
            headers=headers,
            fetched_at=datetime.fromisoformat(decoded["fetched_at"]),
            rendered=decoded.get("rendered", "false").lower() == "true",
            depth=int(decoded.get("depth", "0")),
            job_id=decoded.get("job_id"),
        )

    def process_message(self, msg: CrawlMessage):
        """Run all pipelines on a crawled page and store results."""
        logger.info(f"Processing: {msg.url}")

        page_id = self._store.save_crawled_page(msg)

        # Markdown extraction (run first -- other pipelines could use it)
        md_results = self._markdown_pipeline.extract(msg.html, msg.url)
        if md_results:
            self._store.save_markdown(page_id, md_results[0])
            logger.info(f"  Extracted markdown ({md_results[0].word_count} words) from {msg.url}")

        # Entity extraction
        entities = self._entity_pipeline.extract(msg.html, msg.url)
        if entities:
            entities = self._entity_resolver.resolve(entities)
            self._store.save_entities(page_id, entities)
            logger.info(f"  Extracted {len(entities)} entities from {msg.url}")

        # Schema detection
        schemas = self._schema_pipeline.extract(msg.html, msg.url)
        if schemas:
            self._store.save_schemas(page_id, schemas)
            logger.info(f"  Detected {len(schemas)} schemas from {msg.url}")

    def run(self):
        """Main loop: consume from Redis Streams, process, acknowledge."""
        self.setup()
        logger.info(f"Worker started, consuming from {self._stream}")

        while True:
            try:
                messages = self._redis.xreadgroup(
                    groupname=self._group,
                    consumername=self._consumer,
                    streams={self._stream: ">"},
                    count=1,
                    block=5000,
                )

                if not messages:
                    continue

                for stream_name, stream_messages in messages:
                    for msg_id, raw in stream_messages:
                        try:
                            msg = self.parse_message(raw)
                            self.process_message(msg)
                            self._redis.xack(self._stream, self._group, msg_id)
                        except Exception:
                            logger.exception(f"Error processing message {msg_id}")

            except KeyboardInterrupt:
                logger.info("Worker shutting down")
                break
            except Exception:
                logger.exception("Unexpected error in worker loop")
                time.sleep(1)

    def close(self):
        self._store.close()
        self._redis.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker = Worker()
    try:
        worker.run()
    finally:
        worker.close()


if __name__ == "__main__":
    main()
