"""Tests for batch collection partial-failure handling and BatchResult"""

from unittest.mock import patch

import pytest
import requests
import responses

from crawl4weibo import Comment, Post
from crawl4weibo.exceptions.base import NetworkError, ParseError
from crawl4weibo.models.batch import BatchResult, PageFailureReason


def _make_post(post_id: str) -> Post:
    return Post.from_dict({"id": post_id, "bid": f"bid{post_id}", "user_id": "1"})


def _make_comment(comment_id: str) -> Comment:
    return Comment.from_dict({"id": comment_id, "text": f"comment {comment_id}"})


@pytest.mark.unit
class TestBatchPartialFailure:
    """Partial page failures must not discard already collected items"""

    def test_search_all_posts_preserves_items_across_failed_page(
        self, client_no_rate_limit
    ):
        """A failed middle page is recorded; later pages still collected"""
        client = client_no_rate_limit
        pages = {
            1: ([_make_post("1")], {"page": 2, "has_more": True}),
            3: ([_make_post("3")], {"page": None, "has_more": False}),
        }

        def fake_search_posts(query, page=1, **kwargs):
            if page == 2:
                raise NetworkError(
                    "Encountered 432 anti-crawler block", code="BLOCKED_432"
                )
            return pages[page]

        with patch.object(client, "search_posts", side_effect=fake_search_posts):
            result = client.search_all_posts_with_result("Python")

        assert [post.id for post in result.items] == ["1", "3"]
        assert result.success_pages == 2
        assert result.failed_page_count == 1
        assert result.failed_pages[0].page == 2
        assert result.failed_pages[0].reason == PageFailureReason.BLOCKED_432
        assert result.stop_reason == "exhausted"
        assert result.finished_normally is True

    def test_get_all_comments_preserves_items_across_failed_page(
        self, client_no_rate_limit
    ):
        """Comment batch keeps pages fetched before and after a failure"""
        client = client_no_rate_limit
        pages = {
            1: ([_make_comment("1")], {"total_number": 30, "max": 3}),
            3: ([_make_comment("3")], {"total_number": 30, "max": 3}),
        }

        def fake_get_comments(post_id, page=1, **kwargs):
            if page == 2:
                raise NetworkError("Request failed: timed out", code="TIMEOUT")
            return pages[page]

        with patch.object(client, "get_comments", side_effect=fake_get_comments):
            result = client.get_all_comments_with_result("123456")

        assert [comment.id for comment in result.items] == ["1", "3"]
        assert result.success_pages == 2
        assert result.failed_page_count == 1
        assert result.failed_pages[0].page == 2
        assert result.failed_pages[0].reason == PageFailureReason.TIMEOUT
        assert result.stop_reason == "exhausted"

    def test_search_posts_by_count_returns_list_on_partial_failure(
        self, client_no_rate_limit
    ):
        """Backward-compatible list API also preserves collected items"""
        client = client_no_rate_limit
        pages = {
            1: ([_make_post("1")], {"page": 2, "has_more": True}),
            3: ([_make_post("3")], {"page": None, "has_more": False}),
        }

        def fake_search_posts(query, page=1, **kwargs):
            if page == 2:
                raise NetworkError(
                    "Encountered 432 anti-crawler block", code="BLOCKED_432"
                )
            return pages[page]

        with patch.object(client, "search_posts", side_effect=fake_search_posts):
            posts = client.search_posts_by_count("Python", count=10)

        assert isinstance(posts, list)
        assert [post.id for post in posts] == ["1", "3"]


@pytest.mark.unit
class TestPageFailureClassification:
    """Failed pages must be classified into distinguishable reasons"""

    def test_classifies_432_block(self, client_no_rate_limit):
        client = client_no_rate_limit

        def fake_get_comments(post_id, page=1, **kwargs):
            raise NetworkError("Encountered 432 anti-crawler block", code="BLOCKED_432")

        with patch.object(client, "get_comments", side_effect=fake_get_comments):
            result = client.get_all_comments_with_result("123456", max_pages=2)

        assert result.items == []
        assert result.failed_page_count == 2
        assert all(
            failure.reason == PageFailureReason.BLOCKED_432
            for failure in result.failed_pages
        )
        assert [failure.page for failure in result.failed_pages] == [1, 2]

    def test_classifies_timeout(self, client_no_rate_limit):
        client = client_no_rate_limit

        def fake_get_comments(post_id, page=1, **kwargs):
            raise NetworkError("Request failed: read timed out", code="TIMEOUT")

        with patch.object(client, "get_comments", side_effect=fake_get_comments):
            result = client.get_all_comments_with_result("123456", max_pages=1)

        assert result.failed_page_count == 1
        assert result.failed_pages[0].reason == PageFailureReason.TIMEOUT

    def test_classifies_empty_parse(self, client_no_rate_limit):
        """A successful request with unparseable/empty payload is empty_parse"""
        client = client_no_rate_limit

        with patch.object(client, "search_posts", return_value=([], {})):
            result = client.search_all_posts_with_result("Python", max_pages=2)

        assert result.items == []
        assert result.failed_page_count == 2
        assert all(
            failure.reason == PageFailureReason.EMPTY_PARSE
            for failure in result.failed_pages
        )

    def test_classifies_parse_error_as_empty_parse(self, client_no_rate_limit):
        client = client_no_rate_limit

        def fake_get_comments(post_id, page=1, **kwargs):
            raise ParseError("Failed to parse comments: boom")

        with patch.object(client, "get_comments", side_effect=fake_get_comments):
            result = client.get_all_comments_with_result("123456", max_pages=1)

        assert result.failed_pages[0].reason == PageFailureReason.EMPTY_PARSE

    def test_consecutive_failures_abort_unlimited_pagination(
        self, client_no_rate_limit
    ):
        """Unlimited pagination stops after consecutive failures"""
        client = client_no_rate_limit

        def fake_get_comments(post_id, page=1, **kwargs):
            raise NetworkError("Encountered 432 anti-crawler block", code="BLOCKED_432")

        with patch.object(client, "get_comments", side_effect=fake_get_comments):
            result = client.get_all_comments_with_result("123456")

        assert result.stop_reason == "max_consecutive_failures"
        assert result.finished_normally is False
        assert result.failed_page_count == 5
        assert [failure.page for failure in result.failed_pages] == [1, 2, 3, 4, 5]


@pytest.mark.unit
class TestBatchResultSuccess:
    """Fully successful batches report no failed pages"""

    def test_all_pages_successful_has_empty_failed_pages(self, client_no_rate_limit):
        client = client_no_rate_limit
        pages = {
            1: ([_make_post("1")], {"page": 2, "has_more": True}),
            2: ([_make_post("2")], {"page": None, "has_more": False}),
        }

        with patch.object(
            client, "search_posts", side_effect=lambda q, page=1, **kw: pages[page]
        ):
            result = client.search_all_posts_with_result("Python")

        assert isinstance(result, BatchResult)
        assert [post.id for post in result.items] == ["1", "2"]
        assert result.failed_pages == []
        assert result.has_failures is False
        assert result.success_pages == 2
        assert result.stop_reason == "exhausted"
        assert result.exhausted is True
        assert result.finished_normally is True

    def test_max_pages_stop_reason(self, client_no_rate_limit):
        client = client_no_rate_limit

        def fake_search_posts(query, page=1, **kwargs):
            return [_make_post(str(page))], {"page": page + 1, "has_more": True}

        with patch.object(client, "search_posts", side_effect=fake_search_posts):
            result = client.search_all_posts_with_result("Python", max_pages=2)

        assert len(result.items) == 2
        assert result.failed_pages == []
        assert result.stop_reason == "max_pages"
        assert result.reached_max_pages is True
        assert result.finished_normally is True

    def test_count_reached_stop_reason(self, client_no_rate_limit):
        client = client_no_rate_limit

        def fake_search_posts(query, page=1, **kwargs):
            return [_make_post("1"), _make_post("2")], {
                "page": page + 1,
                "has_more": True,
            }

        with patch.object(client, "search_posts", side_effect=fake_search_posts):
            result = client.search_posts_by_count_with_result("Python", count=2)

        assert len(result.items) == 2
        assert result.failed_pages == []
        assert result.stop_reason == "count_reached"
        assert result.finished_normally is True

    def test_get_all_comments_all_success(self, client_no_rate_limit):
        client = client_no_rate_limit
        pages = {
            1: ([_make_comment("1")], {"total_number": 2, "max": 2}),
            2: ([_make_comment("2")], {"total_number": 2, "max": 2}),
        }

        with patch.object(
            client, "get_comments", side_effect=lambda pid, page=1, **kw: pages[page]
        ):
            result = client.get_all_comments_with_result("123456")

        assert [comment.id for comment in result.items] == ["1", "2"]
        assert result.failed_pages == []
        assert result.success_pages == 2
        assert result.stop_reason == "exhausted"


@pytest.mark.unit
class TestRequestErrorClassification:
    """_request must tag raised NetworkErrors with classifiable codes"""

    @responses.activate
    def test_432_exhaustion_raises_blocked_432_code(self, client_no_rate_limit):
        weibo_api_url = "https://m.weibo.cn/api/container/getIndex"
        for _ in range(3):
            responses.add(responses.GET, weibo_api_url, status=432)

        with (
            patch("crawl4weibo.core.client.time.sleep"),
            pytest.raises(NetworkError) as exc_info,
        ):
            client_no_rate_limit._request(weibo_api_url, {})

        assert exc_info.value.code == "BLOCKED_432"

    @responses.activate
    def test_timeout_exhaustion_raises_timeout_code(self, client_no_rate_limit):
        weibo_api_url = "https://m.weibo.cn/api/container/getIndex"
        for _ in range(3):
            responses.add(
                responses.GET,
                weibo_api_url,
                body=requests.exceptions.ConnectTimeout("connect timed out"),
            )

        with (
            patch("crawl4weibo.core.client.time.sleep"),
            pytest.raises(NetworkError) as exc_info,
        ):
            client_no_rate_limit._request(weibo_api_url, {})

        assert exc_info.value.code == "TIMEOUT"

    @responses.activate
    def test_other_network_error_keeps_default_code(self, client_no_rate_limit):
        weibo_api_url = "https://m.weibo.cn/api/container/getIndex"
        for _ in range(3):
            responses.add(
                responses.GET,
                weibo_api_url,
                body=requests.exceptions.ConnectionError("connection reset"),
            )

        with (
            patch("crawl4weibo.core.client.time.sleep"),
            pytest.raises(NetworkError) as exc_info,
        ):
            client_no_rate_limit._request(weibo_api_url, {})

        assert exc_info.value.code == "NETWORK_ERROR"
