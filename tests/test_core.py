from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import requests

import scrapi_reddit.core as core
from scrapi_reddit.core import (
    BASE_URL,
    build_search_target,
    ListingTarget,
    ScrapeOptions,
    derive_filename,
    extract_links,
    rebuild_csv_from_cache,
    normalize_media_filter_tokens,
)


def test_extract_links_parses_permalinks():
    listing_json = {
        "data": {
            "children": [
                {
                    "data": {
                        "id": "abc123",
                        "title": "Hello World",
                        "permalink": "r/test/comments/abc123/hello_world",
                        "created_utc": 1700000000,
                    }
                }
            ]
        }
    }

    links = extract_links(listing_json)

    assert len(links) == 1
    link = links[0]
    assert link["rank"] == 1
    assert link["url"].startswith(BASE_URL)
    assert link["url"].endswith(".json")


def test_derive_filename_includes_rank_and_slug():
    link_info = {
        "rank": 5,
        "id": "xy1",
        "title": "A neat post",
        "created_utc": 1700000000,
        "permalink": "/r/test/comments/xy1/a_neat_post/",
    }
    filename = derive_filename(link_info, post_data=None)

    assert filename.startswith("005_")
    assert filename.endswith(".json")
    assert "A_neat_post" in filename


def test_build_search_target_scopes_and_normalizes_parameters():
    target = build_search_target(
        "python scraping",
        search_types=["post", "comment"],
        sort="top",
        time_filter="day",
        subreddit="learnpython",
        restrict_to_subreddit=True,
        include_over_18=True,
        limit=80,
        after="t3_example",
    )

    assert target.url == f"{BASE_URL}/r/learnpython/search.json"
    assert target.context == "r/learnpython"
    assert target.output_segments[0] == "search"

    params = target.params
    assert params["q"] == "python scraping"
    assert params["type"] == "link,comment"
    assert params["sort"] == "top"
    assert params["t"] == "day"
    assert params["limit"] == 80
    assert params["restrict_sr"] == "on"
    assert params["include_over_18"] == "on"
    assert params["after"] == "t3_example"


def test_build_search_target_rejects_invalid_type():
    with pytest.raises(ValueError):
        build_search_target("python", search_types=["invalid"])


def test_build_search_target_supports_all_type_keyword():
    target = build_search_target("asyncio", search_types=["all"], sort="hot")

    assert target.url == f"{BASE_URL}/search.json"
    assert "type" not in target.params
    assert target.params["sort"] == "hot"


def test_rebuild_csv_from_cache(tmp_path: Path):
    subreddit = "example"
    target = ListingTarget(
        label="r/example top (day)",
        output_segments=("subreddits", "example", "top_day"),
        url=f"{BASE_URL}/r/{subreddit}/top/.json",
        params={"t": "day"},
        context=subreddit,
    )
    base_dir = target.output_dir(tmp_path)
    posts_dir = base_dir / "post_jsons"
    posts_dir.mkdir(parents=True)

    listing_entry = {
        "kind": "Listing",
        "data": {
            "children": [
                {
                    "data": {
                        "id": "pq1",
                        "title": "Post title",
                        "created_utc": 1700000500,
                        "permalink": "/r/example/comments/pq1/post_title/",
                    }
                }
            ]
        },
    }
    comments_entry = {
        "kind": "Listing",
        "data": {
            "children": [
                {
                    "kind": "t1",
                    "data": {
                        "id": "c1",
                        "parent_id": "t3_pq1",
                        "author": "foo",
                        "body": "Nice",
                        "score": 1,
                        "created_utc": 1700000600,
                        "permalink": "/r/example/comments/pq1/post_title/c1/",
                    },
                }
            ]
        },
    }
    post_json_path = posts_dir / "001_post.json"
    post_json_path.write_text(json.dumps([listing_entry, comments_entry]), encoding="utf-8")

    links_path = base_dir / "links.json"
    links_path.write_text(
        json.dumps(
            [
                {
                    "rank": 1,
                    "id": "pq1",
                    "title": "Post title",
                    "created_utc": 1700000500,
                    "permalink": "/r/example/comments/pq1/post_title/",
                    "url": f"{BASE_URL}/r/example/comments/pq1/post_title/.json",
                }
            ]
        ),
        encoding="utf-8",
    )

    rebuild_csv_from_cache(target, tmp_path)

    posts_csv = (base_dir / "posts.csv").read_text(encoding="utf-8")
    comments_csv = (base_dir / "comments.csv").read_text(encoding="utf-8")

    assert "Post title" in posts_csv
    assert "c1" in comments_csv


def test_scrape_options_enforces_bounds(tmp_path: Path):
    options = ScrapeOptions(
        output_root=tmp_path,
        listing_limit=1000,
        comment_limit=9999,
        delay=0.1,
        time_filter="day",
        output_formats={"json"},
    )

    assert options.listing_limit == 1000
    assert options.comment_limit == 500
    assert options.delay >= 1.0
    assert options.listing_page_size == 100

    unlimited = ScrapeOptions(
        output_root=tmp_path,
        listing_limit=0,
        comment_limit=250,
        delay=2.0,
        time_filter="day",
        output_formats={"json"},
    )

    assert unlimited.listing_limit is None
    assert unlimited.comment_limit == 250

    zero_comment = ScrapeOptions(
        output_root=tmp_path,
        listing_limit=10,
        comment_limit=0,
        delay=2.0,
        time_filter="day",
        output_formats={"json"},
    )

    assert zero_comment.comment_limit == 500


def test_process_listing_resume_skips_fetch(tmp_path: Path, monkeypatch) -> None:
    subreddit = "example"
    target = ListingTarget(
        label="r/example top (day)",
        output_segments=("subreddits", "example", "top_day"),
        url=f"{BASE_URL}/r/{subreddit}/top/.json",
        params={"t": "day"},
        context=subreddit,
    )

    base_dir = target.output_dir(tmp_path)
    posts_dir = base_dir / "post_jsons"
    posts_dir.mkdir(parents=True)

    listing_json = {
        "data": {
            "children": [
                {
                    "data": {
                        "id": "pq1",
                        "title": "Post title",
                        "created_utc": 1700000500,
                        "permalink": "/r/example/comments/pq1/post_title/",
                    }
                }
            ],
            "after": None,
        }
    }

    post_json = [
        {
            "data": {
                "children": [
                    {
                        "data": {
                            "id": "pq1",
                            "title": "Post title",
                            "created_utc": 1700000500,
                            "permalink": "/r/example/comments/pq1/post_title/",
                        }
                    }
                ]
            }
        },
        {
            "data": {"children": []}
        },
    ]

    cached_path = posts_dir / "001_pq1_cached.json"
    cached_path.write_text(json.dumps(post_json), encoding="utf-8")

    responses = [listing_json]

    def fake_fetch_json(session, url, *, params=None, retries=3, backoff=1.0):  # noqa: D401
        assert responses, "Unexpected additional fetch call"
        return responses.pop(0)

    monkeypatch.setattr(core, "fetch_json", fake_fetch_json)

    sleeps: list[float] = []
    monkeypatch.setattr(core.time, "sleep", lambda seconds: sleeps.append(seconds))

    options = ScrapeOptions(
        output_root=tmp_path,
        listing_limit=10,
        comment_limit=250,
        delay=1.0,
        time_filter="day",
        output_formats={"json"},
        fetch_comments=True,
        resume=True,
    )

    core.process_listing(target, session=object(), options=options)

    assert responses == []
    # Only the post delay should run (no rate limit waits, no fetch delays)
    assert sleeps == []


def test_process_listing_rate_limit_retries(tmp_path: Path, monkeypatch) -> None:
    subreddit = "example"
    target = ListingTarget(
        label="r/example hot",
        output_segments=("subreddits", "example", "hot"),
        url=f"{BASE_URL}/r/{subreddit}/hot/.json",
        context=subreddit,
    )

    base_dir = target.output_dir(tmp_path)
    (base_dir / "post_jsons").mkdir(parents=True)

    listing_json = {
        "data": {
            "children": [
                {
                    "data": {
                        "id": "pq1",
                        "title": "Post title",
                        "created_utc": 1700000500,
                        "permalink": "/r/example/comments/pq1/post_title/",
                    }
                }
            ],
            "after": None,
        }
    }

    post_json = [
        {
            "data": {
                "children": [
                    {
                        "data": {
                            "id": "pq1",
                            "title": "Post title",
                            "created_utc": 1700000500,
                            "permalink": "/r/example/comments/pq1/post_title/",
                        }
                    }
                ]
            }
        },
        {
            "data": {"children": []}
        },
    ]

    call_state = {"count": 0}

    def fake_fetch_json(session, url, *, params=None, retries=3, backoff=1.0):  # noqa: D401
        if "hot" in url and call_state["count"] == 0:
            call_state["count"] += 1
            return listing_json
        # Subsequent calls are for the post JSON
        post_call = call_state.setdefault("post_calls", 0)
        if post_call == 0:
            call_state["post_calls"] = 1
            raise RuntimeError("HTTP 429 Too Many Requests")
        if post_call == 1:
            call_state["post_calls"] = 2
            raise RuntimeError("429 second wave")
        return post_json

    monkeypatch.setattr(core, "fetch_json", fake_fetch_json)

    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(core.time, "sleep", fake_sleep)

    options = ScrapeOptions(
        output_root=tmp_path,
        listing_limit=10,
        comment_limit=250,
        delay=0.1,
        time_filter="day",
        output_formats={"json"},
        fetch_comments=True,
    )

    core.process_listing(target, session=object(), options=options)

    # First rate limit should wait 60s, second should wait 120s before succeeding
    assert sleeps[:2] == [60, 120]


def test_collect_media_urls_pulls_multiple_sources() -> None:
    link_info = {
        "content_url": "https://www.reddit.com/r/example/comments/abc123/post/.json",
    }
    child_data = {
        "url_overridden_by_dest": "https://i.redd.it/example.png",
        "preview": {
            "images": [
                {
                    "source": {"url": "https://preview.redd.it/image.jpg"},
                    "variants": {
                        "gif": {"source": {"url": "https://preview.redd.it/image.gif"}},
                        "mp4": {"source": {"url": "https://preview.redd.it/image.mp4"}},
                    },
                }
            ]
        },
        "media_metadata": {
            "abc": {
                "s": {"u": "https://i.redd.it/gallery1.jpg"},
            }
        },
        "gallery_data": {
            "items": [
                {"media_id": "abc"},
            ]
        },
    }
    post_data = {
        "secure_media": {
            "reddit_video": {
                "fallback_url": "https://v.redd.it/video.mp4",
            }
        }
    }

    urls = core._collect_media_urls(link_info, child_data, post_data)

    assert "https://i.redd.it/example.png" in urls
    assert len(urls) == 4
    assert "https://i.redd.it/example.png" in urls
    assert "https://preview.redd.it/image.mp4" in urls
    assert "https://i.redd.it/gallery1.jpg" in urls
    assert "https://v.redd.it/video.mp4" in urls
    assert all("poster.jpg" not in url for url in urls)


def test_download_media_items_saves_files(tmp_path: Path) -> None:
    class FakeResponse:
        def __init__(self, *, headers: dict[str, str], payload: bytes, status_code: int = 200) -> None:
            self.headers = headers
            self._payload = payload
            self.status_code = status_code
            self.closed = False

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise requests.exceptions.HTTPError(str(self.status_code))

        def iter_content(self, chunk_size: int = 8192):  # noqa: D401 - generator helper
            yield self._payload

        def close(self) -> None:  # pragma: no cover - defensive
            self.closed = True

    class FakeSession:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, url: str, *, stream: bool, timeout: int):  # noqa: D401 - signature matches requests
            self.calls.append(url)
            if url.endswith("example.png"):
                return FakeResponse(headers={"Content-Type": "image/png"}, payload=b"png-bytes")
            return FakeResponse(headers={"Content-Type": "video/mp4"}, payload=b"mp4-bytes")

    session = FakeSession()
    urls = [
        "https://i.redd.it/example.png",
        "https://v.redd.it/example",
    ]

    saved = core._download_media_items(
        session,
        urls,
        media_dir=tmp_path,
        base_name="post",
        downloaded_urls=set(),
        resume=False,
        scope="posts",
    )

    assert saved == 2
    png_files = list((tmp_path / "posts" / "png").glob("*.png"))
    mp4_files = list((tmp_path / "posts" / "mp4").glob("*.mp4"))
    assert len(png_files) == 1
    assert len(mp4_files) == 1
    assert png_files[0].name.startswith("post_media01")
    assert mp4_files[0].name.startswith("post_media02")
    assert session.calls == urls


def test_collect_media_urls_skips_poster_thumbnails() -> None:
    link_info = {"content_url": "https://media.redgifs.com/example-poster.jpg"}
    urls = core._collect_media_urls(link_info, None, None)

    assert urls == []


def test_collect_media_urls_converts_gifv_to_mp4() -> None:
    gifv_url = "https://i.imgur.com/Example.gifv"
    link_info = {"content_url": gifv_url}
    child_data = {"url": gifv_url}

    urls = core._collect_media_urls(link_info, child_data, None)

    assert urls == ["https://i.imgur.com/Example.mp4"]


def test_collect_media_urls_prefers_video_over_duplicate_gif() -> None:
    link_info = {"content_url": "https://i.imgur.com/demo.gif"}
    child_data = {
        "url_overridden_by_dest": "https://i.imgur.com/demo.gifv",
        "preview": {
            "images": [
                {
                    "variants": {
                        "mp4": {"source": {"url": "https://i.imgur.com/demo.mp4"}},
                        "gif": {"source": {"url": "https://i.imgur.com/demo.gif"}},
                    }
                }
            ]
        },
    }

    urls = core._collect_media_urls(link_info, child_data, None)

    assert urls == ["https://i.imgur.com/demo.mp4"]


def test_normalize_media_filter_tokens_accepts_categories_and_extensions() -> None:
    tokens = normalize_media_filter_tokens(["video", "mp4", "gif"])
    assert tokens == {"video", ".mp4", ".gif"}


def test_normalize_media_filter_tokens_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        normalize_media_filter_tokens(["unknown"])


def test_download_media_items_respects_manifest_on_resume(tmp_path: Path) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, url: str, *, stream: bool, timeout: int):  # noqa: D401 - signature compatibility
            self.calls.append(url)
            raise AssertionError("Download should be skipped when manifest already records URL")

    manifest_path = tmp_path / "media_manifest.json"
    manifest = {"https://i.redd.it/example.png": "posts/png/post_media01.png"}

    saved = core._download_media_items(
        FakeSession(),
        ["https://i.redd.it/example.png"],
        media_dir=tmp_path,
        base_name="post",
        downloaded_urls=set(),
        resume=True,
        manifest=manifest,
        manifest_path=manifest_path,
    scope="posts",
    )

    assert saved == 0
    assert not manifest_path.exists()


def test_download_media_items_applies_filters(tmp_path: Path) -> None:
    class FakeResponse:
        def __init__(self, *, headers: dict[str, str], payload: bytes) -> None:
            self.headers = headers
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int = 8192):  # noqa: D401
            yield self._payload

        def close(self) -> None:  # pragma: no cover - interface compliance
            return None

    class FakeSession:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, url: str, *, stream: bool, timeout: int):  # noqa: D401
            self.calls.append(url)
            if url.endswith(".mp4"):
                return FakeResponse(headers={"Content-Type": "video/mp4"}, payload=b"mp4")
            if url.endswith(".jpg"):
                return FakeResponse(headers={"Content-Type": "image/jpeg"}, payload=b"jpg")
            raise AssertionError(f"Unexpected URL {url}")

    session = FakeSession()
    urls = [
        "https://i.redd.it/example.mp4",
        "https://i.redd.it/example.jpg",
    ]

    saved = core._download_media_items(
        session,
        urls,
        media_dir=tmp_path,
        base_name="post",
        downloaded_urls=set(),
        resume=False,
        allowed_filters={".mp4"},
        scope="posts",
    )

    assert saved == 1
    assert session.calls == ["https://i.redd.it/example.mp4"]
    files = list((tmp_path / "posts" / "mp4").glob("*.mp4"))
    assert len(files) == 1


class _FakeMediaResponse:
    """Minimal stand-in for a streamed ``requests`` response."""

    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        payload: bytes = b"bytes",
        status_code: int = 200,
    ) -> None:
        self.headers = headers or {}
        self._payload = payload
        self.status_code = status_code
        self.closed = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} Client Error", response=self)

    def iter_content(self, chunk_size: int = 8192):  # noqa: D401 - generator helper
        yield self._payload

    def close(self) -> None:  # pragma: no cover - interface compliance
        self.closed = True


class _FakeMediaSession:
    """Local, network-free session that downloads ``<id>`` URLs."""

    def __init__(self, routes: dict[str, Any], *, fail_markers: tuple[str, ...] = ()) -> None:
        self.routes = routes
        self.fail_markers = fail_markers
        self.calls: list[str] = []

    def get(self, url: str, *, stream: bool, timeout: int):  # noqa: D401
        self.calls.append(url)
        for marker in self.fail_markers:
            if marker in url:
                raise requests.exceptions.ConnectionError("simulated network failure")
        for marker, factory in self.routes.items():
            if marker in url:
                return factory()
        raise AssertionError(f"Unexpected URL {url}")


def test_download_media_items_reports_success(tmp_path: Path) -> None:
    routes = {
        "example.png": lambda: _FakeMediaResponse(headers={"Content-Type": "image/png"}, payload=b"png"),
        "example.mp4": lambda: _FakeMediaResponse(headers={"Content-Type": "video/mp4"}, payload=b"mp4"),
    }
    session = _FakeMediaSession(routes)
    summary = core.MediaDownloadSummary()
    manifest_path = tmp_path / "media_manifest.json"

    returned = core._download_media_items(
        session,
        ["https://i.redd.it/example.png", "https://i.redd.it/example.mp4"],
        media_dir=tmp_path,
        base_name="post",
        downloaded_urls=set(),
        resume=False,
        manifest={},
        manifest_path=manifest_path,
        scope="posts",
        summary=summary,
    )

    assert returned == 2
    data = summary.as_dict()
    assert data["outcome"] == "success"
    assert data["url_count"] == 2
    assert data["saved"] == 2
    assert data["filtered"] == 0
    assert data["failed"] == 0
    assert data["failures"] == []
    assert data["manifest_updated"] is True
    assert manifest_path.exists()


def test_download_media_items_reports_filtered_urls(tmp_path: Path) -> None:
    routes = {".mp4": lambda: _FakeMediaResponse(headers={"Content-Type": "video/mp4"})}
    session = _FakeMediaSession(routes)
    summary = core.MediaDownloadSummary()

    returned = core._download_media_items(
        session,
        ["https://i.redd.it/example.mp4", "https://i.redd.it/example.jpg"],
        media_dir=tmp_path,
        base_name="post",
        downloaded_urls=set(),
        resume=False,
        allowed_filters={".mp4"},
        scope="posts",
        summary=summary,
    )

    assert returned == 1
    data = summary.as_dict()
    assert data["outcome"] == "success"
    assert data["saved"] == 1
    assert data["filtered"] == 1
    assert data["failures"] == []
    assert data["filtered_urls"] == ["https://i.redd.it/example.jpg"]
    assert session.calls == ["https://i.redd.it/example.mp4"]


def test_download_media_items_continues_after_single_failure(tmp_path: Path) -> None:
    routes = {
        "good.png": lambda: _FakeMediaResponse(headers={"Content-Type": "image/png"}, payload=b"png"),
        "good.jpg": lambda: _FakeMediaResponse(headers={"Content-Type": "image/jpeg"}, payload=b"jpg"),
    }
    session = _FakeMediaSession(routes, fail_markers=("bad.jpg",))
    summary = core.MediaDownloadSummary()
    manifest_path = tmp_path / "media_manifest.json"

    returned = core._download_media_items(
        session,
        [
            "https://i.redd.it/good.png",
            "https://i.redd.it/bad.jpg",
            "https://i.redd.it/good.jpg",
        ],
        media_dir=tmp_path,
        base_name="post",
        downloaded_urls=set(),
        resume=False,
        manifest={},
        manifest_path=manifest_path,
        scope="posts",
        summary=summary,
    )

    assert returned == 2
    data = summary.as_dict()
    assert data["outcome"] == "partial_failure"
    assert data["url_count"] == 3
    assert data["saved"] == 2
    assert data["failed"] == 1
    assert [entry["url"] for entry in data["failures"]] == ["https://i.redd.it/bad.jpg"]
    assert "simulated network failure" in data["failures"][0]["reason"]

    # The success that followed the failure still landed on disk, and the
    # failed URL was not recorded in the manifest.
    png_files = list((tmp_path / "posts" / "png").glob("*.png"))
    jpg_files = list((tmp_path / "posts" / "jpg").glob("*.jpg"))
    assert len(png_files) == 1
    assert len(jpg_files) == 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "https://i.redd.it/bad.jpg" not in manifest
    assert "https://i.redd.it/good.jpg" in manifest


def test_download_media_items_reports_all_failed(tmp_path: Path) -> None:
    session = _FakeMediaSession(
        {},
        fail_markers=("broken.png", "broken.mp4"),
    )
    summary = core.MediaDownloadSummary()

    returned = core._download_media_items(
        session,
        ["https://i.redd.it/broken.png", "https://i.redd.it/broken.mp4"],
        media_dir=tmp_path,
        base_name="post",
        downloaded_urls=set(),
        resume=False,
        scope="posts",
        summary=summary,
    )

    assert returned == 0
    data = summary.as_dict()
    assert data["outcome"] == "all_failed"
    assert data["failed"] == 2
    assert data["saved"] == 0
    assert len(data["failures"]) == 2


def test_download_media_items_reports_no_media(tmp_path: Path) -> None:
    summary = core.MediaDownloadSummary()

    returned = core._download_media_items(
        _FakeMediaSession({}),
        [],
        media_dir=tmp_path,
        base_name="post",
        downloaded_urls=set(),
        resume=False,
        scope="posts",
        summary=summary,
    )

    assert returned == 0
    data = summary.as_dict()
    assert data["outcome"] == "no_media"
    assert data["url_count"] == 0
    assert data["failed"] == 0


def test_download_media_items_reports_resume_skip(tmp_path: Path) -> None:
    session = _FakeMediaSession({}, fail_markers=("already.png", "already.gif"))
    manifest = {
        "https://i.redd.it/already.png": "posts/png/post_media01.png",
        "https://i.redd.it/already.gif": "posts/gif/post_media02.gif",
    }
    summary = core.MediaDownloadSummary()
    manifest_path = tmp_path / "media_manifest.json"

    returned = core._download_media_items(
        session,
        ["https://i.redd.it/already.png", "https://i.redd.it/already.gif"],
        media_dir=tmp_path,
        base_name="post",
        downloaded_urls=set(),
        resume=True,
        manifest=manifest,
        manifest_path=manifest_path,
        scope="posts",
        summary=summary,
    )

    assert returned == 0
    data = summary.as_dict()
    assert data["outcome"] == "already_downloaded"
    assert data["resume_skipped"] == 2
    assert data["failed"] == 0
    assert data["failures"] == []
    assert session.calls == []
    assert not manifest_path.exists()


def test_process_listing_writes_media_report_and_logs_counts(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    target = ListingTarget(
        label="r/example hot",
        output_segments=("subreddits", "example", "hot"),
        url=f"{BASE_URL}/r/example/hot/.json",
        context="example",
    )

    listing_json = {
        "data": {
            "children": [
                {
                    "data": {
                        "id": "pq1",
                        "title": "Post title",
                        "created_utc": 1700000500,
                        "permalink": "/r/example/comments/pq1/post_title/",
                        "url_overridden_by_dest": "https://i.redd.it/good.png",
                    }
                },
                {
                    "data": {
                        "id": "pq2",
                        "title": "Second post",
                        "created_utc": 1700000600,
                        "permalink": "/r/example/comments/pq2/second_post/",
                        "url_overridden_by_dest": "https://i.redd.it/broken.jpg",
                    }
                },
            ],
            "after": None,
        }
    }

    monkeypatch.setattr(core, "fetch_json", lambda *a, **k: listing_json)
    monkeypatch.setattr(core.time, "sleep", lambda seconds: None)

    routes = {"good.png": lambda: _FakeMediaResponse(headers={"Content-Type": "image/png"}, payload=b"png")}
    session = _FakeMediaSession(routes, fail_markers=("broken.jpg",))

    options = ScrapeOptions(
        output_root=tmp_path,
        listing_limit=10,
        comment_limit=250,
        delay=1.0,
        time_filter="day",
        output_formats={"json"},
        download_media=True,
    )

    with caplog.at_level("INFO", logger=core.logger.name):
        core.process_listing(target, session=session, options=options)

    base_dir = target.output_dir(tmp_path)
    report_path = base_dir / core.MEDIA_REPORT_FILENAME
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["outcome"] == "partial_failure"
    assert report["url_count"] == 2
    assert report["saved"] == 1
    assert report["filtered"] == 0
    assert report["failed"] == 1
    assert report["failures"][0]["url"] == "https://i.redd.it/broken.jpg"
    assert len(report["posts"]) == 2
    post_ids = {entry["post_id"] for entry in report["posts"]}
    assert post_ids == {"pq1", "pq2"}

    completion = [record.message for record in caplog.records if "Completed r/example hot" in record.message]
    assert completion, "completion log line missing"
    assert "media_saved=1" in completion[-1]
    assert "media_failed=1" in completion[-1]
    assert "media_outcome=partial_failure" in completion[-1]

    # A successful sibling file survives the failure.
    png_files = list((base_dir / "media" / "posts" / "png").glob("*.png"))
    assert len(png_files) == 1


def test_process_listing_media_report_distinguishes_no_media(
    tmp_path: Path, monkeypatch
) -> None:
    target = ListingTarget(
        label="r/example new",
        output_segments=("subreddits", "example", "new"),
        url=f"{BASE_URL}/r/example/new/.json",
        context="example",
    )

    listing_json = {
        "data": {
            "children": [
                {
                    "data": {
                        "id": "txt1",
                        "title": "Text only",
                        "created_utc": 1700000700,
                        "permalink": "/r/example/comments/txt1/text_only/",
                        "url_overridden_by_dest": "https://www.reddit.com/r/example/comments/txt1/text_only/",
                    }
                }
            ],
            "after": None,
        }
    }

    monkeypatch.setattr(core, "fetch_json", lambda *a, **k: listing_json)
    monkeypatch.setattr(core.time, "sleep", lambda seconds: None)

    options = ScrapeOptions(
        output_root=tmp_path,
        listing_limit=10,
        comment_limit=250,
        delay=1.0,
        time_filter="day",
        output_formats={"json"},
        download_media=True,
    )

    core.process_listing(target, session=_FakeMediaSession({}), options=options)

    report = json.loads(
        (target.output_dir(tmp_path) / core.MEDIA_REPORT_FILENAME).read_text(encoding="utf-8")
    )
    assert report["outcome"] == "no_media"
    assert report["url_count"] == 0
    assert report["failed"] == 0


def test_process_post_writes_media_report(tmp_path: Path, monkeypatch) -> None:
    target = core.PostTarget(
        label="post r/example",
        output_segments=("posts", "example", "pq1"),
        url=f"{BASE_URL}/r/example/comments/pq1/.json",
        context="r/example",
    )

    post_json = [
        {
            "data": {
                "children": [
                    {
                        "data": {
                            "id": "pq1",
                            "title": "Media post",
                            "created_utc": 1700000500,
                            "permalink": "/r/example/comments/pq1/media_post/",
                            "url_overridden_by_dest": "https://i.redd.it/ok.png",
                        }
                    }
                ]
            }
        },
        {"data": {"children": []}},
    ]

    return_values: list[Any] = [post_json]

    def fake_fetch_json(session, url, *, params=None, retries=3, backoff=1.0):
        return return_values.pop(0)

    monkeypatch.setattr(core, "fetch_json", fake_fetch_json)

    session = _FakeMediaSession(
        {"ok.png": lambda: _FakeMediaResponse(headers={"Content-Type": "image/png"}, payload=b"png")},
        fail_markers=("nope.jpg",),
    )
    # Add a second media URL to the post so one succeeds and one fails.
    post_json[0]["data"]["children"][0]["data"]["preview"] = {
        "images": [{"source": {"url": "https://i.redd.it/nope.jpg"}}]
    }

    options = ScrapeOptions(
        output_root=tmp_path,
        listing_limit=None,
        comment_limit=250,
        delay=1.0,
        time_filter="day",
        output_formats={"json"},
        download_media=True,
    )

    core.process_post(target, session=session, options=options)

    report = json.loads(
        (target.output_dir(tmp_path) / core.MEDIA_REPORT_FILENAME).read_text(encoding="utf-8")
    )
    assert report["outcome"] == "partial_failure"
    assert report["saved"] == 1
    assert report["failed"] == 1
    assert report["failures"][0]["url"] == "https://i.redd.it/nope.jpg"
    assert report["posts"][0]["post_id"] == "pq1"


def test_process_listing_resume_counts_manifest_hits_as_skips(
    tmp_path: Path, monkeypatch
) -> None:
    target = ListingTarget(
        label="r/example top",
        output_segments=("subreddits", "example", "top"),
        url=f"{BASE_URL}/r/example/top/.json",
        context="example",
    )
    base_dir = target.output_dir(tmp_path)
    base_dir.mkdir(parents=True)
    manifest_path = base_dir / "media_manifest.json"
    manifest_path.write_text(
        json.dumps({"https://i.redd.it/already.png": "posts/png/pq1_media.png"}),
        encoding="utf-8",
    )

    listing_json = {
        "data": {
            "children": [
                {
                    "data": {
                        "id": "pq1",
                        "title": "Already downloaded",
                        "created_utc": 1700000500,
                        "permalink": "/r/example/comments/pq1/already/",
                        "url_overridden_by_dest": "https://i.redd.it/already.png",
                    }
                },
                {
                    "data": {
                        "id": "pq2",
                        "title": "Fresh post",
                        "created_utc": 1700000600,
                        "permalink": "/r/example/comments/pq2/fresh/",
                        "url_overridden_by_dest": "https://i.redd.it/fresh.jpg",
                    }
                },
            ],
            "after": None,
        }
    }

    monkeypatch.setattr(core, "fetch_json", lambda *a, **k: listing_json)
    monkeypatch.setattr(core.time, "sleep", lambda seconds: None)

    session = _FakeMediaSession(
        {"fresh.jpg": lambda: _FakeMediaResponse(headers={"Content-Type": "image/jpeg"})}
    )

    options = ScrapeOptions(
        output_root=tmp_path,
        listing_limit=10,
        comment_limit=250,
        delay=1.0,
        time_filter="day",
        output_formats={"json"},
        download_media=True,
        resume=True,
    )

    core.process_listing(target, session=session, options=options)

    report = json.loads(
        (base_dir / core.MEDIA_REPORT_FILENAME).read_text(encoding="utf-8")
    )
    assert report["resume_skipped"] == 1
    assert report["saved"] == 1
    assert report["failed"] == 0
    assert report["outcome"] == "success"
    assert session.calls == ["https://i.redd.it/fresh.jpg"]
    # The pre-existing manifest entry survives and the new one is added.
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["https://i.redd.it/already.png"] == "posts/png/pq1_media.png"
    assert "https://i.redd.it/fresh.jpg" in manifest
