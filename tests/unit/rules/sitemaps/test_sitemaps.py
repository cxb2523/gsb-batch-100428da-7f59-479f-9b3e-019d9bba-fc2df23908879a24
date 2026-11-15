"""Smoke tests para reglas de Sitemaps."""

import pytest

from crawlforge.rules._registry import REGISTRY
from crawlforge.rules.sitemaps.sitemap_3xx_urls import Sitemap3xxUrls
from crawlforge.rules.sitemaps.sitemap_403_urls import Sitemap403Urls
from crawlforge.rules.sitemaps.sitemap_4xx_urls import Sitemap4xxUrls
from crawlforge.rules.sitemaps.sitemap_5xx_urls import Sitemap5xxUrls
from crawlforge.rules.sitemaps.sitemap_canonicalized_urls import (
    SitemapCanonicalizedUrls,
)
from crawlforge.rules.sitemaps.sitemap_disallowed_urls import SitemapDisallowedUrls
from crawlforge.rules.sitemaps.sitemap_non_indexable_urls import (
    SitemapNonIndexableUrls,
)
from crawlforge.rules.sitemaps.sitemap_over_50k_urls import SitemapOver50kUrls
from crawlforge.rules.sitemaps.sitemap_over_50mb import SitemapOver50Mb
from crawlforge.rules.sitemaps.sitemap_timeout_urls import SitemapTimeoutUrls
from crawlforge.rules.sitemaps.urls_in_multiple_sitemaps import (
    UrlsInMultipleSitemaps,
)
from crawlforge.rules.sitemaps.urls_not_in_sitemap import UrlsNotInSitemap
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        # url_id, url, status, indexable, from_sitemap, from_robots,
        # canonical, redirect_count, indexability_reason, final_url
        # 1: indexable, in sitemap, all good
        (1, "https://a.com/ok",       200, True,  True,  False, None,                 0, None,           None),
        # 2: indexable but NOT in sitemap (urls_not_in_sitemap)
        (2, "https://a.com/orphan",   200, True,  False, False, None,                 0, None,           None),
        # 3: in sitemap but non-indexable (sitemap_non_indexable_urls)
        (3, "https://a.com/noindex",  200, False, True,  False, None,                 0, "noindex",      None),
        # 4: 404 in sitemap (sitemap_4xx_urls)
        (4, "https://a.com/404",      404, False, True,  False, None,                 0, "404",          None),
        # 5: 500 in sitemap (sitemap_5xx_urls)
        (5, "https://a.com/500",      500, False, True,  False, None,                 0, "5xx",          None),
        # 6: 403 in sitemap (sitemap_403_urls + sitemap_4xx_urls — both fire)
        (6, "https://a.com/403",      403, False, True,  False, None,                 0, "403",          None),
        # 7: 301 in sitemap (sitemap_3xx_urls)
        (7, "https://a.com/old",      200, True,  True,  False, None,                 1, None,           "https://a.com/new"),
        # 8: in sitemap with canonical to other URL
        (8, "https://a.com/dup",      200, False, True,  False, "https://a.com/main", 0, "canonical",    None),
        # 9: in sitemap but blocked by robots
        (9, "https://a.com/blocked",  200, False, True,  True,  None,                 0, "robots",       None),
        # 10: in sitemap, timed out (status 0)
        (10, "https://a.com/timeout", 0,   False, True,  False, None,                 0, "no_response",  None),
        # 11: control - non-indexable, NOT in sitemap (no rule should fire)
        (11, "https://a.com/junk",    200, False, False, False, None,                 0, "noindex",      None),
        # 12: in sitemap with canonical = url (NOT a problem)
        (12, "https://a.com/self",    200, True,  True,  False, "https://a.com/self", 0, None,           None),
    ]
    for row in rows:
        (
            url_id, url, status, indexable, from_sitemap, from_robots,
            canonical, redirect_count, reason, final_url,
        ) = row
        con.execute(
            """
            INSERT INTO urls (
                url_id, url, status_code, content_type, is_indexable,
                from_sitemap, from_robots, canonical, redirect_count,
                indexability_reason, final_url
            ) VALUES (?, ?, ?, 'text/html', ?, ?, ?, ?, ?, ?, ?)
            """,
            [url_id, url, status, indexable, from_sitemap, from_robots,
             canonical, redirect_count, reason, final_url],
        )
    return con


def test_urls_not_in_sitemap(con):
    issues = list(UrlsNotInSitemap().check(con))
    flagged = {i.url_id for i in issues}
    # Indexable + status 200 + from_sitemap=FALSE
    assert flagged == {2}


def test_urls_not_in_sitemap_ignores_404(con):
    # url 11 is non-indexable so should not fire either
    issues = list(UrlsNotInSitemap().check(con))
    assert all(i.url_id != 11 for i in issues)


def test_sitemap_non_indexable_urls(con):
    issues = list(SitemapNonIndexableUrls().check(con))
    flagged = {i.url_id for i in issues}
    # All from_sitemap=TRUE AND is_indexable=FALSE
    assert flagged == {3, 4, 5, 6, 8, 9, 10}


def test_sitemap_4xx_urls(con):
    issues = list(Sitemap4xxUrls().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {4, 6}


def test_sitemap_5xx_urls(con):
    issues = list(Sitemap5xxUrls().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {5}


def test_sitemap_403_urls(con):
    issues = list(Sitemap403Urls().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {6}


def test_sitemap_3xx_urls(con):
    issues = list(Sitemap3xxUrls().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {7}


def test_sitemap_canonicalized_urls(con):
    issues = list(SitemapCanonicalizedUrls().check(con))
    flagged = {i.url_id for i in issues}
    # 8 has canonical to other URL; 12 has canonical=self (not flagged)
    assert flagged == {8}


def test_sitemap_disallowed_urls(con):
    issues = list(SitemapDisallowedUrls().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {9}


def test_sitemap_timeout_urls(con):
    issues = list(SitemapTimeoutUrls().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {10}


def test_todo_sitemap_rules_yield_empty(con):
    for cls in (SitemapOver50kUrls, SitemapOver50Mb, UrlsInMultipleSitemaps):
        assert list(cls().check(con)) == []


def test_sitemap_rules_registered():
    import crawlforge.rules.sitemaps  # noqa: F401

    expected = [
        "sitemap_over_50k_urls", "sitemap_over_50mb", "urls_not_in_sitemap",
        "sitemap_non_indexable_urls", "sitemap_4xx_urls", "sitemap_5xx_urls",
        "sitemap_403_urls", "sitemap_3xx_urls", "sitemap_canonicalized_urls",
        "sitemap_disallowed_urls", "sitemap_timeout_urls",
        "urls_in_multiple_sitemaps",
    ]
    for rid in expected:
        assert rid in REGISTRY, f"{rid} not registered"
