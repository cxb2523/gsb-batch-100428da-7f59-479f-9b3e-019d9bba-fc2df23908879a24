import os
import uuid
import pytest
from datetime import datetime, timezone

from intelligence.models import CrawlMessage, ExtractedEntity, DetectedSchema
from intelligence.store import KnowledgeStore


def get_store():
    """Create a store connected to test database, skip if unavailable."""
    try:
        store = KnowledgeStore()
        return store
    except Exception as e:
        pytest.skip(f"Database not available: {e}")


@pytest.fixture
def store():
    s = get_store()
    yield s
    s.close()


@pytest.fixture
def sample_crawl_message():
    return CrawlMessage(
        url="https://test.example.com/page-" + uuid.uuid4().hex[:8],
        status_code=200,
        content_type="text/html",
        html="<html><body>Test</body></html>",
        headers={"Server": "nginx"},
        fetched_at=datetime.now(timezone.utc),
        rendered=False,
        depth=0,
    )


def test_save_crawled_page(store, sample_crawl_message):
    page_id = store.save_crawled_page(sample_crawl_message)
    assert page_id is not None


def test_save_entities(store, sample_crawl_message):
    page_id = store.save_crawled_page(sample_crawl_message)

    entities = [
        ExtractedEntity(
            entity_type="ORG",
            entity_text="Google",
            start_char=0,
            end_char=6,
            confidence=0.9,
        ),
        ExtractedEntity(
            entity_type="PERSON",
            entity_text="Sundar Pichai",
            start_char=10,
            end_char=23,
            confidence=0.85,
        ),
    ]

    store.save_entities(page_id, entities)

    # Query back
    rows = store.query_entities("Google")
    assert len(rows) >= 1
    assert rows[0]["entity_text"] == "Google"
    assert rows[0]["entity_type"] == "ORG"


def test_save_schemas(store, sample_crawl_message):
    page_id = store.save_crawled_page(sample_crawl_message)

    schemas = [
        DetectedSchema(
            schema_type="JSON_LD",
            schema_data={"@type": "Organization", "name": "Test"},
            source_url=sample_crawl_message.url,
            confidence=0.95,
        ),
    ]

    store.save_schemas(page_id, schemas)


def test_upsert_crawled_page(store):
    url = "https://test.example.com/upsert-" + uuid.uuid4().hex[:8]
    msg1 = CrawlMessage(
        url=url, status_code=200, content_type="text/html",
        html="<html>v1</html>", fetched_at=datetime.now(timezone.utc),
    )
    msg2 = CrawlMessage(
        url=url, status_code=200, content_type="text/html",
        html="<html>v2</html>", fetched_at=datetime.now(timezone.utc),
    )

    id1 = store.save_crawled_page(msg1)
    id2 = store.save_crawled_page(msg2)
    # Same URL should upsert, returning the same page ID
    assert id1 == id2
