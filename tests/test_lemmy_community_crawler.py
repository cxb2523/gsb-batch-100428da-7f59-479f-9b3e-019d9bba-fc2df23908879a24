"""Tests for the failure handling of the Lemmy community crawler."""

import asyncio
import contextlib
import csv

from franck.common import CrawlerException
from franck.lemmy_crawler import LemmyCommunityCrawler

HOST = "community.example"
OTHER_USER_HOST = "user.example"
THIRD_USER_HOST = "third.example"


def _site_response():
    return {
        "version": "0.19.5",
        "site_view": {
            "counts": {
                "users": 10,
                "posts": 20,
                "comments": 30,
                "communities": 2,
                "users_active_day": 4,
                "users_active_week": 8,
                "users_active_month": 12,
                "users_active_half_year": 16,
            },
            "site": {"description": ""},
            "local_site": {"federation_enabled": True, "captcha_enabled": False},
        },
    }


def _community(name):
    return {
        "community": {"name": name},
        "counts": {
            "subscribers": 10,
            "posts": 5,
            "comments": 5,
            "users_active_day": 6,
            "users_active_week": 6,
            "users_active_month": 6,
        },
    }


def _post(username, user_host, post_id):
    return {
        "creator": {
            "actor_id": f"https://{user_host}/u/{username}",
            "name": username,
        },
        "post": {"ap_id": f"https://{HOST}/post/{post_id}"},
    }


class FakeLemmyAPI:
    """Fake ``_fetch_json`` serving the Lemmy endpoints used by the crawler.

    Args:
        communities: names returned by the community list endpoint.
        posts: mapping ``community name -> list of post dicts``.
        failing_post_communities: communities whose post list raises
            ``CrawlerException`` (only from ``fail_on_page`` when given).
        fail_on_page: page number from which the failing post list raises.
        community_list_error: if not ``None``, the community list endpoint
            raises ``CrawlerException`` with this message.
    """

    def __init__(
        self,
        communities,
        posts=None,
        failing_post_communities=None,
        fail_on_page=None,
        community_list_error=None,
    ):
        self.communities = communities
        self.posts = posts or {}
        self.failing_post_communities = set(failing_post_communities or [])
        self.fail_on_page = fail_on_page
        self.community_list_error = community_list_error
        self.calls = []

    async def __call__(self, url, params=None, body=None, op="GET"):
        params = params or {}
        self.calls.append((url, dict(params)))

        if url.endswith("/api/v3/site"):
            return _site_response()

        if url.endswith("/api/v3/community/list"):
            if self.community_list_error is not None:
                raise CrawlerException(self.community_list_error)
            return {"communities": [_community(name) for name in self.communities]}

        if url.endswith("/api/v3/post/list"):
            community = params["community_name"]
            if community in self.failing_post_communities:
                if self.fail_on_page is None or params["page"] >= self.fail_on_page:
                    raise CrawlerException(
                        f"Error code 500 on https://{HOST}/api/v3/post/list"
                    )
            return {"posts": self.posts.get(community, [])}

        raise AssertionError(f"Unexpected endpoint queried: {url}")


@contextlib.contextmanager
def make_crawler(tmp_path, monkeypatch):
    """Create a crawler writing its CSV files inside ``tmp_path``.

    The crawler (and its aiohttp session) is bound to a dedicated event loop,
    which the tests use to run the asynchronous methods.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    loop = asyncio.new_event_loop()

    async def create():
        instance = LemmyCommunityCrawler([HOST])
        instance.crawled_instances = {HOST, OTHER_USER_HOST, THIRD_USER_HOST}
        instance.DELAY_PER_HOST[HOST] = 0
        instance.init_all_files()
        return instance

    instance = loop.run_until_complete(create())
    instance._test_loop = loop
    try:
        yield instance
    finally:
        for _lock, csv_file, _writer in instance.csvs.values():
            csv_file.close()
        loop.run_until_complete(instance.session.close())
        loop.close()


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as csv_file:
        return list(csv.DictReader(csv_file))


def _flush_csvs(crawler):
    for _lock, csv_file, _writer in crawler.csvs.values():
        csv_file.flush()


def _instance_rows(crawler):
    _flush_csvs(crawler)
    return _read_csv(crawler.INSTANCES_CSV)


def _detailed_rows(crawler):
    _flush_csvs(crawler)
    return _read_csv(crawler.DETAILED_INTERACTIONS_CSV)


def _output_rows(crawler, csv_name):
    _flush_csvs(crawler)
    return _read_csv(csv_name)


def _crawl(crawler, fake_api):
    crawler._fetch_json = fake_api
    crawler._test_loop.run_until_complete(crawler.inspect_instance(HOST))


def _queried_communities(fake_api):
    return {
        params["community_name"]
        for url, params in fake_api.calls
        if url.endswith("/api/v3/post/list")
    }


def test_all_communities_succeed(tmp_path, monkeypatch):
    with make_crawler(tmp_path, monkeypatch) as crawler:
        fake_api = FakeLemmyAPI(
            communities=["news", "music"],
            posts={
                "news": [
                    _post("alice", OTHER_USER_HOST, 1),
                    _post("bob", THIRD_USER_HOST, 2),
                ],
                "music": [_post("carol", OTHER_USER_HOST, 3)],
            },
        )

        _crawl(crawler, fake_api)

        instance_rows = _instance_rows(crawler)
        assert len(instance_rows) == 1
        assert instance_rows[0]["host"] == HOST
        assert instance_rows[0]["error"] == ""

        detailed = _detailed_rows(crawler)
        assert {(row["community"], row["username"]) for row in detailed} == {
            (f"news@{HOST}", "alice"),
            (f"news@{HOST}", "bob"),
            (f"music@{HOST}", "carol"),
        }


def test_community_post_failure_does_not_stop_remaining_communities(
    tmp_path, monkeypatch
):
    with make_crawler(tmp_path, monkeypatch) as crawler:
        fake_api = FakeLemmyAPI(
            communities=["news", "broken", "music"],
            posts={
                "news": [_post("alice", OTHER_USER_HOST, 1)],
                "music": [_post("carol", OTHER_USER_HOST, 3)],
            },
            failing_post_communities={"broken"},
        )

        _crawl(crawler, fake_api)

        # The communities after the failing one were still crawled.
        detailed = _detailed_rows(crawler)
        assert {row["community"] for row in detailed} == {
            f"news@{HOST}",
            f"music@{HOST}",
        }
        assert _queried_communities(fake_api) == {"news", "broken", "music"}

        # The failure is reported in the instance error field, with the
        # community name and the number of communities successfully processed.
        instance_rows = _instance_rows(crawler)
        assert len(instance_rows) == 1
        error = instance_rows[0]["error"]
        assert "broken" in error
        assert "posts" in error
        assert "2 communities processed successfully" in error


def test_all_communities_failing_reports_zero_success(tmp_path, monkeypatch):
    with make_crawler(tmp_path, monkeypatch) as crawler:
        fake_api = FakeLemmyAPI(
            communities=["broken1", "broken2"],
            failing_post_communities={"broken1", "broken2"},
        )

        _crawl(crawler, fake_api)

        assert _detailed_rows(crawler) == []
        error = _instance_rows(crawler)[0]["error"]
        assert "broken1" in error
        assert "broken2" in error
        assert "0 communities processed successfully" in error


def test_community_post_failure_on_later_page_keeps_previous_pages(
    tmp_path, monkeypatch
):
    with make_crawler(tmp_path, monkeypatch) as crawler:
        # Page 1 is full (MAX_PAGE_SIZE posts), the failure happens on page 2.
        page_one = [
            _post(f"user{i}", OTHER_USER_HOST, i)
            for i in range(LemmyCommunityCrawler.MAX_PAGE_SIZE)
        ]
        fake_api = FakeLemmyAPI(
            communities=["broken", "music"],
            posts={
                "broken": page_one,
                "music": [_post("carol", OTHER_USER_HOST, 999)],
            },
            failing_post_communities={"broken"},
            fail_on_page=2,
        )

        _crawl(crawler, fake_api)

        detailed = _detailed_rows(crawler)
        assert (
            len([row for row in detailed if row["community"] == f"broken@{HOST}"])
            == LemmyCommunityCrawler.MAX_PAGE_SIZE
        )
        assert any(row["community"] == f"music@{HOST}" for row in detailed)

        error = _instance_rows(crawler)[0]["error"]
        assert "broken" in error
        assert "Page 2" in error
        assert "1 communities processed successfully" in error


def test_community_list_failure_gives_up_instance(tmp_path, monkeypatch):
    with make_crawler(tmp_path, monkeypatch) as crawler:
        fake_api = FakeLemmyAPI(
            communities=["news"],
            posts={"news": [_post("alice", OTHER_USER_HOST, 1)]},
            community_list_error="Error code 500 on community list",
        )

        _crawl(crawler, fake_api)

        # No post was fetched, but the instance row is written with an error
        # identifying the community list as the origin of the failure.
        assert not any(url.endswith("/api/v3/post/list") for url, _ in fake_api.calls)
        instance_rows = _instance_rows(crawler)
        assert len(instance_rows) == 1
        error = instance_rows[0]["error"]
        assert "community list" in error
        assert "0 communities processed successfully" in error

        assert _detailed_rows(crawler) == []


def test_community_list_failure_is_distinguishable_from_post_failure(
    tmp_path, monkeypatch
):
    errors = []
    for community_list_error in (None, "Error code 500 on community list"):
        with make_crawler(tmp_path / str(len(errors)), monkeypatch) as crawler:
            fake_api = FakeLemmyAPI(
                communities=["broken"],
                failing_post_communities={"broken"},
                community_list_error=community_list_error,
            )
            _crawl(crawler, fake_api)
            errors.append(_instance_rows(crawler)[0]["error"])

    post_failure_error, community_list_failure_error = errors
    assert post_failure_error != community_list_failure_error
    assert "Error while crawling the community list" in community_list_failure_error
    assert "Error while crawling the posts" in post_failure_error


def _write_csv(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _ownership_row(instance, community):
    return {
        "instance": instance,
        "community": community,
        "subscribers": 1,
        "posts": 1,
        "comments": 1,
        "users_active_day": 1,
        "users_active_week": 1,
        "users_active_month": 1,
    }


def _interaction_row(user_instance, community, username, post_id):
    return {
        "user_instance": user_instance,
        "community": community,
        "community_instance": community.split("@")[1],
        "username": username,
        "post_id": post_id,
    }


def test_postprocessing_skips_unknown_community(tmp_path, monkeypatch, caplog):
    with make_crawler(tmp_path, monkeypatch) as crawler:
        _write_csv(
            crawler.COMMUNITY_OWNERSHIP_CSV,
            crawler.COMMUNITY_OWNERSHIP_FIELDS,
            [
                _ownership_row("inst_a", "comm1"),
                _ownership_row("inst_a", "comm2"),
                _ownership_row("inst_b", "comm3"),
            ],
        )
        _write_csv(
            crawler.DETAILED_INTERACTIONS_CSV,
            crawler.DETAILED_INTERACTIONS_FIELDS,
            [
                # Known communities: activity and weights must be unaffected.
                _interaction_row("inst_a", "comm1@inst_a", "alice", "p1"),
                _interaction_row("inst_a", "comm1@inst_a", "bob", "p2"),
                _interaction_row("inst_b", "comm1@inst_a", "carol", "p3"),
                _interaction_row("inst_a", "comm2@inst_a", "alice", "p4"),
                _interaction_row("inst_b", "comm3@inst_b", "carol", "p5"),
                # Unknown community (failed crawl that wrote partial posts).
                _interaction_row("inst_a", "ghost@inst_a", "alice", "p6"),
                _interaction_row("inst_b", "ghost@inst_a", "carol", "p7"),
                # Unknown user instance, already ignored before this change.
                _interaction_row("inst_unknown", "comm1@inst_a", "dave", "p8"),
            ],
        )

        with caplog.at_level("DEBUG", logger=crawler.logger.name):
            crawler.data_postprocessing()

        assert "ghost@inst_a" in caplog.text
        assert "community unknown" in caplog.text

        activity_rows = {
            row["community"]: int(row["number_posts"])
            for row in _output_rows(crawler, crawler.COMMUNITY_ACTIVITY_CSV)
        }
        assert activity_rows == {
            "comm1@inst_a": 3,
            "comm2@inst_a": 1,
            "comm3@inst_b": 1,
        }

        intra_rows = {
            (row["Source"], row["Target"]): int(row["Weight"])
            for row in _output_rows(crawler, crawler.INTRA_INSTANCE_INTERACTIONS_CSV)
        }
        assert intra_rows == {
            ("inst_a", "inst_a"): 3,
            ("inst_b", "inst_a"): 1,
            ("inst_b", "inst_b"): 1,
        }

        cross_rows = {
            (row["Source"], row["Target"]): int(row["Weight"])
            for row in _output_rows(crawler, crawler.CROSS_INSTANCE_INTERACTIONS_CSV)
        }
        assert cross_rows == {
            ("inst_a", "inst_a"): 2,
            ("inst_a", "inst_b"): 1,
            ("inst_b", "inst_a"): 1,
            ("inst_b", "inst_b"): 2,
        }


def test_postprocessing_matches_graph_without_failed_community_posts(
    tmp_path, monkeypatch
):
    """The matrices are identical to a run where the failed community wrote nothing."""
    known_posts = [
        _interaction_row("inst_a", "comm1@inst_a", "alice", "p1"),
        _interaction_row("inst_b", "comm1@inst_a", "carol", "p2"),
        _interaction_row("inst_a", "comm2@inst_a", "bob", "p3"),
        _interaction_row("inst_b", "comm2@inst_a", "carol", "p4"),
    ]
    ghost_posts = [
        _interaction_row("inst_a", "ghost@inst_a", "alice", "p5"),
        _interaction_row("inst_b", "ghost@inst_a", "carol", "p6"),
    ]
    results = []
    for extra_posts in ([], ghost_posts):
        with make_crawler(tmp_path / str(len(results)), monkeypatch) as crawler:
            _write_csv(
                crawler.COMMUNITY_OWNERSHIP_CSV,
                crawler.COMMUNITY_OWNERSHIP_FIELDS,
                [
                    _ownership_row("inst_a", "comm1"),
                    _ownership_row("inst_a", "comm2"),
                ],
            )
            _write_csv(
                crawler.DETAILED_INTERACTIONS_CSV,
                crawler.DETAILED_INTERACTIONS_FIELDS,
                known_posts + extra_posts,
            )
            crawler.data_postprocessing()

            results.append(
                (
                    sorted(
                        (row["community"], row["number_posts"])
                        for row in _output_rows(crawler, crawler.COMMUNITY_ACTIVITY_CSV)
                    ),
                    sorted(
                        (row["Source"], row["Target"], row["Weight"])
                        for row in _output_rows(
                            crawler, crawler.INTRA_INSTANCE_INTERACTIONS_CSV
                        )
                    ),
                    sorted(
                        (row["Source"], row["Target"], row["Weight"])
                        for row in _output_rows(
                            crawler, crawler.CROSS_INSTANCE_INTERACTIONS_CSV
                        )
                    ),
                )
            )

    assert results[0] == results[1]
