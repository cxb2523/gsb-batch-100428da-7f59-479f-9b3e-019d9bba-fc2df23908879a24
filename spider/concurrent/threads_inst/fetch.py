# _*_ coding: utf-8 _*_

"""
fetch.py by xianhu
"""

import logging

from .base import TPEnum, BaseThread
from ...utilities import TaskFetch, ResultFetch, FailureReason, FailureStage


class FetchThread(BaseThread):
    """
    class of FetchThread, as the subclass of BaseThread
    """

    def __init__(self, name, worker, pool):
        """
        constructor, add proxies to this thread
        """
        super().__init__(name, worker, pool)
        self._proxies = None
        # the fetcher may not call Fetcher.__init__ at all, keep a sane default then
        self._max_repeat = getattr(worker, "max_repeat", None)
        if not isinstance(self._max_repeat, int):
            self._max_repeat = getattr(worker, "_max_repeat", None)
        if not isinstance(self._max_repeat, int):
            self._max_repeat = 3
        return

    def _is_retry_exhausted(self, task: TaskFetch, result: ResultFetch):
        """
        check whether a task asking for repeat has already used up max_repeat
        """
        return bool(getattr(result, "retry_exhausted", False)) or (task.repeat >= self._max_repeat)

    def working(self):
        """
        procedure of fetching, auto running and return True
        """
        # ----*----
        if self._pool.get_proxies_flag() and (not self._proxies):
            self._proxies = self._pool.get_a_task(TPEnum.PROXIES)

        # ----1----
        task: TaskFetch = self._pool.get_a_task(TPEnum.URL_FETCH)

        # ----2----
        result: ResultFetch = self._worker.working(task, proxies=self._proxies)

        # ----3----
        # only attribute an attempt to proxies when it really used one; state_proxies tells
        # whether that proxy is unavaiable (-1) or was returned to the queue (0)
        proxy_invalid = bool(self._proxies) and (result.state_proxies < 0)
        proxy_returned = bool(self._proxies) and (result.state_proxies == 0)
        details = dict(
            repeat=task.repeat, state_code=result.state_code, state_proxies=result.state_proxies,
            excep_class=result.excep_class, excep_string=result.excep_string,
        )
        if self._proxies:
            details["used_proxies"] = True

        def _reasons(reason_list):
            # keep the reason why this attempt did not succeed first, proxy attribution after it
            if proxy_invalid:
                return reason_list + [FailureReason.PROXY_INVALID]
            if proxy_returned:
                return reason_list + [FailureReason.PROXY_RETURNED]
            return reason_list

        exhausted = self._is_retry_exhausted(task, result)

        # a fetcher may report state_code < 0 for a hard failure even when repeat is below
        # max_repeat, so the exhaustion counter only follows an explicit retry_exhausted flag
        retry_exhausted = bool(getattr(result, "retry_exhausted", False)) or \
            (result.state_code == 0 and exhausted)

        if result.state_code > 0:
            self._pool.update_number_dict(TPEnum.URL_FETCH_SUCC, +1)
            self._pool.resolve_failure(task.url, FailureStage.FETCH)
            self._pool.add_a_task(TPEnum.HTM_PARSE, result.task_parse)
        elif result.state_code == 0:
            if exhausted:
                # never re-enter the queue unboundedly: record it as an exhausted retry
                self._pool.update_number_dict(TPEnum.URL_FETCH_FAIL, +1)
                self._pool.update_number_dict(TPEnum.URL_FETCH_RETRY_EXHAUST, +1)
                if proxy_invalid:
                    self._pool.update_number_dict(TPEnum.URL_FETCH_PROXY_FAIL, +1)
                self._pool.record_failure(
                    task.url, FailureStage.FETCH, _reasons([FailureReason.RETRY_EXHAUSTED]), terminal=True, **details,
                )
                logging.error("%s exhausted: %s, %s", result.excep_class, result.excep_string, str(task))
            else:
                self._pool.record_failure(
                    task.url, FailureStage.FETCH, _reasons([FailureReason.RETRY]), terminal=False, **details,
                )
                self._pool.update_number_dict(TPEnum.URL_FETCH_RETRY, +1)
                self._pool.add_a_task(TPEnum.URL_FETCH, TaskFetch.from_task_fetch(task))
                logging.warning("%s repeat: %s, %s", result.excep_class, result.excep_string, str(task))
        else:
            # state_code < 0: this task is finally failed; keep every reason it failed for
            reasons = [FailureReason.FETCH_FAILED]
            if retry_exhausted:
                reasons.insert(0, FailureReason.RETRY_EXHAUSTED)
                self._pool.update_number_dict(TPEnum.URL_FETCH_RETRY_EXHAUST, +1)
            reasons = _reasons(reasons)
            if proxy_invalid:
                self._pool.update_number_dict(TPEnum.URL_FETCH_PROXY_FAIL, +1)
            self._pool.update_number_dict(TPEnum.URL_FETCH_FAIL, +1)
            self._pool.record_failure(task.url, FailureStage.FETCH, reasons, terminal=True, **details)
            logging.error("%s error: %s, %s", result.excep_class, result.excep_string, str(task))

        # ----4----
        self._pool.finish_a_task(TPEnum.URL_FETCH)

        # ----*----
        if self._pool.get_proxies_flag() and self._proxies and (result.state_proxies <= 0):
            if result.state_proxies == 0:
                self._pool.add_a_task(TPEnum.PROXIES, self._proxies)
            else:
                self._pool.update_number_dict(TPEnum.PROXIES_FAIL, +1)
            self._pool.finish_a_task(TPEnum.PROXIES)
            self._proxies = None

        # return
        return True
