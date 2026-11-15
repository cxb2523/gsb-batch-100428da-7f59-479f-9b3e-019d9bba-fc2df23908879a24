# -*- coding: utf-8 -*-
"""Testes de aceitação da paginação auditável.

Cada teste sobe um site local com páginas HTML de exemplo, roda o spider
``quotes`` em um subprocesso (projeto ``Cloud``) e confere o feed de citações
junto com o resumo de desfechos por página (``page_outcomes.json``).

Casos cobertos:
* paginação normal até a última página;
* falha em uma página do meio;
* falha já na primeira página;
* retentativa da mesma página não duplica o desfecho.
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from scrapy.http import Request, TextResponse

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from Cloud.spiders import QuotesSpider  # noqa: E402


def quote_page(quotes, next_href=None, title='Quotes'):
    """Monta uma página no mesmo formato do quotes.toscrape.com."""
    blocks = []
    for author, text, tags in quotes:
        tag_links = ''.join(
            '<a class="tag" href="/tag/%s/">%s</a>' % (tag, tag)
            for tag in tags
        )
        blocks.append(
            '<div class="quote">'
            '<span class="text">%s</span>'
            '<span>by <small class="author">%s</small></span>'
            '<div class="tags">%s</div>'
            '</div>' % (text, author, tag_links)
        )
    if next_href:
        pager = (
            '<ul class="pager"><li class="next">'
            '<a href="%s">Next <span aria-hidden="true">&rarr;</span></a>'
            '</li></ul>' % next_href
        )
    else:
        pager = '<ul class="pager"></ul>'
    return (
        '<!DOCTYPE html><html><head><title>%s</title></head><body>'
        '<div class="container">%s%s</div></body></html>'
        % (title, ''.join(blocks), pager)
    )


class LocalSite(object):
    """Servidor HTTP descartável com contadores de requisição por caminho."""

    def __init__(self):
        self.pages = {}
        self.failures = {}
        self.redirects = {}
        self.hits = {}
        self._lock = threading.Lock()
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split('?')[0]
                with site._lock:
                    site.hits[path] = site.hits.get(path, 0) + 1
                    remaining_failures = site.failures.get(path)
                    redirect = site.redirects.get(path)
                if redirect:
                    self.send_response(302)
                    self.send_header('Location', redirect)
                    self.end_headers()
                    return
                if remaining_failures and remaining_failures[1] > 0:
                    with site._lock:
                        remaining_failures[1] -= 1
                    status = remaining_failures[0]
                    self.send_response(status)
                    self.send_header('Content-Type', 'text/html')
                    self.end_headers()
                    self.wfile.write(b'<html><body>failure</body></html>')
                    return
                if path not in site.pages:
                    body = b'<html><body>not found</body></html>'
                    self.send_response(404)
                    self.send_header('Content-Type', 'text/html')
                    self.end_headers()
                    self.wfile.write(body)
                    return
                body = site.pages[path].encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    @property
    def base_url(self):
        return 'http://127.0.0.1:%d' % self.httpd.server_address[1]

    def fail(self, path, status, times=10 ** 9):
        self.failures[path] = [status, times]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def site():
    local = LocalSite()
    try:
        yield local
    finally:
        local.close()


def run_spider(tmp_path, site, start_path='/page/1/', extra_args=None):
    """Executa ``scrapy crawl quotes`` apontando para o site local."""
    feed = tmp_path / 'quotes.jsonl'
    report = tmp_path / 'page_outcomes.json'
    command = [
        sys.executable, '-m', 'scrapy', 'crawl', 'quotes',
        '-a', 'start_url=%s%s' % (site.base_url, start_path),
        '-O', str(feed),
        '-s', 'PAGE_OUTCOMES_FILE=%s' % report,
        '-s', 'ROBOTSTXT_OBEY=False',
        '-s', 'LOG_LEVEL=ERROR',
        '-s', 'DOWNLOAD_DELAY=0',
        '-s', 'RETRY_TIMES=2',
    ]
    if extra_args:
        command.extend(extra_args)
    env = os.environ.copy()
    env['PYTHONPATH'] = str(PROJECT_DIR)
    result = subprocess.run(
        command, cwd=str(PROJECT_DIR), env=env, timeout=180,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    output = result.stdout.decode('utf-8', errors='replace')
    assert result.returncode == 0, 'scrapy falhou:\n%s' % output
    assert report.exists(), 'relatório não foi gerado:\n%s' % output
    items = []
    if feed.exists():
        for line in feed.read_text(encoding='utf-8').splitlines():
            if line.strip():
                items.append(json.loads(line))
    with open(str(report), encoding='utf-8') as handle:
        outcomes = json.load(handle)
    return items, outcomes


def pages_by_number(outcomes):
    return {page['page']: page for page in outcomes['pages']}


def test_crawl_until_last_page(site, tmp_path):
    site.pages['/page/1/'] = quote_page(
        [('Ana', 'Frase 1', ['vida']), ('Bia', 'Frase 2', ['amor'])],
        next_href='/page/2/',
    )
    site.pages['/page/2/'] = quote_page(
        [('Caio', 'Frase 3', ['vida']), ('Duda', 'Frase 4', ['força'])],
        next_href='/page/3/',
    )
    site.pages['/page/3/'] = quote_page(
        [('Eva', 'Frase 5', ['amizade'])],
        next_href=None,
    )

    items, outcomes = run_spider(tmp_path, site)

    assert len(items) == 5
    assert outcomes['termination']['reason'] == 'last_page'
    assert outcomes['page_count'] == 3
    assert outcomes['pages_ok'] == 3
    assert outcomes['pages_failed'] == 0
    assert outcomes['total_quotes'] == 5
    pages = pages_by_number(outcomes)
    assert sorted(pages) == [1, 2, 3]
    assert [pages[n]['quotes'] for n in (1, 2, 3)] == [2, 2, 1]
    assert all(pages[n]['status'] == 'ok' for n in (1, 2, 3))
    assert site.hits.get('/page/4/') is None


def test_middle_page_failure_stops_and_keeps_quotes(site, tmp_path):
    site.pages['/page/1/'] = quote_page(
        [('Ana', 'Frase 1', ['vida']), ('Bia', 'Frase 2', ['amor'])],
        next_href='/page/2/',
    )
    site.pages['/page/2/'] = quote_page(
        [('Caio', 'Frase 3', ['vida'])],
        next_href='/page/3/',
    )
    site.pages['/page/3/'] = quote_page(
        [('Duda', 'Frase 4', ['força'])],
        next_href='/page/4/',
    )
    site.fail('/page/2/', status=500)

    items, outcomes = run_spider(tmp_path, site)

    assert [item['texto'] for item in items] == ['Frase 1', 'Frase 2']
    assert outcomes['termination']['reason'] == 'page_failed'
    assert outcomes['page_count'] == 2
    pages = pages_by_number(outcomes)
    assert pages[1]['status'] == 'ok'
    assert pages[1]['quotes'] == 2
    assert pages[2]['status'] == 'failed'
    assert pages[2]['quotes'] == 0
    assert pages[2]['http_status'] == 500
    assert 3 not in pages
    assert site.hits.get('/page/3/') is None


def test_first_page_failure_has_only_page_one_outcome(site, tmp_path):
    site.pages['/page/1/'] = quote_page(
        [('Ana', 'Frase 1', ['vida'])],
        next_href='/page/2/',
    )
    site.pages['/page/2/'] = quote_page(
        [('Bia', 'Frase 2', ['amor'])],
        next_href=None,
    )
    site.fail('/page/1/', status=503)

    items, outcomes = run_spider(tmp_path, site)

    assert items == []
    assert outcomes['termination']['reason'] == 'page_failed'
    assert outcomes['page_count'] == 1
    pages = pages_by_number(outcomes)
    assert sorted(pages) == [1]
    assert pages[1]['status'] == 'failed'
    assert pages[1]['http_status'] == 503
    assert site.hits.get('/page/2/') is None


def test_retried_page_is_counted_once(site, tmp_path):
    site.pages['/page/1/'] = quote_page(
        [('Ana', 'Frase 1', ['vida'])],
        next_href='/page/2/',
    )
    site.pages['/page/2/'] = quote_page(
        [('Bia', 'Frase 2', ['amor']), ('Caio', 'Frase 3', ['vida'])],
        next_href='/page/3/',
    )
    site.pages['/page/3/'] = quote_page(
        [('Duda', 'Frase 4', ['força'])],
        next_href=None,
    )
    # A página 2 falha duas vezes e só então responde com sucesso.
    site.fail('/page/2/', status=503, times=2)

    items, outcomes = run_spider(tmp_path, site)

    assert len(items) == 4
    pages = pages_by_number(outcomes)
    assert sorted(pages) == [1, 2, 3]
    assert pages[2]['status'] == 'ok'
    assert pages[2]['quotes'] == 2
    assert pages[2]['retries'] == 2
    assert outcomes['total_quotes'] == 4
    assert outcomes['termination']['reason'] == 'last_page'
    # Uma tentativa com falha + duas retentativas, nunca mais que isso.
    assert site.hits['/page/2/'] == 3


def test_first_page_connection_error_is_page_failure(tmp_path):
    """Exceção (host inacessível) na 1ª página também deixa desfecho."""
    dead_url = 'http://127.0.0.1:%d/page/1/' % free_port()
    feed = tmp_path / 'quotes.jsonl'
    report = tmp_path / 'page_outcomes.json'
    command = [
        sys.executable, '-m', 'scrapy', 'crawl', 'quotes',
        '-a', 'start_url=%s' % dead_url,
        '-O', str(feed),
        '-s', 'PAGE_OUTCOMES_FILE=%s' % report,
        '-s', 'ROBOTSTXT_OBEY=False',
        '-s', 'LOG_LEVEL=ERROR',
        '-s', 'RETRY_TIMES=1',
    ]
    env = os.environ.copy()
    env['PYTHONPATH'] = str(PROJECT_DIR)
    result = subprocess.run(
        command, cwd=str(PROJECT_DIR), env=env, timeout=180,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    assert result.returncode == 0, result.stdout.decode('utf-8', 'replace')
    assert report.exists()
    with open(str(report), encoding='utf-8') as handle:
        outcomes = json.load(handle)
    assert outcomes['termination']['reason'] == 'page_failed'
    pages = pages_by_number(outcomes)
    assert sorted(pages) == [1]
    assert pages[1]['status'] == 'failed'
    assert pages[1]['http_status'] is None
    assert 'ConnectionRefusedError' in pages[1]['error']


def test_middle_page_connection_error_stops_after_page_one(site, tmp_path):
    """Falha de conexão na página 2 para a corrente sem tocar na página 3."""
    site.pages['/page/1/'] = quote_page(
        [('Ana', 'Frase 1', ['vida'])],
        next_href='/page/2/',
    )
    site.pages['/page/3/'] = quote_page(
        [('Bia', 'Frase 2', ['amor'])],
        next_href=None,
    )
    # A página 2 responde com redirect para um host inacessível, gerando
    # exceção de conexão na requisição da página 2.
    dead = free_port()
    site.pages['/page/2/'] = (
        '<html><body></body></html>'
    )
    site.redirects = getattr(site, 'redirects', {})
    site.redirects['/page/2/'] = 'http://127.0.0.1:%d/page/2/' % dead

    items, outcomes = run_spider(tmp_path, site)

    assert [item['texto'] for item in items] == ['Frase 1']
    assert outcomes['termination']['reason'] == 'page_failed'
    pages = pages_by_number(outcomes)
    assert pages[1]['status'] == 'ok'
    assert pages[2]['status'] == 'failed'
    assert 'ConnectionRefusedError' in pages[2]['error']
    assert 3 not in pages
    assert site.hits.get('/page/3/') is None


def free_port():
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_empty_page_is_ok_with_zero_quotes(site, tmp_path):
    """Página sem citações ainda é uma página bem-sucedida (0 citações)."""
    site.pages['/page/1/'] = quote_page(
        [('Ana', 'Frase 1', ['vida'])],
        next_href='/page/2/',
    )
    site.pages['/page/2/'] = quote_page([], next_href=None)

    items, outcomes = run_spider(tmp_path, site)

    assert len(items) == 1
    pages = pages_by_number(outcomes)
    assert pages[2]['status'] == 'ok'
    assert pages[2]['quotes'] == 0
    assert outcomes['termination']['reason'] == 'last_page'
    assert outcomes['total_quotes'] == 1


def test_next_link_loop_is_detected(site, tmp_path):
    """Um link "next" que repete uma URL já visitada não é re-requisitado."""
    site.pages['/page/1/'] = quote_page(
        [('Ana', 'Frase 1', ['vida'])],
        next_href='/page/2/',
    )
    site.pages['/page/2/'] = quote_page(
        [('Bia', 'Frase 2', ['amor'])],
        next_href='/page/1/',
    )

    items, outcomes = run_spider(tmp_path, site)

    assert len(items) == 2
    assert outcomes['termination']['reason'] == 'loop_detected'
    pages = pages_by_number(outcomes)
    assert sorted(pages) == [1, 2]
    assert all(page['status'] == 'ok' for page in pages.values())
    assert outcomes['total_quotes'] == 2
    assert site.hits['/page/1/'] == 1


def test_duplicate_delivery_of_same_page_is_ignored():
    """Uma mesma página entregue duas vezes não duplica o desfecho."""
    html = quote_page(
        [('Ana', 'Frase 1', ['vida']), ('Bia', 'Frase 2', ['amor'])],
        next_href='/page/2/',
    )
    spider = QuotesSpider()
    request = Request('http://127.0.0.1/page/1/', meta={'page': 1})
    response = TextResponse(
        url=request.url, body=html.encode('utf-8'), encoding='utf-8',
        request=request,
    )

    first = list(spider.parse(response))
    second = list(spider.parse(response))

    assert second == []
    assert spider.page_outcomes[1]['quotes'] == 2
    assert len(spider.page_outcomes) == 1
    requests = [item for item in first if isinstance(item, Request)]
    assert len(requests) == 1
    assert requests[0].meta['page'] == 2
    assert requests[0].url.endswith('/page/2/')
    assert spider.termination_reason is None
