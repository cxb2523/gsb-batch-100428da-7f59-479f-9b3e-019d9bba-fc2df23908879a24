'''
Tests for sitemap subgraph outcome tracking in recursive_xml_crawler.

Covers the three required scenarios using locally crafted fixtures only:
  1. normal expansion (plain XML and valid .xml.gz)
  2. corrupt archives (.gz / .zip that cannot be decompressed), kept separate
     from archives that decompress but contain no links
  3. depth truncation (subgraphs not expanded once -de depth is reached)

Also checks the summary is visible on the existing output channels:
terminal (both normal and -p pipeable mode) and the -o output file.
'''
import gzip
import io
import os
import threading
import types
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from endpointscanner import scrapefiles
from endpointscanner.scrapefiles import (
    recursive_xml_crawler,
    render_sitemap_summary_text,
    build_sitemap_summary,
    SITEMAP_PARSED_WITH_LINKS,
    SITEMAP_PARSED_NO_LINKS,
    SITEMAP_DECOMPRESSION_FAILED,
    SITEMAP_DEPTH_SKIPPED,
    SITEMAP_FETCH_FAILED,
)
from endpointscanner.outputendpointsfound import display_and_save_results

TARGET = "http://127.0.0.1:8000"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def make_args(**overrides):
    '''minimal args namespace with only the attributes recursive_xml_crawler reads'''
    defaults = dict(
        disable_structure_files=False,
        depth=None,
        parse_zip=False,
        scan_timeout=None,
        show_prog=False,
        show_source=False,
        no_auto_input=True,
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def make_output_args(**overrides):
    defaults = dict(
        pipeable=False,
        output_file=None,
        only_endpoints=False,
        only_sub=False,
        only_external=False,
        raw_output=True,
        still_show_invalid=False,
        show_source=False,
        show_assets=False,
        show_media=False,
        show_404s=False,
        scan_timeout=None,
        disable_sensitive_endpoint=True,
        extra_details=False,
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


class FakeResponse:
    def __init__(self, status_code=200, text="", content=b"", content_type="application/xml"):
        self.status_code = status_code
        self.text = text
        self.content = content
        self.headers = {"Content-Type": content_type}


def gz_bytes(payload):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(payload)
    return buf.getvalue()


def zip_bytes(member_name, payload):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member_name, payload)
    return buf.getvalue()


def run_crawler(monkeypatch, routes, xml_files, xml_depths, args=None,
                found_paths=None, unique_progress_paths=None):
    '''
    routes: {url_path: FakeResponse or Exception instance}
    Returns (found_paths, sitemap_outcomes).
    '''
    import endpointscanner.scrapefiles as sf

    def fake_get(url, *a, **kw):
        path = url.replace(TARGET, "") or "/"
        route = routes.get(path)
        if isinstance(route, Exception):
            raise route
        if route is None:
            return FakeResponse(status_code=404, text="not found", content_type="text/html")
        return route

    monkeypatch.setattr(sf.requests, "get", fake_get)

    args = args or make_args()
    found_paths = set() if found_paths is None else found_paths
    # seed found_paths with the queued sitemaps, mirroring enumerateendpoint
    for f in xml_files:
        found_paths.add(f)
    unique_progress_paths = set() if unique_progress_paths is None else unique_progress_paths

    _, _, outcomes = recursive_xml_crawler(
        args=args,
        target=TARGET,
        HEADER={},
        session_cookies=None,
        impersonate_settings=None,
        verified_esl_domains=[],
        verified_esl_scripts=[],
        USELESSSTUFF=set(),
        nd=False,
        found_paths=found_paths,
        xml_files=list(xml_files),
        xml_depths=dict(xml_depths),
        unique_progress_paths=unique_progress_paths,
        start_test_time=0.0,
        checktimestatusalready=None,
        autoinput_timeout=1,
    )
    return found_paths, outcomes


def outcome_map(outcomes):
    return {entry["file"]: entry for entry in outcomes}


# --------------------------------------------------------------------------- #
# 1. normal expansion
# --------------------------------------------------------------------------- #
PLAIN_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>http://127.0.0.1:8000/home</loc></url>
  <url><loc>http://127.0.0.1:8000/about</loc></url>
  <url><loc>/contact</loc></url>
</urlset>
"""

INDEX_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>http://127.0.0.1:8000/sitemap-pages.xml.gz</loc></sitemap>
  <sitemap><loc>/sitemap-posts.xml</loc></sitemap>
</sitemapindex>
"""

GZ_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>http://127.0.0.1:8000/gz-page-one</loc></url>
  <url><loc>http://127.0.0.1:8000/gz-page-two</loc></url>
</urlset>
"""

POSTS_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>/blog/post-1</loc></url>
</urlset>
"""


def test_plain_xml_expands_and_is_recorded_as_parsed_with_links(monkeypatch):
    routes = {
        "/sitemap.xml": FakeResponse(text=PLAIN_SITEMAP, content=PLAIN_SITEMAP.encode()),
    }
    found_paths, outcomes = run_crawler(
        monkeypatch, routes,
        xml_files=["/sitemap.xml"], xml_depths={"/sitemap.xml": 1},
        args=make_args(),
    )

    assert "/home" in found_paths
    assert "/about" in found_paths
    assert "/contact" in found_paths

    mapped = outcome_map(outcomes)
    assert "/sitemap.xml" in mapped
    entry = mapped["/sitemap.xml"]
    assert entry["outcome"] == SITEMAP_PARSED_WITH_LINKS
    assert entry["depth"] == 1
    assert "3 link(s)" in entry["detail"]


def test_valid_gz_sitemap_expands_with_pz(monkeypatch):
    routes = {
        "/sitemap-index.xml": FakeResponse(
            text=INDEX_SITEMAP, content=INDEX_SITEMAP.encode()
        ),
        "/sitemap-pages.xml.gz": FakeResponse(
            content=gz_bytes(GZ_SITEMAP), content_type="application/gzip"
        ),
        "/sitemap-posts.xml": FakeResponse(
            text=POSTS_SITEMAP, content=POSTS_SITEMAP.encode()
        ),
    }
    found_paths, outcomes = run_crawler(
        monkeypatch, routes,
        xml_files=["/sitemap-index.xml"],
        xml_depths={"/sitemap-index.xml": 1},
        args=make_args(parse_zip=True),
    )

    # the nested .gz subgraph was actually decompressed and scraped
    assert "/gz-page-one" in found_paths
    assert "/gz-page-two" in found_paths
    # the nested plain xml subgraph was also followed
    assert "/blog/post-1" in found_paths

    mapped = outcome_map(outcomes)
    assert mapped["/sitemap-index.xml"]["outcome"] == SITEMAP_PARSED_WITH_LINKS
    assert mapped["/sitemap-pages.xml.gz"]["outcome"] == SITEMAP_PARSED_WITH_LINKS
    assert mapped["/sitemap-posts.xml"]["outcome"] == SITEMAP_PARSED_WITH_LINKS

    counts = build_sitemap_summary(outcomes)
    assert counts["total"] == 3
    assert counts[SITEMAP_PARSED_WITH_LINKS] == 3
    assert counts[SITEMAP_DECOMPRESSION_FAILED] == 0


# --------------------------------------------------------------------------- #
# 2. corrupt archives vs empty content
# --------------------------------------------------------------------------- #
def test_corrupt_gz_is_decompression_failed_distinct_from_empty(monkeypatch):
    corrupt_gz_route = FakeResponse(
        content=b"this is definitely not gzip data \x00\x01\x02",
        content_type="application/gzip",
    )
    # a valid gzip stream whose decompressed payload has no <loc> links
    empty_gz_route = FakeResponse(
        content=gz_bytes('<?xml version="1.0"?><urlset></urlset>'),
        content_type="application/gzip",
    )
    routes = {
        "/broken.xml.gz": corrupt_gz_route,
        "/empty.xml.gz": empty_gz_route,
    }
    _, outcomes = run_crawler(
        monkeypatch, routes,
        xml_files=["/broken.xml.gz", "/empty.xml.gz"],
        xml_depths={"/broken.xml.gz": 1, "/empty.xml.gz": 1},
        args=make_args(parse_zip=True),
    )

    mapped = outcome_map(outcomes)
    # corrupt bytes must be reported as a decompression failure, with the error
    assert mapped["/broken.xml.gz"]["outcome"] == SITEMAP_DECOMPRESSION_FAILED
    assert mapped["/broken.xml.gz"]["detail"]
    # readable but linkless content is parsed_no_links, never a failure
    assert mapped["/empty.xml.gz"]["outcome"] == SITEMAP_PARSED_NO_LINKS
    assert mapped["/empty.xml.gz"]["detail"] == ""

    counts = build_sitemap_summary(outcomes)
    assert counts[SITEMAP_DECOMPRESSION_FAILED] == 1
    assert counts[SITEMAP_PARSED_NO_LINKS] == 1


def test_corrupt_zip_is_decompression_failed(monkeypatch):
    routes = {
        "/broken.xml.zip": FakeResponse(
            content=b"PK\x03\x04 corrupted zip payload",
            content_type="application/zip",
        ),
        "/valid-empty.xml.zip": FakeResponse(
            content=zip_bytes("sitemap.xml", '<?xml version="1.0"?><urlset/>'),
            content_type="application/zip",
        ),
    }
    _, outcomes = run_crawler(
        monkeypatch, routes,
        xml_files=["/broken.xml.zip", "/valid-empty.xml.zip"],
        xml_depths={"/broken.xml.zip": 1, "/valid-empty.xml.zip": 1},
        args=make_args(parse_zip=True),
    )

    mapped = outcome_map(outcomes)
    assert mapped["/broken.xml.zip"]["outcome"] == SITEMAP_DECOMPRESSION_FAILED
    assert "BadZipFile" in mapped["/broken.xml.zip"]["detail"] or mapped["/broken.xml.zip"]["detail"]
    assert mapped["/valid-empty.xml.zip"]["outcome"] == SITEMAP_PARSED_NO_LINKS


def test_pz_disabled_keeps_original_behavior_and_records_no_links(monkeypatch):
    # -pz not passed: archives are not expanded (existing behavior must remain)
    routes = {
        "/sitemap-pages.xml.gz": FakeResponse(
            content=gz_bytes(GZ_SITEMAP), content_type="application/gzip"
        ),
    }
    found_paths, outcomes = run_crawler(
        monkeypatch, routes,
        xml_files=["/sitemap-pages.xml.gz"],
        xml_depths={"/sitemap-pages.xml.gz": 1},
        args=make_args(parse_zip=False),
    )

    # nothing decompressed -> no endpoints extracted from the archive
    assert "/gz-page-one" not in found_paths
    mapped = outcome_map(outcomes)
    assert mapped["/sitemap-pages.xml.gz"]["outcome"] == SITEMAP_PARSED_NO_LINKS
    assert "pz" in mapped["/sitemap-pages.xml.gz"]["detail"]


def test_non_200_and_request_error_are_fetch_failures(monkeypatch):
    routes = {
        "/missing.xml": FakeResponse(status_code=404, text="nope", content_type="text/html"),
        "/boom.xml": ConnectionError("connection refused"),
    }
    _, outcomes = run_crawler(
        monkeypatch, routes,
        xml_files=["/missing.xml", "/boom.xml"],
        xml_depths={"/missing.xml": 1, "/boom.xml": 1},
        args=make_args(),
    )

    mapped = outcome_map(outcomes)
    assert mapped["/missing.xml"]["outcome"] == SITEMAP_FETCH_FAILED
    assert "404" in mapped["/missing.xml"]["detail"]
    assert mapped["/boom.xml"]["outcome"] == SITEMAP_FETCH_FAILED
    assert "ConnectionError" in mapped["/boom.xml"]["detail"]


# --------------------------------------------------------------------------- #
# 3. depth truncation
# --------------------------------------------------------------------------- #
def test_depth_limit_skips_queued_subgraph_without_fetching(monkeypatch):
    # a depth-2 subgraph while -de 1 is in force: must never be requested,
    # and must be recorded as depth_skipped rather than silently dropped
    routes = {
        "/deep.xml": FakeResponse(text=PLAIN_SITEMAP, content=PLAIN_SITEMAP.encode()),
    }
    captured_urls = []

    def fake_get(url, *a, **kw):
        captured_urls.append(url)
        return routes.get(url.replace(TARGET, ""), FakeResponse(status_code=404))

    monkeypatch.setattr(scrapefiles.requests, "get", fake_get)

    _, outcomes = run_crawler(
        monkeypatch, routes,
        xml_files=["/deep.xml"],
        xml_depths={"/deep.xml": 2},
        args=make_args(depth=1),
    )

    assert not any(u.endswith("/deep.xml") for u in captured_urls)
    mapped = outcome_map(outcomes)
    assert mapped["/deep.xml"]["outcome"] == SITEMAP_DEPTH_SKIPPED
    assert mapped["/deep.xml"]["depth"] == 2
    assert "depth limit 1" in mapped["/deep.xml"]["detail"]


def test_depth_limit_prevents_nesting_and_records_skipped_child(monkeypatch):
    # index at depth 1 with -de 1: its own links are extracted, but any nested
    # sitemap it references must not be expanded and must be recorded
    routes = {
        "/sitemap-index.xml": FakeResponse(
            text=INDEX_SITEMAP, content=INDEX_SITEMAP.encode()
        ),
        # if the crawler wrongly fetches these, the test route returns links
        "/sitemap-pages.xml.gz": FakeResponse(
            content=gz_bytes(GZ_SITEMAP), content_type="application/gzip"
        ),
        "/sitemap-posts.xml": FakeResponse(
            text=POSTS_SITEMAP, content=POSTS_SITEMAP.encode()
        ),
    }
    found_paths, outcomes = run_crawler(
        monkeypatch, routes,
        xml_files=["/sitemap-index.xml"],
        xml_depths={"/sitemap-index.xml": 1},
        args=make_args(depth=1, parse_zip=True),
    )

    mapped = outcome_map(outcomes)
    # parent still parsed successfully (its own behavior unchanged)
    assert mapped["/sitemap-index.xml"]["outcome"] == SITEMAP_PARSED_WITH_LINKS
    # both nested subgraphs recorded as depth-skipped, none fetched/expanded
    assert mapped["/sitemap-pages.xml.gz"]["outcome"] == SITEMAP_DEPTH_SKIPPED
    assert mapped["/sitemap-posts.xml"]["outcome"] == SITEMAP_DEPTH_SKIPPED
    # endpoints that only exist inside the unexpanded children must not appear
    assert "/gz-page-one" not in found_paths
    assert "/blog/post-1" not in found_paths

    counts = build_sitemap_summary(outcomes)
    assert counts[SITEMAP_DEPTH_SKIPPED] == 2
    assert counts[SITEMAP_PARSED_WITH_LINKS] == 1


def test_every_processed_subgraph_has_exactly_one_outcome(monkeypatch):
    routes = {
        "/sitemap.xml": FakeResponse(text=INDEX_SITEMAP, content=INDEX_SITEMAP.encode()),
        "/sitemap-pages.xml.gz": FakeResponse(
            content=b"not gzip at all", content_type="application/gzip"
        ),
        "/sitemap-posts.xml": FakeResponse(
            text='<?xml version="1.0"?><urlset/>', content_type="application/xml"
        ),
    }
    _, outcomes = run_crawler(
        monkeypatch, routes,
        xml_files=["/sitemap.xml"],
        xml_depths={"/sitemap.xml": 1},
        args=make_args(parse_zip=True),
    )

    files = [entry["file"] for entry in outcomes]
    assert len(files) == len(set(files))
    assert set(files) == {
        "/sitemap.xml",
        "/sitemap-pages.xml.gz",
        "/sitemap-posts.xml",
    }


# --------------------------------------------------------------------------- #
# summary rendering on the existing output channels
# --------------------------------------------------------------------------- #
def _sample_outcomes():
    return [
        {"file": "/sitemap.xml", "outcome": SITEMAP_PARSED_WITH_LINKS,
         "depth": 1, "detail": "2 link(s) extracted"},
        {"file": "/broken.xml.gz", "outcome": SITEMAP_DECOMPRESSION_FAILED,
         "depth": 2, "detail": "BadGzipFile: not a gzip file"},
        {"file": "/empty.xml", "outcome": SITEMAP_PARSED_NO_LINKS,
         "depth": 1, "detail": ""},
        {"file": "/deep.xml", "outcome": SITEMAP_DEPTH_SKIPPED,
         "depth": 3, "detail": "depth limit 2 reached"},
    ]


def test_render_pipeable_uses_machine_friendly_hash_lines():
    text = render_sitemap_summary_text(_sample_outcomes(), pipeable=True)
    lines = text.splitlines()
    # every emitted line is prefixed so a pipe consumer can distinguish it
    assert all(line.startswith("# sitemap") for line in lines)
    assert "# sitemap-subgraphs-processed: 4" in lines
    assert "# sitemap-decompression_failed: 1" in lines
    assert "# sitemap-depth_skipped: 1" in lines
    assert any("/broken.xml.gz" in line and "decompression_failed" in line for line in lines)


def test_render_human_readable_lists_each_subgraph():
    text = render_sitemap_summary_text(_sample_outcomes(), pipeable=False)
    assert "SITEMAP SUBGRAPHS" in text
    assert "Total subgraphs processed: 4" in text
    assert "/broken.xml.gz" in text
    assert "Decompression failed" in text
    assert "depth limit" in text


def test_empty_outcomes_render_nothing():
    assert render_sitemap_summary_text([], pipeable=True) == ""
    assert render_sitemap_summary_text([], pipeable=False) == ""


def _call_display(args, tmp_path, output_file=None):
    outcomes = _sample_outcomes()
    display_and_save_results(
        args=args,
        show_dead=False,
        target=TARGET,
        results_200=[], results_services=[], results_ext=[], results_subd=[],
        results_frameworks=[], results_30x=[], results_protected=[],
        results_assets=[], results_dead=[], unsorted=[],
        assets_suffix="", dead_suffix="", invalidated_suffix="",
        all_scanned_sources=[],
        unsorted_paths=["/one", "/two"], invalidated_count=0,
        invalidated_endpoints=[], sitemap_outcomes=outcomes,
    )
    if output_file:
        with open(output_file, "r", encoding="utf-8") as fh:
            return fh.read()
    return None


def test_summary_visible_in_pipe_mode_and_output_file(capsys, tmp_path):
    out_file = os.path.join(str(tmp_path), "pipe-results.txt")
    args = make_output_args(pipeable=True, output_file=out_file)
    _call_display(args, tmp_path, output_file=out_file)

    captured = capsys.readouterr().out
    assert "# sitemap-subgraphs-processed: 4" in captured
    assert "/broken.xml.gz" in captured

    file_text = _call_display(args, tmp_path, output_file=out_file)
    assert "# sitemap-subgraphs-processed: 4" in file_text
    assert "# sitemap-decompression_failed: 1" in file_text


def test_summary_visible_in_normal_mode_output_file(capsys, tmp_path):
    out_file = os.path.join(str(tmp_path), "normal-results.txt")
    args = make_output_args(pipeable=False, output_file=out_file)
    _call_display(args, tmp_path, output_file=out_file)

    captured = capsys.readouterr().out
    assert "SITEMAP SUBGRAPHS" in captured
    assert "Total subgraphs processed: 4" in captured

    with open(out_file, "r", encoding="utf-8") as fh:
        file_text = fh.read()
    assert "SITEMAP SUBGRAPHS" in file_text
    assert "Total subgraphs processed: 4" in file_text
    assert "/deep.xml" in file_text


def test_filtered_branch_also_carries_summary(capsys, tmp_path):
    # the -oe/-os/-ox branch (filter_endpoints returns a list)
    out_file = os.path.join(str(tmp_path), "filtered-results.txt")
    args = make_output_args(pipeable=True, output_file=out_file, only_endpoints=True)
    _call_display(args, tmp_path, output_file=out_file)

    captured = capsys.readouterr().out
    assert "# sitemap-subgraphs-processed: 4" in captured

    with open(out_file, "r", encoding="utf-8") as fh:
        file_text = fh.read()
    assert "# sitemap-subgraphs-processed: 4" in file_text


# --------------------------------------------------------------------------- #
# end-to-end against a real loopback HTTP server (no mocked HTTP layer)
# --------------------------------------------------------------------------- #
def test_end_to_end_local_http_server_covers_all_three_scenarios():
    # relative locs so the fixture works against any random loopback port
    gz_sitemap_relative = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>/gz-page-one</loc></url>'
        '<url><loc>/gz-page-two</loc></url>'
        '</urlset>'
    )
    valid_gz = gz_bytes(gz_sitemap_relative)
    # depth-2 index that points at an even deeper subgraph (depth 3)
    posts_index = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<sitemap><loc>/sitemap-deep.xml</loc></sitemap>'
        '</sitemapindex>'
    )
    # relative locs so nested subgraphs resolve against the random test port
    root_index = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<sitemap><loc>/sitemap-pages.xml.gz</loc></sitemap>'
        '<sitemap><loc>/sitemap-posts.xml</loc></sitemap>'
        '</sitemapindex>'
    )
    server_routes = {
        "/sitemap.xml": ("application/xml", root_index.encode("utf-8")),
        "/sitemap-pages.xml.gz": ("application/gzip", valid_gz),
        "/sitemap-posts.xml": ("application/xml", posts_index.encode("utf-8")),
        # /sitemap-deep.xml intentionally has no route: it must never be fetched
        "/corrupt.xml.gz": ("application/gzip", b"broken gzip bytes \x00\x01"),
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            route = server_routes.get(self.path)
            if route is None:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"not found")
                return
            content_type, body = route
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        local_target = f"http://127.0.0.1:{port}"
        # depth 2: depth-1 and depth-2 subgraphs expand, depth-3 is truncated
        args = make_args(parse_zip=True, depth=2)
        found_paths = set()
        xml_files = ["/sitemap.xml", "/corrupt.xml.gz"]
        xml_depths = {"/sitemap.xml": 1, "/corrupt.xml.gz": 1}
        for f in xml_files:
            found_paths.add(f)

        _, _, outcomes = recursive_xml_crawler(
            args=args,
            target=local_target,
            HEADER={},
            session_cookies=None,
            impersonate_settings=None,
            verified_esl_domains=[],
            verified_esl_scripts=[],
            USELESSSTUFF=set(),
            nd=False,
            found_paths=found_paths,
            xml_files=list(xml_files),
            xml_depths=dict(xml_depths),
            unique_progress_paths=set(),
            start_test_time=0.0,
            checktimestatusalready=None,
            autoinput_timeout=1,
        )

        mapped = outcome_map(outcomes)
        # scenario 1: index and valid nested gz both expand and yield links
        assert mapped["/sitemap.xml"]["outcome"] == SITEMAP_PARSED_WITH_LINKS
        assert mapped["/sitemap-pages.xml.gz"]["outcome"] == SITEMAP_PARSED_WITH_LINKS
        assert "/gz-page-one" in found_paths
        # the depth-2 posts index is fetched and parsed before its child is cut off
        assert mapped["/sitemap-posts.xml"]["outcome"] == SITEMAP_PARSED_WITH_LINKS
        # scenario 2: corrupt archive reported distinctly as decompression failure
        assert mapped["/corrupt.xml.gz"]["outcome"] == SITEMAP_DECOMPRESSION_FAILED
        # scenario 3: the depth-3 subgraph is never expanded and is recorded as such
        assert mapped["/sitemap-deep.xml"]["outcome"] == SITEMAP_DEPTH_SKIPPED
        assert mapped["/sitemap-deep.xml"]["depth"] == 3

        counts = build_sitemap_summary(outcomes)
        assert counts["total"] == 5
        assert counts[SITEMAP_PARSED_WITH_LINKS] == 3
        assert counts[SITEMAP_DECOMPRESSION_FAILED] == 1
        assert counts[SITEMAP_DEPTH_SKIPPED] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
