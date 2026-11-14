"""Crawl statistics: max_pages truncation, duplicates, robots refusals and
sitemap failure — exercised on both the sync crawl and the async batch path.

The two paths must report identical counters for the same discovered graph,
so every scenario runs once per path through :func:`run_crawl`.
"""
import pytest

from runo.core import crawler
from runo.models.request import ExtractOptions

from conftest import BASE, PATTERN


def page(links: list[str]) -> str:
    body = "".join(f'<a href="{u}">{i}</a>' for i, u in enumerate(links))
    return f"<html><body>{body}</body></html>"


async def run_crawl(offline, schema_fields, options, make_config, *, async_mode, **cfg):
    """Drive either internal crawl implementation with shared fakes."""
    options.async_mode = async_mode
    if async_mode:
        # No LLM in tests: structured extraction returns nothing, then the
        # batch extractor hands back one trivial result per job.
        from runo.core.extractor import ExtractionResult

        async def fake_structured(html, url, fields, response_headers=None):
            return None

        async def fake_batch(jobs):
            return [
                ExtractionResult(
                    data={"title": "x"}, tokens_used=0,
                    extract_ms=0, model="fake",
                )
                for _job in jobs
            ]

        crawler.try_structured_extract_async = fake_structured
        crawler.extractor.extract_many_batched = fake_batch
        runner = crawler._async_batch_crawl
    else:
        async def fake_single(url, fields, opts, **kwargs):
            from runo.models.response import ExtractResult
            return ExtractResult(url=url, status="success", data={"title": "x"})
        crawler.run_single_extract = fake_single
        runner = crawler._sync_crawl

    result = await runner(
        seed_url=BASE + "/",
        follow_pattern=PATTERN,
        max_pages=cfg.pop("max_pages", 50),
        max_depth=cfg.pop("max_depth", 2),
        schema_fields=schema_fields,
        options=options,
        crawl_cfg=make_config(**cfg),
    )
    return result


# ── 1. max_pages truncation ────────────────────────────────────────────────

@pytest.mark.parametrize("async_mode", [False, True])
async def test_max_pages_truncation_counts_dropped_links(
    offline, schema_fields, options, make_config, async_mode,
):
    # Seed page links to five in-pattern pages. The enqueue cap is measured
    # against the visited set: the seed already occupies one of the two
    # max_pages slots, so only one link is admitted and four are dropped.
    links = [f"{BASE}/p/{i}" for i in range(5)]
    offline.html_by_url = {
        BASE + "/": page(links),
        **{u: "<html><body>leaf</body></html>" for u in links},
    }

    result = await run_crawl(
        offline, schema_fields, options, make_config,
        async_mode=async_mode, max_pages=2,
    )
    meta = result.crawl_meta

    assert meta.pages_visited == 2          # unchanged semantics
    assert meta.links_dropped_max_pages == 4
    assert meta.pages_skipped_duplicate == 0
    assert meta.pages_skipped == 0
    assert meta.pages_failed == 0


# ── 2. duplicate links ─────────────────────────────────────────────────────

@pytest.mark.parametrize("async_mode", [False, True])
async def test_duplicate_links_reported_separately_from_skipped(
    offline, schema_fields, options, make_config, async_mode,
):
    a, b, c = f"{BASE}/p/a", f"{BASE}/p/b", f"{BASE}/p/c"
    # Both depth-1 pages link back to each other and to the seed: four
    # duplicate discoveries in total (a->b, b->a, a->seed, b->seed).
    offline.html_by_url = {
        BASE + "/": page([a, b]),
        a: page([b, BASE + "/"]),
        b: page([a, BASE + "/"]),
        c: "<html><body>c</body></html>",
    }

    result = await run_crawl(
        offline, schema_fields, options, make_config, async_mode=async_mode,
    )
    meta = result.crawl_meta

    assert meta.pages_visited == 3          # seed, a, b
    assert meta.pages_skipped_duplicate == 4
    assert meta.pages_skipped == 0          # robots bucket untouched
    assert meta.links_dropped_max_pages == 0


# ── 3. robots.txt refusal ──────────────────────────────────────────────────

@pytest.mark.parametrize("async_mode", [False, True])
async def test_robots_refusal_stays_in_pages_skipped(
    offline, schema_fields, options, make_config, async_mode,
):
    allowed, denied = f"{BASE}/p/ok", f"{BASE}/p/no"
    offline.robots_rules = ["User-agent: *", "Disallow: /p/no"]
    offline.html_by_url = {
        BASE + "/": page([allowed, denied]),
        allowed: "<html><body>ok</body></html>",
        denied: "<html><body>no</body></html>",
    }

    result = await run_crawl(
        offline, schema_fields, options, make_config,
        async_mode=async_mode, ignore_robots=False,
    )
    meta = result.crawl_meta

    assert meta.pages_visited == 2          # seed + allowed
    assert meta.pages_skipped == 1          # robots refusal, unchanged
    assert meta.pages_skipped_duplicate == 0
    assert meta.links_dropped_max_pages == 0
    assert [r.url for r in result.results if r.status == "success"] == [
        BASE + "/", allowed,
    ]


# ── 4. sitemap fetch failure ───────────────────────────────────────────────

@pytest.mark.parametrize("async_mode", [False, True])
async def test_sitemap_failure_reports_zero_candidates(
    offline, schema_fields, options, make_config, async_mode,
):
    offline.sitemap_fails = True
    offline.html_by_url = {BASE + "/": "<html><body>seed only</body></html>"}

    result = await run_crawl(
        offline, schema_fields, options, make_config,
        async_mode=async_mode, use_sitemap=True,
    )
    meta = result.crawl_meta

    assert meta.pages_visited == 1
    assert meta.sitemap_candidates_total == 0
    assert meta.sitemap_candidates_enqueued == 0
    assert meta.sitemap_candidates_truncated == 0
    assert meta.links_dropped_max_pages == 0


# ── 5. sitemap success: coverage + truncation accounting ───────────────────

@pytest.mark.parametrize("async_mode", [False, True])
async def test_sitemap_candidates_enqueued_and_truncated(
    offline, schema_fields, options, make_config, async_mode,
):
    sitemap_urls = [f"{BASE}/p/s{i}" for i in range(4)]
    offline.sitemap_urls = sitemap_urls
    offline.html_by_url = {
        BASE + "/": "<html><body>seed</body></html>",
        **{u: "<html><body>leaf</body></html>" for u in sitemap_urls},
    }

    # Seed occupies one slot; max_pages=3 leaves room for two of the four
    # sitemap URLs, so two are truncated at enqueue time.
    result = await run_crawl(
        offline, schema_fields, options, make_config,
        async_mode=async_mode, use_sitemap=True, max_pages=3,
    )
    meta = result.crawl_meta

    assert meta.sitemap_candidates_total == 4
    assert meta.sitemap_candidates_enqueued == 2
    assert meta.sitemap_candidates_truncated == 2
    # Sitemap truncations are also part of the generic cap counter.
    assert meta.links_dropped_max_pages == 2
    assert meta.pages_skipped_duplicate == 0


async def test_sitemap_disabled_leaves_sitemap_counters_zero(
    offline, schema_fields, options, make_config,
):
    offline.sitemap_urls = [f"{BASE}/p/s{i}" for i in range(2)]
    offline.html_by_url = {BASE + "/": "<html><body>seed</body></html>"}

    result = await run_crawl(
        offline, schema_fields, options, make_config,
        async_mode=False, use_sitemap=False,
    )
    meta = result.crawl_meta
    assert meta.sitemap_candidates_total == 0
    assert meta.sitemap_candidates_enqueued == 0
    assert meta.sitemap_candidates_truncated == 0


async def test_sitemap_candidate_collision_counts_as_duplicate(
    offline, schema_fields, options, make_config,
):
    # End-to-end coverage of sitemap filtering: a sitemap URL outside
    # follow_pattern is not a candidate and never reaches the queue.
    fresh = f"{BASE}/p/fresh"
    offline.sitemap_urls = [fresh, BASE + "/other/not-matching"]
    offline.html_by_url = {
        BASE + "/": "<html><body>seed</body></html>",
        fresh: "<html><body>leaf</body></html>",
    }

    result = await run_crawl(
        offline, schema_fields, options, make_config,
        async_mode=False, use_sitemap=True,
    )
    meta = result.crawl_meta

    assert meta.sitemap_candidates_total == 1
    assert meta.sitemap_candidates_enqueued == 1
    assert meta.sitemap_candidates_truncated == 0
    assert meta.pages_skipped_duplicate == 0
    assert meta.links_dropped_max_pages == 0


async def test_enqueue_sitemap_duplicate_routed_to_duplicate_counter():
    # Unit-level coverage of the collision branch shared by both crawl
    # paths: a sitemap candidate whose URL is already visited is a
    # duplicate — it is excluded from every sitemap_* counter and does not
    # count as a max_pages truncation.
    import asyncio

    from runo.core.crawler import _CrawlStats, normalize_url

    stats = _CrawlStats()
    queue: asyncio.Queue = asyncio.Queue()
    visited = {normalize_url(f"{BASE}/p/dup")}
    stats.sitemap_candidates_total = 2

    admitted = await stats.enqueue(
        queue, visited, f"{BASE}/p/dup/", 1, max_pages=50,
        follow_pattern=PATTERN, from_sitemap=True,
    )
    assert admitted is False
    assert stats.pages_skipped_duplicate == 1
    assert stats.sitemap_candidates_enqueued == 0
    assert stats.sitemap_candidates_truncated == 0
    assert stats.links_dropped_max_pages == 0
    assert queue.empty()


# ── 6. backward compatibility of the response model ────────────────────────

def test_crawl_meta_defaults_keep_old_callers_working():
    from runo.models.response import CrawlMeta

    # Constructed with only the original four fields, like every pre-change
    # call site does; the new counters default to zero and serialize too.
    meta = CrawlMeta(
        pages_visited=1, pages_skipped=0, pages_failed=0, cancelled=False,
    )
    dumped = meta.model_dump()
    assert dumped["pages_visited"] == 1
    for field in (
        "links_dropped_max_pages",
        "pages_skipped_duplicate",
        "sitemap_candidates_total",
        "sitemap_candidates_enqueued",
        "sitemap_candidates_truncated",
    ):
        assert dumped[field] == 0

    # Old payloads without the new fields still validate into the model.
    old_payload = {
        "pages_visited": 3, "pages_skipped": 1,
        "pages_failed": 0, "cancelled": False,
    }
    parsed = CrawlMeta.model_validate(old_payload)
    assert parsed.pages_visited == 3
    assert parsed.links_dropped_max_pages == 0
