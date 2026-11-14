"""Crawl scheduler — periodically re-crawls known URLs via the crawl API."""
import logging
import time
import os
from datetime import datetime, timezone, timedelta

import psycopg
import redis

from intelligence.config import Config

logger = logging.getLogger(__name__)

DEFAULT_RECRAWL_HOURS = int(os.getenv("RECRAWL_INTERVAL_HOURS", "24"))
DEFAULT_MAX_AGE_HOURS = int(os.getenv("MAX_PAGE_AGE_HOURS", "72"))


class CrawlScheduler:
    """Checks for stale pages and queues them for re-crawling."""

    def __init__(self, recrawl_hours: int = DEFAULT_RECRAWL_HOURS, max_age_hours: int = DEFAULT_MAX_AGE_HOURS):
        self._conn = psycopg.connect(Config.postgres_dsn())
        self._conn.autocommit = True
        self._redis = redis.from_url(Config.REDIS_URL, decode_responses=True)
        self._recrawl_interval = timedelta(hours=recrawl_hours)
        self._max_age = timedelta(hours=max_age_hours)
        self._stream = "phantombot:crawl_requests"

    def get_stale_pages(self, limit: int = 50) -> list[dict]:
        """Find pages that haven't been crawled recently."""
        cutoff = datetime.now(timezone.utc) - self._max_age
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, url, fetched_at
                FROM crawled_pages
                WHERE fetched_at < %s
                ORDER BY fetched_at ASC
                LIMIT %s
                """,
                (cutoff, limit),
            )
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def queue_recrawl(self, url: str) -> str:
        """Queue a URL for re-crawling via Redis stream."""
        import uuid
        job_id = str(uuid.uuid4())
        self._redis.xadd(
            self._stream,
            {
                "url": url,
                "max_depth": "0",
                "job_id": job_id,
            },
        )
        return job_id

    def run_once(self) -> int:
        """Check for stale pages and queue them. Returns count of pages queued."""
        stale = self.get_stale_pages()
        if not stale:
            logger.info("No stale pages found")
            return 0

        queued = 0
        for page in stale:
            job_id = self.queue_recrawl(page["url"])
            logger.info(f"Queued re-crawl: {page['url']} (job={job_id}, last crawled={page['fetched_at']})")
            queued += 1

        return queued

    def run(self):
        """Run continuously, checking for stale pages on an interval."""
        logger.info(f"Scheduler started (interval={self._recrawl_interval}, max_age={self._max_age})")
        while True:
            try:
                count = self.run_once()
                logger.info(f"Scheduled {count} re-crawls")
            except KeyboardInterrupt:
                logger.info("Scheduler shutting down")
                break
            except Exception:
                logger.exception("Scheduler error")

            time.sleep(self._recrawl_interval.total_seconds())

    def close(self):
        self._conn.close()
        self._redis.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    scheduler = CrawlScheduler()
    try:
        scheduler.run()
    finally:
        scheduler.close()


if __name__ == "__main__":
    main()
