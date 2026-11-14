# _*_ coding: utf-8 _*_

"""
inst_fetch.py by xianhu
"""

import time

from ..utilities import TaskFetch, ResultFetch


class Fetcher(object):
    """
    class of Fetcher, must include function working()
    """

    def __init__(self, sleep_time=0, max_repeat=3):
        """
        constructor
        :param sleep_time: default 0, sleeping time before fetching
        :param max_repeat: default 3, maximum repeat count of fetching
        """
        self._sleep_time = sleep_time
        self._max_repeat = max_repeat
        return

    @property
    def max_repeat(self):
        """
        maximum repeat count of fetching, a task with repeat >= max_repeat is the last chance
        """
        return self._max_repeat

    @max_repeat.setter
    def max_repeat(self, value):
        """
        allow subclasses to set max_repeat directly
        """
        self._max_repeat = value
        return

    def working(self, task_fetch: TaskFetch, proxies=None) -> ResultFetch:
        """
        working function, must "try-except" and return ResultFetch()
        """
        time.sleep(self._sleep_time)

        try:
            result_fetch = self.url_fetch(task_fetch, proxies=proxies)
        except Exception as excep:
            retry_exhausted = task_fetch.repeat >= self._max_repeat
            state_code = -1 if retry_exhausted else 0
            kwargs = dict(excep_class=self.__class__.__name__, excep_string=str(excep))
            result_fetch = ResultFetch(state_code=state_code, state_proxies=-1, task_parse=None,
                                       retry_exhausted=retry_exhausted, **kwargs)

        return result_fetch

    def url_fetch(self, task_fetch: TaskFetch, proxies=None) -> ResultFetch:
        """
        fetch the content of an url. Parameters and returns refer to self.working()
        """
        raise NotImplementedError
