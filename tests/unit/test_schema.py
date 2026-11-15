from crawlforge.storage.schema import SCHEMA_VERSION, init_database


def test_init_creates_all_tables():
    con = init_database()
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='main' ORDER BY table_name"
    ).fetchall()
    names = {r[0] for r in rows}
    expected = {
        "crawl_meta",
        "hreflang",
        "issues",
        "links",
        "redirects",
        "resources",
        "structured_data",
        "urls",
    }
    assert expected.issubset(names), f"Missing tables: {expected - names}"


def test_schema_version_recorded():
    con = init_database()
    row = con.execute(
        "SELECT value FROM crawl_meta WHERE key='schema_version'"
    ).fetchone()
    assert row is not None
    assert row[0] == SCHEMA_VERSION


def test_can_insert_and_query_urls():
    con = init_database()
    con.execute(
        """
        INSERT INTO urls (url_id, url, status_code, title)
        VALUES (1, 'https://a.com/', 200, 'Hello')
        """
    )
    row = con.execute("SELECT url, title FROM urls WHERE url_id=1").fetchone()
    assert row == ("https://a.com/", "Hello")
