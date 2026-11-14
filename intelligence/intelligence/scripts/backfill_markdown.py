"""One-time script to backfill markdown for already-crawled pages."""
import logging
import psycopg

from intelligence.config import Config
from intelligence.pipelines.markdown import MarkdownPipeline
from intelligence.pipelines.entity import EntityPipeline
from intelligence.pipelines.entity_resolver import EntityResolver
from intelligence.store import KnowledgeStore

logger = logging.getLogger(__name__)


def backfill():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    store = KnowledgeStore()
    md_pipeline = MarkdownPipeline()
    entity_pipeline = EntityPipeline()
    resolver = EntityResolver()

    conn = psycopg.connect(Config.postgres_dsn())
    conn.autocommit = True

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, url, html FROM crawled_pages
            WHERE html IS NOT NULL AND LENGTH(html) > 100
            ORDER BY fetched_at DESC
        """)
        pages = [(str(row[0]), row[1], row[2]) for row in cur.fetchall()]

    logger.info(f"Found {len(pages)} pages to process")

    for page_id, url, html in pages:
        # Backfill markdown if missing
        with conn.cursor() as cur:
            cur.execute("SELECT markdown IS NOT NULL FROM crawled_pages WHERE id = %s", (page_id,))
            has_markdown = cur.fetchone()[0]

        if not has_markdown:
            md_results = md_pipeline.extract(html, url)
            if md_results:
                store.save_markdown(page_id, md_results[0])
                logger.info(f"  Markdown: {url} ({md_results[0].word_count} words)")

        # Re-extract entities with v2 pipeline + resolver
        with conn.cursor() as cur:
            cur.execute("DELETE FROM entities WHERE page_id = %s", (page_id,))

        entities = entity_pipeline.extract(html, url)
        if entities:
            entities = resolver.resolve(entities)
            store.save_entities(page_id, entities)
            logger.info(f"  Entities: {len(entities)} from {url}")

    store.close()
    conn.close()
    logger.info("Backfill complete!")


if __name__ == "__main__":
    backfill()
