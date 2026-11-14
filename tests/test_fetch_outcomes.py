"""Per-URL fetch outcomes: build and sync record *why* a URL was not written.

Self-made static site with one normal page plus one URL per failure category:
404 (http_error), robots.txt-denied (robots_denied), non-HTML (non_html),
empty body with no links (empty). A content-less stub *with* a link stays a
discovery-only page, not a failure.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from okf_kit.config import STATE_DIRNAME, STATE_FILENAME
from okf_kit.crawl import build_bundle
from okf_kit.model import (
    FAIL_EMPTY,
    FAIL_HTTP_ERROR,
    FAIL_NON_HTML,
    FAIL_ROBOTS_DENIED,
)
from okf_kit.sync import run_sync

from serve_util import serve

_INDEX = """<!doctype html><html><head><title>Home</title></head>
<body><main><h1>Home</h1><p>Welcome to the outcome test site.</p>
<a href="/about.html">About</a>
<a href="/missing.html">Missing</a>
<a href="/blocked/secret.html">Secret</a>
<a href="/empty.html">Empty</a>
<a href="/stub.html">Stub</a>
<a href="/notes.txt">Notes</a>
</main></body></html>"""

_ABOUT = """<!doctype html><html><head><title>About</title></head>
<body><main><h1>About</h1><p>The one normal page with real content.</p></main></body></html>"""

# No title, no headings/paragraphs/list items, no links -> nothing to extract.
_EMPTY = "<!doctype html><html><body></body></html>"

# No extractable body (textless anchor), but a link to follow -> discovery-only.
_STUB = '<!doctype html><html><body><a href="/about.html"></a></body></html>'

_ROBOTS = "User-agent: *\nDisallow: /blocked/\n"


def _make_site(root: Path) -> Path:
    site = root / "site"
    (site / "blocked").mkdir(parents=True)
    (site / "index.html").write_text(_INDEX)
    (site / "about.html").write_text(_ABOUT)
    (site / "empty.html").write_text(_EMPTY)
    (site / "stub.html").write_text(_STUB)
    (site / "notes.txt").write_text("plain text, not html")
    (site / "robots.txt").write_text(_ROBOTS)
    (site / "blocked" / "secret.html").write_text(_ABOUT.replace("About", "Secret"))
    return site


@pytest.fixture
def outcome_site(tmp_path):
    site_dir = _make_site(tmp_path)
    server, url = serve(site_dir)
    try:
        yield url, site_dir
    finally:
        server.shutdown()
        server.server_close()


def _state(bundle: Path) -> dict:
    return json.loads((bundle / STATE_DIRNAME / STATE_FILENAME).read_text(encoding="utf8"))


def _failures_by_category(state: dict) -> dict[str, str]:
    return {f["category"]: f["url"] for f in state["failures"]}


def test_build_records_per_url_outcomes(outcome_site, tmp_path, capsys):
    url, _ = outcome_site
    bundle = tmp_path / "b"
    rc = build_bundle(url, output=str(bundle), max_pages=50)
    assert rc == 0

    # Successful pages still written exactly as before.
    assert (bundle / "pages" / "home.md").exists()   # site root
    assert (bundle / "pages" / "about.md").exists()  # the normal page

    state = _state(bundle)
    # pages records keep their compatible shape
    assert state["page_count"] == len(state["pages"]) == 2
    assert all(set(p) == {"path", "url", "title", "hash"} for p in state["pages"])

    by_cat = _failures_by_category(state)
    assert set(by_cat) == {FAIL_HTTP_ERROR, FAIL_ROBOTS_DENIED, FAIL_EMPTY, FAIL_NON_HTML}
    assert by_cat[FAIL_HTTP_ERROR].endswith("/missing.html")
    assert by_cat[FAIL_ROBOTS_DENIED].endswith("/blocked/secret.html")
    assert by_cat[FAIL_EMPTY].endswith("/empty.html")
    assert by_cat[FAIL_NON_HTML].endswith("/notes.txt")
    # The stub (empty body but a followable link) is discovery, not a failure.
    assert all("/stub.html" not in f["url"] for f in state["failures"])

    # Category counts are printed at the end of the build.
    out = capsys.readouterr().out
    for cat in (FAIL_HTTP_ERROR, FAIL_ROBOTS_DENIED, FAIL_EMPTY, FAIL_NON_HTML):
        assert f"{cat}: 1" in out


def test_sync_updates_failures_without_touching_diff(outcome_site, tmp_path):
    url, site = outcome_site
    bundle = tmp_path / "b"
    build_bundle(url, output=str(bundle), max_pages=50)

    # Delete the normal page: its link now 404s.
    (site / "about.html").unlink()

    report = asyncio.run(run_sync(str(bundle)))

    # The diff reflects only the real change: one page genuinely gone.
    assert (report["added"], report["changed"], report["removed"]) == (0, 0, 1)
    assert not (bundle / "pages" / "about.md").exists()

    # This round's failure list is in the report and persisted in state.
    assert any(f["url"].endswith("/about.html") and f["category"] == FAIL_HTTP_ERROR
               for f in report["failures"])
    state = _state(bundle)
    failed = {f["url"].rsplit("/", 1)[-1]: f["category"] for f in state["failures"]}
    # The deleted page now shows up as an HTTP error, alongside the usual 404.
    assert failed["about.html"] == FAIL_HTTP_ERROR
    assert failed["missing.html"] == FAIL_HTTP_ERROR
    # The persistent failures remain; nothing was silently dropped.
    assert failed["secret.html"] == FAIL_ROBOTS_DENIED
    assert failed["empty.html"] == FAIL_EMPTY
    assert failed["notes.txt"] == FAIL_NON_HTML
    # last_sync carries the same list.
    assert state["last_sync"]["failures"] == state["failures"]
    # pages records still compatible
    assert state["page_count"] == len(state["pages"]) == 1
