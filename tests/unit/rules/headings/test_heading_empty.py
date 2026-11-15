import pytest

from crawlforge.rules.headings.heading_empty import HeadingEmpty
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        # url_id, url, status, ctype, h1, h2, h3
        (1, "https://a.com/clean",   200, "text/html", ["Real heading"], ["Real section"], None),
        (2, "https://a.com/h2-blank", 200, "text/html", ["Real heading"], ["Real", "   "], None),
        (3, "https://a.com/h3-blank", 200, "text/html", ["Real heading"], None, ["", "Other"]),
        (4, "https://a.com/null-all", 200, "text/html", None, None, None),
        (5, "https://a.com/all-empty-arrays", 200, "text/html", [], [], []),
    ]
    for url_id, url, status, ctype, h1, h2, h3 in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, h1, h2, h3) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [url_id, url, status, ctype, h1, h2, h3],
        )
    return con


def test_flags_pages_with_blank_headings(con):
    issues = list(HeadingEmpty().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {2, 3}


def test_does_not_flag_clean_or_null_pages(con):
    issues = list(HeadingEmpty().check(con))
    flagged = {i.url_id for i in issues}
    for uid in (1, 4, 5):
        assert uid not in flagged


def test_evidence_includes_level(con):
    issues = list(HeadingEmpty().check(con))
    by_uid = {i.url_id: i for i in issues}
    assert by_uid[2].evidence["level"] == "h2"
    assert by_uid[3].evidence["level"] == "h3"
