# emacs: -*- mode: python; py-indent-offset: 4; tab-width: 4; indent-tabs-mode: nil -*-
# ex: set sts=4 ts=4 sw=4 noet:
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
#
#   See COPYING file distributed along with the datalad package for the
#   copyright and license terms.
#
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
# now with some recursive structure of directories

import socket
from contextlib import contextmanager
from glob import glob
from http.server import HTTPServer, BaseHTTPRequestHandler
from os.path import exists, join as opj
from threading import Thread
from datalad.tests.utils_pytest import eq_, ok_
from datalad.tests.utils_pytest import serve_path_via_http, with_tree

from datalad.tests.utils_pytest import skip_if_scrapy_without_selector
skip_if_scrapy_without_selector()

from ..crawl_url import crawl_url
from ..crawl_url import parse_checksums
from ..matches import a_href_match
from ...pipeline import run_pipeline
from datalad.tests.utils_pytest import (
    assert_equal,
    assert_false,
    assert_in,
    assert_not_in,
    assert_raises,
    assert_true,
    patch_config,
)
from datalad.utils import updated

pages_loop = dict(
    tree=(
        ('index.html', """<html><body>
                            <a href="page2.html">mypage2</a>
                            <a href="page4.html">mypage4</a>
                          </body></html>"""),
        ('page2.html', '<html><body><a href="page3.html">mypage3</a></body></html>'),
        ('page3.html', '<html><body><a href="/">root</a></body></html>'),
        ('page4.html', '<html><body><a href="page5.html">mypage5</a></body></html>'),
        ('page5.html', '<html><body><a href="page3.html">mypage3</a></body></html>'),
    )
)

@with_tree(**pages_loop)
@serve_path_via_http()
def test_recurse_loop_http(path=None, url=None):
    def visit(url, matchers):
        return sorted((d['url'].replace(url, '')
                       for d in crawl_url(url, matchers=matchers)()))

    target_pages = ['', 'page2.html', 'page3.html', 'page4.html', 'page5.html']
    eq_(visit(url, [a_href_match('.*')]), target_pages)

    # test recursive loop as implemented by pipeline
    crawler = crawl_url(url)
    pipeline = [
        {'output': 'outputs'},  # just to simplify testing here
        crawler,
        [
            {'loop': True, 'output': 'input+outputs'},
            a_href_match('.*'),
            crawler.recurse
        ]
    ]
    pipeline_results = run_pipeline(pipeline)
    eq_(sorted([d['url'].replace(url, '') for d in pipeline_results]), target_pages)


def test_parse_checksums():
    response = """\
abc  f1.txt
bcd  d1/f1.txt
"""
    out = list(parse_checksums(digest='md5')(
            {'response': response,
            'url': 'http://example.com/subdir/checksums.md5'}))
    assert_equal(len(out), 2)
    assert_equal(out[0]['path'], '')
    assert_equal(out[0]['filename'], 'f1.txt')
    assert_equal(out[0]['url'], 'http://example.com/subdir/f1.txt')

    assert_equal(out[1]['path'], 'd1')
    assert_equal(out[1]['filename'], 'f1.txt')
    assert_equal(out[1]['url'], 'http://example.com/subdir/d1/f1.txt')


#
# Redirect tracking
#

# mapping of path -> (status code, Location header or None, body)
REDIRECT_PAGES = {
    '/start.html': (302, '/hop1.html', None),
    '/hop1.html': (302, '/hop2.html', None),
    '/hop2.html': (200, None,
                   '<html><body>final destination'
                   '<a href="plain.html">plain</a>'
                   '</body></html>'),
    '/plain.html': (200, None, '<html><body>no redirects here</body></html>'),
    '/missing.html': (404, None, 'not found'),
    # a redirect pointing at itself must never cause an infinite loop
    '/loop.html': (302, '/loop.html', None),
}


class _RedirectHandler(BaseHTTPRequestHandler):
    """Serves a small, fully local set of pages including redirects"""

    # paths starting with this prefix always redirect to the next number,
    # i.e. an endless redirect chain
    endless_prefix = '/endless'

    def log_message(self, *args):
        # keep the test output clean
        pass

    def do_HEAD(self):
        self._respond(include_body=False)

    def do_GET(self):
        self._respond(include_body=True)

    def _respond(self, include_body):
        if self.path.startswith(self.endless_prefix):
            n = int(self.path[len(self.endless_prefix):] or 0)
            self.send_response(302)
            self.send_header('Location', '%s%d' % (self.endless_prefix, n + 1))
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            return
        spec = REDIRECT_PAGES.get(self.path)
        if spec is None:
            self.send_response(404)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            if include_body:
                self.wfile.write(b'not found')
            return
        status, location, body = spec
        self.send_response(status)
        if location is not None:
            # relative redirect, resolved against this same server
            self.send_header('Location', location)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        if include_body and body is not None:
            self.wfile.write(body.encode('utf-8'))


@contextmanager
def redirect_http_server():
    """Start a local HTTP server with multi-hop redirect pages

    Yields
    -------
    baseurl : str
    """
    httpd = HTTPServer(('127.0.0.1', 0), _RedirectHandler)
    thread = Thread(target=httpd.serve_forever)
    thread.daemon = True
    thread.start()
    try:
        yield 'http://127.0.0.1:%d' % httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _free_port_url():
    """A url pointing at a port with nothing listening on it"""
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return 'http://127.0.0.1:%d/unreachable.html' % port


def test_redirect_tracking_chain():
    # multi-hop redirect chain is reported in order, with the hop count
    with redirect_http_server() as baseurl:
        start = baseurl + '/start.html'
        results = list(crawl_url(start, track_redirects=True)())
        assert_equal(len(results), 1)
        result = results[0]
        assert_equal(result['redirect_count'], 2)
        assert_equal(result['redirects'],
                     [start,
                      baseurl + '/hop1.html',
                      baseurl + '/hop2.html'])
        # final page/url behavior is unchanged otherwise
        assert_equal(result['url'], baseurl + '/hop2.html')
        assert_in('final destination', result['response'])

        # a fresh crawler with a populated redirects cache hits the cache
        # on its first visit: the chain must still contain the original
        # url followed by the cached target url
        cached_crawler = crawl_url(start, track_redirects=True)
        cached_crawler._redirects_cache[start] = baseurl + '/hop2.html'
        cached = list(cached_crawler({'url': start}))
        assert_equal(len(cached), 1)
        assert_equal(cached[0]['redirects'],
                     [start, baseurl + '/hop2.html'])
        assert_equal(cached[0]['redirect_count'], 1)
        assert_equal(cached[0]['url'], baseurl + '/hop2.html')

        # dedup behavior is unchanged: revisiting an already seen url with
        # the same crawler still produces nothing
        revisit = crawl_url(start, track_redirects=True)
        assert_equal(len(list(revisit({'url': start}))), 1)
        assert_equal(list(revisit({'url': start})), [])


def test_redirect_tracking_no_redirect():
    # without any redirect the chain contains just the original url and
    # the count is zero
    with redirect_http_server() as baseurl:
        url = baseurl + '/plain.html'
        result = list(crawl_url(url, track_redirects=True)())
        assert_equal(len(result), 1)
        assert_equal(result[0]['redirects'], [url])
        assert_equal(result[0]['redirect_count'], 0)
        assert_equal(result[0]['url'], url)


def test_redirect_tracking_failed():
    # a failing url yields a failure record instead of nothing
    with redirect_http_server() as baseurl:
        missing = baseurl + '/missing.html'
        results = list(crawl_url(missing, failed='skip',
                                 track_redirects=True)())
        assert_equal(len(results), 1)
        record = results[0]
        assert_equal(record['status'], 'failed')
        assert_equal(record['url'], missing)
        ok_(record['error'])
        assert_equal(record['redirects'], [missing])
        assert_equal(record['redirect_count'], 0)

        # default failed=None must behave the same: skipped but reported
        results = list(crawl_url(missing, track_redirects=True)())
        assert_equal(len(results), 1)
        assert_equal(results[0]['status'], 'failed')
        assert_equal(results[0]['url'], missing)

        # a redirect to the same url is a failure, not an infinite loop
        loop = baseurl + '/loop.html'
        results = list(crawl_url(loop, failed='skip',
                                 track_redirects=True)())
        assert_equal(len(results), 1)
        assert_equal(results[0]['status'], 'failed')
        assert_equal(results[0]['url'], loop)
        ok_(results[0]['error'])

    # an unreachable address must also produce a failure record
    unreachable = _free_port_url()
    with patch_config({'datalad.downloaders.retry': 0}):
        results = list(crawl_url(unreachable, failed='skip',
                                 track_redirects=True)())
    assert_equal(len(results), 1)
    assert_equal(results[0]['status'], 'failed')
    assert_equal(results[0]['url'], unreachable)
    ok_(results[0]['error'])


def test_redirect_tracking_failed_raises():
    # any other value of 'failed' keeps the original raising behavior
    with redirect_http_server() as baseurl:
        missing = baseurl + '/missing.html'
        with assert_raises(Exception):
            list(crawl_url(missing, failed='raise',
                           track_redirects=True)())

        loop = baseurl + '/loop.html'
        with assert_raises(Exception):
            list(crawl_url(loop, failed='raise',
                           track_redirects=True)())


def test_redirect_tracking_disabled_by_default():
    # by default nothing changes: no new keys, failures stay silent
    with redirect_http_server() as baseurl:
        result = list(crawl_url(baseurl + '/start.html')())
        assert_equal(len(result), 1)
        assert_not_in('redirects', result[0])
        assert_not_in('redirect_count', result[0])
        assert_not_in('status', result[0])

        missing = baseurl + '/missing.html'
        assert_equal(list(crawl_url(missing, failed='skip')()), [])
        # explicit opt-out yields the same data
        result = list(crawl_url(baseurl + '/plain.html',
                                track_redirects=False)())
        assert_not_in('redirects', result[0])
        assert_not_in('redirect_count', result[0])


def test_redirect_tracking_loop_guard():
    # redirect loops must be reported as failures and must never spin
    # forever: both a redirect to the same url and an endless chain
    with redirect_http_server() as baseurl:
        for loop_url in (baseurl + '/loop.html',
                         baseurl + '/endless0'):
            results = list(crawl_url(loop_url, failed='skip',
                                     track_redirects=True,
                                     cache_redirects=False)())
            assert_equal(len(results), 1)
            record = results[0]
            assert_equal(record['status'], 'failed')
            assert_equal(record['url'], loop_url)
            ok_(record['error'])
            # the chain never repeats an url
            assert_equal(len(record['redirects']),
                         len(set(record['redirects'])))
            # and it stays well below the 100 manual-redirect ceiling
            assert_true(record['redirect_count'] <= 100)

            # with failure raising, the exception still propagates
            with assert_raises(Exception):
                list(crawl_url(loop_url, failed='raise',
                               track_redirects=True,
                               cache_redirects=False)())


def test_redirect_tracking_keeps_recursion_and_dedup():
    # tracking must not alter recursive crawling: a matcher discovered on
    # the final page of a redirect chain is still followed and every
    # produced record carries its own chain
    with redirect_http_server() as baseurl:
        crawler = crawl_url(baseurl + '/start.html',
                            matchers=[a_href_match('.*')],
                            track_redirects=True)
        results = list(crawler())
        by_url = {r['url']: r for r in results}
        # the redirected start page and the page it links to were visited
        assert_in(baseurl + '/hop2.html', by_url)
        assert_in(baseurl + '/plain.html', by_url)
        # each record carries its own, correctly sized chain
        assert_equal(by_url[baseurl + '/hop2.html']['redirect_count'], 2)
        assert_equal(by_url[baseurl + '/plain.html']['redirects'],
                     [baseurl + '/plain.html'])
        assert_equal(by_url[baseurl + '/plain.html']['redirect_count'], 0)
        # no failure records: the link was valid
        assert_false(any(r.get('status') == 'failed' for r in results))

        # seen-dedup still short-circuits a repeated top-level visit
        assert_equal(list(crawler()), [])
