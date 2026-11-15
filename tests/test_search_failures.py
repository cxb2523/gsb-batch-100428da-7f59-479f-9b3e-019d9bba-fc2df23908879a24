"""Tests for the search failure reporting and mirror handling.

They use only the standard library: every probe is served by a controllable
``urlopen`` double, so no external network access is required.

Run with::

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from unittest import mock

import tgphind
from tgphind import Failure, MirrorSet, NoMirrorsAvailable, SearchResult, TGPhind

OK = object()
MISSING = object()


class FakeResponse:
    def __init__(self, url: str):
        self.url = url
        self.closed = False

    def close(self):
        self.closed = True


class Server:
    """Controllable replacement for ``tgphind.urlopen``.

    ``rules`` maps ``(host, path)`` to ``OK``, ``MISSING``, an HTTP status code
    or an exception to raise.  Unlisted pairs use ``default``.
    """

    def __init__(self, rules=None, default=MISSING):
        self.rules = dict(rules or {})
        self.default = default
        self.calls = []

    def __call__(self, request, timeout=None):
        parts = urlsplit(request.full_url)
        host, path = parts.netloc.lower(), parts.path.lstrip('/')
        self.calls.append((host, path))
        rule = self.rules.get((host, path), self.default)
        if isinstance(rule, BaseException):
            raise rule
        if rule is MISSING:
            raise HTTPError(request.full_url, 404, 'Not Found', {}, None)
        if isinstance(rule, int):
            raise HTTPError(request.full_url, rule, f'HTTP {rule}', {}, None)
        return FakeResponse(request.full_url)

    @property
    def probed(self):
        return list(self.calls)


TIMEOUT = URLError(TimeoutError('timed out'))
REFUSED = URLError(ConnectionRefusedError(10061, 'connection refused'))


def mirror_rules(up):
    """Build probe rules: ``up`` is the iterable of reachable mirrors."""
    rules = {}
    for host in tgphind.MIRRORS:
        rules[(host, '')] = OK if host in up else REFUSED
    return rules


class SearchTest(unittest.TestCase):
    def make(self, rules=None, default=MISSING):
        server = Server(rules, default)
        patcher = mock.patch.object(tgphind, 'urlopen', server)
        patcher.start()
        self.addCleanup(patcher.stop)
        return TGPhind(4), server

    def test_hits_are_returned_like_before(self):
        se, _ = self.make({
            ('te.legra.ph', 'a-01-01'): OK,
            ('te.legra.ph', 'a-01-01-2'): OK,
        })
        result = se.search_iter(['a'])
        self.assertIsInstance(result, SearchResult)
        self.assertIsInstance(result, tuple)
        self.assertEqual(tuple(result), ('a-01-01', 'a-01-01-2'))
        self.assertEqual(result.failures, ())

    def test_failure_has_title_mirror_and_reason(self):
        se, _ = self.make({('te.legra.ph', 'a-01-01'): TIMEOUT})
        result = se.search_iter(['a'])
        self.assertEqual(tuple(result), ())
        self.assertEqual(len(result.failures), 1)
        failure = result.failures[0]
        self.assertEqual(
            (failure.title, failure.mirror, failure.reason),
            ('a', 'te.legra.ph', tgphind.REASON_TIMEOUT),
        )
        self.assertIn('timed out', failure.detail)

    def test_one_failure_does_not_stop_other_titles(self):
        se, _ = self.make({
            ('te.legra.ph', 'bad-01-01'): TIMEOUT,
            ('te.legra.ph', 'good-01-01'): OK,
        })
        result = se.search_iter(['bad', 'good'])
        self.assertIn('good-01-01', result)
        self.assertEqual([f.title for f in result.failures], ['bad'])

    def test_reason_categories(self):
        se, _ = self.make({
            ('te.legra.ph', 'to-01-01'): TIMEOUT,
            ('te.legra.ph', 'cn-01-01'): REFUSED,
            ('te.legra.ph', 'ht-01-01'): 503,
        })
        result = se.search_iter(['to', 'cn', 'ht'])
        self.assertEqual({f.title: f.reason for f in result.failures}, {
            'to': tgphind.REASON_TIMEOUT,
            'cn': tgphind.REASON_CONNECTION,
            'ht': tgphind.REASON_HTTP,
        })

    def test_404_is_a_negative_answer(self):
        se, _ = self.make()
        result = se.search_iter(['a'])
        self.assertEqual(tuple(result), ())
        self.assertEqual(result.failures, ())

    def test_numbered_chain_stops_on_failure_keeping_hits(self):
        se, server = self.make({
            ('te.legra.ph', 'a-01-01'): OK,
            ('te.legra.ph', 'a-01-01-2'): OK,
            ('te.legra.ph', 'a-01-01-3'): TIMEOUT,
        })
        result = se.search_iter(['a'])
        self.assertEqual(tuple(result), ('a-01-01', 'a-01-01-2'))
        self.assertEqual([f.path for f in result.failures], ['a-01-01-3'])
        self.assertNotIn('a-01-01-4', [path for _, path in server.probed])


class MirrorTest(unittest.TestCase):
    def make(self, rules):
        server = Server(rules)
        patcher = mock.patch.object(tgphind, 'urlopen', server)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, tgphind, 'MIRRORS', list(tgphind.MIRRORS))
        return TGPhind(4), server

    def test_global_mirrors_are_not_modified(self):
        se, _ = self.make(mirror_rules({'graph.org', 'telegra.ph'}))
        original = list(tgphind.MIRRORS)
        mirrors = se.test_mirrors()
        self.assertEqual(tgphind.MIRRORS, original)
        self.assertEqual(mirrors, {'graph.org', 'telegra.ph'})

    def test_first_available_mirror_is_default(self):
        se, _ = self.make(mirror_rules({'graph.org', 'telegra.ph'}))
        mirrors = se.test_mirrors()
        self.assertEqual(mirrors.first, 'graph.org')
        self.assertEqual(list(mirrors), ['graph.org', 'telegra.ph'])
        self.assertEqual(se.available_mirrors, mirrors)

    def test_search_uses_first_available_mirror_only(self):
        rules = mirror_rules({'graph.org', 'telegra.ph'})
        rules[('graph.org', 'a-01-01')] = OK
        se, server = self.make(rules)
        se.test_mirrors()
        server.calls.clear()
        result = se.search_iter(['a'])
        self.assertEqual(result.available_mirrors.first, 'graph.org')
        self.assertIn(('graph.org', 'a-01-01'), server.probed)
        self.assertNotIn('te.legra.ph', [host for host, _ in server.calls])

    def test_all_mirrors_down_is_reported(self):
        se, _ = self.make(mirror_rules(set()))
        self.assertEqual(se.test_mirrors(), set())
        self.assertEqual(len(se.mirror_failures), len(tgphind.MIRRORS))
        with self.assertRaises(NoMirrorsAvailable) as ctx:
            se.search_iter(['a'])
        message = str(ctx.exception)
        self.assertIn('all mirrors are unavailable', message)
        for host in tgphind.MIRRORS:
            self.assertIn(host, message)
        # still an URLError, so legacy handlers keep working
        self.assertIsInstance(ctx.exception, URLError)

    def test_urlopen_without_mirrors_reports(self):
        se, _ = self.make(mirror_rules(set()))
        se.test_mirrors()
        with self.assertRaises(NoMirrorsAvailable):
            se.urlopen('x')


class CliTest(unittest.TestCase):
    def run_cli(self, server, argv):
        import tgphind.__main__ as cli
        patcher = mock.patch.object(tgphind, 'urlopen', server)
        patcher.start()
        self.addCleanup(patcher.stop)
        argv_patcher = mock.patch.object(sys, 'argv', ['tgphind', *argv])
        argv_patcher.start()
        self.addCleanup(argv_patcher.stop)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main()
        return code, out.getvalue(), err.getvalue()

    def test_failure_summary_goes_to_stderr(self):
        rules = mirror_rules(set(tgphind.MIRRORS))
        rules[('te.legra.ph', 'good-01-01')] = OK
        rules[('te.legra.ph', 'bood-01-01')] = TIMEOUT
        code, out, err = self.run_cli(Server(rules), ['<gb>ood', '-tc', '2'])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], 'te.legra.ph, graph.org, telegra.ph')
        self.assertEqual(lines[1], '')
        self.assertIn('good-01-01', lines)
        self.assertIn('Failed probes', err)
        self.assertIn('bood', err)
        self.assertIn('te.legra.ph', err)
        self.assertIn('timeout', err)

    def test_no_failure_summary_on_success(self):
        rules = mirror_rules(set(tgphind.MIRRORS))
        rules[('te.legra.ph', 'article-01-01')] = OK
        code, out, err = self.run_cli(Server(rules), ['article', '-tc', '2'])
        self.assertEqual(code, 0)
        self.assertIn('article-01-01', out.splitlines())
        self.assertEqual(err, '')

    def test_all_mirrors_down_is_explicit(self):
        server = Server(mirror_rules(set()))
        code, out, err = self.run_cli(server, ['article', '-tc', '2'])
        self.assertNotEqual(code, 0)
        self.assertIn('all mirrors are unavailable', err)
        self.assertEqual(out.strip(), '')


class TypesTest(unittest.TestCase):
    def test_search_result_is_a_tuple(self):
        result = SearchResult(['b', 'a'])
        self.assertEqual(result, ('b', 'a'))
        self.assertEqual(result[0], 'b')
        self.assertEqual(result.failures, ())

    def test_failure_is_a_named_tuple(self):
        failure = Failure('t', 'm', 'timeout', 'boom', 'p', None, 't')
        self.assertEqual(failure.title, 't')
        self.assertEqual(failure.mirror, 'm')
        self.assertEqual(tuple(failure), ('t', 'm', 'timeout', 'boom', 'p', None, 't'))

    def test_mirror_set_behaves_like_a_set(self):
        mirrors = MirrorSet(['b', 'a'])
        self.assertEqual(mirrors, {'a', 'b'})
        self.assertIn('a', mirrors)
        self.assertEqual(list(mirrors), ['b', 'a'])
        self.assertFalse(MirrorSet(()))


if __name__ == '__main__':
    unittest.main()
