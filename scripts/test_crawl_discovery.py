"""Regression: crawl_site discovery must apply include/exclude filters BEFORE
the max_pages quota, and BFS must keep traversing filtered links so branches
behind excluded pages are not dropped.

The crawl result also carries two auditable manifests:
  * excluded_links  — links rejected by include_paths/exclude_paths
  * truncated_links — qualifying links not collected because of max_depth
                      (reason="depth_limit") or max_pages (reason="quota_limit")

This script builds a multi-layer static site on disk (navigation, excluded
/tag/ pages, deep qualifying pages, static assets, external links), serves it
with a real local HTTP server, and drives the unmodified crawl_site() through
the real fast HTTP engine.

Run: .venv/Scripts/python scripts/test_crawl_discovery.py   (exit 0 = PASS)
"""
from __future__ import annotations

import functools
import http.server
import shutil
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pyrecrawl.engines as E  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {name}  {detail if not cond else ''}")
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------------------
# Build the multi-layer static site in a fresh, empty temp directory
# ---------------------------------------------------------------------------

PAGE_TEMPLATE = """<!doctype html>
<html><head><title>{title}</title></head>
<body>
<header><a href="/">Home</a></header>
<nav>
{nav}
</nav>
<main><h1>{title}</h1><p>Body of {path}</p>{extra}</main>
<footer><a href="/assets/site.js">asset</a>
<a href="https://other.example.com/x">external</a></footer>
</body></html>"""


def _page(path: Path, title: str, nav_links: list[tuple[str, str]], extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    nav = "\n".join(f'<a href="{href}">{label}</a>' for href, label in nav_links)
    path.write_text(PAGE_TEMPLATE.format(title=title, path=path.as_posix(), nav=nav, extra=extra),
                    encoding="utf-8")


def build_site(root: Path) -> None:
    tag_slugs = ["foo", "bar"] + [f"t{i:02d}" for i in range(1, 20)]  # 21 tag pages
    tag_links = [(f"/tag/{s}/", f"tag {s}") for s in tag_slugs]
    # Root nav: 21 excluded /tag/ links FIRST, then the 6 qualifying links,
    # then a static asset and an external link.
    root_links = tag_links + [
        ("/about/", "About"),
        ("/blog/", "Blog"),
        ("/docs/intro.html", "Docs intro"),
        ("/guide/start.html", "Guide start"),
        ("/guide/next.html", "Guide next"),
        ("/news/", "News"),
        ("/assets/site.js", "js asset"),
        ("https://other.example.com/x", "external"),
    ]
    _page(root / "index.html", "Home", root_links)

    # Simple leaf section pages.
    _page(root / "about" / "index.html", "About", [("/", "Home")])
    _page(root / "guide" / "start.html", "Guide Start", [("/", "Home"), ("/guide/next.html", "Next")])
    _page(root / "guide" / "next.html", "Guide Next", [("/", "Home")])
    _page(root / "news" / "index.html", "News", [("/", "Home")])

    # Blog chain: /blog/ (d1) -> post-1 (d2) -> post-2 (d3) -> post-3 (d4, deep)
    _page(root / "blog" / "index.html", "Blog", [
        ("/", "Home"), ("/blog/post-1.html", "post 1"), ("/tag/bar/", "tag bar")])
    _page(root / "blog" / "post-1.html", "Post 1", [
        ("/", "Home"), ("/blog/post-2.html", "post 2"), ("/tag/foo/", "tag foo")])
    _page(root / "blog" / "post-2.html", "Post 2", [
        ("/", "Home"), ("/blog/post-3.html", "post 3")])
    _page(root / "blog" / "post-3.html", "Post 3", [("/", "Home")])

    # Docs chain: intro (d1) -> s-1 (d2) -> s-2 (d3) -> s-3 (d4, deep)
    _page(root / "docs" / "intro.html", "Docs Intro", [
        ("/", "Home"), ("/docs/s-1.html", "section 1")])
    _page(root / "docs" / "s-1.html", "Docs S1", [("/", "Home"), ("/docs/s-2.html", "section 2")])
    _page(root / "docs" / "s-2.html", "Docs S2", [("/", "Home"), ("/docs/s-3.html", "section 3")])
    _page(root / "docs" / "s-3.html", "Docs S3", [("/", "Home")])

    # Excluded /tag/ hub: the ONLY route to /articles/* is behind /tag/foo.
    # Old BFS drops the filtered link and never reaches the articles branch.
    for s in tag_slugs:
        extra = ""
        links = [("/", "Home")]
        if s == "foo":
            links += [("/tag/bar/", "tag bar"), ("/articles/a-1.html", "article 1")]
        if s == "bar":
            links.append(("/tag/foo/", "tag foo"))
        _page(root / "tag" / s / "index.html", f"Tag {s}", links, extra=extra)

    # Articles branch: a-1 (d2 via excluded tag/foo) -> a-2 (d3)
    _page(root / "articles" / "a-1.html", "Article 1", [
        ("/", "Home"), ("/articles/a-2.html", "article 2")])
    _page(root / "articles" / "a-2.html", "Article 2", [("/", "Home")])

    # Static asset (must be silently skipped, never listed anywhere).
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "site.js").write_text("console.log('asset');\n", encoding="utf-8")


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # silence request logging
        pass


def serve(root: Path):
    handler = functools.partial(_QuietHandler, directory=str(root))
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    return httpd, f"http://127.0.0.1:{port}"


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------

def _urls(records: list[dict]) -> set[str]:
    return {r["url"] for r in records}


def _no_overlaps(label: str, result: E.CrawlResult, base: str) -> None:
    page_urls = {p.url for p in result.pages}
    exc, trunc = _urls(result.excluded_links), _urls(result.truncated_links)
    check(f"{label}: excluded/truncated/page sets are disjoint",
          not (exc & trunc) and not (exc & page_urls) and not (trunc & page_urls),
          f"exc∩trunc={exc & trunc}")
    check(f"{label}: manifests have no duplicate URLs",
          len(exc) == len(result.excluded_links) and len(trunc) == len(result.truncated_links))
    check(f"{label}: static asset never listed",
          not any("site.js" in u for u in exc | trunc | page_urls))
    check(f"{label}: external link never listed",
          not any("other.example.com" in u for u in exc | trunc | page_urls))
    check(f"{label}: every manifest entry references the local site",
          all(u.startswith(base) for u in exc | trunc | page_urls))


def main() -> int:
    site_parent = Path(tempfile.mkdtemp(prefix="pyrecrawl-crawl-site-"))
    site_root = site_parent / "site"
    site_root.mkdir()
    try:
        build_site(site_root)
        httpd, base = serve(site_root)
        print(f"serving test site at {base} (root={site_root})")
        try:
            tag_urls = {f"{base}/tag/foo/", f"{base}/tag/bar/"} | {
                f"{base}/tag/t{i:02d}/" for i in range(1, 20)}
            # The root page links to itself (header "Home"), so the flat
            # harvest sees root first, then the six section links.
            qual_flat = [
                base + "/",
                f"{base}/about/", f"{base}/blog/", f"{base}/docs/intro.html",
                f"{base}/guide/start.html", f"{base}/guide/next.html", f"{base}/news/",
            ]

            # ---------------------------------------------------------------
            print("\n[1] FLAT harvest (max_depth=0), exclude /tag/, max_pages=5")
            flat = E.crawl_site(base + "/", max_pages=5, prefer="fast",
                                exclude_paths=r"/tag/", max_depth=0)
            flat_pages = [p.url for p in flat.pages]
            print("    pages     :", flat_pages)
            print("    excluded  :", len(flat.excluded_links), "links")
            print("    truncated :", flat.truncated_links)
            check("flat: quota filled (5 pages) despite 21 excluded nav links",
                  len(flat.pages) == 5, str(flat_pages))
            check("flat: first five qualifying links collected in order",
                  flat_pages == qual_flat[:5], str(flat_pages))
            check("flat: no /tag/ page collected",
                  not any("/tag/" in u for u in flat_pages))
            check("flat: all 21 tag links reported as excluded",
                  _urls(flat.excluded_links) == tag_urls,
                  str(sorted(_urls(flat.excluded_links))[:3]))
            check("flat: excluded entries name the exclude rule + found_on",
                  all(r["reason"] == "exclude_paths" and r["found_on"] == base + "/"
                      for r in flat.excluded_links))
            check("flat: qualifying links past the cap reported quota-truncated",
                  [(r["url"], r["reason"]) for r in flat.truncated_links]
                  == [(f"{base}/guide/next.html", "quota_limit"),
                      (f"{base}/news/", "quota_limit")],
                  str(flat.truncated_links))
            check("flat: method label", flat.method == "map+scrape")
            _no_overlaps("flat", flat, base)

            # ---------------------------------------------------------------
            print("\n[2] BFS (max_depth=3), exclude /tag/, max_pages=50")
            bfs = E.crawl_site(base + "/", max_pages=50, prefer="fast",
                               exclude_paths=r"/tag/", max_depth=3)
            bfs_pages = {p.url for p in bfs.pages}
            print("    pages     :", sorted(bfs_pages))
            print("    excluded  :", len(bfs.excluded_links), "links")
            print("    truncated :", bfs.truncated_links)
            expected_pages = {
                base + "/",
                f"{base}/about/", f"{base}/blog/", f"{base}/docs/intro.html",
                f"{base}/guide/start.html", f"{base}/guide/next.html", f"{base}/news/",
                f"{base}/blog/post-1.html", f"{base}/docs/s-1.html",
                f"{base}/articles/a-1.html",  # only reachable THROUGH excluded /tag/foo
                f"{base}/blog/post-2.html", f"{base}/docs/s-2.html",
                f"{base}/articles/a-2.html",
            }
            check("bfs: all 13 qualifying pages within depth 3 collected",
                  bfs_pages == expected_pages, f"missing={expected_pages - bfs_pages}, "
                  f"extra={bfs_pages - expected_pages}")
            check("bfs: deep branch behind excluded /tag/foo was traversed",
                  f"{base}/articles/a-1.html" in bfs_pages
                  and f"{base}/articles/a-2.html" in bfs_pages)
            check("bfs: no /tag/ page collected (transit-only)",
                  not any("/tag/" in u for u in bfs_pages))
            check("bfs: 21 tag links reported excluded with depth + found_on",
                  _urls(bfs.excluded_links) == tag_urls
                  and all(r["reason"] == "exclude_paths" for r in bfs.excluded_links)
                  and all(r["depth"] == 1 and r["found_on"] == base + "/"
                          for r in bfs.excluded_links if r["url"].startswith(base + "/tag/")))
            depth_trunc = {r["url"]: r for r in bfs.truncated_links}
            check("bfs: depth-4 pages reported depth_limit-truncated",
                  set(depth_trunc) == {f"{base}/blog/post-3.html", f"{base}/docs/s-3.html"}
                  and all(r["reason"] == "depth_limit" and r["depth"] == 4
                          for r in depth_trunc.values()),
                  str(bfs.truncated_links))
            check("bfs: method label", bfs.method == "bfs")
            _no_overlaps("bfs", bfs, base)

            # ---------------------------------------------------------------
            print("\n[3] BFS quota cut (max_depth=3), exclude /tag/, max_pages=4")
            cut = E.crawl_site(base + "/", max_pages=4, prefer="fast",
                               exclude_paths=r"/tag/", max_depth=3)
            cut_pages = [p.url for p in cut.pages]
            print("    pages     :", cut_pages)
            print("    truncated :", cut.truncated_links)
            check("quota: exactly max_pages collected (hard cap)", len(cut.pages) == 4,
                  str(cut_pages))
            check("quota: root + first three depth-1 pages in BFS order",
                  cut_pages == [base + "/", f"{base}/about/", f"{base}/blog/",
                                f"{base}/docs/intro.html"], str(cut_pages))
            quota = {r["url"]: r["reason"] for r in cut.truncated_links}
            expected_quota = {
                f"{base}/guide/start.html": "quota_limit",
                f"{base}/guide/next.html": "quota_limit",
                f"{base}/news/": "quota_limit",
                # a-1 is discovered BEFORE the cap trips: the BFS reaches it
                # by traversing the excluded /tag/foo transit page — the very
                # branch the old code dropped. It then waits in the queue and
                # is reported as quota-truncated once the cap is enforced.
                f"{base}/articles/a-1.html": "quota_limit",
                f"{base}/blog/post-1.html": "quota_limit",
                f"{base}/docs/s-1.html": "quota_limit",
            }
            check("quota: undiscovered-when-cut qualifying links all reported",
                  quota == expected_quota, str(quota))
            check("quota: a-1 truncation found_on names the excluded transit page",
                  next(r for r in cut.truncated_links
                       if r["url"] == f"{base}/articles/a-1.html")["found_on"]
                  == f"{base}/tag/foo/")
            check("quota: excluded manifest still complete (21 tags)",
                  _urls(cut.excluded_links) == tag_urls)
            _no_overlaps("quota", cut, base)

            # ---------------------------------------------------------------
            print("\n[4] BFS with include_paths=/blog/ (max_depth=3, max_pages=50)")
            inc = E.crawl_site(base + "/", max_pages=50, prefer="fast",
                               include_paths=r"/blog/", max_depth=3)
            inc_pages = [p.url for p in inc.pages]
            print("    pages     :", inc_pages)
            print("    excluded  :", len(inc.excluded_links), "links")
            print("    truncated :", inc.truncated_links)
            check("include: only root + /blog/ pages collected",
                  inc_pages == [base + "/", f"{base}/blog/",
                                f"{base}/blog/post-1.html", f"{base}/blog/post-2.html"],
                  str(inc_pages))
            inc_exc = _urls(inc.excluded_links)
            check("include: non-blog pages reported with include_paths reason",
                  f"{base}/about/" in inc_exc and f"{base}/tag/foo/" in inc_exc
                  and all(r["reason"] == "include_paths" for r in inc.excluded_links))
            check("include: post-3 beyond depth still depth-truncated (rule takes "
                  "precedence only when filtered)",
                  [(r["url"], r["reason"]) for r in inc.truncated_links]
                  == [(f"{base}/blog/post-3.html", "depth_limit")],
                  str(inc.truncated_links))
            _no_overlaps("include", inc, base)

            # ---------------------------------------------------------------
            print("\n[5] No-filter control: depth truncation only, nothing excluded")
            plain = E.crawl_site(base + "/", max_pages=50, prefer="fast", max_depth=2)
            check("plain: excluded list empty without filters",
                  plain.excluded_links == [])
            plain_trunc = {r["url"] for r in plain.truncated_links}
            # depth-3 qualifying pages discovered from depth-2 pages
            check("plain: depth-3 links truncated, no rule rejections",
                  {f"{base}/blog/post-2.html", f"{base}/docs/s-2.html",
                   f"{base}/articles/a-2.html"} <= plain_trunc
                  and all(r["reason"] == "depth_limit" for r in plain.truncated_links),
                  str(plain.truncated_links))
        finally:
            httpd.shutdown()
            httpd.server_close()
    finally:
        shutil.rmtree(site_parent, ignore_errors=True)

    print("\nRESULT:", "PASS" if not failures else f"FAIL -> {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
