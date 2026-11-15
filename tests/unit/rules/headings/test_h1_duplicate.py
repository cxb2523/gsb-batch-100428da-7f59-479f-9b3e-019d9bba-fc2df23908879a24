import pytest

from crawlforge.rules.headings.h1_duplicate import H1Duplicate
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        (1, "https://a.com/dup-1", 200, "text/html", ["Shared headline"], True),
        (2, "https://a.com/dup-2", 200, "text/html", ["Shared headline"], True),
        (3, "https://a.com/uniq",  200, "text/html", ["Different headline"], True),
        (4, "https://a.com/null",  200, "text/html", None, True),
        (5, "https://a.com/empty", 200, "text/html", [], True),
        # Duplicates but not indexable -> ignored
        (6, "https://a.com/noidx-1", 200, "text/html", ["Non-indexable shared"], False),
        (7, "https://a.com/noidx-2", 200, "text/html", ["Non-indexable shared"], False),
    ]
    for url_id, url, status, ctype, h1, idx in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, h1, is_indexable) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [url_id, url, status, ctype, h1, idx],
        )
    return con


def test_flags_duplicates_across_indexable_urls(con):
    issues = list(H1Duplicate().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {1, 2}
    for issue in issues:
        assert issue.evidence["duplicate_count"] == 2


def test_does_not_flag_unique_or_non_indexable(con):
    issues = list(H1Duplicate().check(con))
    flagged = {i.url_id for i in issues}
    for uid in (3, 4, 5, 6, 7):
        assert uid not in flagged
