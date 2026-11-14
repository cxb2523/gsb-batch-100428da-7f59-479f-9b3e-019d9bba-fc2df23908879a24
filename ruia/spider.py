"""
    Created by howie.hu at 2018.
    Description:  Crawler startup class
    Changelog: all notable changes to this file will be documented
"""

import asyncio
import contextvars
import sys
import typing

try:
    # python 3.6+
    import collections.abc as collectionsAbc
except ImportError:
    import collections as collectionsAbc

from datetime import datetime
from functools import reduce
from inspect import isawaitable
from signal import SIGINT, SIGTERM
from types import AsyncGeneratorType

from aiohttp import ClientSession

from ruia.exceptions import NothingMatchedError, NotImplementedParseError
from ruia.item import Item
from ruia.middleware import Middleware
from ruia.record import RequestOutcome, RequestRecord, RequestRecordStore
from ruia.request import Request
from ruia.response import Response
from ruia.spider_hook import SpiderHook
from ruia.utils import get_logger

if sys.version_info >= (3, 8) and sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if sys.version_info >= (3, 9):
    async_all_tasks = asyncio.all_tasks
    async_current_task = asyncio.current_task
else:
    async_all_tasks = asyncio.Task.all_tasks
    async_current_task = asyncio.tasks.Task.current_task
try:
    import uvloop

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass


class Spider(SpiderHook):
    """
    Spider is used for control requests better
    """

    name = "Ruia"
    request_config = None

    # Default values passing to each request object. Not implemented yet.
    headers: dict = None
    metadata: dict = None
    aiohttp_kwargs: dict = None

    # Some fields for statistics
    failed_counts: int = 0
    success_counts: int = 0

    # Enable per-request outcome records. When it is False the crawling
    # behaviour and the statistics are exactly the same as before.
    record_requests: bool = False

    # Concurrency control
    worker_numbers: int = 2
    concurrency: int = 3

    # Spider entry
    start_urls: list = []

    # A queue to save coroutines
    worker_tasks: list = []

    def __init__(
        self,
        middleware: typing.Union[typing.Iterable, Middleware] = None,
        loop=None,
        is_async_start: bool = False,
        cancel_tasks: bool = True,
        record_requests: bool = None,
        **spider_kwargs,
    ):
        """
        Init spider object.
        :param middleware: a list of or a single Middleware
        :param loop: asyncio event llo
        :param is_async_start: start spider by using async
        :param record_requests: enable per-request outcome records
        :param spider_kwargs
        """
        if not self.start_urls or not isinstance(
            self.start_urls, collectionsAbc.Iterable
        ):
            raise ValueError(
                "Ruia spider must have a param named start_urls, eg: start_urls = ['https://www.github.com']"
            )

        self.loop = loop
        asyncio.set_event_loop(self.loop)

        # Init object-level properties
        self.callback_result_map = self.callback_result_map or {}
        self.request_config = self.request_config or {}
        self.headers = self.headers or {}
        self.metadata = self.metadata or {}
        self.aiohttp_kwargs = self.aiohttp_kwargs or {}
        self.spider_kwargs = spider_kwargs
        self.request_config = self.request_config or {}
        # The aiohttp session is created on first use: recent aiohttp versions
        # require a running event loop to build a ClientSession, while a
        # spider object is usually created before the loop is running.
        self._request_session = None
        # A subclass may provide a session as a class attribute; honour it.
        subclass_session = getattr(type(self), "request_session", None)
        if isinstance(subclass_session, ClientSession):
            self._request_session = subclass_session

        self.cancel_tasks = cancel_tasks
        self.is_async_start = is_async_start

        # Per-request outcome records (opt-in feature)
        if record_requests is None:
            record_requests = self.record_requests
        self._record_requests = bool(record_requests)
        self.record_requests = self._record_requests
        self.request_records = RequestRecordStore()
        # Friendly alias of ``request_records``
        self.records = self.request_records
        # The request currently being processed by a callback. It is used to
        # link a yielded Request to the request which produced it.
        self._current_request = contextvars.ContextVar(
            f"ruia_current_request_{id(self)}", default=None
        )

        # set logger
        self.logger = get_logger(name=self.name)

        # customize middleware
        if isinstance(middleware, list):
            self.middleware = reduce(lambda x, y: x + y, middleware)
        else:
            self.middleware = middleware or Middleware()

        # async queue as a producer
        self.request_queue = asyncio.Queue()

        # semaphore, used for concurrency control
        self.sem = asyncio.Semaphore(self.concurrency)

    @property
    def request_session(self):
        """The aiohttp session, created on first use inside the event loop"""
        if self._request_session is None:
            self._request_session = ClientSession()
        return self._request_session

    @request_session.setter
    def request_session(self, value):
        self._request_session = value

    async def _process_async_callback(
        self, callback_result: AsyncGeneratorType, response: Response = None
    ):
        # Remember which request produced these callback results, so that any
        # Request yielded below can be linked to its parent request.
        token = None
        if self.record_requests:
            parent_request = getattr(response, "_request", None) if response else None
            if parent_request is None:
                parent_request = self._current_request.get()
            token = self._current_request.set(parent_request)
        try:
            async for each_callback in callback_result:
                if isinstance(each_callback, AsyncGeneratorType):
                    await self._process_async_callback(each_callback)
                elif isinstance(each_callback, Request):
                    self._bind_parent_request(each_callback)
                    self.request_queue.put_nowait(
                        self.handle_request(request=each_callback)
                    )
                elif isinstance(each_callback, typing.Coroutine):
                    self.request_queue.put_nowait(
                        self.handle_callback(
                            aws_callback=each_callback, response=response
                        )
                    )
                elif isinstance(each_callback, Item):
                    # Process target item
                    await self.process_item(each_callback)
                else:
                    await self.process_callback_result(each_callback)
        except NothingMatchedError as e:
            error_info = f"<Field: {str(e).lower()}" + f", error url: {response.url}>"
            self.logger.error(error_info)
        except Exception as e:
            self.logger.error(e)
        finally:
            if token is not None:
                self._current_request.reset(token)

    def _bind_parent_request(self, request: Request):
        """
        Bind a Request yielded by a callback to the request which produced it,
        so that the pagination chain stays traceable.
        """
        if not self.record_requests:
            return
        parent_request = self._current_request.get()
        if parent_request is None:
            return
        if getattr(request, "parent_url", None) is None:
            request.parent_url = parent_request.url
        if getattr(request, "parent_request", None) is None:
            request.parent_request = parent_request
        if getattr(request, "producer_callback", None) is None:
            request.producer_callback = parent_request.request_callback_name

    def _record_request(
        self,
        request: Request,
        response: Response = None,
        *,
        error: str = None,
    ):
        """
        Build and store the outcome record of ``request``.
        """
        if not self.record_requests:
            return None

        attempts = int(getattr(request, "attempts", 0) or 0)
        status = getattr(response, "status", None)
        ok = bool(response) and bool(getattr(response, "ok", False)) and status != -1
        if ok:
            outcome = (
                RequestOutcome.SUCCESS
                if attempts <= 1
                else RequestOutcome.RETRIED_SUCCESS
            )
        else:
            outcome = RequestOutcome.FAILED
            # The abandoned request is returned as an empty response whose
            # status is -1; keep the real status when the response did carry
            # one (e.g. a VALID hook rejected an otherwise normal response).
            status = getattr(response, "status", None)
            if status is None:
                status = -1
            if error is None:
                error = getattr(request, "last_error", None)
            if error is None and status != -1:
                error = f"Request url failed with status {status}!"
            if error is None:
                error = "request was abandoned"

        record = RequestRecord(
            url=request.url,
            method=getattr(request, "method", "GET"),
            outcome=outcome,
            attempts=attempts,
            status=status,
            error=error,
            callback=(
                getattr(request, "producer_callback", None)
                or request.request_callback_name
            ),
            request_callback=request.request_callback_name,
            parent_url=getattr(request, "parent_url", None),
        )
        self.request_records.add(record)
        request._recorded = True
        return record

    def get_request_records(self, url: str = None, outcome: str = None) -> list:
        """
        Return the per-request outcome records.

        :param url: only return the records of this URL
        :param outcome: only return the records with this outcome
            (``success`` / ``retried_success`` / ``failed``)
        """
        return self.request_records.find(url=url, outcome=outcome)

    def get_request_record(self, url: str, default=None):
        """Return the last outcome record of ``url``"""
        return self.request_records.get(url, default)

    def get_outcome_counts(self) -> dict:
        """
        Return the number of records per outcome.

        Successful records (``success`` + ``retried_success``) match
        ``success_counts``, ``failed`` matches ``failed_counts``.
        """
        return self.request_records.counts()

    async def _process_response(self, request: Request, response: Response):
        """
        Process Ruia's Response:
            count whether each request was successful or not, and call the handler function finally.
        """
        if response:
            if response.ok:
                # Process succeed response
                self.success_counts += 1
                if self.record_requests:
                    self._record_request(request, response)
                await self.process_succeed_response(request, response)
            else:
                # Process failed response
                self.failed_counts += 1
                if self.record_requests:
                    self._record_request(request, response)
                await self.process_failed_response(request, response)

    async def _run_request_middleware(self, request: Request):
        if self.middleware.request_middleware:
            for middleware in self.middleware.request_middleware:
                if callable(middleware):
                    try:
                        aws_middleware_func = middleware(self, request)
                        if isawaitable(aws_middleware_func):
                            await aws_middleware_func
                        else:
                            msg = f"<Middleware {middleware.__name__}: must be a coroutine function"
                            self.logger.error(msg)
                    except Exception as e:
                        msg = f"<Middleware {middleware.__name__}: {e}"
                        self.logger.error(msg)

    async def _run_response_middleware(self, request: Request, response: Response):
        if self.middleware.response_middleware:
            for middleware in self.middleware.response_middleware:
                if callable(middleware):
                    try:
                        aws_middleware_func = middleware(self, request, response)
                        if isawaitable(aws_middleware_func):
                            await aws_middleware_func
                        else:
                            msg = f"<Middleware {middleware.__name__}: must be a coroutine function"
                            self.logger.error(msg)
                    except Exception as e:
                        msg = f"<Middleware {middleware.__name__}: {e}"
                        self.logger.error(msg)

    async def _start(self, after_start=None, before_stop=None):
        """
        Crawler startup entry
        Args:
            after_start (_type_, optional): Hook function: executed before the crawler starts. Defaults to None.
            before_stop (_type_, optional): Hook function: executed before the crawler closes. Defaults to None.
        """
        self.logger.info("Spider started!")
        start_time = datetime.now()

        # Add signal
        for signal in (SIGINT, SIGTERM):
            try:
                self.loop.add_signal_handler(
                    signal, lambda: asyncio.ensure_future(self.stop(signal))
                )
            except NotImplementedError:
                self.logger.warning(
                    f"{self.name} tried to use loop.add_signal_handler "
                    "but it is not implemented on this platform."
                )
        # Run hook before spider start crawling
        await self._run_spider_hook(after_start)

        # Actually run crawling
        try:
            await self.start_master()
        finally:
            # Run hook after spider finished crawling
            await self._run_spider_hook(before_stop)
            if self._request_session is not None:
                await self._request_session.close()
            # Display logs about this crawl task
            end_time = datetime.now()
            self.logger.info(
                f"Total requests: {self.failed_counts + self.success_counts}"
            )

            if self.failed_counts:
                self.logger.info(f"Failed requests: {self.failed_counts}")
            self.logger.info(f"Time usage: {end_time - start_time}")
            self.logger.info("Spider finished!")

    @classmethod
    async def async_start(
        cls,
        middleware: typing.Union[typing.Iterable, Middleware] = None,
        loop=None,
        after_start=None,
        before_stop=None,
        cancel_tasks: bool = True,
        **spider_kwargs,
    ):
        """
        Start an async spider
        :param middleware: customize middleware or a list of middleware
        :param loop:
        :param after_start: hook
        :param before_stop: hook
        :param cancel_tasks: cancel async tasks
        :param spider_kwargs: Additional keyword args to initialize spider
        :return: An instance of :cls:`Spider`
        """
        loop = loop or asyncio.get_event_loop()
        spider_ins = cls(
            middleware=middleware,
            loop=loop,
            is_async_start=True,
            cancel_tasks=cancel_tasks,
            **spider_kwargs,
        )
        await spider_ins._start(after_start=after_start, before_stop=before_stop)

        return spider_ins

    @staticmethod
    async def cancel_all_tasks():
        """
        Cancel all tasks
        """
        tasks = []
        for task in async_all_tasks():
            if task is not async_current_task():
                tasks.append(task)
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @classmethod
    def start(
        cls,
        middleware: typing.Union[typing.Iterable, Middleware] = None,
        loop=None,
        after_start=None,
        before_stop=None,
        close_event_loop=True,
        **spider_kwargs,
    ):
        """
        Start a spider
        :param after_start: hook
        :param before_stop: hook
        :param middleware: customize middleware or a list of middleware
        :param loop: event loop
        :param close_event_loop: bool
        :param spider_kwargs: Additional keyword args to initialize spider
        :return: An instance of :cls:`Spider`
        """
        loop = loop or asyncio.new_event_loop()
        spider_ins = cls(middleware=middleware, loop=loop, **spider_kwargs)

        # Actually start crawling
        spider_ins.loop.run_until_complete(
            spider_ins._start(after_start=after_start, before_stop=before_stop)
        )
        spider_ins.loop.run_until_complete(spider_ins.loop.shutdown_asyncgens())
        if close_event_loop:
            spider_ins.loop.close()

        return spider_ins

    async def handle_callback(self, aws_callback: typing.Coroutine, response):
        """
        Process coroutine callback function
        """
        callback_result = None

        try:
            callback_result = await aws_callback
        except NothingMatchedError as e:
            self.logger.error(f"<Item: {str(e).lower()}>")
        except Exception as e:
            self.logger.error(f"<Callback[{aws_callback.__name__}]: {e}")

        return callback_result, response

    async def handle_request(
        self, request: Request
    ) -> typing.Tuple[AsyncGeneratorType, Request, Response]:
        """
        Wrap request with middleware
        Args:
            request (Request): Ruia's Request

        Returns:
            typing.Tuple[AsyncGeneratorType, Request, Response]: Returns a result tuple after each request
        """
        callback_result, response = None, None

        if self.record_requests:
            # A request fetched directly inside a callback (e.g. through
            # ``multiple_request``) is a child of the request being processed.
            # Queued requests run in the worker context, where no parent is
            # set, so they are not affected here.
            self._bind_parent_request(request)
        try:
            await self._run_request_middleware(request)
            callback_result, response = await request.fetch_callback(self.sem)
            if response is not None and self.record_requests:
                # Let the callback processing know which request produced this
                # response, so yielded requests can be linked to their parent.
                response._request = request
            await self._run_response_middleware(request, response)
            await self._process_response(request=request, response=response)
        except NotImplementedParseError as e:
            self.logger.error(e)
        except NothingMatchedError as e:
            error_info = f"<Field: {str(e).lower()}" + f", error url: {request.url}>"
            self.logger.error(error_info)
        except Exception as e:
            self.logger.error(f"<Callback[{request.callback.__name__}]: {e}")
        finally:
            if self.record_requests and not getattr(request, "_recorded", False):
                # Make sure every request entering the queue leaves a record,
                # even when something went wrong before the response could be
                # processed. Such an exceptional request never reaches the
                # success/failed counting above, so count it as failed here to
                # keep the statistics and the records consistent.
                if response is None:
                    self.failed_counts += 1
                self._record_request(request, response)

        return callback_result, request, response

    async def multiple_request(self, urls, is_gather=False, **kwargs):
        """
        For crawling multiple urls
        """
        if is_gather:
            resp_results = await asyncio.gather(
                *[self.handle_request(self.request(url=url, **kwargs)) for url in urls],
                return_exceptions=True,
            )
            for index, task_result in enumerate(resp_results):
                if not isinstance(task_result, RuntimeError) and task_result:
                    _, _, response = task_result
                    response.index = index
                    yield response
        else:
            for index, url in enumerate(urls):
                _, _, response = await self.handle_request(
                    self.request(url=url, **kwargs)
                )
                response.index = index
                yield response

    async def parse(self, response: Response):
        """
        Used for subclasses, directly parse the responses corresponding with start_urls
        :param response: Response
        :return:
        """
        raise NotImplementedParseError("<!!! parse function is expected !!!>")

    async def process_start_urls(self):
        """
        Process the start URLs
        :return: AN async iterator
        """
        for url in self.start_urls:
            yield self.request(url=url, callback=self.parse, metadata=self.metadata)

    def request(
        self,
        url: str,
        method: str = "GET",
        *,
        callback=None,
        encoding: typing.Optional[str] = None,
        headers: dict = None,
        metadata: dict = None,
        request_config: dict = None,
        request_session=None,
        **aiohttp_kwargs,
    ):
        """
        Init a Request class for crawling html
        Args:
            url (str):  Target url
            method (str, optional): HTTP method. Defaults to "GET".
            callback (_type_, optional): Callback func. Defaults to None.
            encoding (typing.Optional[str], optional): Html encoding. Defaults to None.
            headers (dict, optional): _description_. Request headers to None.
            metadata (dict, optional): _description_. Send the data to callback func to None.
            request_config (dict, optional): Manage the target request. Defaults to None.
            request_session (_type_, optional):  aiohttp.ClientSession. Defaults to None.

        Returns:
            _type_: Request
        """
        headers = headers or {}
        metadata = metadata or {}
        request_config = request_config or {}

        if request_session is None:
            request_session = self.request_session
            close_request_session = False
        else:
            close_request_session = True

        headers.update(self.headers.copy())
        request_config.update(self.request_config.copy())
        aiohttp_kwargs.update(self.aiohttp_kwargs.copy())

        return Request(
            url=url,
            method=method,
            callback=callback,
            encoding=encoding,
            headers=headers,
            metadata=metadata,
            request_config=request_config,
            request_session=request_session,
            close_request_session=close_request_session,
            **aiohttp_kwargs,
        )

    async def start_master(self):
        """
        Actually start crawling
        """
        async for request_ins in self.process_start_urls():
            self.request_queue.put_nowait(self.handle_request(request_ins))
        workers = [
            asyncio.ensure_future(self.start_worker())
            for i in range(self.worker_numbers)
        ]
        for worker in workers:
            self.logger.info(f"Worker started: {id(worker)}")
        await self.request_queue.join()

        if not self.is_async_start:
            await self.stop(SIGINT)
        else:
            if self.cancel_tasks:
                await self.cancel_all_tasks()

    async def start_worker(self):
        """
        Start spider worker
        :return:
        """
        while True:
            request_item = await self.request_queue.get()
            self.worker_tasks.append(request_item)
            if self.request_queue.empty():
                results = await asyncio.gather(
                    *self.worker_tasks, return_exceptions=True
                )
                for task_result in results:
                    if not isinstance(task_result, RuntimeError) and task_result:
                        callback_result = task_result[0]
                        request: Request = task_result[1]
                        if isinstance(callback_result, AsyncGeneratorType):
                            await self._process_async_callback(
                                callback_result, task_result[2]
                            )
                        # Process Request's session
                        await request.close_request()

                self.worker_tasks = []
            self.request_queue.task_done()

    async def stop(self, _signal):
        """
        Finish all running tasks, cancel remaining tasks.
        :param _signal:
        :return:
        """
        self.logger.info(f"Stopping spider: {self.name}")
        await self.cancel_all_tasks()
        # self.loop.stop()
