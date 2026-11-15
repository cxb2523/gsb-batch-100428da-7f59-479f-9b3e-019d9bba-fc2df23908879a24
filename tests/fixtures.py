# -*- coding: utf-8 -*-
"""Shared HTML samples and response helpers for the test suite."""

from __future__ import annotations

from scrapy.http import HtmlResponse, Request

BASE = "https://www.olx.com/lahore/q-iphone"

LIST_PAGE_WITH_ADS = """
<html><body>
<table class="results">
  <tr><td class="offer">
    <h3><a href="/item/iphone-11-111"><span>iPhone 11 - 128GB</span></a></h3>
    <p class="price"><strong>Rs 1,25,000</strong></p>
    <span class="color-9 lheight14 margintop3 small">Yesterday</span>
  </td></tr>
  <tr><td class="offer">
    <h3><a href="/item/iphone-neg-222"><span>iPhone - swap possible</span></a></h3>
    <p class="price"><strong>Negotiable</strong></p>
    <span class="color-9 lheight14 margintop3 small">Today</span>
  </td></tr>
  <tr><td class="offer">
    <h3><a href="/item/iphone-13-333"><span>iPhone 13</span></a></h3>
    <p class="price"><strong>Rs 2,00,000</strong></p>
    <span class="color-9 lheight14 margintop3 small">2 days ago</span>
  </td></tr>
  <tr><td class="offer">
    <h3><a href="/item/cheap-444"><span>iPhone 6</span></a></h3>
    <p class="price"><strong>Rs 15,000</strong></p>
    <span class="color-9 lheight14 margintop3 small">Today</span>
  </td></tr>
  <tr><td class="offer">
    <h3><a href="/item/iphone-14-555"><span>iPhone 14</span></a></h3>
    <p class="price"><strong>Rs&nbsp;3 50 000</strong></p>
    <span class="color-9 lheight14 margintop3 small">Today</span>
  </td></tr>
</table>
</body></html>
"""

LIST_PAGE_EMPTY = """
<html><body>
<div class="listings"> </div>
<div class="errormsg">No ads found matching your search.</div>
</body></html>
"""

LIST_PAGE_INVALID = """
<html><body>
<div class="marketing-home">
  <h1>OLX - buy and sell</h1>
  <p>Welcome to the marketplace home page.</p>
</div>
</body></html>
"""

LIST_PAGE_BLOCKED = """
<html><body>
<h1>Access denied</h1>
<p>Please complete the CAPTCHA to continue.</p>
</body></html>
"""

DETAIL_FULL = """
<html><body>
<div class="userdetails"><span>Ali Khan</span></div>
<div class="contactitem"><strong>0300 1234567</strong></div>
<div id="textContent"><p>Great phone, minor scratches, original box included.</p></div>
</body></html>
"""

DETAIL_MISSING_PHONE = """
<html><body>
<div class="userdetails"><span>Sana</span></div>
<div id="textContent"><p>Second hand iPhone, works perfectly.</p></div>
</body></html>
"""

DETAIL_MISSING_DESCRIPTION = """
<html><body>
<div class="userdetails"><span>Bilal</span></div>
<div class="contactitem"><strong>0321 7654321</strong></div>
<div id="textContent"></div>
</body></html>
"""

DETAIL_MISSING_BOTH = """
<html><body>
<div id="textContent"> </div>
</body></html>
"""


def make_response(url, html, request=None, status=200):
    """Build an :class:`HtmlResponse` suitable for spider callbacks."""
    if request is None:
        request = Request(url)
    return HtmlResponse(
        url=url,
        body=html.encode("utf-8"),
        encoding="utf-8",
        request=request,
        status=status,
    )


def detail_response(path, html, meta=None):
    """Detail page response carrying the meta set by the list callback."""
    meta = dict(meta or {})
    url = "https://www.olx.com" + path
    meta.setdefault("url", url)
    request = Request(url, meta=meta)
    return make_response(url, html, request=request)
