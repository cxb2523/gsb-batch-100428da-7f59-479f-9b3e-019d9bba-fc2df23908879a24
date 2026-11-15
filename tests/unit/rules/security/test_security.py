"""Smoke tests para reglas de Security."""

import pytest

from crawlforge.rules._registry import REGISTRY
from crawlforge.rules.security.bad_content_type import BadContentType
from crawlforge.rules.security.form_on_http_url import FormOnHttpUrl
from crawlforge.rules.security.form_url_insecure import FormUrlInsecure
from crawlforge.rules.security.http_password_field import HttpPasswordField
from crawlforge.rules.security.http_url import HttpUrl
from crawlforge.rules.security.https_links_to_http import HttpsLinksToHttp
from crawlforge.rules.security.missing_csp_header import MissingCspHeader
from crawlforge.rules.security.missing_hsts_header import MissingHstsHeader
from crawlforge.rules.security.missing_referrer_policy import MissingReferrerPolicy
from crawlforge.rules.security.missing_x_content_type_options import (
    MissingXContentTypeOptions,
)
from crawlforge.rules.security.missing_x_frame_options import MissingXFrameOptions
from crawlforge.rules.security.mixed_content import MixedContent
from crawlforge.rules.security.protocol_relative_resource import (
    ProtocolRelativeResource,
)
from crawlforge.rules.security.unsafe_cross_origin_links import UnsafeCrossOriginLinks
from crawlforge.storage.schema import init_database


@pytest.fixture
def con():
    con = init_database()

    # url_id, url, status, ctype, csp, hsts, xfo, refpol
    rows = [
        # 1: HTTPS perfecta — no debería disparar nada de headers que SÍ tiene
        (1, "https://a.com/",          200, "text/html",
         "default-src 'self'", "max-age=31536000", "DENY", "no-referrer"),
        # 2: HTTP — debería disparar http_url
        (2, "http://a.com/legacy",     200, "text/html",
         None, None, None, None),
        # 3: HTTPS pero sin ningún header de seguridad
        (3, "https://a.com/no-headers", 200, "text/html",
         None, None, None, None),
        # 4: HTTPS con mixed content (ver tabla resources abajo)
        (4, "https://a.com/mixed",     200, "text/html",
         "default-src 'self'", "max-age=600", "DENY", "no-referrer"),
        # 5: HTTPS que enlaza a HTTP (ver tabla links abajo)
        (5, "https://a.com/linker",    200, "text/html",
         "default-src 'self'", "max-age=600", "DENY", "no-referrer"),
        # 6: HTML servido como text/plain → bad_content_type
        (6, "https://a.com/page.html", 200, "text/plain",
         "default-src 'self'", "max-age=600", "DENY", "no-referrer"),
        # 7: .css con content-type correcto → no debería disparar bad_content_type
        (7, "https://a.com/style.css", 200, "text/css; charset=utf-8",
         "default-src 'self'", "max-age=600", "DENY", "no-referrer"),
        # 8: source con link externo target=_blank sin rel
        (8, "https://a.com/external",  200, "text/html",
         "default-src 'self'", "max-age=600", "DENY", "no-referrer"),
    ]
    for url_id, url, status, ctype, csp, hsts, xfo, refpol in rows:
        con.execute(
            """
            INSERT INTO urls
                (url_id, url, status_code, content_type,
                 header_csp, header_hsts, header_x_frame_options, header_referrer_policy)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [url_id, url, status, ctype, csp, hsts, xfo, refpol],
        )

    # Resources for mixed_content (url_id=4 carga http://cdn/x.js)
    con.execute(
        """
        INSERT INTO resources (source_url_id, resource_url, resource_type)
        VALUES (?, ?, ?)
        """,
        [4, "http://cdn.evil.com/x.js", "script"],
    )
    con.execute(
        """
        INSERT INTO resources (source_url_id, resource_url, resource_type)
        VALUES (?, ?, ?)
        """,
        [1, "https://cdn.good.com/y.js", "script"],
    )

    # Links: url_id=5 enlaza a http://target (https_links_to_http)
    # url_id=8 enlaza a externo sin rel (unsafe_cross_origin_links)
    con.execute(
        """
        INSERT INTO links
            (source_url_id, target_url, target_url_id, anchor, rel, link_type)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [5, "http://insecure.com/page", None, "Insecure", None, "a"],
    )
    con.execute(
        """
        INSERT INTO links
            (source_url_id, target_url, target_url_id, anchor, rel, link_type)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [8, "https://external.com/page", None, "Ext no rel", None, "a"],
    )
    # Misma source pero CON rel noopener — no debería disparar
    con.execute(
        """
        INSERT INTO links
            (source_url_id, target_url, target_url_id, anchor, rel, link_type)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [1, "https://safe-external.com/", None, "Safe", "noopener noreferrer", "a"],
    )
    return con


# --- http_url --------------------------------------------------------------

def test_http_url_flags_http(con):
    issues = list(HttpUrl().check(con))
    assert {i.url_id for i in issues} == {2}


def test_http_url_ignores_https(con):
    issues = list(HttpUrl().check(con))
    assert all(i.url_id != 1 for i in issues)


# --- mixed_content ---------------------------------------------------------

def test_mixed_content_detects_http_resource_on_https_page(con):
    issues = list(MixedContent().check(con))
    assert {i.url_id for i in issues} == {4}
    [issue] = issues
    assert "http://cdn.evil.com/x.js" in issue.evidence["http_resources"]


def test_mixed_content_does_not_flag_https_resource(con):
    issues = list(MixedContent().check(con))
    assert all(i.url_id != 1 for i in issues)


# --- https_links_to_http ---------------------------------------------------

def test_https_links_to_http_detects(con):
    issues = list(HttpsLinksToHttp().check(con))
    assert {i.url_id for i in issues} == {5}
    [issue] = issues
    assert "http://insecure.com/page" in issue.evidence["http_targets"]


# --- missing_hsts_header ---------------------------------------------------

def test_missing_hsts_only_for_https_with_null(con):
    flagged = {i.url_id for i in MissingHstsHeader().check(con)}
    # url_id=2 es HTTP → no aplica; url_id=3 es HTTPS sin HSTS → sí
    assert 2 not in flagged
    assert 3 in flagged
    assert 1 not in flagged  # tiene HSTS


# --- missing_csp_header ----------------------------------------------------

def test_missing_csp_flags_null_csp(con):
    flagged = {i.url_id for i in MissingCspHeader().check(con)}
    assert 3 in flagged
    assert 2 in flagged  # HTTP también debería disparar (CSP NULL)
    assert 1 not in flagged


# --- missing_x_frame_options -----------------------------------------------

def test_missing_x_frame_options_flags_null(con):
    flagged = {i.url_id for i in MissingXFrameOptions().check(con)}
    assert 3 in flagged
    assert 1 not in flagged


# --- missing_referrer_policy -----------------------------------------------

def test_missing_referrer_policy_flags_null(con):
    flagged = {i.url_id for i in MissingReferrerPolicy().check(con)}
    assert 3 in flagged
    assert 1 not in flagged


# --- bad_content_type ------------------------------------------------------

def test_bad_content_type_flags_html_as_plain(con):
    flagged = {i.url_id for i in BadContentType().check(con)}
    assert 6 in flagged  # .html servido como text/plain
    assert 7 not in flagged  # .css servido como text/css ✓


# --- unsafe_cross_origin_links ---------------------------------------------

def test_unsafe_cross_origin_links_flags_external_no_rel(con):
    issues = list(UnsafeCrossOriginLinks().check(con))
    flagged = {i.url_id for i in issues}
    assert 8 in flagged
    assert 1 not in flagged  # tiene rel=noopener noreferrer


# --- skipped (TODO schema) -------------------------------------------------

def test_form_url_insecure_returns_empty(con):
    assert list(FormUrlInsecure().check(con)) == []


def test_form_on_http_url_returns_empty(con):
    assert list(FormOnHttpUrl().check(con)) == []


def test_http_password_field_returns_empty(con):
    assert list(HttpPasswordField().check(con)) == []


def test_protocol_relative_resource_returns_empty(con):
    assert list(ProtocolRelativeResource().check(con)) == []


def test_missing_x_content_type_options_returns_empty(con):
    assert list(MissingXContentTypeOptions().check(con)) == []


# --- registry --------------------------------------------------------------

def test_all_rules_registered():
    import crawlforge.rules.security  # noqa: F401

    expected = {
        "http_url",
        "mixed_content",
        "form_url_insecure",
        "form_on_http_url",
        "http_password_field",
        "https_links_to_http",
        "protocol_relative_resource",
        "missing_hsts_header",
        "missing_csp_header",
        "missing_x_content_type_options",
        "missing_x_frame_options",
        "missing_referrer_policy",
        "unsafe_cross_origin_links",
        "bad_content_type",
    }
    for rid in expected:
        assert rid in REGISTRY, f"{rid} not registered"
