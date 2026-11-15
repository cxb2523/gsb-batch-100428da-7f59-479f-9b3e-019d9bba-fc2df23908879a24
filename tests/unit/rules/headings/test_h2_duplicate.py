import pytest

from crawlforge.rules.headings.h2_duplicate import H2Duplicate
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        # url_id, url, status, ctype, h2, indexable
        (1, "https://a.com/dup-1", 200, "text/html", ["Shared section", "Unique 1"], True),
        (2, "https://a.com/dup-2", 200, "text/html", ["Shared section", "Unique 2"], True),
        (3, "https://a.com/uniq",  200, "text/html", ["Different one"], True),
        (4, "https://a.com/null",  200, "text/html", None, True),
        # Duplicate but not indexable -> ignored
        (5, "https://a.com/noidx-1", 200, "text/html", ["Non-indexable"], False),
        (6, "https://a.com/noidx-2", 200, "text/html", ["Non-indexable"], False),
    ]
    for url_id, url, status, ctype, h2, idx in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, h2, is_indexable) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [url_id, url, status, ctype, h2, idx],
        )
    return con


def test_flags_duplicate_h2_across_indexable_urls(con):
    issues = list(H2Duplicate().check(con))
    flagged = {i.url_id for i in issues}
    # urls 1 and 2 share "Shared section"
    assert {1, 2}.issubset(flagged)


def test_does_not_flag_unique_or_non_indexable(con):
    issues = list(H2Duplicate().check(con))
    flagged = {i.url_id for i in issues}
    for uid in (3, 4, 5, 6):
        assert uid not in flagged
