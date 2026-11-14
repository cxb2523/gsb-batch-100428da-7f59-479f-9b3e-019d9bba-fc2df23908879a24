# -*- coding: utf-8 -*-
"""search.py 的错误分类、重试、空结果与缓存行为测试。

运行：python -m unittest test_search -v
"""

import unittest
from unittest import mock

import requests

import search
from search import DeepSeekSearch, SearchError


def make_response(status=200, payload=None, text=""):
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = payload if payload is not None else {}
    resp.text = text
    return resp


class PostErrorClassTests(unittest.TestCase):
    """_post：可重试 vs 不可重试错误的分类与退避重试。"""

    def setUp(self):
        self.search = DeepSeekSearch(base_url="http://api.test", api_key="k", timeout=1)
        sleep_patch = mock.patch.object(search.time, "sleep")
        self.sleep = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def test_429_retries_then_raises_retryable(self):
        self.search.session.post = mock.Mock(return_value=make_response(429))
        with self.assertRaises(SearchError) as ctx:
            self.search._post("/tools/search", {})
        self.assertTrue(ctx.exception.retryable)
        self.assertIn("429", str(ctx.exception))
        self.assertEqual(self.search.session.post.call_count, 3)  # 1 次 + 2 次重试
        self.assertEqual(self.sleep.call_count, 2)

    def test_5xx_retries_then_raises_retryable(self):
        for status in (500, 502, 503, 504, 408):
            self.search.session.post = mock.Mock(return_value=make_response(status))
            with self.assertRaises(SearchError) as ctx:
                self.search._post("/tools/search", {})
            self.assertTrue(ctx.exception.retryable, "HTTP %s 应当可重试" % status)
            self.assertEqual(self.search.session.post.call_count, 3)

    def test_4xx_raises_immediately_without_retry(self):
        self.search.session.post = mock.Mock(
            return_value=make_response(403, text="forbidden")
        )
        with self.assertRaises(SearchError) as ctx:
            self.search._post("/tools/search", {})
        self.assertFalse(ctx.exception.retryable)
        self.assertIn("403", str(ctx.exception))
        self.assertEqual(self.search.session.post.call_count, 1)
        self.sleep.assert_not_called()

    def test_timeout_is_retryable(self):
        self.search.session.post = mock.Mock(
            side_effect=requests.exceptions.Timeout("boom")
        )
        with self.assertRaises(SearchError) as ctx:
            self.search._post("/tools/search", {})
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(self.search.session.post.call_count, 3)

    def test_connection_error_is_retryable(self):
        self.search.session.post = mock.Mock(
            side_effect=requests.exceptions.ConnectionError("reset")
        )
        with self.assertRaises(SearchError) as ctx:
            self.search._post("/tools/search", {})
        self.assertTrue(ctx.exception.retryable)

    def test_retry_recovers_on_later_attempt(self):
        self.search.session.post = mock.Mock(
            side_effect=[make_response(503), make_response(200, {"ok": 1})]
        )
        data = self.search._post("/tools/search", {})
        self.assertEqual(data, {"ok": 1})
        self.assertEqual(self.search.session.post.call_count, 2)

    def test_bad_json_is_not_retryable(self):
        resp = make_response(200)
        resp.json.side_effect = ValueError("bad json")
        self.search.session.post = mock.Mock(return_value=resp)
        with self.assertRaises(SearchError) as ctx:
            self.search._post("/tools/search", {})
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(self.search.session.post.call_count, 1)

    def test_missing_api_key_not_retryable(self):
        client = DeepSeekSearch(base_url="http://api.test", api_key="", timeout=1)
        client.api_key = ""
        with self.assertRaises(SearchError) as ctx:
            client._post("/tools/search", {})
        self.assertFalse(ctx.exception.retryable)


class SearchResultTests(unittest.TestCase):
    """search：空结果不算异常、缓存命中不发真实请求。"""

    def setUp(self):
        self.search = DeepSeekSearch(base_url="http://api.test", api_key="k", timeout=1)
        self.search.cache_seconds = 600

    def test_empty_results_return_empty_list_not_exception(self):
        self.search.session.post = mock.Mock(
            return_value=make_response(200, {"results": []})
        )
        results = self.search.search("一个肯定搜不到的词")
        self.assertEqual(results, [])  # 调用方用空列表区分“搜过但没有结果”

    def test_cache_hit_skips_real_request(self):
        self.search.session.post = mock.Mock(
            return_value=make_response(
                200, {"results": [{"title": "t", "url": "http://x"}]}
            )
        )
        first = self.search.search("最新价格")
        second = self.search.search("最新价格")
        self.assertEqual(self.search.session.post.call_count, 1)
        self.assertEqual(first, second)
        self.assertIsNot(first[0], second[0])  # 缓存返回的是拷贝，改它不影响缓存

    def test_search_error_propagates_with_retryable_flag(self):
        self.search.session.post = mock.Mock(
            return_value=make_response(401, text="bad key")
        )
        with self.assertRaises(SearchError) as ctx:
            self.search.search("今天新闻")
        self.assertFalse(ctx.exception.retryable)

    def test_empty_query_raises(self):
        with self.assertRaises(SearchError):
            self.search.search("   ")


if __name__ == "__main__":
    unittest.main()
