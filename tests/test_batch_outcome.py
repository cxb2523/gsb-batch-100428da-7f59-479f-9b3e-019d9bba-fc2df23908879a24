# _*_ coding: utf-8 _*_

"""
test_batch_outcome.py

Local, network-free tests for the batch outcome of WebSpider: how a run ended,
and which urls failed for what reason.

Run from the repo root:
    python -m unittest tests.test_batch_outcome -v
"""

import threading
import time
import unittest

import spider


class RecordingBehavior(object):
    """
    a callable fetcher behavior shared by all deep-copied fetchers
    """

    def __init__(self, func):
        self._func = func
        self.calls = []
        self._lock = threading.Lock()

    def __deepcopy__(self, memo):
        # the fetcher is deep-copied per thread, keep one shared behavior instead
        return self

    def __call__(self, task_fetch, proxies):
        with self._lock:
            self.calls.append(task_fetch.url)
        return self._func(task_fetch, proxies)

    def count(self, url):
        with self._lock:
            return self.calls.count(url)


class FakeFetcher(spider.Fetcher):
    """
    fetcher driven by a local behavior, never touches the network
    """

    def __init__(self, behavior, max_repeat=3):
        super().__init__(sleep_time=0, max_repeat=max_repeat)
        self.behavior = behavior

    def url_fetch(self, task_fetch, proxies=None):
        return self.behavior(task_fetch, proxies)


class FakeParser(spider.Parser):
    """
    parser returning links configured per url
    """

    def __init__(self, links=None):
        super().__init__()
        self.links = links or {}

    def htm_parse(self, task_parse):
        urls = self.links.get(task_parse.url, [])
        task_fetch_list = [spider.TaskFetch.from_task_parse(task_parse, url_new=url) for url in urls]
        task_save = spider.TaskSave.from_task_parse(task_parse, item={"url": task_parse.url})
        return spider.ResultParse(state_code=1, task_fetch_list=task_fetch_list, task_save=task_save)


class FakeSaver(spider.Saver):
    """
    saver collecting items in memory
    """

    def __init__(self, items=None):
        super().__init__()
        self.items = items if items is not None else []

    def item_save(self, task_save):
        self.items.append(task_save.item)
        return spider.ResultSave(state_code=1)


class FakeProxieser(spider.Proxieser):
    """
    proxieser handing out a bounded number of local fake proxies
    """

    def __init__(self, count=20):
        super().__init__()
        self._count = count
        self._lock = threading.Lock()

    def proxies_get(self):
        time.sleep(0.002)
        with self._lock:
            if self._count <= 0:
                return spider.ResultProxies(state_code=1, proxies_list=[])
            self._count -= 1
            number = self._count
        return spider.ResultProxies(state_code=1, proxies_list=[{"https": "https://fake-%d" % number}])


def fetch_ok(task_fetch, proxies):
    task_parse = spider.TaskParse.from_task_fetch(task_fetch, content=("<html>%s</html>" % task_fetch.url))
    return spider.ResultFetch(state_code=1, state_proxies=1, task_parse=task_parse)


def fetch_always_repeat(task_fetch, proxies):
    return spider.ResultFetch(state_code=0, state_proxies=1, excep_string="never finishes")


def fetch_always_failed(task_fetch, proxies):
    return spider.ResultFetch(state_code=-1, state_proxies=1, excep_class="FakeError", excep_string="boom")


def fetch_proxy_invalid(task_fetch, proxies):
    return spider.ResultFetch(state_code=0, state_proxies=-1, excep_string="proxy unusable")


def fetch_proxy_invalid_terminal(task_fetch, proxies):
    return spider.ResultFetch(state_code=-1, state_proxies=-1, excep_class="FakeProxyError", excep_string="proxy unusable")


class TestBatchOutcome(unittest.TestCase):
    """
    tests around the batch outcome returned by wait_for_finished()
    """

    def _run(self, pool, start_urls, fetchers_num=2):
        for url in start_urls:
            pool.set_start_task(spider.TaskFetch(priority=0, keys={}, deep=0, url=url))
        pool.start_working(fetchers_num=fetchers_num)
        return pool.wait_for_finished()

    def test_completed_with_parser_and_saver(self):
        """
        a normal run converges, duplicate links keep being filtered and nothing fails
        """
        behavior = RecordingBehavior(fetch_ok)
        parser = FakeParser(links={"http://index": ["http://a", "http://b", "http://a"]})
        saver = FakeSaver()
        pool = spider.WebSpider(
            FakeFetcher(behavior), parser, saver, url_filter=spider.UrlFilter(),
        )
        result = self._run(pool, ["http://index"])

        self.assertEqual(result.outcome, spider.BatchOutcome.COMPLETED)
        self.assertTrue(result.converged)
        self.assertTrue(result.drained)
        self.assertTrue(result.success)
        self.assertEqual(result.failures, [])
        self.assertEqual(result.failed_urls, [])
        self.assertEqual(result[spider.TPEnum.URL_FETCH_SUCC], 3)
        self.assertEqual(result["url_fetch_succ"], 3)  # readable by string alias too
        self.assertEqual(behavior.count("http://a"), 1)  # UrlFilter still dedupes
        self.assertEqual(len(saver.items), 3)
        self.assertEqual(result.retry_exhausted_count, 0)
        self.assertEqual(result.proxy_fail_count, 0)

    def test_retry_exhausted_is_terminal(self):
        """
        a url that always asks for repeat must stop at max_repeat instead of looping forever
        """
        behavior = RecordingBehavior(fetch_always_repeat)
        pool = spider.WebSpider(FakeFetcher(behavior, max_repeat=2), FakeParser(), FakeSaver())
        result = self._run(pool, ["http://poison"])

        self.assertEqual(behavior.count("http://poison"), 3)  # repeat=0,1 retried, repeat=2 exhausted
        self.assertEqual(result.outcome, spider.BatchOutcome.RETRY_EXHAUSTED)
        self.assertFalse(result.converged)
        self.assertTrue(result.drained)
        self.assertEqual(result[spider.TPEnum.URL_FETCH_RETRY], 2)
        self.assertEqual(result.retry_exhausted_count, 1)
        self.assertEqual(result[spider.TPEnum.URL_FETCH_FAIL], 1)
        self.assertEqual(result.failed_urls, ["http://poison"])
        self.assertEqual(result.failures[0]["reason"], spider.FailureReason.RETRY_EXHAUSTED)
        self.assertTrue(result.failures[0]["terminal"])
        self.assertEqual(result.failures[0]["repeat"], 2)
        self.assertEqual(result.failure_total, 1)  # only the terminal failure
        self.assertEqual(result.failure_event_total, 3)  # two retry events + one terminal event

    def test_proxy_invalid_attribution(self):
        """
        a retry exhausted because the proxy was unusable is told apart from a plain retry
        """
        behavior = RecordingBehavior(fetch_proxy_invalid)
        pool = spider.WebSpider(
            FakeFetcher(behavior, max_repeat=1), FakeParser(), FakeSaver(), proxieser=FakeProxieser(count=10),
        )
        result = self._run(pool, ["http://proxy-poison"], fetchers_num=1)

        self.assertEqual(result.outcome, spider.BatchOutcome.RETRY_AND_PROXIES_EXHAUSTED)
        self.assertFalse(result.converged)
        self.assertEqual(result.retry_exhausted_count, 1)
        self.assertEqual(result.proxy_fail_count, 1)
        self.assertGreaterEqual(result[spider.TPEnum.PROXIES_FAIL], 1)
        self.assertEqual(result.failed_urls, ["http://proxy-poison"])
        self.assertIn(spider.FailureReason.RETRY_EXHAUSTED, result.failures[0]["reasons"])
        self.assertIn(spider.FailureReason.PROXY_INVALID, result.failures[0]["reasons"])
        self.assertTrue(result.failures[0]["used_proxies"])
        self.assertEqual(result.failures[0]["state_proxies"], -1)

    def test_proxy_invalid_only(self):
        """
        a terminal failure caused by an unusable proxy gives the proxies_exhausted outcome
        """
        behavior = RecordingBehavior(fetch_proxy_invalid_terminal)
        pool = spider.WebSpider(
            FakeFetcher(behavior, max_repeat=3), FakeParser(), FakeSaver(), proxieser=FakeProxieser(count=10),
        )
        result = self._run(pool, ["http://proxy-fail"], fetchers_num=1)

        self.assertEqual(result.outcome, spider.BatchOutcome.PROXIES_EXHAUSTED)
        self.assertFalse(result.converged)
        self.assertTrue(result.drained)
        self.assertEqual(result.retry_exhausted_count, 0)  # not a retry problem
        self.assertEqual(result.proxy_fail_count, 1)
        self.assertEqual(result.failure_reasons,
                         {spider.FailureReason.FETCH_FAILED: 1, spider.FailureReason.PROXY_INVALID: 1})
        self.assertIn(spider.FailureReason.PROXY_INVALID, result.failures[0]["reasons"])
        self.assertIn(spider.FailureReason.FETCH_FAILED, result.failures[0]["reasons"])

    def test_without_parser_and_saver(self):
        """
        a pool with neither parser nor saver still works and reports a proper outcome
        """
        behavior = RecordingBehavior(fetch_ok)
        pool = spider.WebSpider(FakeFetcher(behavior))
        result = self._run(pool, ["http://only"], fetchers_num=1)

        self.assertEqual(result.outcome, spider.BatchOutcome.COMPLETED)
        self.assertTrue(result.converged)
        self.assertEqual(result[spider.TPEnum.URL_FETCH_SUCC], 1)

    def test_failures_are_bounded(self):
        """
        failure details are capped while the total count keeps the full picture
        """
        behavior = RecordingBehavior(fetch_always_failed)
        pool = spider.WebSpider(FakeFetcher(behavior, max_repeat=3), FakeParser(), FakeSaver(), max_failures=5)
        urls = ["http://bad-%d" % i for i in range(20)]
        result = self._run(pool, urls, fetchers_num=4)

        self.assertEqual(result.outcome, spider.BatchOutcome.COMPLETED_WITH_FAILURES)
        self.assertTrue(result.converged)  # drained, no retry/proxy exhaustion
        self.assertEqual(result[spider.TPEnum.URL_FETCH_FAIL], 20)
        self.assertEqual(result.failure_total, 20)
        self.assertEqual(result.failure_event_total, 20)
        self.assertEqual(result.failure_reasons, {spider.FailureReason.FETCH_FAILED: 20})
        self.assertEqual(result.max_failures, 5)
        self.assertEqual(len(result.failures), 5)
        self.assertEqual(len(result.failed_urls), 5)
        self.assertEqual(len(set(result.failed_urls)), 5)
        for failure in result.failures:
            self.assertEqual(failure["reason"], spider.FailureReason.FETCH_FAILED)
            self.assertEqual(failure["stage"], "fetch")

    def test_recovered_retry_is_not_a_failure(self):
        """
        a url that succeeds after a retry must not be reported as failed
        """
        attempts = {}
        lock = threading.Lock()

        def fetch_flaky(task_fetch, proxies):
            with lock:
                count = attempts.get(task_fetch.url, 0)
                attempts[task_fetch.url] = count + 1
            if count == 0:
                return spider.ResultFetch(state_code=0, state_proxies=1, excep_string="first attempt fails")
            return fetch_ok(task_fetch, proxies)

        behavior = RecordingBehavior(fetch_flaky)
        pool = spider.WebSpider(FakeFetcher(behavior, max_repeat=3), FakeParser(), FakeSaver())
        result = self._run(pool, ["http://flaky"], fetchers_num=1)

        self.assertEqual(result.outcome, spider.BatchOutcome.COMPLETED)
        self.assertTrue(result.converged)
        self.assertEqual(result.failures, [])
        self.assertEqual(result.failed_urls, [])
        self.assertEqual(result[spider.TPEnum.URL_FETCH_RETRY], 1)
        self.assertEqual(result.retry_reasons.get(spider.FailureReason.RETRY), 1)
        self.assertEqual(result.failure_total, 0)

    def test_exception_retry_exhausted(self):
        """
        a fetcher that keeps raising is stopped by max_repeat and reported as exhausted
        """
        attempts = {}

        def raise_fetch(task_fetch, proxies):
            attempts[task_fetch.url] = attempts.get(task_fetch.url, 0) + 1
            raise RuntimeError("always raises")

        behavior = RecordingBehavior(raise_fetch)
        pool = spider.WebSpider(FakeFetcher(behavior, max_repeat=2), FakeParser(), FakeSaver())
        result = self._run(pool, ["http://raises"], fetchers_num=1)

        # Fetcher.working() turns the exception into state_code=0 until repeat reaches max_repeat
        self.assertEqual(behavior.count("http://raises"), 3)
        self.assertEqual(result.outcome, spider.BatchOutcome.RETRY_EXHAUSTED)
        self.assertFalse(result.converged)
        self.assertEqual(result.failures[0]["reason"], spider.FailureReason.RETRY_EXHAUSTED)
        self.assertEqual(result.failures[0]["excep_string"], "always raises")
        self.assertEqual(result.failures[0]["repeat"], 2)

    def test_failure_recorder_is_bounded_and_resolves(self):
        """
        FailureRecorder caps retained urls, keeps totals and drops resolved retries
        """
        recorder = spider.FailureRecorder(max_failures=2)
        for index in range(5):
            recorder.record("http://u%d" % index, spider.FailureStage.FETCH,
                            [spider.FailureReason.RETRY], terminal=False)
        self.assertEqual(recorder.total, 5)
        self.assertEqual(recorder.terminal_total, 0)

        recorder.record("http://u0", spider.FailureStage.FETCH, [spider.FailureReason.RETRY_EXHAUSTED])
        failures, pending, total, terminal = recorder.snapshot()
        self.assertEqual(total, 6)
        self.assertEqual(terminal, 1)
        self.assertEqual([item["url"] for item in failures], ["http://u0"])
        # u0's earlier retry detail was already evicted by the bounded window, so only
        # the terminal reason survives while the retry counters still count it
        self.assertEqual(failures[0]["reasons"], [spider.FailureReason.RETRY_EXHAUSTED])
        self.assertEqual(failures[0]["reason"], spider.FailureReason.RETRY_EXHAUSTED)
        self.assertEqual(len(pending), 2)  # only the last two distinct retry urls are kept

        recorder.resolve("http://u4", spider.FailureStage.FETCH)
        _, pending, _, _ = recorder.snapshot()
        self.assertEqual([item["url"] for item in pending], ["http://u3"])
        self.assertEqual(recorder.reason_counts(terminal=False), {spider.FailureReason.RETRY: 5})

    def test_legacy_counts_and_priority_are_preserved(self):
        """
        the returned result keeps every original counter and the priority queue still works
        """
        behavior = RecordingBehavior(fetch_ok)
        pool = spider.WebSpider(FakeFetcher(behavior))
        result = self._run(pool, ["http://p5"], fetchers_num=1)
        self.assertEqual(result[spider.TPEnum.URL_FETCH_SUCC], 1)
        self.assertIn(spider.TPEnum.URL_FETCH_RUN, result)
        self.assertIn(spider.TPEnum.PROXIES_FAIL, result)
        self.assertIn(spider.TPEnum.ITEM_SAVE_SUCC, result)
        self.assertEqual(result["url_fetch_succ"], 1)
        self.assertIsInstance(result, dict)
        self.assertEqual(result.outcome, spider.BatchOutcome.COMPLETED)

        # priority queue: lower priority value is fetched first
        order = []
        lock = threading.Lock()

        def record(task_fetch, proxies):
            with lock:
                order.append(task_fetch.url)
            return fetch_ok(task_fetch, proxies)

        order_pool = spider.WebSpider(FakeFetcher(RecordingBehavior(record)))
        for priority, url in [(5, "http://p5"), (1, "http://p1"), (3, "http://p3"), (0, "http://p0")]:
            order_pool.set_start_task(spider.TaskFetch(priority=priority, url=url))
        order_pool.start_working(1)
        order_result = order_pool.wait_for_finished()
        self.assertEqual(order_result[spider.TPEnum.URL_FETCH_SUCC], 4)
        self.assertEqual(order, ["http://p0", "http://p1", "http://p3", "http://p5"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
