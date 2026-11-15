from crawlforge.engine.extract import extract_url_row


HTML_BASIC = b"""<!DOCTYPE html>
<html lang="en">
<head>
  <title>Hello World</title>
  <meta name="description" content="A test page">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="/canonical-target">
  <link rel="alternate" hreflang="es" href="/es/">
  <meta name="viewport" content="width=device-width">
  <meta charset="UTF-8">
</head>
<body>
  <nav><a href="/nav-link">Nav</a></nav>
  <h1>Main heading</h1>
  <h2>Section A</h2>
  <h2>Section B</h2>
  <p>Some content with words. Several words here in fact.</p>
  <a href="/internal">Internal link</a>
  <a href="https://external.example.com/x">External</a>
  <img src="/foo.webp" alt="A foo" loading="lazy" width="100" height="50">
  <footer><a href="/footer-link">Footer</a></footer>
</body>
</html>
"""


def test_extracts_basic_metadata():
    row = extract_url_row(
        url="https://example.com/page",
        final_url="https://example.com/page",
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
        body=HTML_BASIC,
    )
    assert row["title"] == "Hello World"
    assert row["meta_description"] == "A test page"
    assert row["meta_robots"] == "index, follow"
    assert row["lang"] == "en"
    assert row["viewport"] == "width=device-width"
    assert row["canonical"] == "https://example.com/canonical-target"


def test_extracts_headings_as_lists():
    row = extract_url_row(
        url="https://example.com/",
        final_url="https://example.com/",
        status_code=200,
        headers={"content-type": "text/html"},
        body=HTML_BASIC,
    )
    assert row["h1"] == ["Main heading"]
    assert row["h2"] == ["Section A", "Section B"]


def test_extracts_links_with_nav_and_footer_flags():
    row = extract_url_row(
        url="https://example.com/",
        final_url="https://example.com/",
        status_code=200,
        headers={"content-type": "text/html"},
        body=HTML_BASIC,
    )
    by_target = {l["target_url"]: l for l in row["_links"]}
    assert "https://example.com/nav-link" in by_target
    assert by_target["https://example.com/nav-link"]["in_navigation"] is True
    assert by_target["https://example.com/footer-link"]["in_footer"] is True
    assert by_target["https://example.com/internal"]["in_navigation"] is False


def test_extracts_hreflang():
    row = extract_url_row(
        url="https://example.com/",
        final_url="https://example.com/",
        status_code=200,
        headers={"content-type": "text/html"},
        body=HTML_BASIC,
    )
    assert len(row["_hreflang"]) == 1
    assert row["_hreflang"][0]["lang"] == "es"
    assert row["_hreflang"][0]["href"] == "https://example.com/es"


def test_extracts_images_as_resources():
    row = extract_url_row(
        url="https://example.com/",
        final_url="https://example.com/",
        status_code=200,
        headers={"content-type": "text/html"},
        body=HTML_BASIC,
    )
    images = [r for r in row["_resources"] if r["resource_type"] == "image"]
    assert len(images) == 1
    assert images[0]["alt"] == "A foo"
    assert images[0]["format"] == "webp"
    assert images[0]["loading_attr"] == "lazy"
    assert images[0]["width"] == 100


def test_indexability_blocks_on_noindex():
    body = b"<html><head><title>x</title><meta name='robots' content='noindex'></head><body></body></html>"
    row = extract_url_row(
        url="https://a.com/",
        final_url="https://a.com/",
        status_code=200,
        headers={"content-type": "text/html"},
        body=body,
    )
    assert row["is_indexable"] is False
    assert "noindex" in row["indexability_reason"].lower()


def test_indexability_blocks_on_canonical_to_other():
    body = b"<html><head><title>x</title><link rel='canonical' href='https://other.com/'></head><body></body></html>"
    row = extract_url_row(
        url="https://a.com/",
        final_url="https://a.com/",
        status_code=200,
        headers={"content-type": "text/html"},
        body=body,
    )
    assert row["is_indexable"] is False
    assert "canonical" in row["indexability_reason"].lower()


def test_indexability_passes_for_clean_200():
    body = b"<html><head><title>x</title></head><body><p>hi</p></body></html>"
    row = extract_url_row(
        url="https://a.com/",
        final_url="https://a.com/",
        status_code=200,
        headers={"content-type": "text/html"},
        body=body,
    )
    assert row["is_indexable"] is True
    assert row["indexability_reason"] is None


def test_non_html_returns_minimal_row():
    row = extract_url_row(
        url="https://a.com/x.png",
        final_url="https://a.com/x.png",
        status_code=200,
        headers={"content-type": "image/png"},
        body=b"\x89PNG\r\n",
    )
    assert row["title"] is None
    assert row["h1"] == []
    assert row["_links"] == []
