import pytest

from crawlforge.rules.meta_description.meta_desc_duplicate import MetaDescDuplicate
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        # url_id, url, status, ctype, meta_description, indexable
        (1, "https://a.com/dup-1", 200, "text/html", "Same description for two URLs", True),
        (2, "https://a.com/dup-2", 200, "text/html", "Same description for two URLs", True),
        (3, "https://a.com/uniq",  200, "text/html", "Totally unique description.",   True),
        (4, "https://a.com/null",  200, "text/html", None,                            True),
        (5, "https://a.com/empty", 200, "text/html", "",                              True),
        # Duplicate but not indexable -> should be ignored
        (6, "https://a.com/noidx-1", 200, "text/html", "Non-indexable shared", False),
        (7, "https://a.com/noidx-2", 200, "text/html", "Non-indexable shared", False),
    ]
    for url_id, url, status, ctype, md, idx in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, "
            "meta_description, is_indexable) VALUES (?, ?, ?, ?, ?, ?)",
            [url_id, url, status, ctype, md, idx],
        )
    return con


def test_flags_duplicates_across_indexable_urls(con):
    issues = list(MetaDescDuplicate().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {1, 2}
    for issue in issues:
        assert issue.evidence["duplicate_count"] == 2


def test_does_not_flag_unique_or_non_indexable(con):
    issues = list(MetaDescDuplicate().check(con))
    flagged = {i.url_id for i in issues}
    for uid in (3, 4, 5, 6, 7):
        assert uid not in flagged
