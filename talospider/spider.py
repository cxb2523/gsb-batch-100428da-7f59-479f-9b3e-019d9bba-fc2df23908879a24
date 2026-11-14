# !/usr/bin/env python
# -*- coding:utf-8 -*-

from datetime import datetime
from lxml import etree
from multiprocessing import cpu_count, freeze_support, Pool

from talospider import Request
from talospider.utils import Logger


def _run_request(each_request):
    """
    Top-level worker so it can be pickled for the process pool.

    It runs one request and returns the callback results together with the
    :class:`RequestOutcome` produced in the worker process, so the outcome
    can travel back to the main process instead of being lost at the
    process boundary.
    """
    callback = each_request()
    return callback, each_request.outcome


class Spider():
    """
    Spider class
    """
    name = 'talospider'
    start_urls = []
    request_config = None
    set_mul = False
    pool_size = cpu_count()

    # Outcomes of every Request executed in the latest :meth:`start` run,
    # ordered by completion. Reset on each run, including in worker-driven
    # (multi-process) runs where outcomes are merged back from the pool.
    request_outcomes = []

    def __init__(self):
        setattr(self, 'name', self.name)
        if not getattr(self, 'start_urls', None):
            raise ValueError('spider must have a start_urls')
        self.log = Logger(name=self.name)

    def start_request(self):
        for url in self.start_urls:
            yield Request(url=url,
                          request_config=getattr(self, 'request_config'),
                          headers=getattr(self, 'headers', None),
                          callback=self.parse,
                          **getattr(self, 'kwargs', {}))

    def parse(self, res):
        raise NotImplementedError

    @classmethod
    def start(cls):
        """
        Start crawling
        """
        spider_instance = cls()
        # A fresh run starts with an empty outcome list, otherwise results
        # of a previous run would leak into the new one.
        cls.request_outcomes = []
        spider_instance.logger.info('{name} started'.format(name=cls.name))
        start = datetime.now()
        gen_request = list(spider_instance.start_request())
        if cls.set_mul:
            try:
                cls.gen_call(gen_request=gen_request)
            except Exception as e:
                spider_instance.logger.info(e)
                cls.except_gen_call(gen_request=gen_request)
        else:
            cls.except_gen_call(gen_request=gen_request)
        spider_instance.logger.info('Time usage：{seconds}'.format(seconds=(datetime.now() - start)))
        return cls.request_outcomes

    @classmethod
    def gen_call(cls, gen_request):
        freeze_support()
        pool = Pool(cls.pool_size)
        try:
            results = []
            for each_main_request in gen_request:
                result = pool.apply_async(_run_request, args=(each_main_request,))
                results.append(result)
            pool.close()
            pool.join()
            for result in results:
                each_callback, outcome = result.get()
                cls._collect_outcome(outcome)
                if each_callback is not None:
                    # Child requests yielded by parse are executed through
                    # the same pool, so their outcomes flow back here too.
                    cls.gen_call(gen_request=each_callback)
        finally:
            pool.terminate()

    @classmethod
    def except_gen_call(cls, gen_request):
        for each_request in gen_request:
            callback = each_request()
            cls._collect_outcome(each_request.outcome)
            if callback is not None:
                cls.except_gen_call(callback)

    @classmethod
    def _collect_outcome(cls, outcome):
        if outcome is not None:
            cls.request_outcomes.append(outcome)

    @property
    def logger(self):
        return self.log

    def e_html(self, html):
        return etree.HTML(html)

    def __getstate__(self):
        # The Logger wraps runtime logging state; drop it when the instance
        # crosses a process boundary (bound parse callbacks are sent to the
        # pool together with their Request objects) and rebuild it lazily.
        state = self.__dict__.copy()
        state.pop('log', None)
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        self.log = Logger(name=self.name)
