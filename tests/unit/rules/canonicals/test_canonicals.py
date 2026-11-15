"""Tests para reglas de Canonicals (categoría densa: 31 reglas)."""

import pytest

from crawlforge.rules._registry import REGISTRY
from crawlforge.rules.canonicals.canonical_chain import CanonicalChain
from crawlforge.rules.canonicals.canonical_contains_fragment import (
    CanonicalContainsFragment,
)
from crawlforge.rules.canonicals.canonical_loop import CanonicalLoop
from crawlforge.rules.canonicals.canonical_malformed_or_empty import (
    CanonicalMalformedOrEmpty,
)
from crawlforge.rules.canonicals.canonical_missing import CanonicalMissing
from crawlforge.rules.canonicals.canonical_to_3xx import CanonicalTo3xx
from crawlforge.rules.canonicals.canonical_to_4xx import CanonicalTo4xx
from crawlforge.rules.canonicals.canonical_to_5xx import CanonicalTo5xx
from crawlforge.rules.canonicals.canonical_to_different_internal import (
    CanonicalToDifferentInternal,
)
from crawlforge.rules.canonicals.canonical_to_disallowed import (
    CanonicalToDisallowed,
)
from crawlforge.rules.canonicals.canonical_to_external import (
    CanonicalToExternal,
)
from crawlforge.rules.canonicals.canonical_to_homepage import (
    CanonicalToHomepage,
)
from crawlforge.rules.canonicals.canonical_to_http import CanonicalToHttp
from crawlforge.rules.canonicals.canonical_to_https import CanonicalToHttps
from crawlforge.rules.canonicals.canonical_to_noindex import (
    CanonicalToNoindex,
)
from crawlforge.rules.canonicals.canonical_to_non_indexable import (
    CanonicalToNonIndexable,
)
from crawlforge.rules.canonicals.canonical_unlinked import CanonicalUnlinked
from crawlforge.rules.canonicals.canonicalised import Canonicalised
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    """Fixture compartido con un grafo de URLs realista para canonicals."""
    con = init_database()

    # Schema columns of interest:
    # url_id, url, status_code, content_type, canonical, is_indexable,
    # indexability_reason, from_robots, redirect_count, meta_robots, x_robots_tag

    rows = [
        # 1: missing canonical (should fire canonical_missing)
        (1, "https://a.com/no-canonical", 200, "text/html", None, True, None, False, 0, None, None),
        # 2: self-canonical OK (should NOT fire anything)
        (2, "https://a.com/ok", 200, "text/html", "https://a.com/ok", True, None, False, 0, None, None),
        # 3: canonical with #fragment + canonicalised
        (3, "https://a.com/frag", 200, "text/html", "https://a.com/target#section", True, None, False, 0, None, None),
        # 4: malformed canonical (empty)
        (4, "https://a.com/empty-canon", 200, "text/html", "", True, None, False, 0, None, None),
        # 5: malformed canonical (not http)
        (5, "https://a.com/bad-canon", 200, "text/html", "javascript:void(0)", True, None, False, 0, None, None),
        # 6: canonical to non-indexable target (#7)
        (6, "https://a.com/to-noidx", 200, "text/html", "https://a.com/noindex-target", True, None, False, 0, None, None),
        # 7: target — non-indexable due to noindex meta
        (7, "https://a.com/noindex-target", 200, "text/html", "https://a.com/noindex-target", False, "noindex", False, 0, "noindex,follow", None),
        # 8: canonical to 4xx target (#9)
        (8, "https://a.com/to-404", 200, "text/html", "https://a.com/404-target", True, None, False, 0, None, None),
        (9, "https://a.com/404-target", 404, "text/html", None, False, "404", False, 0, None, None),
        # 10: canonical to 5xx (#11)
        (10, "https://a.com/to-500", 200, "text/html", "https://a.com/500-target", True, None, False, 0, None, None),
        (11, "https://a.com/500-target", 503, "text/html", None, False, "503", False, 0, None, None),
        # 12: canonical to 3xx redirect target (#13)
        (12, "https://a.com/to-redir", 200, "text/html", "https://a.com/redir-target", True, None, False, 0, None, None),
        (13, "https://a.com/redir-target", 301, "text/html", None, False, "redirect", False, 1, None, None),
        # 14: canonical to robots-disallowed (#15)
        (14, "https://a.com/to-robots", 200, "text/html", "https://a.com/blocked-target", True, None, False, 0, None, None),
        (15, "https://a.com/blocked-target", 200, "text/html", "https://a.com/blocked-target", False, "robots", True, 0, None, None),
        # 16: canonical to homepage (homepage = #17)
        (16, "https://a.com/category", 200, "text/html", "https://a.com/", True, None, False, 0, None, None),
        (17, "https://a.com/", 200, "text/html", "https://a.com/", True, None, False, 0, None, None),
        # 18: HTTPS page with canonical to HTTP (mixed)
        (18, "https://a.com/secure", 200, "text/html", "http://a.com/secure", True, None, False, 0, None, None),
        # 19: HTTP page with canonical to HTTPS
        (19, "http://a.com/insecure", 200, "text/html", "https://a.com/insecure", True, None, False, 0, None, None),
        # 20: canonical to external domain
        (20, "https://a.com/syndicated", 200, "text/html", "https://otherdomain.com/orig", True, None, False, 0, None, None),
        # 21: canonical to different internal
        (21, "https://a.com/variant", 200, "text/html", "https://a.com/canonical-version", True, None, False, 0, None, None),
        (22, "https://a.com/canonical-version", 200, "text/html", "https://a.com/canonical-version", True, None, False, 0, None, None),
        # 23-25: canonical chain A -> B -> C
        (23, "https://a.com/chain-a", 200, "text/html", "https://a.com/chain-b", True, None, False, 0, None, None),
        (24, "https://a.com/chain-b", 200, "text/html", "https://a.com/chain-c", True, None, False, 0, None, None),
        (25, "https://a.com/chain-c", 200, "text/html", "https://a.com/chain-c", True, None, False, 0, None, None),
        # 26-27: canonical loop A -> B -> A
        (26, "https://a.com/loop-a", 200, "text/html", "https://a.com/loop-b", True, None, False, 0, None, None),
        (27, "https://a.com/loop-b", 200, "text/html", "https://a.com/loop-a", True, None, False, 0, None, None),
        # 28: canonical to URL with x-robots-tag noindex
        (28, "https://a.com/to-xrobots", 200, "text/html", "https://a.com/xrobots-target", True, None, False, 0, None, None),
        (29, "https://a.com/xrobots-target", 200, "text/html", "https://a.com/xrobots-target", False, "x-robots-noindex", False, 0, None, "noindex"),
        # 30: a non-html (no canonical needed)
        (30, "https://a.com/style.css", 200, "text/css", None, True, None, False, 0, None, None),
        # 31: canonicalised target unlinked from any page (canonical_unlinked).
        # Only #21 -> #22 is linked; let's add a separate canonical to URL not in links
        # We'll add #32 canonicalising to https://a.com/lonely-target which is NOT in links table
        (32, "https://a.com/lonely-source", 200, "text/html", "https://a.com/lonely-target", True, None, False, 0, None, None),
        (33, "https://a.com/lonely-target", 200, "text/html", "https://a.com/lonely-target", True, None, False, 0, None, None),
    ]
    for r in rows:
        con.execute(
            """
            INSERT INTO urls (
                url_id, url, status_code, content_type, canonical,
                is_indexable, indexability_reason, from_robots,
                redirect_count, meta_robots, x_robots_tag
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            list(r),
        )

    # Populate links so canonical_unlinked has both signal and noise.
    # Pretend most canonicalised targets ARE linked from other pages,
    # except https://a.com/lonely-target.
    link_targets = [
        # source_url_id, target_url, target_url_id
        (1, "https://a.com/ok", 2),
        (1, "https://a.com/target#section", None),
        (1, "https://a.com/noindex-target", 7),
        (1, "https://a.com/404-target", 9),
        (1, "https://a.com/500-target", 11),
        (1, "https://a.com/redir-target", 13),
        (1, "https://a.com/blocked-target", 15),
        (1, "https://a.com/", 17),
        (1, "http://a.com/secure", None),
        (1, "https://a.com/insecure", None),
        (1, "https://otherdomain.com/orig", None),
        (1, "https://a.com/canonical-version", 22),
        (1, "https://a.com/chain-b", 24),
        (1, "https://a.com/chain-c", 25),
        (1, "https://a.com/loop-a", 26),
        (1, "https://a.com/loop-b", 27),
        (1, "https://a.com/xrobots-target", 29),
        # NOTE: deliberately NOT including https://a.com/lonely-target
    ]
    for source_id, target_url, target_id in link_targets:
        con.execute(
            "INSERT INTO links (source_url_id, target_url, target_url_id) "
            "VALUES (?, ?, ?)",
            [source_id, target_url, target_id],
        )

    return con


# ---------- canonical_missing ----------

def test_canonical_missing_flags_html_without_canonical(con):
    issues = list(CanonicalMissing().check(con))
    flagged = {i.url_id for i in issues}
    # url 1 has no canonical, url 9/11/13/30 are 4xx/5xx/3xx/non-html
    assert 1 in flagged


def test_canonical_missing_ignores_non_html(con):
    issues = list(CanonicalMissing().check(con))
    flagged = {i.url_id for i in issues}
    assert 30 not in flagged  # text/css


def test_canonical_missing_ignores_non_200(con):
    issues = list(CanonicalMissing().check(con))
    flagged = {i.url_id for i in issues}
    assert 9 not in flagged and 11 not in flagged


# ---------- canonical_contains_fragment ----------

def test_canonical_contains_fragment_flags_hash(con):
    issues = list(CanonicalContainsFragment().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {3}


def test_canonical_contains_fragment_negative(con):
    issues = list(CanonicalContainsFragment().check(con))
    assert all(i.url_id != 2 for i in issues)


# ---------- canonical_malformed_or_empty ----------

def test_canonical_malformed_or_empty_flags_empty_and_bad(con):
    issues = list(CanonicalMalformedOrEmpty().check(con))
    flagged = {i.url_id for i in issues}
    assert 4 in flagged
    assert 5 in flagged


def test_canonical_malformed_or_empty_negative_self(con):
    issues = list(CanonicalMalformedOrEmpty().check(con))
    assert all(i.url_id != 2 for i in issues)


# ---------- canonicalised ----------

def test_canonicalised_flags_self_different(con):
    issues = list(Canonicalised().check(con))
    flagged = {i.url_id for i in issues}
    # url 6 is canonicalised to noindex-target, etc.
    assert 6 in flagged
    assert 16 in flagged
    assert 21 in flagged


def test_canonicalised_does_not_flag_self_canonical(con):
    issues = list(Canonicalised().check(con))
    assert all(i.url_id != 2 for i in issues)


# ---------- canonical_to_non_indexable (cross-URL) ----------

def test_canonical_to_non_indexable_cross_url(con):
    issues = list(CanonicalToNonIndexable().check(con))
    flagged = {i.url_id for i in issues}
    # 6 -> 7 (noindex), 14 -> 15 (robots), 28 -> 29 (x-robots-noindex)
    assert 6 in flagged
    assert 14 in flagged
    assert 28 in flagged


def test_canonical_to_non_indexable_negative(con):
    issues = list(CanonicalToNonIndexable().check(con))
    assert all(i.url_id != 21 for i in issues)  # 21 -> 22 indexable


# ---------- canonical_unlinked (cross-URL) ----------

def test_canonical_unlinked_cross_url(con):
    issues = list(CanonicalUnlinked().check(con))
    flagged = {i.url_id for i in issues}
    # 32 -> https://a.com/lonely-target which is NOT in links table
    assert 32 in flagged


def test_canonical_unlinked_negative(con):
    issues = list(CanonicalUnlinked().check(con))
    flagged = {i.url_id for i in issues}
    # 21 -> canonical-version IS in links (target_id=22)
    assert 21 not in flagged


# ---------- canonical_to_4xx ----------

def test_canonical_to_4xx_cross_url(con):
    issues = list(CanonicalTo4xx().check(con))
    flagged = {i.url_id for i in issues}
    assert 8 in flagged


def test_canonical_to_4xx_does_not_flag_5xx(con):
    issues = list(CanonicalTo4xx().check(con))
    flagged = {i.url_id for i in issues}
    assert 10 not in flagged


# ---------- canonical_to_5xx ----------

def test_canonical_to_5xx_cross_url(con):
    issues = list(CanonicalTo5xx().check(con))
    flagged = {i.url_id for i in issues}
    assert 10 in flagged


def test_canonical_to_5xx_does_not_flag_4xx(con):
    issues = list(CanonicalTo5xx().check(con))
    flagged = {i.url_id for i in issues}
    assert 8 not in flagged


# ---------- canonical_to_3xx ----------

def test_canonical_to_3xx_cross_url(con):
    issues = list(CanonicalTo3xx().check(con))
    flagged = {i.url_id for i in issues}
    assert 12 in flagged  # canonical to redirect


def test_canonical_to_3xx_negative(con):
    issues = list(CanonicalTo3xx().check(con))
    flagged = {i.url_id for i in issues}
    assert 21 not in flagged


# ---------- canonical_to_disallowed ----------

def test_canonical_to_disallowed_cross_url(con):
    issues = list(CanonicalToDisallowed().check(con))
    flagged = {i.url_id for i in issues}
    assert 14 in flagged


def test_canonical_to_disallowed_negative(con):
    issues = list(CanonicalToDisallowed().check(con))
    flagged = {i.url_id for i in issues}
    assert 6 not in flagged  # noindex but not robots-blocked


# ---------- canonical_to_noindex ----------

def test_canonical_to_noindex_meta(con):
    issues = list(CanonicalToNoindex().check(con))
    flagged = {i.url_id for i in issues}
    assert 6 in flagged  # target 7 has meta_robots=noindex,follow


def test_canonical_to_noindex_x_robots_tag(con):
    issues = list(CanonicalToNoindex().check(con))
    flagged = {i.url_id for i in issues}
    assert 28 in flagged  # target 29 has x_robots_tag=noindex


# ---------- canonical_chain ----------

def test_canonical_chain_detects_two_hop(con):
    issues = list(CanonicalChain().check(con))
    flagged = {i.url_id for i in issues}
    # 23 -> 24 -> 25 (and 24 -> 25 self), so 23 should fire
    assert 23 in flagged


def test_canonical_chain_skips_loops(con):
    issues = list(CanonicalChain().check(con))
    flagged = {i.url_id for i in issues}
    # loop urls (26, 27) should NOT appear in chain because final == self
    # canonical_chain filters out final_target == url
    # 26 -> 27 -> 26 ; final_target for 26 is 26, so skipped
    assert 26 not in flagged


# ---------- canonical_loop ----------

def test_canonical_loop_detects_cycle(con):
    issues = list(CanonicalLoop().check(con))
    flagged = {i.url_id for i in issues}
    # 26 -> 27 -> 26 forms a loop
    assert 26 in flagged or 27 in flagged


def test_canonical_loop_negative_chain(con):
    issues = list(CanonicalLoop().check(con))
    flagged = {i.url_id for i in issues}
    # 23 -> 24 -> 25 is a chain not a loop
    assert 23 not in flagged
    assert 24 not in flagged


# ---------- canonical_to_homepage ----------

def test_canonical_to_homepage_flags_internal_pointing_root(con):
    issues = list(CanonicalToHomepage().check(con))
    flagged = {i.url_id for i in issues}
    assert 16 in flagged


def test_canonical_to_homepage_negative_root_self(con):
    issues = list(CanonicalToHomepage().check(con))
    flagged = {i.url_id for i in issues}
    assert 17 not in flagged  # the homepage itself


# ---------- canonical_to_http ----------

def test_canonical_to_http_flags_https_to_http(con):
    issues = list(CanonicalToHttp().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {18}


# ---------- canonical_to_https ----------

def test_canonical_to_https_flags_http_to_https(con):
    issues = list(CanonicalToHttps().check(con))
    flagged = {i.url_id for i in issues}
    assert flagged == {19}


# ---------- canonical_to_external ----------

def test_canonical_to_external_flags_other_domain(con):
    issues = list(CanonicalToExternal().check(con))
    flagged = {i.url_id for i in issues}
    assert 20 in flagged


def test_canonical_to_external_negative_same_domain(con):
    issues = list(CanonicalToExternal().check(con))
    flagged = {i.url_id for i in issues}
    assert 21 not in flagged


# ---------- canonical_to_different_internal ----------

def test_canonical_to_different_internal_same_host(con):
    issues = list(CanonicalToDifferentInternal().check(con))
    flagged = {i.url_id for i in issues}
    assert 21 in flagged


def test_canonical_to_different_internal_negative_external(con):
    issues = list(CanonicalToDifferentInternal().check(con))
    flagged = {i.url_id for i in issues}
    assert 20 not in flagged  # external, not internal


# ---------- registry sanity ----------

def test_all_canonical_rules_registered():
    import crawlforge.rules.canonicals  # noqa: F401

    expected = {
        "canonical_missing",
        "canonical_contains_fragment",
        "canonical_malformed_or_empty",
        "canonicalised",
        "canonical_to_non_indexable",
        "canonical_unlinked",
        "canonical_to_4xx",
        "canonical_to_5xx",
        "canonical_to_3xx",
        "canonical_to_disallowed",
        "canonical_to_noindex",
        "canonical_chain",
        "canonical_loop",
        "canonical_to_homepage",
        "canonical_to_http",
        "canonical_to_https",
        "canonical_to_external",
        "canonical_to_different_internal",
        # TODO(schema) — registered but disabled
        "canonical_relative",
        "canonical_multiple",
        "canonical_multiple_conflicting",
        "canonical_outside_head",
        "canonical_invalid_attribute",
        "canonical_superfluous_attrs",
        "canonical_html_vs_header_mismatch",
        "canonical_html_and_header",
        "canonical_only_in_rendered_dom",
        "canonical_render_vs_source_mismatch",
        "base_url_malformed",
        "base_url_multiple",
        "base_url_multiple_mismatched",
    }
    for rid in expected:
        assert rid in REGISTRY, f"{rid} not registered"
    assert len(expected) == 31


# ---------- TODO(schema) rules return empty ----------

@pytest.mark.parametrize(
    "rule_module",
    [
        "canonical_relative",
        "canonical_multiple",
        "canonical_multiple_conflicting",
        "canonical_outside_head",
        "canonical_invalid_attribute",
        "canonical_superfluous_attrs",
        "canonical_html_vs_header_mismatch",
        "canonical_html_and_header",
        "canonical_only_in_rendered_dom",
        "canonical_render_vs_source_mismatch",
        "base_url_malformed",
        "base_url_multiple",
        "base_url_multiple_mismatched",
    ],
)
def test_todo_schema_rules_return_empty(con, rule_module):
    import importlib

    mod = importlib.import_module(f"crawlforge.rules.canonicals.{rule_module}")
    rule_cls = next(
        v
        for v in vars(mod).values()
        if isinstance(v, type) and v.__module__ == mod.__name__ and hasattr(v, "id")
    )
    rule = rule_cls()
    assert rule.enabled_by_default is False
    assert list(rule.check(con)) == []
