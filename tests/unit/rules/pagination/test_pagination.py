"""Tests para reglas de Pagination (7 reglas)."""

import pytest

from crawlforge.rules._registry import REGISTRY
from crawlforge.rules.pagination.pagination_non_indexable import (
    PaginationNonIndexable,
)
from crawlforge.rules.pagination.pagination_unlinked import PaginationUnlinked
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        # url_id, url, status_code, content_type, is_indexable, indexability_reason
        (1, "https://a.com/category", 200, "text/html", True, None),
        (2, "https://a.com/category?page=2", 200, "text/html", True, None),
        (3, "https://a.com/category?page=3", 200, "text/html", True, None),
        # paginated but with noindex
        (4, "https://a.com/blog?page=4", 200, "text/html", False, "noindex"),
        # /page/N path-based pagination, unlinked
        (5, "https://a.com/news/page/2", 200, "text/html", True, None),
        # ?p= variant indexable but linked
        (6, "https://a.com/shop?p=2", 200, "text/html", True, None),
        # non-paginated URL — should never fire pagination rules
        (7, "https://a.com/about", 200, "text/html", True, None),
    ]
    for r in rows:
        con.execute(
            """
            INSERT INTO urls (
                url_id, url, status_code, content_type,
                is_indexable, indexability_reason
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            list(r),
        )

    # Populate inlinks: 2 and 6 are linked; 3 and 5 are not.
    link_rows = [
        # source_url_id, target_url, target_url_id
        (1, "https://a.com/category?page=2", 2),
        (1, "https://a.com/shop?p=2", 6),
        # url 3 not linked from anywhere
        # url 4 not linked
        # url 5 not linked
    ]
    for source_id, target_url, target_id in link_rows:
        con.execute(
            "INSERT INTO links (source_url_id, target_url, target_url_id) "
            "VALUES (?, ?, ?)",
            [source_id, target_url, target_id],
        )

    return con


# ---------- pagination_unlinked ----------

def test_pagination_unlinked_flags_page_query_without_inlinks(con):
    issues = list(PaginationUnlinked().check(con))
    flagged = {i.url_id for i in issues}
    assert 3 in flagged  # ?page=3 not linked
    assert 4 in flagged  # ?page=4 not linked
    assert 5 in flagged  # /page/2 path-based, not linked


def test_pagination_unlinked_does_not_flag_linked_pages(con):
    issues = list(PaginationUnlinked().check(con))
    flagged = {i.url_id for i in issues}
    assert 2 not in flagged  # linked
    assert 6 not in flagged  # linked
    assert 7 not in flagged  # not paginated at all


# ---------- pagination_non_indexable ----------

def test_pagination_non_indexable_flags_noindex_paginated(con):
    issues = list(PaginationNonIndexable().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {4}


def test_pagination_non_indexable_negative_indexable_pages(con):
    issues = list(PaginationNonIndexable().check(con))
    flagged = {i.url_id for i in issues}
    assert 2 not in flagged
    assert 7 not in flagged


# ---------- registry sanity ----------

def test_all_pagination_rules_registered():
    import crawlforge.rules.pagination  # noqa: F401

    expected = {
        "pagination_unlinked",
        "pagination_non_indexable",
        "pagination_not_in_anchor",
        "pagination_non_200",
        "pagination_multiple",
        "pagination_loop",
        "pagination_sequence_error",
    }
    for rid in expected:
        assert rid in REGISTRY, f"{rid} not registered"
    assert len(expected) == 7


# ---------- TODO(schema) rules return empty ----------

@pytest.mark.parametrize(
    "rule_module",
    [
        "pagination_not_in_anchor",
        "pagination_non_200",
        "pagination_multiple",
        "pagination_loop",
        "pagination_sequence_error",
    ],
)
def test_todo_schema_rules_return_empty(con, rule_module):
    import importlib

    mod = importlib.import_module(f"crawlforge.rules.pagination.{rule_module}")
    rule_cls = next(
        v
        for v in vars(mod).values()
        if isinstance(v, type) and v.__module__ == mod.__name__ and hasattr(v, "id")
    )
    rule = rule_cls()
    assert rule.enabled_by_default is False
    assert list(rule.check(con)) == []
