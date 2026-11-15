"""Smoke tests para reglas de Directives."""

import pytest

from crawlforge.rules._registry import REGISTRY
from crawlforge.rules.directives.html_over_2mb import HtmlOver2Mb
from crawlforge.rules.directives.nofollow import Nofollow
from crawlforge.rules.directives.nofollow_html_and_header import NofollowHtmlAndHeader
from crawlforge.rules.directives.nofollow_html_vs_header_mismatch import (
    NofollowHtmlVsHeaderMismatch,
)
from crawlforge.rules.directives.noimageindex import Noimageindex
from crawlforge.rules.directives.noindex import Noindex
from crawlforge.rules.directives.noindex_html_and_header import NoindexHtmlAndHeader
from crawlforge.rules.directives.noindex_html_vs_header_mismatch import (
    NoindexHtmlVsHeaderMismatch,
)
from crawlforge.rules.directives.noindex_nofollow_combined import (
    NoindexNofollowCombined,
)
from crawlforge.rules.directives.none import NoneDirective
from crawlforge.rules.directives.noodp import Noodp
from crawlforge.rules.directives.nosnippet import Nosnippet
from crawlforge.rules.directives.notranslate import Notranslate
from crawlforge.rules.directives.noydir import Noydir
from crawlforge.rules.directives.unavailable_after import UnavailableAfter
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()
    rows = [
        # url_id, url, content_type, content_length, meta_robots, x_robots_tag
        (1, "https://a.com/clean",      "text/html",  10_000,         None,                                          None),
        (2, "https://a.com/noindex",    "text/html",  10_000,         "noindex",                                     None),
        (3, "https://a.com/nofollow",   "text/html",  10_000,         "nofollow",                                    None),
        (4, "https://a.com/both",       "text/html",  10_000,         "noindex, nofollow",                           None),
        (5, "https://a.com/none",       "text/html",  10_000,         "none",                                        None),
        (6, "https://a.com/header-ni",  "text/html",  10_000,         None,                                          "noindex"),
        (7, "https://a.com/big",        "text/html",  3 * 1024 * 1024, None,                                         None),
        (8, "https://a.com/legacy",     "text/html",  10_000,         "noodp, noydir",                               None),
        (9, "https://a.com/snippet",    "text/html",  10_000,         "nosnippet, noimageindex, notranslate",        None),
        (10, "https://a.com/avail",     "text/html",  10_000,         "unavailable_after: 2025-01-01",               None),
        (11, "https://a.com/redundant", "text/html",  10_000,         "noindex",                                     "noindex"),
        (12, "https://a.com/ndmix",     "text/html",  10_000,         "noindex",                                     "noarchive"),
        (13, "https://a.com/nofo-mix",  "text/html",  10_000,         "nofollow",                                    "noarchive"),
        (14, "https://a.com/nf-both",   "text/html",  10_000,         "nofollow",                                    "nofollow"),
        # nonexistent: must NOT trigger `none` (token-aware)
        (15, "https://a.com/nonex",     "text/html",  10_000,         "nonexistent",                                 None),
    ]
    for url_id, url, ctype, clen, mr, xrt in rows:
        con.execute(
            """
            INSERT INTO urls (url_id, url, status_code, content_type,
                              content_length, meta_robots, x_robots_tag)
            VALUES (?, ?, 200, ?, ?, ?, ?)
            """,
            [url_id, url, ctype, clen, mr, xrt],
        )
    return con


def test_noindex_flags_meta_and_header(con):
    issues = list(Noindex().check(con))
    flagged = {i.url_id for i in issues}
    # 2 (meta), 4 (combined), 5 (none also has noindex semantics? — no, "none" string contains no "noindex" substring),
    # 6 (header), 11 (both), 12 (meta only)
    assert flagged == {2, 4, 6, 11, 12}


def test_noindex_does_not_flag_clean(con):
    issues = list(Noindex().check(con))
    assert all(i.url_id not in (1, 3) for i in issues)


def test_nofollow_flags_correctly(con):
    issues = list(Nofollow().check(con))
    flagged = {i.url_id for i in issues}
    # 3 (meta), 4 (combined), 13 (meta only), 14 (both)
    assert flagged == {3, 4, 13, 14}


def test_none_directive_token_aware(con):
    issues = list(NoneDirective().check(con))
    flagged = {i.url_id for i in issues}
    # 5 has "none" as a token; 15 has "nonexistent" — must NOT match
    assert flagged == {5}


def test_noindex_nofollow_combined(con):
    issues = list(NoindexNofollowCombined().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {4}


def test_unavailable_after(con):
    issues = list(UnavailableAfter().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {10}


def test_nosnippet_noimageindex_notranslate(con):
    assert {i.url_id for i in Nosnippet().check(con)} == {9}
    assert {i.url_id for i in Noimageindex().check(con)} == {9}
    assert {i.url_id for i in Notranslate().check(con)} == {9}


def test_legacy_directives(con):
    assert {i.url_id for i in Noodp().check(con)} == {8}
    assert {i.url_id for i in Noydir().check(con)} == {8}


def test_noindex_html_vs_header_mismatch(con):
    issues = list(NoindexHtmlVsHeaderMismatch().check(con))
    flagged = {i.url_id for i in issues}
    # url 2: meta noindex, no header -> mismatch
    # url 6: header noindex, no meta -> mismatch
    # url 12: meta noindex, header noarchive (no noindex) -> mismatch
    # url 11: both have noindex -> NOT mismatch
    assert flagged == {2, 4, 6, 12}


def test_nofollow_html_vs_header_mismatch(con):
    issues = list(NofollowHtmlVsHeaderMismatch().check(con))
    flagged = {i.url_id for i in issues}
    # url 3: meta nofollow, no header
    # url 4: meta nofollow, no header
    # url 13: meta nofollow, header noarchive (no nofollow)
    # url 14: both have nofollow -> NOT mismatch
    assert flagged == {3, 4, 13}


def test_noindex_html_and_header_redundant(con):
    issues = list(NoindexHtmlAndHeader().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {11}


def test_nofollow_html_and_header_redundant(con):
    issues = list(NofollowHtmlAndHeader().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {14}


def test_html_over_2mb(con):
    issues = list(HtmlOver2Mb().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {7}
    for issue in issues:
        assert issue.evidence["content_length_bytes"] > 2 * 1024 * 1024


def test_html_over_2mb_only_html(con):
    # Insert non-HTML 3MB resource — must not trigger
    con.execute(
        """
        INSERT INTO urls (url_id, url, status_code, content_type, content_length)
        VALUES (99, 'https://a.com/big.pdf', 200, 'application/pdf', ?)
        """,
        [3 * 1024 * 1024],
    )
    issues = list(HtmlOver2Mb().check(con))
    assert all(i.url_id != 99 for i in issues)


def test_directives_rules_registered():
    import crawlforge.rules.directives  # noqa: F401

    expected = [
        "noindex", "nofollow", "none", "noindex_nofollow_combined",
        "unavailable_after", "nosnippet", "noimageindex", "noodp",
        "noydir", "notranslate", "noindex_html_vs_header_mismatch",
        "nofollow_html_vs_header_mismatch", "noindex_html_and_header",
        "nofollow_html_and_header", "html_over_2mb",
        # TODO(schema) stubs — should also be registered (just disabled)
        "form_with_get_method", "robots_meta_outside_head",
        "noindex_multiple", "nofollow_multiple", "noindex_only_in_source",
        "nofollow_only_in_source", "disallowed_js_file", "disallowed_image",
        "disallowed_css", "internal_disallowed_links",
        "head_noscript_with_image", "head_invalid_html", "head_noscript_tag",
    ]
    for rid in expected:
        assert rid in REGISTRY, f"{rid} not registered"


def test_todo_rules_yield_empty(con):
    """All TODO(schema) stubs should yield no issues."""
    from crawlforge.rules.directives.disallowed_css import DisallowedCss
    from crawlforge.rules.directives.disallowed_image import DisallowedImage
    from crawlforge.rules.directives.disallowed_js_file import DisallowedJsFile
    from crawlforge.rules.directives.form_with_get_method import FormWithGetMethod
    from crawlforge.rules.directives.head_invalid_html import HeadInvalidHtml
    from crawlforge.rules.directives.head_noscript_tag import HeadNoscriptTag
    from crawlforge.rules.directives.head_noscript_with_image import (
        HeadNoscriptWithImage,
    )
    from crawlforge.rules.directives.internal_disallowed_links import (
        InternalDisallowedLinks,
    )
    from crawlforge.rules.directives.nofollow_multiple import NofollowMultiple
    from crawlforge.rules.directives.nofollow_only_in_source import (
        NofollowOnlyInSource,
    )
    from crawlforge.rules.directives.noindex_multiple import NoindexMultiple
    from crawlforge.rules.directives.noindex_only_in_source import (
        NoindexOnlyInSource,
    )
    from crawlforge.rules.directives.robots_meta_outside_head import (
        RobotsMetaOutsideHead,
    )

    for cls in (
        DisallowedCss, DisallowedImage, DisallowedJsFile, FormWithGetMethod,
        HeadInvalidHtml, HeadNoscriptTag, HeadNoscriptWithImage,
        InternalDisallowedLinks, NofollowMultiple, NofollowOnlyInSource,
        NoindexMultiple, NoindexOnlyInSource, RobotsMetaOutsideHead,
    ):
        assert list(cls().check(con)) == []
