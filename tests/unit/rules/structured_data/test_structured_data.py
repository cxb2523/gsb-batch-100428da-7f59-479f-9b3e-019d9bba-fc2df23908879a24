import pytest

import crawlforge.rules.structured_data  # noqa: F401
from crawlforge.rules._registry import REGISTRY
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    # url 1: HTML 200 with valid schema
    # url 2: HTML 200 with INVALID schema (validation_errors)
    # url 3: HTML 200 indexable WITHOUT schema (triggers schema_missing)
    # url 4: 404 — should not appear
    urls = [
        (1, "https://a.com/p1", 200, "text/html", True),
        (2, "https://a.com/p2", 200, "text/html", True),
        (3, "https://a.com/p3", 200, "text/html", True),
        (4, "https://a.com/p4", 404, "text/html", False),
    ]
    for r in urls:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, is_indexable) "
            "VALUES (?, ?, ?, ?, ?)",
            list(r),
        )
    con.execute(
        "INSERT INTO structured_data (url_id, syntax, schema_type, valid, validation_errors) "
        "VALUES (1, 'json-ld', 'Article', TRUE, NULL)"
    )
    con.execute(
        "INSERT INTO structured_data (url_id, syntax, schema_type, valid, validation_errors) "
        "VALUES (2, 'json-ld', 'Product', FALSE, ['missing required field price'])"
    )
    return con


def test_validation_errors_flags_url_2(con):
    issues = list(REGISTRY["schema_validation_errors"]().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {2}


def test_validation_errors_does_not_flag_valid(con):
    issues = list(REGISTRY["schema_validation_errors"]().check(con))
    assert all(i.url_id != 1 for i in issues)


def test_schema_missing_flags_url_3(con):
    issues = list(REGISTRY["schema_missing"]().check(con))
    flagged = {i.url_id for i in issues}
    assert 3 in flagged


def test_schema_missing_skips_404(con):
    issues = list(REGISTRY["schema_missing"]().check(con))
    assert all(i.url_id != 4 for i in issues)
