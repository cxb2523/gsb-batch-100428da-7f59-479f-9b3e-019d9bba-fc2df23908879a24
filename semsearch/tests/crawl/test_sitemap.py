import httpx
import pytest

from semsearch.crawl import sitemap as sitemap_module
from semsearch.crawl.sitemap import (
    SITEMAP_MAX_URLS,
    SitemapLoader,
    SitemapStatus,
)

URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a</loc></url>
  <url><loc>https://example.com/b</loc></url>
  <url><loc>https://example.com/c</loc></url>
</urlset>"""

SITEMAPINDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap2.xml</loc></sitemap>
</sitemapindex>"""

URLSET_1 = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/page1</loc></url>
</urlset>"""

URLSET_2 = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/page2</loc></url>
</urlset>"""


def make_client(responses: dict[str, tuple[int, str]]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        status, body = responses.get(url, (404, ""))
        return httpx.Response(status, text=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------
# basic urlset parsing
# ------------------------------------------------------------------


async def test_urlset_returns_all_locs():
    client = make_client({"https://example.com/sitemap.xml": (200, URLSET)})
    loader = SitemapLoader(client)
    result = await loader.load(
        "example.com", ["https://example.com/sitemap.xml"]
    )
    assert result.pages == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]
    assert result.total_pages == 3
    assert result.truncated is False
    assert result.truncation_limit == SITEMAP_MAX_URLS
    outcome = result.outcome_for("https://example.com/sitemap.xml")
    assert outcome is not None
    assert outcome.depth == 0
    assert outcome.status is SitemapStatus.SUCCESS
    assert outcome.pages_contributed == 3


async def test_fallback_to_default_sitemap_url():
    client = make_client({"https://example.com/sitemap.xml": (200, URLSET)})
    loader = SitemapLoader(client)
    result = await loader.load("example.com", [])  # no sitemap URLs provided
    assert len(result.pages) == 3


# ------------------------------------------------------------------
# sitemapindex recursion
# ------------------------------------------------------------------


async def test_sitemapindex_recurses_into_children():
    client = make_client(
        {
            "https://example.com/sitemap_index.xml": (200, SITEMAPINDEX),
            "https://example.com/sitemap1.xml": (200, URLSET_1),
            "https://example.com/sitemap2.xml": (200, URLSET_2),
        }
    )
    loader = SitemapLoader(client)
    result = await loader.load(
        "example.com", ["https://example.com/sitemap_index.xml"]
    )
    assert "https://example.com/page1" in result.pages
    assert "https://example.com/page2" in result.pages
    # index itself contributes no pages; children carry the counts
    index_outcome = result.outcome_for("https://example.com/sitemap_index.xml")
    assert index_outcome is not None
    assert index_outcome.status is SitemapStatus.SUCCESS
    assert index_outcome.depth == 0
    assert index_outcome.pages_contributed == 0
    for child in ("sitemap1.xml", "sitemap2.xml"):
        outcome = result.outcome_for(f"https://example.com/{child}")
        assert outcome is not None
        assert outcome.depth == 1
        assert outcome.status is SitemapStatus.SUCCESS
        assert outcome.pages_contributed == 1


async def test_sitemapindex_deduplicates_child_sitemaps():
    # same child listed twice
    index = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap1.xml</loc></sitemap>
</sitemapindex>"""
    call_count = 0

    def handler(request):
        nonlocal call_count
        url = str(request.url)
        if "sitemap_index" in url:
            return httpx.Response(200, text=index)
        call_count += 1
        return httpx.Response(200, text=URLSET_1)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    loader = SitemapLoader(client)
    result = await loader.load(
        "example.com", ["https://example.com/sitemap_index.xml"]
    )
    assert call_count == 1
    # the second mention is recorded as a duplicate skip, not fetched
    dupes = [
        o
        for o in result.outcomes
        if o.url == "https://example.com/sitemap1.xml"
    ]
    assert [o.status for o in dupes] == [
        SitemapStatus.SUCCESS,
        SitemapStatus.DUPLICATE_SKIPPED,
    ]
    assert dupes[1].depth == 1
    assert dupes[1].pages_contributed == 0
    # the child is still only counted once
    assert result.pages == ["https://example.com/page1"]
    assert result.total_pages == 1


# ------------------------------------------------------------------
# error handling
# ------------------------------------------------------------------


async def test_non_200_response_returns_empty():
    client = make_client({"https://example.com/sitemap.xml": (404, "")})
    loader = SitemapLoader(client)
    result = await loader.load(
        "example.com", ["https://example.com/sitemap.xml"]
    )
    assert result.pages == []
    outcome = result.outcome_for("https://example.com/sitemap.xml")
    assert outcome is not None
    assert outcome.status is SitemapStatus.NON_200
    assert outcome.depth == 0
    assert outcome.pages_contributed == 0


async def test_invalid_xml_returns_empty():
    client = make_client(
        {"https://example.com/sitemap.xml": (200, "not xml {{}")}
    )
    loader = SitemapLoader(client)
    result = await loader.load(
        "example.com", ["https://example.com/sitemap.xml"]
    )
    assert result.pages == []
    outcome = result.outcome_for("https://example.com/sitemap.xml")
    assert outcome is not None
    assert outcome.status is SitemapStatus.PARSE_ERROR


async def test_network_error_returns_empty():
    def handler(request):
        raise httpx.ConnectError("refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    loader = SitemapLoader(client)
    result = await loader.load(
        "example.com", ["https://example.com/sitemap.xml"]
    )
    assert result.pages == []
    outcome = result.outcome_for("https://example.com/sitemap.xml")
    assert outcome is not None
    assert outcome.status is SitemapStatus.NETWORK_ERROR


async def test_filters_non_http_locs():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/valid</loc></url>
  <url><loc>ftp://example.com/invalid</loc></url>
  <url><loc>mailto:someone@example.com</loc></url>
</urlset>"""
    client = make_client({"https://example.com/sitemap.xml": (200, xml)})
    loader = SitemapLoader(client)
    result = await loader.load(
        "example.com", ["https://example.com/sitemap.xml"]
    )
    assert result.pages == ["https://example.com/valid"]
    outcome = result.outcome_for("https://example.com/sitemap.xml")
    assert outcome is not None
    assert outcome.status is SitemapStatus.SUCCESS
    # only the http/https loc counts as a contributed page
    assert outcome.pages_contributed == 1
    assert result.total_pages == 1


# ------------------------------------------------------------------
# required sample: index with a duplicate child and a broken-XML child
# ------------------------------------------------------------------

INDEX_WITH_DUPE_AND_BROKEN = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/broken.xml</loc></sitemap>
</sitemapindex>"""


async def test_index_with_duplicate_and_broken_xml_records_every_outcome():
    fetched: list[str] = []

    def handler(request):
        url = str(request.url)
        fetched.append(url)
        if url == "https://example.com/sitemap_index.xml":
            return httpx.Response(200, text=INDEX_WITH_DUPE_AND_BROKEN)
        if url == "https://example.com/sitemap1.xml":
            return httpx.Response(200, text=URLSET_1)
        if url == "https://example.com/broken.xml":
            return httpx.Response(200, text="<<<not valid xml>>>")
        return httpx.Response(404, text="")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    loader = SitemapLoader(client)
    result = await loader.load(
        "example.com", ["https://example.com/sitemap_index.xml"]
    )

    # each sitemap address is fetched at most once
    assert fetched.count("https://example.com/sitemap1.xml") == 1
    assert fetched.count("https://example.com/broken.xml") == 1

    # one outcome row per encounter, in traversal order
    assert [(o.url, o.depth, o.status) for o in result.outcomes] == [
        (
            "https://example.com/sitemap_index.xml",
            0,
            SitemapStatus.SUCCESS,
        ),
        ("https://example.com/sitemap1.xml", 1, SitemapStatus.SUCCESS),
        (
            "https://example.com/sitemap1.xml",
            1,
            SitemapStatus.DUPLICATE_SKIPPED,
        ),
        ("https://example.com/broken.xml", 1, SitemapStatus.PARSE_ERROR),
    ]
    by_url: dict[str, list] = {}
    for outcome in result.outcomes:
        by_url.setdefault(outcome.url, []).append(outcome)
    assert [o.status for o in by_url["https://example.com/sitemap1.xml"]] == [
        SitemapStatus.SUCCESS,
        SitemapStatus.DUPLICATE_SKIPPED,
    ]
    broken = by_url["https://example.com/broken.xml"][0]
    assert broken.pages_contributed == 0

    # good child still contributes; broken one contributes nothing
    assert result.pages == ["https://example.com/page1"]
    assert result.total_pages == 1
    assert result.truncated is False


# ------------------------------------------------------------------
# required sample: urlset over the per-domain cap
# ------------------------------------------------------------------


def _big_urlset(count: int) -> str:
    locs = "".join(
        f"  <url><loc>https://example.com/page-{i}</loc></url>\n"
        for i in range(count)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{locs}</urlset>"
    )


async def test_urlset_over_limit_is_truncated_but_keeps_raw_contribution():
    limit = 5
    raw_count = 8
    client = make_client(
        {
            "https://example.com/sitemap.xml": (
                200,
                _big_urlset(raw_count),
            )
        }
    )
    loader = SitemapLoader(client)
    result = await loader.load(
        "example.com",
        ["https://example.com/sitemap.xml"],
        max_urls=limit,
    )

    assert result.truncated is True
    assert result.truncation_limit == limit
    # final page list keeps only the first N, in original order
    assert len(result.pages) == limit
    assert result.pages == [
        f"https://example.com/page-{i}" for i in range(limit)
    ]
    # truncation does not rewrite the raw totals / per-sitemap contribution
    assert result.total_pages == raw_count
    outcome = result.outcome_for("https://example.com/sitemap.xml")
    assert outcome is not None
    assert outcome.status is SitemapStatus.SUCCESS
    assert outcome.pages_contributed == raw_count


async def test_urlset_at_or_under_limit_is_not_truncated():
    client = make_client(
        {"https://example.com/sitemap.xml": (200, _big_urlset(3))}
    )
    loader = SitemapLoader(client)
    result = await loader.load(
        "example.com",
        ["https://example.com/sitemap.xml"],
        max_urls=3,
    )
    assert result.truncated is False
    assert len(result.pages) == 3
    assert result.total_pages == 3


# ------------------------------------------------------------------
# required sample: urlset containing an ftp page address
# ------------------------------------------------------------------


async def test_urlset_with_ftp_address_keeps_only_http_pages():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/valid</loc></url>
  <url><loc>ftp://example.com/pub/file.txt</loc></url>
</urlset>"""
    client = make_client({"https://example.com/sitemap.xml": (200, xml)})
    loader = SitemapLoader(client)
    result = await loader.load(
        "example.com", ["https://example.com/sitemap.xml"]
    )

    assert result.pages == ["https://example.com/valid"]
    outcome = result.outcome_for("https://example.com/sitemap.xml")
    assert outcome is not None
    assert outcome.status is SitemapStatus.SUCCESS
    assert outcome.depth == 0
    assert outcome.pages_contributed == 1
    assert result.total_pages == 1


# ------------------------------------------------------------------
# non-HTTP sitemap addresses and depth limit
# ------------------------------------------------------------------


async def test_non_http_sitemap_address_is_filtered_without_fetching():
    index = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>ftp://example.com/ftp-sitemap.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap1.xml</loc></sitemap>
</sitemapindex>"""
    client = make_client(
        {
            "https://example.com/sitemap_index.xml": (200, index),
            "https://example.com/sitemap1.xml": (200, URLSET_1),
        }
    )
    loader = SitemapLoader(client)
    result = await loader.load(
        "example.com", ["https://example.com/sitemap_index.xml"]
    )

    ftp_outcome = result.outcome_for("ftp://example.com/ftp-sitemap.xml")
    assert ftp_outcome is not None
    assert ftp_outcome.status is SitemapStatus.NON_HTTP_FILTERED
    assert ftp_outcome.depth == 1
    assert ftp_outcome.pages_contributed == 0
    # http sibling still loads normally
    assert result.pages == ["https://example.com/page1"]


async def test_top_level_non_http_sitemap_address_is_filtered():
    client = make_client({})
    loader = SitemapLoader(client)
    result = await loader.load(
        "example.com", ["ftp://example.com/sitemap.xml"]
    )
    assert result.pages == []
    assert len(result.outcomes) == 1
    assert result.outcomes[0].status is SitemapStatus.NON_HTTP_FILTERED


async def test_recursion_beyond_max_depth_is_recorded(monkeypatch):
    monkeypatch.setattr(sitemap_module, "_MAX_DEPTH", 1)
    index_0 = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/index-1.xml</loc></sitemap>
</sitemapindex>"""
    index_1 = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/index-2.xml</loc></sitemap>
</sitemapindex>"""
    client = make_client(
        {
            "https://example.com/index-0.xml": (200, index_0),
            "https://example.com/index-1.xml": (200, index_1),
        }
    )
    loader = SitemapLoader(client)
    result = await loader.load(
        "example.com", ["https://example.com/index-0.xml"]
    )

    deep = result.outcome_for("https://example.com/index-2.xml")
    assert deep is not None
    assert deep.depth == 2
    assert deep.status is SitemapStatus.DEPTH_EXCEEDED
    assert deep.pages_contributed == 0
    # nothing below the limit was fetched (only index-0 and index-1)
    assert result.pages == []
    assert result.total_pages == 0


@pytest.mark.parametrize(
    "status",
    [
        SitemapStatus.SUCCESS,
        SitemapStatus.NON_200,
        SitemapStatus.PARSE_ERROR,
        SitemapStatus.NETWORK_ERROR,
        SitemapStatus.DEPTH_EXCEEDED,
        SitemapStatus.DUPLICATE_SKIPPED,
        SitemapStatus.NON_HTTP_FILTERED,
    ],
)
def test_status_values_are_stable_strings(status):
    # the crawler summary prints status.value; lock the wire spelling
    assert isinstance(status.value, str)
    assert status.value == str(status)
