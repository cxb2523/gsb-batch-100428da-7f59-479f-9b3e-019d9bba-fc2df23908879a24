import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta


def test_get_stale_pages():
    """Test that stale pages query works."""
    with patch("intelligence.scheduler.psycopg") as mock_psycopg, \
         patch("intelligence.scheduler.redis"):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("id",), ("url",), ("fetched_at",)]
        mock_cursor.fetchall.return_value = [
            ("id-1", "https://example.com", datetime(2026, 4, 1, tzinfo=timezone.utc)),
        ]
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_psycopg.connect.return_value = mock_conn

        from intelligence.scheduler import CrawlScheduler
        scheduler = CrawlScheduler.__new__(CrawlScheduler)
        scheduler._conn = mock_conn
        scheduler._redis = MagicMock()
        scheduler._max_age = timedelta(hours=72)
        scheduler._stream = "phantombot:crawl_requests"

        pages = scheduler.get_stale_pages()
        assert len(pages) == 1
        assert pages[0]["url"] == "https://example.com"


def test_queue_recrawl():
    """Test that re-crawl queuing works."""
    from intelligence.scheduler import CrawlScheduler
    scheduler = CrawlScheduler.__new__(CrawlScheduler)
    scheduler._redis = MagicMock()
    scheduler._stream = "phantombot:crawl_requests"

    job_id = scheduler.queue_recrawl("https://example.com")
    assert len(job_id) > 0
    scheduler._redis.xadd.assert_called_once()


def test_run_once_no_stale():
    """Test run_once with no stale pages."""
    from intelligence.scheduler import CrawlScheduler
    scheduler = CrawlScheduler.__new__(CrawlScheduler)
    scheduler._conn = MagicMock()
    scheduler._redis = MagicMock()
    scheduler._max_age = timedelta(hours=72)
    scheduler._stream = "phantombot:crawl_requests"

    mock_cursor = MagicMock()
    mock_cursor.description = [("id",), ("url",), ("fetched_at",)]
    mock_cursor.fetchall.return_value = []
    scheduler._conn.cursor.return_value.__enter__ = lambda s: mock_cursor
    scheduler._conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    count = scheduler.run_once()
    assert count == 0
