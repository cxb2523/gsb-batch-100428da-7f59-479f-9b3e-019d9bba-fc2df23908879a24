from __future__ import annotations

__author__ = 'Nikita Denissov'

import socket
from dataclasses import dataclass
from http.client import HTTPException, HTTPResponse
from multiprocessing import cpu_count
from multiprocessing.pool import ThreadPool
from string import punctuation
from threading import Lock
from typing import Iterable, NamedTuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tgphind.parser import *

__all__ = (
    'TRANSCRIPT', 'TGPhind', 'MIRRORS', 'Proxy',
    'Failure', 'MirrorSet', 'NoMirrorsAvailable', 'SearchResult',
    'REASON_TIMEOUT', 'REASON_CONNECTION', 'REASON_HTTP', 'REASON_ERROR',
)

TRANSCRIPT = str.maketrans(
    {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd',
        'е': 'e', 'ё': 'yo', 'ж': 'zh', 'з': 'z', 'и': 'i',
        'й': 'j', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n',
        'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't',
        'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'ch',
        'ш': 'sh', 'щ': 'shch', 'ъ': '', 'ы': 'y', 'ь': '',
        'э': 'eh', 'ю': 'yu', 'я': 'ya', ' ': '-'
    } | {i: '' for i in punctuation.replace(' ', '')})

MONTH = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
MIRRORS = ['te.legra.ph', 'graph.org', 'telegra.ph']

TIMEOUT = 5.
# Upper bound of the numbered pages (-2, -3, ...) probed for a single path.
# Real publications never reach it; it only guards against a server that
# answers 200 to every path.
MAX_PAGES = 100

# A 404/410 response is the normal "this article does not exist" answer of the
# mirrors, so it is not an error and must not be reported as a failed request.
NOT_FOUND_STATUS = frozenset({404, 410})

# Failure categories reported through :class:`Failure`.
REASON_TIMEOUT = 'timeout'
REASON_CONNECTION = 'connection'
REASON_HTTP = 'http'
REASON_ERROR = 'error'


class Failure(NamedTuple):
    """A single failed article probe.

    ``title`` is the expanded title as written by the user, ``name`` is the
    transliterated slug used in the URL and ``path`` is the probed path.
    ``mirror`` identifies the mirror that was used, ``reason`` is one of
    ``'timeout'``, ``'connection'``, ``'http'`` or ``'error'`` and ``detail``
    keeps the original, human readable error message.

    As a named tuple a failure unpacks as
    ``(title, mirror, reason, detail, path, status, name)`` and its string
    form ``"title @ mirror: reason (detail) [path]"`` is what the CLI prints.
    """

    title: str
    mirror: str
    reason: str
    detail: str = ''
    path: str = ''
    status: int | None = None
    name: str = ''

    @property
    def host(self) -> str:
        """Alias of :attr:`mirror`."""
        return self.mirror

    @property
    def message(self) -> str:
        """Alias of :attr:`detail`."""
        return self.detail

    def __str__(self) -> str:
        if not self.title:
            # Mirror-level failure (rejected by test_mirrors).
            message = f'{self.mirror}: {self.reason}'
        else:
            message = f'{self.title} @ {self.mirror}: {self.reason}'
        if self.detail:
            message += f' ({self.detail})'
        if self.path:
            message += f' [{self.path}]'
        return message


class SearchResult(tuple):
    """Tuple of hit paths carrying the failure information of the same search.

    It behaves exactly like the plain ``tuple[str]`` that ``search`` and
    ``search_iter`` used to return, so existing code keeps working, while
    :attr:`failures` and :attr:`available_mirrors` expose the diagnostic data.
    """

    def __new__(cls, paths: Iterable[str] = (), failures: Iterable[Failure] = (),
                available_mirrors: Iterable[str] = ()) -> SearchResult:
        self = super().__new__(cls, paths)
        self.failures = tuple(failures)
        self.available_mirrors = MirrorSet(available_mirrors)
        return self

    @property
    def errors(self) -> tuple[Failure, ...]:
        """Alias of :attr:`failures`."""
        return self.failures


class MirrorSet(set):
    """Set of the mirrors usable in this run, kept in :data:`MIRRORS` order.

    It is a real ``set`` (so membership, equality with sets and set operations
    behave as expected) that additionally remembers the probing order:
    iteration and indexing follow :data:`MIRRORS`, which makes :attr:`first`
    the mirror :meth:`TGPhind.urlopen` uses by default.  Nothing in the
    package mutates it.
    """

    def __new__(cls, hosts: Iterable[str] = ()) -> MirrorSet:
        hosts = tuple(dict.fromkeys(hosts))
        self = super().__new__(cls, hosts)
        self._order = hosts
        return self

    @property
    def first(self) -> str:
        """The mirror used by default; this is why the order is preserved."""
        return self._order[0]

    @property
    def ordered(self) -> tuple[str, ...]:
        """The usable mirrors in :data:`MIRRORS` order."""
        return self._order

    def __getitem__(self, index):
        return self._order[index]

    def __iter__(self):
        return iter(self._order)

    def __eq__(self, other) -> bool:
        if isinstance(other, (list, tuple)):
            return self._order == tuple(other)
        return set.__eq__(self, other)

    def __ne__(self, other) -> bool:
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __repr__(self) -> str:
        return f'{type(self).__name__}({list(self._order)!r})'


class NoMirrorsAvailable(URLError, RuntimeError):
    """Reported when no mirror can be used, instead of returning empty results.

    An empty result would be indistinguishable from "the article does not
    exist", so the unusable state is raised explicitly.  ``reason`` (inherited)
    and :attr:`message` carry the per-mirror explanation, :attr:`mirrors` lists
    the mirrors that were considered and :attr:`failures` the probe failures.
    """

    def __init__(self, message: str = 'all mirrors are unavailable',
                 mirrors: Iterable[str] = (), failures: Iterable[Failure] = ()):
        super().__init__(message)
        self.message = message
        self.mirrors = tuple(mirrors)
        self.failures = tuple(failures)

    def __str__(self) -> str:
        return self.message


def _is_timeout(error: BaseException) -> bool:
    if isinstance(error, (TimeoutError, socket.timeout)):
        return True
    text = str(error).lower()
    return 'timed out' in text or 'timeout' in text


# Everything ``urlopen`` may raise for a network problem.  Anything else is a
# programming error and is deliberately left to propagate.
REQUEST_ERRORS = (URLError, HTTPException, OSError, TimeoutError, ValueError)


def _classify_error(error: BaseException) -> tuple[str, str, int | None]:
    """Map an exception raised by ``urlopen`` to ``(reason, detail, status)``.

    ``reason`` is the empty string for a 404/410 response: that is the mirror
    saying "no such article", not a failed request.
    """
    if isinstance(error, HTTPError):
        status = error.code
        detail = f'HTTP {status}: {error.reason}'
        if status in NOT_FOUND_STATUS:
            return '', detail, status
        return REASON_HTTP, detail, status
    if isinstance(error, URLError):
        reason = error.reason
        detail = str(reason) or str(error)
        if _is_timeout(reason) or _is_timeout(error):
            return REASON_TIMEOUT, detail, None
        return REASON_CONNECTION, detail, None
    if _is_timeout(error):
        return REASON_TIMEOUT, str(error) or 'timed out', None
    if isinstance(error, OSError):
        return REASON_CONNECTION, str(error) or type(error).__name__, None
    return REASON_ERROR, f'{type(error).__name__}: {error}', None


@dataclass
class Proxy:
    host: str = ''
    protocol: str = 'https'

    def __bool__(self) -> bool:
        return bool(self.host and self.protocol)


class TGPhind:
    __slots__ = (
        'MAX_TH', 'proxy', 'brackets',
        '__result', '__failures', '__mirrors', '__mirror_failures', '__lock',
        '__dict__',
    )

    def __init__(self, max_th: int | None = None,
                 proxy: Proxy | None = None,
                 brackets='<>'):
        self.__result = ()
        self.__failures = ()
        self.__mirrors = MirrorSet(MIRRORS)
        self.__mirror_failures = ()
        self.__lock = Lock()
        self.MAX_TH = max_th or cpu_count()
        self.proxy = Proxy() if proxy is None else proxy
        self.brackets = brackets

    @property
    def failures(self) -> tuple[Failure, ...]:
        """Failures collected by the most recent search."""
        return self.__failures

    @property
    def errors(self) -> tuple[Failure, ...]:
        """Alias of :attr:`failures`."""
        return self.__failures

    @property
    def available_mirrors(self) -> MirrorSet:
        """Mirrors usable in this run, in :data:`MIRRORS` order."""
        return self.__mirrors

    @property
    def mirror_failures(self) -> tuple[Failure, ...]:
        """Mirrors rejected by the most recent :meth:`test_mirrors`."""
        return self.__mirror_failures

    @property
    def mirror_errors(self) -> dict[str, str]:
        """``{mirror: 'reason: detail'}`` of the mirrors rejected by :meth:`test_mirrors`."""
        return {failure.mirror: (f'{failure.reason}: {failure.detail}' if failure.detail
                                 else failure.reason)
                for failure in self.__mirror_failures}

    def search(self, template: str) -> SearchResult:
        return self.search_iter(parse(template, self.brackets))

    def search_iter(self, names: Iterable[str]) -> SearchResult:
        self.__result = ()
        self.__failures = ()
        if not self.__mirrors:
            raise NoMirrorsAvailable(
                self.__no_mirrors_message(),
                MIRRORS,
                self.__mirror_failures,
            )
        titles = tuple((title, title.lower().translate(TRANSCRIPT)) for title in names)
        args = ()
        for m in range(1, 13):
            for d in range(1, MONTH[m - 1] + 1):
                for title, name in titles:
                    args += (title, name, m, d),
        with ThreadPool(self.MAX_TH) as pool:
            pool.starmap(self.__handle, args)
        self.__failures = tuple(sorted(
            self.__failures,
            key=lambda failure: (failure.title, failure.path, failure.mirror, failure.reason),
        ))
        return SearchResult(sorted(self.__result), self.__failures, self.__mirrors)

    def test_mirrors(self) -> MirrorSet:
        """Probe every entry of :data:`MIRRORS` and return the usable ones.

        The global :data:`MIRRORS` list is never modified: the outcome of the
        probe is stored on the instance and returned to the caller.  A mirror
        that answers the probe is usable, even with 404/410 (that still proves
        the host responds); timeouts, connection errors and other HTTP errors
        make it unusable and are kept in :attr:`mirror_failures`.
        """
        available = []
        failures = []
        for host in tuple(MIRRORS):
            try:
                response = self.urlopen('', host)
            except NoMirrorsAvailable:
                raise
            except REQUEST_ERRORS as error:
                reason, detail, status = _classify_error(error)
                if not reason:
                    # 404/410: the mirror is reachable, it just has no root page.
                    available.append(host)
                    continue
                failures.append(Failure(
                    title='',
                    mirror=host,
                    reason=reason,
                    detail=detail,
                    status=status,
                ))
                continue
            self.__close(response)
            available.append(host)
        mirrors = MirrorSet(available)
        with self.__lock:
            self.__mirrors = mirrors
            self.__mirror_failures = tuple(failures)
        return mirrors

    def __handle(self, title: str, name: str, m: int, d: int):
        base = f'{name}-{m:0>2}-{d:0>2}'
        n = 0
        while True:
            path = base if not n else f'{base}-{n}'
            host = self.__default_mirror()
            try:
                response = self.urlopen(path)
            except NoMirrorsAvailable:
                raise
            except REQUEST_ERRORS as error:
                reason, detail, status = _classify_error(error)
                # A 404/410 is a valid "no such article" answer, not a failure.
                if reason:
                    self.__record_failure(Failure(
                        title=title,
                        name=name,
                        mirror=host,
                        reason=reason,
                        detail=detail,
                        path=path,
                        status=status,
                    ))
                return
            self.__close(response)
            with self.__lock:
                self.__result += path,
            if n >= MAX_PAGES:
                return
            n = 2 if not n else n + 1

    def __record_failure(self, failure: Failure):
        with self.__lock:
            self.__failures += failure,

    def __default_mirror(self) -> str:
        """The first mirror of the current usable set, in :data:`MIRRORS` order."""
        if not self.__mirrors:
            raise NoMirrorsAvailable(
                self.__no_mirrors_message(),
                MIRRORS,
                self.__mirror_failures,
            )
        return self.__mirrors.first

    def __no_mirrors_message(self) -> str:
        if self.__mirror_failures:
            details = '; '.join(
                f'{failure.mirror}: {failure.reason}'
                + (f' ({failure.detail})' if failure.detail else '')
                for failure in self.__mirror_failures
            )
            return f'all mirrors are unavailable ({details})'
        return f'all mirrors are unavailable: {", ".join(MIRRORS)}'

    @staticmethod
    def __close(response) -> None:
        close = getattr(response, 'close', None)
        if callable(close):
            close()

    def urlopen(self, path: str, host=None) -> HTTPResponse:
        if not host:
            host = self.__default_mirror()
        proxy = self.proxy
        protocol = proxy.protocol
        req = Request(join(protocol, host, path), headers={
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/102.0.0.0 Safari/537.36'})
        if proxy:
            req.set_proxy(proxy.host, protocol)
        return urlopen(req, timeout=TIMEOUT)

    def map_hosts(self, results: tuple[str] | tuple = (), protocol: str | None = None, test_mirrors: bool = True
                  ) -> tuple[tuple[str]]:
        mirrors = self.test_mirrors() if test_mirrors else self.__mirrors
        if not mirrors:
            raise NoMirrorsAvailable(
                self.__no_mirrors_message(),
                MIRRORS,
                self.__mirror_failures,
            )
        if not protocol:
            protocol = self.proxy.protocol
        return tuple(tuple(join(protocol, host, name) for host in mirrors) for name in results)
