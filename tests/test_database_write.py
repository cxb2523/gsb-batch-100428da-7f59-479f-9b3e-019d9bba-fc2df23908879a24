from types import SimpleNamespace

from scrapyhub.pipelines import RankingPipeline


class FakeCursor:
    def __init__(self, indexes=None):
        self.indexes = indexes or []
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))

    def fetchone(self):
        return None

    def fetchall(self):
        return self.indexes

    def close(self):
        pass


class FakeConnection:
    def __init__(self, indexes=None):
        self.cursor_instance = FakeCursor(indexes)

    def cursor(self):
        return self.cursor_instance

    def close(self):
        pass


def test_open_spider_creates_table_and_processes_item(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(
        "scrapyhub.pipelines.DBPoolManager.get_connection",
        lambda settings: connection,
    )
    pipeline = RankingPipeline({"DATABASE": "test"})
    spider = SimpleNamespace(name="baidu", logger=SimpleNamespace(info=lambda *_: None, debug=lambda *_: None))

    pipeline.open_spider(spider)
    pipeline.process_item(
        {"title": "Database topic", "url": "https://example.com/db", "hot_rank": 1, "source": "baidu"},
        spider,
    )

    queries = connection.cursor_instance.queries
    assert "CREATE TABLE IF NOT EXISTS ranking" in queries[0][0]
    assert "UNIQUE KEY uniq_title (title)" in queries[0][0]
    assert any("ALTER TABLE ranking ADD UNIQUE KEY" in query for query, _ in queries)
    assert any("INSERT INTO ranking" in query for query, _ in queries)


def test_existing_unique_index_is_not_added_again(monkeypatch):
    indexes = [
        ("ranking", 0, "PRIMARY", 1, "id"),
        ("ranking", 0, "uniq_title", 1, "title"),
    ]
    connection = FakeConnection(indexes)
    monkeypatch.setattr(
        "scrapyhub.pipelines.DBPoolManager.get_connection",
        lambda settings: connection,
    )
    pipeline = RankingPipeline({"DATABASE": "test"})
    spider = SimpleNamespace(name="baidu", logger=SimpleNamespace(info=lambda *_: None, debug=lambda *_: None))

    pipeline.open_spider(spider)

    assert not any("ALTER TABLE ranking ADD UNIQUE KEY" in query for query, _ in connection.cursor_instance.queries)


class FailingAlterCursor(FakeCursor):
    def execute(self, sql, params=None):
        super().execute(sql, params)
        if "ALTER TABLE ranking ADD UNIQUE KEY" in sql:
            raise RuntimeError("Duplicate entry 'Topic' for key 'uniq_title'")


class FakeFailingConnection(FakeConnection):
    def __init__(self):
        self.cursor_instance = FailingAlterCursor()


def test_legacy_table_with_duplicate_titles_still_opens_and_warns(monkeypatch):
    connection = FakeFailingConnection()
    monkeypatch.setattr(
        "scrapyhub.pipelines.DBPoolManager.get_connection",
        lambda settings: connection,
    )
    warnings = []
    spider = SimpleNamespace(
        name="baidu",
        logger=SimpleNamespace(
            info=lambda *_: None,
            debug=lambda *_: None,
            warning=lambda message, *_: warnings.append(message),
        ),
    )

    pipeline = RankingPipeline({"DATABASE": "test"})
    pipeline.open_spider(spider)
    pipeline.process_item(
        {"title": "Topic", "url": "https://example.com/db", "hot_rank": 1, "source": "baidu"},
        spider,
    )

    assert len(warnings) == 1
    assert "uniq_title" in warnings[0] or "唯一约束" in warnings[0]
    assert any("INSERT INTO ranking" in query for query, _ in connection.cursor_instance.queries)


class DuplicateIndexCursor(FakeCursor):
    def execute(self, sql, params=None):
        super().execute(sql, params)
        if "ALTER TABLE ranking ADD UNIQUE KEY" in sql:
            raise RuntimeError(1061, "Duplicate key name 'uniq_title'")


class FakeDuplicateIndexConnection(FakeConnection):
    def __init__(self):
        self.cursor_instance = DuplicateIndexCursor()


def test_repeated_table_setup_does_not_fail_when_index_exists(monkeypatch):
    connection = FakeDuplicateIndexConnection()
    monkeypatch.setattr(
        "scrapyhub.pipelines.DBPoolManager.get_connection",
        lambda settings: connection,
    )
    spider = SimpleNamespace(
        name="baidu",
        logger=SimpleNamespace(info=lambda *_: None, debug=lambda *_: None, warning=lambda *_: None),
    )

    RankingPipeline({"DATABASE": "test"}).open_spider(spider)

    assert any("ALTER TABLE ranking ADD UNIQUE KEY" in query for query, _ in connection.cursor_instance.queries)
