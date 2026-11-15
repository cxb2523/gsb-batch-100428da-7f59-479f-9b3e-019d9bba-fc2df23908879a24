"""HTML extraction — raw response → dict ready for `urls` table.

Pure function: takes the bytes + headers + URL of a single response and
returns a dict matching the `urls` schema. No I/O, no DB, no async.
This is what makes the engine swappable (httpx today, Scrapy/Playwright
tomorrow) and trivially testable.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urljoin

from lxml import html as lxml_html

from crawlforge.engine.normalize import canonicalize, url_id

_WHITESPACE_RE = re.compile(r"\s+")


def _list_text(elements: list[Any]) -> list[str]:
    return [_WHITESPACE_RE.sub(" ", el.text_content()).strip() for el in elements]


def _first_attr(tree: Any, xpath: str) -> str | None:
    nodes = tree.xpath(xpath)
    if not nodes:
        return None
    val = nodes[0]
    if isinstance(val, str):
        return val.strip() or None
    return None


def _first_text(tree: Any, xpath: str) -> str | None:
    nodes = tree.xpath(xpath)
    if not nodes:
        return None
    return _WHITESPACE_RE.sub(" ", nodes[0].text_content()).strip() or None


def _content_hash(text: str) -> str:
    normalized = _WHITESPACE_RE.sub(" ", text).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _simhash64(text: str) -> int:
    """Tiny simhash64 over whitespace-separated tokens.

    Not crypto. Good enough for "two pages with the same boilerplate but
    different titles" detection. Replace with datasketch for production.
    """
    tokens = _WHITESPACE_RE.split(text.strip().lower())
    if not tokens:
        return 0
    bits = [0] * 64
    for tok in tokens:
        if not tok:
            continue
        h = int.from_bytes(
            hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest(),
            "big",
            signed=False,
        )
        for i in range(64):
            bits[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(64):
        if bits[i] > 0:
            out |= 1 << i
    return out


def extract_url_row(
    *,
    url: str,
    final_url: str | None,
    status_code: int,
    headers: dict[str, str],
    body: bytes,
    response_time_ms: int = 0,
    depth: int = 0,
    redirect_count: int = 0,
    crawled_at: Any = None,
    persist_html: bool = False,
) -> dict[str, Any]:
    canon = canonicalize(url)
    final_canon = canonicalize(final_url) if final_url else canon
    content_type = headers.get("content-type", "") or headers.get("Content-Type", "")
    content_length = int(
        headers.get("content-length", "0") or headers.get("Content-Length", "0") or 0
    )

    base_row: dict[str, Any] = {
        "url_id": url_id(canon),
        "url": canon,
        "final_url": final_canon,
        "status_code": status_code,
        "redirect_count": redirect_count,
        "content_type": content_type,
        "content_length": content_length or len(body),
        "response_time_ms": response_time_ms,
        "depth": depth,
        "crawled_at": crawled_at,
        "from_sitemap": False,
        "from_robots": False,
        "rendered": False,
        "x_robots_tag": headers.get("x-robots-tag") or headers.get("X-Robots-Tag"),
        "header_csp": headers.get("content-security-policy")
        or headers.get("Content-Security-Policy"),
        "header_hsts": headers.get("strict-transport-security")
        or headers.get("Strict-Transport-Security"),
        "header_x_frame_options": headers.get("x-frame-options")
        or headers.get("X-Frame-Options"),
        "header_referrer_policy": headers.get("referrer-policy")
        or headers.get("Referrer-Policy"),
        "title": None,
        "meta_description": None,
        "meta_robots": None,
        "canonical": None,
        "viewport": None,
        "charset": None,
        "lang": None,
        "h1": [],
        "h2": [],
        "h3": [],
        "h4": [],
        "h5": [],
        "h6": [],
        "is_indexable": None,
        "indexability_reason": None,
        "word_count": None,
        "text_to_html_ratio": None,
        "content_hash": None,
        "simhash": None,
        "lcp_ms": None,
        "cls": None,
        "inp_ms": None,
        "raw_html": body if persist_html else None,
        "rendered_html": None,
        "_links": [],
        "_resources": [],
        "_hreflang": [],
    }

    if "text/html" not in content_type.lower() and not body.lstrip().startswith(b"<"):
        return base_row

    try:
        tree = lxml_html.fromstring(body)
    except Exception:
        return base_row

    base_row["title"] = _first_text(tree, "//head/title")
    base_row["meta_description"] = _first_attr(
        tree, "//head/meta[translate(@name,'DESCRIPTION','description')='description']/@content"
    )
    base_row["meta_robots"] = _first_attr(
        tree, "//head/meta[translate(@name,'ROBOTS','robots')='robots']/@content"
    )
    base_row["canonical"] = _first_attr(
        tree,
        "//head/link[translate(@rel,'CANONICAL','canonical')='canonical']/@href",
    )
    if base_row["canonical"]:
        base_row["canonical"] = canonicalize(urljoin(canon, base_row["canonical"]))
    base_row["viewport"] = _first_attr(
        tree, "//head/meta[translate(@name,'VIEWPORT','viewport')='viewport']/@content"
    )
    base_row["lang"] = _first_attr(tree, "//html/@lang")
    base_row["charset"] = _first_attr(tree, "//head/meta/@charset")

    base_row["h1"] = _list_text(tree.xpath("//h1"))
    base_row["h2"] = _list_text(tree.xpath("//h2"))
    base_row["h3"] = _list_text(tree.xpath("//h3"))
    base_row["h4"] = _list_text(tree.xpath("//h4"))
    base_row["h5"] = _list_text(tree.xpath("//h5"))
    base_row["h6"] = _list_text(tree.xpath("//h6"))

    body_text = _WHITESPACE_RE.sub(" ", tree.text_content()).strip()
    base_row["word_count"] = len(body_text.split()) if body_text else 0
    if body:
        base_row["text_to_html_ratio"] = round(
            len(body_text) / max(len(body), 1), 4
        )
    base_row["content_hash"] = _content_hash(body_text) if body_text else None
    base_row["simhash"] = _simhash64(body_text) if body_text else None

    base_row["is_indexable"], base_row["indexability_reason"] = _compute_indexability(
        status_code,
        base_row["meta_robots"],
        base_row["x_robots_tag"],
        base_row["canonical"],
        canon,
    )

    base_row["_links"] = _extract_links(tree, canon)
    base_row["_hreflang"] = _extract_hreflang(tree, canon)
    base_row["_resources"] = _extract_resources(tree, canon)

    return base_row


def _compute_indexability(
    status_code: int,
    meta_robots: str | None,
    x_robots_tag: str | None,
    canonical: str | None,
    own_url: str,
) -> tuple[bool, str | None]:
    if status_code != 200:
        return False, f"Non-200 status: {status_code}"
    blockers = " ".join(filter(None, [meta_robots or "", x_robots_tag or ""])).lower()
    if "noindex" in blockers:
        return False, "noindex directive"
    if "none" in blockers:
        return False, "robots: none"
    if canonical and canonical != own_url:
        return False, f"canonicalised to {canonical}"
    return True, None


def _extract_links(tree: Any, base_url: str) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for pos, a in enumerate(tree.xpath("//a[@href]")):
        href = a.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        try:
            target = canonicalize(urljoin(base_url, href))
        except Exception:
            continue
        anchor = _WHITESPACE_RE.sub(" ", a.text_content()).strip()
        in_nav = bool(a.xpath("ancestor::nav") or a.xpath("ancestor::header"))
        in_footer = bool(a.xpath("ancestor::footer"))
        links.append(
            {
                "target_url": target,
                "anchor": anchor,
                "rel": a.get("rel"),
                "link_type": "a",
                "in_navigation": in_nav,
                "in_footer": in_footer,
                "position": pos,
            }
        )
    return links


def _extract_hreflang(tree: Any, base_url: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for link in tree.xpath(
        "//head/link[translate(@rel,'ALTERNATE','alternate')='alternate' and @hreflang]"
    ):
        href = link.get("href", "").strip()
        lang = link.get("hreflang", "").strip()
        if not href or not lang:
            continue
        out.append(
            {
                "lang": lang,
                "href": canonicalize(urljoin(base_url, href)),
                "from_html": True,
                "from_header": False,
                "from_sitemap": False,
            }
        )
    return out


def _extract_resources(tree: Any, base_url: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for img in tree.xpath("//img"):
        src = img.get("src", "").strip()
        if not src:
            continue
        ext = src.rsplit(".", 1)[-1].split("?")[0].lower()
        out.append(
            {
                "resource_url": canonicalize(urljoin(base_url, src)),
                "resource_type": "image",
                "alt": img.get("alt"),
                "loading_attr": img.get("loading"),
                "width": _maybe_int(img.get("width")),
                "height": _maybe_int(img.get("height")),
                "format": ext if ext in {"webp", "avif", "png", "jpg", "jpeg", "gif", "svg"} else None,
            }
        )
    return out


def _maybe_int(v: str | None) -> int | None:
    if not v:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
