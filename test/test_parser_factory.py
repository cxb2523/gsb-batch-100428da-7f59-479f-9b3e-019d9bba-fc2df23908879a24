# coding: utf-8
"""Offline tests for the parser_factory dispatch chain.

Every test builds its own response objects, so no network access is needed.
"""
import os
from unittest import TestCase, mock
from urllib.parse import urljoin

import requests

import page_content_extractor
from page_content_extractor import ContentFormat, ParserType, parser_factory
from page_content_extractor.html import HtmlContentExtractor
from page_content_extractor.pdf import PdfExtractor

ARTICLE_URL = 'https://example.com/posts/hello'
JINA_URL = page_content_extractor.jina_prefix + ARTICLE_URL

HTML_BODY = b'''<!DOCTYPE html>
<html>
<head><title>Hello</title><link rel="shortcut icon" href="/favicon.ico"></head>
<body><article><p>%s</p><img src="images/cover.png"></article></body>
</html>''' % (b'The quick brown fox jumps over the lazy dog. ' * 20)

PDF_PATH = os.path.join(os.path.dirname(__file__), 'fixtures/cpi.pdf')
with open(PDF_PATH, 'rb') as _fp:
    PDF_BODY = _fp.read()


class FakeResponse(object):
    def __init__(self, content=b'', headers=None, url='', history=None):
        if isinstance(content, str):
            content = content.encode('utf-8')
        self.content = content
        self.headers = headers or {}
        self.url = url
        self.history = list(history or [])
        self.status_code = 200

    @property
    def text(self):
        return self.content.decode('utf-8', 'replace')

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.RequestException('HTTP %s' % self.status_code)


class FakeSession(object):
    """Maps url -> FakeResponse or exception, and records the calls."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append((url, headers))
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


class ParserFactoryDispatchTestCase(TestCase):
    def factory(self, url, responses, **kwargs):
        fake = FakeSession(responses)
        with mock.patch.object(page_content_extractor, 'session', fake):
            parser = parser_factory(url, **kwargs)
        return parser, fake

    def assertNoJinaLeak(self, parser, expected_url=ARTICLE_URL):
        self.assertEqual(parser.final_url, expected_url)
        self.assertEqual(parser.url, expected_url)
        self.assertNotIn('r.jina.ai', parser.url)
        self.assertNotIn('r.jina.ai', parser.get_favicon_url())
        # Relative image paths are resolved against the article, not the proxy
        for img in parser.article.find_all('img') if hasattr(parser, 'article') else []:
            self.assertNotIn('r.jina.ai', img.get('src', ''))

    def test_html_with_content_type(self):
        resp = FakeResponse(HTML_BODY, {'content-type': 'text/html; charset=utf-8'},
                            ARTICLE_URL)
        parser, fake = self.factory(ARTICLE_URL, {ARTICLE_URL: resp})

        result = parser.parse_result
        self.assertEqual(result.parser_type, ParserType.HTML)
        self.assertEqual(result.final_url, ARTICLE_URL)
        self.assertEqual(result.requested_url, ARTICLE_URL)
        self.assertFalse(result.via_jina)
        self.assertFalse(result.content_type_missing)
        self.assertFalse(result.content_type_mismatch)
        self.assertFalse(result.redirected)
        self.assertEqual(result.sniffed_format, ContentFormat.HTML)
        self.assertEqual(len(result.attempts), 1)
        self.assertTrue(parser.get_content().startswith('The quick brown fox'))
        self.assertNoJinaLeak(parser)
        self.assertEqual(fake.calls, [(ARTICLE_URL, None)])

    def test_follows_redirect_in_final_url(self):
        final = 'https://www.example.com/posts/hello'
        # requests puts the hop urls (those that answered with a redirect) in history
        history = [FakeResponse(url=ARTICLE_URL)]
        resp = FakeResponse(HTML_BODY, {'content-type': 'text/html'}, final, history)
        parser, _ = self.factory(ARTICLE_URL, {ARTICLE_URL: resp})

        result = parser.parse_result
        self.assertEqual(result.final_url, final)
        self.assertEqual(result.response_url, final)
        self.assertEqual(result.requested_url, ARTICLE_URL)
        self.assertTrue(result.redirected)
        self.assertEqual(result.redirects, (ARTICLE_URL,))
        # Relative favicon resolution must use the redirect target
        self.assertEqual(parser.get_favicon_url(), 'https://www.example.com/favicon.ico')

    def test_missing_content_type_html(self):
        resp = FakeResponse(HTML_BODY, {}, ARTICLE_URL)
        parser, _ = self.factory(ARTICLE_URL, {ARTICLE_URL: resp})

        result = parser.parse_result
        self.assertEqual(result.parser_type, ParserType.HTML)
        self.assertTrue(result.content_type_missing)
        self.assertEqual(result.declared_content_type, None)
        self.assertEqual(result.sniffed_format, ContentFormat.HTML)
        self.assertFalse(result.content_type_mismatch)
        self.assertTrue(any('missing' in note for note in result.notes))

    def test_missing_content_type_pdf_still_prefers_pdf(self):
        resp = FakeResponse(PDF_BODY, {}, ARTICLE_URL)
        parser, _ = self.factory(ARTICLE_URL, {ARTICLE_URL: resp})

        result = parser.parse_result
        self.assertEqual(result.parser_type, ParserType.PDF)
        self.assertTrue(result.content_type_missing)
        self.assertEqual(result.sniffed_format, ContentFormat.PDF)
        self.assertIsInstance(parser, PdfExtractor)
        self.assertTrue(parser.get_content().startswith('Systems code is often written'))

    def test_pdf_content_type_with_charset(self):
        resp = FakeResponse(PDF_BODY, {'content-type': 'application/pdf; charset=utf-8'},
                            ARTICLE_URL)
        parser, _ = self.factory(ARTICLE_URL, {ARTICLE_URL: resp})

        self.assertEqual(parser.parse_result.parser_type, ParserType.PDF)
        self.assertFalse(parser.parse_result.content_type_mismatch)
        self.assertIsInstance(parser, PdfExtractor)

    def test_html_header_but_pdf_body(self):
        resp = FakeResponse(PDF_BODY, {'content-type': 'text/html'}, ARTICLE_URL)
        parser, _ = self.factory(ARTICLE_URL, {ARTICLE_URL: resp})

        result = parser.parse_result
        self.assertEqual(result.parser_type, ParserType.PDF)
        self.assertTrue(result.content_type_mismatch)
        self.assertEqual(result.declared_content_type, 'text/html')
        self.assertEqual(result.sniffed_format, ContentFormat.PDF)
        self.assertTrue(any('disagrees' in note for note in result.notes))
        self.assertIsInstance(parser, PdfExtractor)

    def test_pdf_header_but_html_body_falls_back_to_html(self):
        resp = FakeResponse(HTML_BODY, {'content-type': 'application/pdf'}, ARTICLE_URL)
        parser, _ = self.factory(ARTICLE_URL, {ARTICLE_URL: resp})

        result = parser.parse_result
        self.assertEqual(result.parser_type, ParserType.HTML)
        self.assertTrue(result.content_type_mismatch)
        self.assertEqual(result.sniffed_format, ContentFormat.HTML)
        self.assertIsInstance(parser, HtmlContentExtractor)
        self.assertTrue(any('fell back to the html parser' in note for note in result.notes))

    def test_plain_text_body(self):
        body = b'Just a plain text article, no markup at all. ' * 10
        resp = FakeResponse(body, {'content-type': 'text/plain'}, ARTICLE_URL)
        parser, _ = self.factory(ARTICLE_URL, {ARTICLE_URL: resp})

        result = parser.parse_result
        self.assertEqual(result.parser_type, ParserType.HTML)
        self.assertFalse(result.content_type_mismatch)
        self.assertEqual(result.sniffed_format, ContentFormat.TEXT)
        self.assertIn('plain text article', parser.get_content())

    def test_unknown_content_type_with_binary_body_raises(self):
        resp = FakeResponse(b'\x00\x01\x02\x03binary', {'content-type': 'image/png'},
                            ARTICLE_URL)
        with self.assertRaises(TypeError):
            self.factory(ARTICLE_URL, {ARTICLE_URL: resp})

    def test_pdf_header_with_binary_body_keeps_the_pdf_branch(self):
        # A real pdf is binary, so a pdf header over binary bytes is not a mismatch
        resp = FakeResponse(b'\x00\x01\x02\x03binary', {'content-type': 'application/pdf'},
                            ARTICLE_URL)
        parser, _ = self.factory(ARTICLE_URL, {ARTICLE_URL: resp})

        result = parser.parse_result
        self.assertEqual(result.parser_type, ParserType.PDF)
        self.assertFalse(result.content_type_mismatch)
        self.assertEqual(result.sniffed_format, ContentFormat.BINARY)

    def test_empty_jina_response_does_not_retry_again(self):
        empty = FakeResponse(b'', {'content-type': 'text/html'}, ARTICLE_URL)
        also_empty = FakeResponse(b'', {'content-type': 'text/html'}, JINA_URL)
        parser, fake = self.factory(ARTICLE_URL, {ARTICLE_URL: empty, JINA_URL: also_empty})

        self.assertEqual(len(fake.calls), 2)  # one retry only, no recursion
        result = parser.parse_result
        self.assertTrue(result.via_jina)
        self.assertEqual(result.final_url, ARTICLE_URL)
        self.assertIsInstance(parser, HtmlContentExtractor)

    def test_empty_html_retries_through_jina_and_restores_url(self):
        empty = FakeResponse(b'', {'content-type': 'text/html'}, ARTICLE_URL)
        proxied = FakeResponse(HTML_BODY, {'content-type': 'text/html'}, JINA_URL)
        parser, fake = self.factory(ARTICLE_URL, {ARTICLE_URL: empty, JINA_URL: proxied})

        result = parser.parse_result
        self.assertTrue(result.via_jina)
        self.assertEqual(result.parser_type, ParserType.HTML)
        self.assertEqual(result.final_url, ARTICLE_URL)
        self.assertEqual(result.requested_url, ARTICLE_URL)
        self.assertEqual(result.response_url, JINA_URL)
        self.assertEqual(result.sniffed_format, ContentFormat.HTML)
        self.assertEqual(len(result.attempts), 2)
        self.assertFalse(result.attempts[0].via_jina)
        self.assertTrue(result.attempts[1].via_jina)
        self.assertEqual(result.attempts[1].url, JINA_URL)
        # The proxy address must not leak into favicon/image/cache-key resolution
        self.assertNoJinaLeak(parser)
        self.assertTrue(parser.get_content().startswith('The quick brown fox'))
        self.assertEqual(fake.calls[1], (JINA_URL, {'x-respond-with': 'html'}))

    def test_jina_retry_not_triggered_for_non_empty_html(self):
        resp = FakeResponse(HTML_BODY, {'content-type': 'text/html'}, ARTICLE_URL)
        parser, fake = self.factory(ARTICLE_URL, {ARTICLE_URL: resp})
        self.assertFalse(parser.parse_result.via_jina)
        self.assertEqual(len(fake.calls), 1)

    def test_jina_retry_failure_keeps_original_parser(self):
        empty = FakeResponse(b'', {'content-type': 'text/html'}, ARTICLE_URL)
        parser, _ = self.factory(
            ARTICLE_URL, {ARTICLE_URL: empty, JINA_URL: RuntimeError('proxy down')})

        result = parser.parse_result
        self.assertFalse(result.via_jina)
        self.assertEqual(result.final_url, ARTICLE_URL)
        self.assertIsInstance(parser, HtmlContentExtractor)
        self.assertTrue(any('jina retry failed' in note for note in result.notes))

    def test_jina_flags_a_missing_content_type_in_the_result(self):
        empty = FakeResponse(b'', {'content-type': 'text/html'}, ARTICLE_URL)
        proxied = FakeResponse(HTML_BODY, {}, JINA_URL)
        parser, _ = self.factory(ARTICLE_URL, {ARTICLE_URL: empty, JINA_URL: proxied})

        result = parser.parse_result
        self.assertTrue(result.via_jina)
        self.assertTrue(result.content_type_missing)
        self.assertTrue(any('missing' in note for note in result.notes))
        self.assertNoJinaLeak(parser)

    def test_url_without_scheme_gets_http(self):
        url = 'example.com/posts/hello'
        resp = FakeResponse(HTML_BODY, {'content-type': 'text/html'},
                            'http://' + url)
        parser, fake = self.factory(url, {'http://' + url: resp})

        self.assertEqual(fake.calls[0][0], 'http://' + url)
        self.assertEqual(parser.parse_result.requested_url, 'http://' + url)
        self.assertEqual(parser.parse_result.final_url, 'http://' + url)

    def test_embeddable_branch_is_untouched(self):
        # Detection still keys off the requested url, as it always did, while the
        # result reports the redirect-resolved url and the untouched embed html.
        url = 'https://www.youtube.com/watch?v=HlLCtjJzHVI'
        final = 'https://www.youtube.com/watch?v=HlLCtjJzHVI&t=10'
        resp = FakeResponse(b'<html></html>', {'content-type': 'text/html'}, final,
                            [FakeResponse(url=url)])
        parser, _ = self.factory(url, {url: resp})

        result = parser.parse_result
        self.assertEqual(result.parser_type, ParserType.EMBEDDABLE)
        self.assertEqual(result.final_url, final)
        self.assertEqual(result.requested_url, url)
        self.assertTrue(result.redirected)
        self.assertFalse(result.via_jina)
        self.assertTrue(parser.get_content().startswith('<iframe'))

    def test_non_provider_redirect_is_not_embeddable(self):
        # Faithful to the old check: only the requested url decides this, so a
        # provider reached only through a redirect stays on the normal path.
        short = 'https://youtu.be/HlLCtjJzHVI'
        final = 'https://www.youtube.com/watch?v=HlLCtjJzHVI'
        resp = FakeResponse(HTML_BODY, {'content-type': 'text/html'}, final,
                            [FakeResponse(url=short)])
        parser, _ = self.factory(short, {short: resp})

        result = parser.parse_result
        self.assertEqual(result.parser_type, ParserType.HTML)
        self.assertEqual(result.final_url, final)

    def test_embeddable_returns_before_the_empty_body_check(self):
        # Old order preserved: an embeddable page is returned as-is, so an empty
        # body never sends it through the jina proxy.
        url = 'https://www.youtube.com/watch?v=HlLCtjJzHVI'
        empty = FakeResponse(b'', {'content-type': 'text/html'}, url)
        parser, fake = self.factory(url, {url: empty})
        self.assertEqual(parser.parse_result.parser_type, ParserType.EMBEDDABLE)
        self.assertFalse(parser.parse_result.via_jina)
        self.assertEqual(len(fake.calls), 1)
        self.assertIn('embed/HlLCtjJzHVI', parser.get_content())

    def test_jina_retry_can_return_a_pdf_body(self):
        empty = FakeResponse(b'', {'content-type': 'text/html'}, ARTICLE_URL)
        proxied = FakeResponse(PDF_BODY, {'content-type': 'application/pdf'}, JINA_URL)
        parser, _ = self.factory(ARTICLE_URL, {ARTICLE_URL: empty, JINA_URL: proxied})

        result = parser.parse_result
        self.assertTrue(result.via_jina)
        self.assertEqual(result.parser_type, ParserType.PDF)
        self.assertEqual(result.final_url, ARTICLE_URL)
        self.assertNotIn('r.jina.ai', parser.get_favicon_url())
        self.assertTrue(parser.get_content().startswith('Systems code is often written'))

    def test_unknown_content_type_with_html_body_is_parsed(self):
        resp = FakeResponse(HTML_BODY, {'content-type': 'application/octet-stream'},
                            ARTICLE_URL)
        parser, _ = self.factory(ARTICLE_URL, {ARTICLE_URL: resp})

        result = parser.parse_result
        self.assertEqual(result.parser_type, ParserType.HTML)
        self.assertEqual(result.sniffed_format, ContentFormat.HTML)
        self.assertTrue(result.content_type_mismatch)

    def test_unknown_content_type_with_text_body_is_parsed(self):
        body = b'Just a plain text article, no markup at all. ' * 10
        resp = FakeResponse(body, {'content-type': 'application/octet-stream'},
                            ARTICLE_URL)
        parser, _ = self.factory(ARTICLE_URL, {ARTICLE_URL: resp})

        result = parser.parse_result
        self.assertEqual(result.parser_type, ParserType.HTML)
        self.assertEqual(result.sniffed_format, ContentFormat.TEXT)
        self.assertTrue(result.content_type_mismatch)

    def test_empty_body_with_unknown_content_type_still_raises(self):
        resp = FakeResponse(b'', {'content-type': 'application/octet-stream'}, ARTICLE_URL)
        with self.assertRaises(TypeError):
            self.factory(ARTICLE_URL, {ARTICLE_URL: resp})

    def test_result_can_be_serialized_without_network(self):
        resp = FakeResponse(HTML_BODY, {'content-type': 'text/html'}, ARTICLE_URL)
        parser, _ = self.factory(ARTICLE_URL, {ARTICLE_URL: resp})
        data = parser.parse_result.to_dict()
        self.assertEqual(data['parser_type'], ParserType.HTML)
        self.assertEqual(data['final_url'], ARTICLE_URL)
        self.assertEqual(data['attempts'][0]['response_url'], ARTICLE_URL)


class FakePageParser(object):
    def __init__(self, content, result):
        self._content = content
        self.parse_result = result
        self.final_url = result.final_url
        self.parser_type = result.parser_type
        self.via_jina = result.via_jina

    def get_content(self, max_length=None):
        return self._content

    def get_favicon_url(self):
        return urljoin(self.final_url, '/favicon.ico')

    def get_illustration(self):
        return None


class NewsIntegrationTestCase(TestCase):
    """News.pull_content keeps working and exposes the dispatch information."""

    def test_pull_content_reports_the_jina_chain_without_leaking_it(self):
        import db
        import db.summary
        from hacker_news import news as news_module
        from hacker_news.news import News

        store = {}
        monkey_cache = mock.Mock()
        monkey_cache.get.side_effect = lambda url: store.get(url) or db.Summary(url)
        monkey_cache.put.side_effect = lambda summary: store.setdefault(summary.url, summary)

        content = 'A short body fetched through the proxy.'
        result = parser_factory_result(url=ARTICLE_URL, final_url=ARTICLE_URL,
                                       via_jina=True, response_url=JINA_URL)
        page = FakePageParser(content, result)
        news = News(rank=1, title='Hello', url=ARTICLE_URL)
        with mock.patch.object(news_module, 'parser_factory', return_value=page), \
                mock.patch.object(news_module.db, 'summary', monkey_cache):
            news.pull_content()

        self.assertEqual(news.summary, content)
        self.assertEqual(news.final_url, ARTICLE_URL)
        self.assertEqual(news.parser_type, ParserType.HTML)
        self.assertTrue(news.via_jina)
        self.assertEqual(news.favicon, 'https://example.com/favicon.ico')
        # The cache key stays the originally submitted article url
        self.assertEqual(list(store), [ARTICLE_URL])
        self.assertEqual(store[ARTICLE_URL].summary, content)


def parser_factory_result(url, final_url, via_jina=False, response_url=None,
                          parser_type=ParserType.HTML, attempts=()):
    from page_content_extractor import ParseResult
    return ParseResult(
        requested_url=url,
        final_url=final_url,
        parser_type=parser_type,
        via_jina=via_jina,
        response_url=response_url or final_url,
        notes=[],
        attempts=list(attempts),
    )

