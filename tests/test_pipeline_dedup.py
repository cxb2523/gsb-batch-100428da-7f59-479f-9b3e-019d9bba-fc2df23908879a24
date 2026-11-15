from types import SimpleNamespace

import pytest

from scrapyhub.items import ItemValidationError
from scrapyhub.pipelines import RankingPipeline


class FakeCursor:
    def __init__(self, existing=None, insert_error=None):
        self.existing = existing
        self.insert_error = insert_error
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        if self.insert_error is not None and sql.lstrip().upper().startswith("INSERT"):
            raise self.insert_error

    def fetchone(self):
        return self.existing

    def fetchall(self):
        return []


class DuplicateKeyError(Exception):
    """Stand-in for pymysql.err.IntegrityError on a unique-key conflict."""

    def __init__(self, message="Duplicate entry 'Topic' for key 'uniq_title'"):
        super().__init__(1062, message)


def spider():
    return SimpleNamespace(name="weibo", logger=SimpleNamespace(debug=lambda *_: None))


def test_same_title_from_another_source_updates_one_existing_record():
    pipeline = RankingPipeline({})
    pipeline.cursor = FakeCursor(existing=(42,))

    item = {"title": "Topic", "url": "https://example.com", "hot_rank": 2, "source": "weibo"}
    result = pipeline.process_item(item, spider())

    assert result["hot_rank"] == 2
    assert len(pipeline.cursor.queries) == 2

    check_sql, check_params = pipeline.cursor.queries[0]
    assert "title = %s" in check_sql
    assert "source" not in check_sql.lower()
    assert check_params == ("Topic",)

    update_sql, update_params = pipeline.cursor.queries[1]
    assert update_sql.lstrip().startswith("UPDATE ranking")
    assert "created_at" not in update_sql.lower()
    assert not any("created_at" in str(value).lower() for value in update_params)
    assert update_params == (2, 1, pipeline.batch_timestamp, "Topic")


def test_missing_record_is_inserted():
    pipeline = RankingPipeline({})
    pipeline.cursor = FakeCursor(existing=None)

    pipeline.process_item(
        {"title": "New topic", "url": "https://example.com/new", "hot_rank": 1, "source": "baidu"},
        spider(),
    )

    assert len(pipeline.cursor.queries) == 2
    assert pipeline.cursor.queries[1][0].lstrip().startswith("INSERT INTO ranking")
    assert pipeline.cursor.queries[1][1] == (
        "New topic",
        "https://example.com/new",
        1,
        pipeline.batch_timestamp,
        3,
    )


def test_title_is_stripped_before_lookup():
    pipeline = RankingPipeline({})
    pipeline.cursor = FakeCursor(existing=None)

    pipeline.process_item(
        {"title": "  Padded topic  ", "url": "https://example.com/padded", "hot_rank": 1, "source": "zhihu"},
        spider(),
    )

    check_sql, check_params = pipeline.cursor.queries[0]
    assert "title = %s" in check_sql
    assert check_params == ("Padded topic",)
    assert pipeline.cursor.queries[1][1][0] == "Padded topic"


def test_whitespace_only_title_is_still_rejected():
    pipeline = RankingPipeline({})
    pipeline.cursor = FakeCursor(existing=None)

    with pytest.raises(ItemValidationError):
        pipeline.process_item(
            {"title": "   ", "url": "https://example.com", "hot_rank": 1, "source": "weibo"},
            spider(),
        )

    assert pipeline.cursor.queries == []


def test_duplicate_key_on_insert_falls_back_to_update():
    pipeline = RankingPipeline({})
    pipeline.cursor = FakeCursor(existing=None, insert_error=DuplicateKeyError())

    pipeline.process_item(
        {"title": "Topic", "url": "https://example.com/race", "hot_rank": 5, "source": "douyin"},
        spider(),
    )

    sqls = [sql for sql, _ in pipeline.cursor.queries]
    assert sqls[0].lstrip().startswith("SELECT")
    assert sqls[1].lstrip().startswith("INSERT INTO ranking")
    assert sqls[2].lstrip().startswith("UPDATE ranking")

    update_sql, update_params = pipeline.cursor.queries[2]
    assert "created_at" not in update_sql.lower()
    assert update_params == (5, 5, pipeline.batch_timestamp, "Topic")
