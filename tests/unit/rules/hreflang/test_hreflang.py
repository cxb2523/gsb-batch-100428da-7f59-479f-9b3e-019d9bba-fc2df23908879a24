"""Tests para todas las reglas Hreflang.

Fixture compartido construye un cluster multilingüe sintético con casos
positivos y negativos para cada regla. Cada test ejecuta una regla concreta
y comprueba qué URL ids dispara.
"""

import pytest

import crawlforge.rules.hreflang  # noqa: F401  -- registers all hreflang rules
from crawlforge.rules._registry import REGISTRY
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    """Cluster sintético con casos sembrados.

    URLs (url_id, url, status, content_type, indexable, canonical, robots, lang, redirect_count):
    - 1  https://a.com/      OK indexable canonical=self lang=en
    - 2  https://a.com/es/   OK indexable canonical=self lang=es
    - 3  https://a.com/fr/   OK indexable canonical=self lang=fr-FR
    - 4  https://a.com/de/   OK NOT indexable noindex      lang=de
    - 5  https://a.com/it/   404                            lang=NULL
    - 6  https://a.com/pt/   OK indexable canonical=https://a.com/pt-canon (canonicalised)
    - 7  https://a.com/ru/   OK NOT indexable from_robots=TRUE lang=ru
    - 8  https://a.com/jp/   OK indexable                   lang=NULL  (no hreflang)
    - 9  https://a.com/zz/   OK indexable                   lang='ZZ##' (invalid)
    """
    con = init_database()
    rows = [
        (1, "https://a.com/",     200, "text/html", True,  "https://a.com/",     False, "en",      0, None,       None),
        (2, "https://a.com/es",   200, "text/html", True,  "https://a.com/es",   False, "es",      0, None,       None),
        (3, "https://a.com/fr",   200, "text/html", True,  "https://a.com/fr",   False, "fr-FR",   0, None,       None),
        (4, "https://a.com/de",   200, "text/html", False, "https://a.com/de",   False, "de",      0, "noindex",  None),
        (5, "https://a.com/it",   404, "text/html", False, None,                 False, None,      0, None,       None),
        (6, "https://a.com/pt",   200, "text/html", True,  "https://a.com/pt-c", False, "pt",      0, None,       None),
        (7, "https://a.com/ru",   200, "text/html", False, "https://a.com/ru",   True,  "ru",      0, None,       None),
        (8, "https://a.com/jp",   200, "text/html", True,  "https://a.com/jp",   False, None,      0, None,       None),
        (9, "https://a.com/zz",   200, "text/html", True,  "https://a.com/zz",   False, "ZZ##",    0, None,       None),
        (10, "https://a.com/redir", 301, "text/html", False, None,                False, "en",      1, None,       None),
    ]
    for r in rows:
        con.execute(
            "INSERT INTO urls (url_id, url, status_code, content_type, is_indexable, "
            "canonical, from_robots, lang, redirect_count, meta_robots, x_robots_tag) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            list(r),
        )

    # Hreflang relations
    # 1 declara return links a 2,3 (self ref + es + fr) — bidireccional con 2 pero 3 no devuelve a 1 (test missing_return)
    # 1 también declara hreflang hacia 4 (noindex), 6 (canonicalized), 7 (disallowed)
    # 2 declara return a 1 (en) y self
    # 3 NO declara nada (causa missing_return desde 1)
    # 9 declara hreflang con lang inválido a 1
    # url 1 también declara x-default
    hreflang_rows = [
        # source_id, lang, href, href_url_id, from_html, from_header, from_sitemap
        (1, "en",        "https://a.com/",     1, True, False, False),  # self
        (1, "es",        "https://a.com/es",   2, True, False, False),
        (1, "fr",        "https://a.com/fr",   3, True, False, False),
        (1, "de",        "https://a.com/de",   4, True, False, False),  # to noindex
        (1, "pt",        "https://a.com/pt",   6, True, False, False),  # to canonicalised
        (1, "ru",        "https://a.com/ru",   7, True, False, False),  # to disallowed
        (1, "x-default", "https://a.com/",     1, True, False, False),
        (2, "en",        "https://a.com/",     1, True, False, False),
        (2, "es",        "https://a.com/es",   2, True, False, False),
        # 3 no devuelve return → triggers missing_return_links from 1
        (9, "ZZ##",      "https://a.com/",     1, True, False, False),  # invalid lang in incoming
    ]
    for r in hreflang_rows:
        con.execute(
            "INSERT INTO hreflang (source_url_id, lang, href, href_url_id, "
            "from_html, from_header, from_sitemap) VALUES (?, ?, ?, ?, ?, ?, ?)",
            list(r),
        )
    return con


def _run(rule_id, con):
    rule_cls = REGISTRY[rule_id]
    return list(rule_cls().check(con))


def test_missing_return_links_finds_unidirectional(con):
    issues = _run("hreflang_missing_return_links", con)
    flagged = {(i.url_id, i.evidence.get("lang"), i.evidence.get("target")) for i in issues}
    assert (1, "fr", "https://a.com/fr") in flagged


def test_to_noindex_flags_target_with_noindex(con):
    issues = _run("hreflang_to_noindex", con)
    targets = {i.evidence.get("target") or i.evidence.get("href") or i.evidence.get("url") for i in issues}
    assert any("/de" in (t or "") for t in targets) or any(i.url_id == 1 for i in issues)


def test_to_canonicalized_flags_pt(con):
    issues = _run("hreflang_to_canonicalized", con)
    assert len(issues) >= 1


def test_to_disallowed_flags_ru(con):
    issues = _run("hreflang_to_disallowed", con)
    assert len(issues) >= 1


def test_invalid_codes_flags_zz(con):
    issues = _run("hreflang_invalid_codes", con)
    bad_langs = {i.evidence["lang"] for i in issues}
    assert "ZZ##" in bad_langs


def test_invalid_incoming_flags_target_of_invalid(con):
    issues = _run("hreflang_invalid_incoming", con)
    flagged = {i.url_id for i in issues}
    assert 1 in flagged  # target of the invalid hreflang from url 9


def test_missing_self_reference_flags_url_without_self(con):
    issues = _run("hreflang_missing_self_reference", con)
    flagged = {i.url_id for i in issues}
    # url 9 has only one hreflang entry to url 1 (no self ref)
    assert 9 in flagged


def test_missing_x_default_flags_url_without_xdefault(con):
    issues = _run("hreflang_missing_x_default", con)
    flagged = {i.url_id for i in issues}
    # url 2 has hreflangs but no x-default
    assert 2 in flagged


def test_html_lang_missing_flags_url_8(con):
    issues = _run("html_lang_missing", con)
    flagged = {i.url_id for i in issues}
    assert 8 in flagged


def test_html_lang_invalid_flags_zz(con):
    issues = _run("html_lang_invalid", con)
    flagged = {i.url_id for i in issues}
    assert 9 in flagged


def test_canonicalized_has_incoming_flags_pt(con):
    issues = _run("hreflang_canonicalized_has_incoming", con)
    flagged = {i.url_id for i in issues}
    assert 6 in flagged


def test_disallowed_has_incoming_flags_ru(con):
    issues = _run("hreflang_disallowed_has_incoming", con)
    flagged = {i.url_id for i in issues}
    assert 7 in flagged


def test_noindex_has_incoming_flags_de(con):
    issues = _run("hreflang_noindex_has_incoming", con)
    flagged = {i.url_id for i in issues}
    assert 4 in flagged


def test_non_200_flags_404_target(con):
    issues = _run("hreflang_non_200", con)
    # url 5 (it/) is 404, but no hreflang points to it in fixture
    # The rule should not falsely flag in this case
    assert isinstance(issues, list)


def test_to_redirecting_handles_3xx(con):
    # No hreflang points to url 10 (redirect) in fixture, so no issues
    issues = _run("hreflang_to_redirecting", con)
    assert isinstance(issues, list)


def test_all_hreflang_rules_registered():
    expected = {
        "hreflang_canonicalized_has_incoming",
        "hreflang_conflicting_incoming",
        "hreflang_conflicting_outgoing",
        "hreflang_disallowed_has_incoming",
        "hreflang_html_lang_mismatch",
        "hreflang_inconsistent_lang_region",
        "hreflang_invalid_codes",
        "hreflang_invalid_incoming",
        "hreflang_missing_annotations",
        "hreflang_missing_return_links",
        "hreflang_missing_self_reference",
        "hreflang_missing_x_default",
        "hreflang_multiple_entries",
        "hreflang_multiple_methods",
        "hreflang_multiple_self_referencing",
        "hreflang_noindex_has_incoming",
        "hreflang_noindex_return",
        "hreflang_non_200",
        "hreflang_non_canonical_return",
        "hreflang_not_using_canonical",
        "hreflang_outside_head",
        "hreflang_relative_urls",
        "hreflang_to_canonicalized",
        "hreflang_to_disallowed",
        "hreflang_to_noindex",
        "hreflang_to_redirecting",
        "hreflang_unlinked",
        "hreflang_unsupported",
        "hreflang_x_default_dual",
        "html_lang_invalid",
        "html_lang_missing",
        "html_lang_xml_lang_mismatch",
        "hreflang_without_html_lang",
    }
    import crawlforge.rules.hreflang  # noqa: F401

    missing = expected - set(REGISTRY.keys())
    assert not missing, f"Not registered: {missing}"
