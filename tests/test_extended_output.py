"""
Tests for extended (page content) output when individual pages fail to render.

The page fetcher is replaced with a controllable local stand-in so no network
or browser is required.
"""

import pytest

from search_ai import extractor
from search_ai.search_result import (
    FETCH_FAILED_MESSAGE,
    AsyncSearchResult,
    AsyncSearchResults,
    SearchResult,
    SearchResults,
    _safe_get_page,
    _safe_get_page_sync,
)

SUCCESS_URL = 'https://example.com/success'
RENDER_ERROR_URL = 'https://example.com/render-error'
EMPTY_URL = 'https://example.com/empty'
ALL_FAILED_URLS = (RENDER_ERROR_URL, EMPTY_URL, 'https://example.com/timeout')

SUCCESS_HTML = """
<html>
  <head>
    <title>Successful Page</title>
    <meta name="description" content="Description found on the successful page.">
    <meta name="author" content="Jane Doe">
  </head>
  <body>
    <h1>Successful Page Heading</h1>
    <p>This is the complete body content of the successful page.</p>
  </body>
</html>
"""

# A render failure and an empty page both surface from the fetcher as ''.
PAGE_SOURCES = {SUCCESS_URL: SUCCESS_HTML, RENDER_ERROR_URL: '', EMPTY_URL: ''}

SAMPLE_RESULTS = [
    {
        'title': 'Success Result',
        'link': SUCCESS_URL,
        'description': 'Original search description for the working page.',
    },
    {
        'title': 'Render Error Result',
        'link': RENDER_ERROR_URL,
        'description': 'Original search description for the broken page.',
    },
    {
        'title': 'Empty Result',
        'link': EMPTY_URL,
        'description': 'Original search description for the empty page.',
    },
]


def _fetch_sources(mapping):
    """Sync stand-in for get_page_sync that resolves URLs against ``mapping``."""

    def fetch(url, proxy=None):
        if isinstance(url, list):
            return [mapping.get(item, '') for item in url]
        return mapping.get(url, '')

    return fetch


def _async_fetch_sources(mapping):
    """Async stand-in for get_page that resolves URLs against ``mapping``."""

    async def fetch(url, proxy=None):
        if isinstance(url, list):
            return [mapping.get(item, '') for item in url]
        return mapping.get(url, '')

    return fetch


@pytest.fixture
def batch(monkeypatch):
    def build(result_cls=SearchResult, results_cls=SearchResults, mapping=PAGE_SOURCES):
        monkeypatch.setattr('search_ai.search_result.get_page_sync', _fetch_sources(mapping))
        return results_cls([result_cls(**data) for data in SAMPLE_RESULTS])

    return build


@pytest.fixture
def async_batch(monkeypatch):
    def build(result_cls=AsyncSearchResult, results_cls=AsyncSearchResults, mapping=PAGE_SOURCES):
        monkeypatch.setattr('search_ai.search_result.get_page', _async_fetch_sources(mapping))
        return results_cls([result_cls(**data) for data in SAMPLE_RESULTS])

    return build


def _markdown_entries(markdown: str) -> list[str]:
    return markdown.split('\n----------\n')


# ---------------------------------------------------------------------------
# Batch output: partial failure
# ---------------------------------------------------------------------------


def test_batch_markdown_keeps_every_result_when_some_pages_fail(batch):
    markdown = batch().markdown(extend=True)

    entries = _markdown_entries(markdown)
    assert len(entries) == 3

    success, render_error, empty = entries

    # The successful page keeps its full metadata and body.
    assert '**Title:** Successful Page' in success
    assert '**Author:** Jane Doe' in success
    assert 'This is the complete body content of the successful page.' in success
    assert '**Fetch Failed:**' not in success

    # Failed pages keep title, link and original description, with a clear marker.
    for entry, data in ((render_error, SAMPLE_RESULTS[1]), (empty, SAMPLE_RESULTS[2])):
        assert f'**Title:** {data["title"]}' in entry
        assert f'**Link:** {data["link"]}' in entry
        assert f'**Description:** {data["description"]}' in entry
        assert f'**Fetch Failed:** {FETCH_FAILED_MESSAGE}' in entry
        assert '## Page Preview:' not in entry


def test_batch_json_keeps_every_result_when_some_pages_fail(batch):
    payload = batch().json(extend=True)

    assert len(payload) == 3

    success, render_error, empty = payload

    assert success['title'] == 'Successful Page'
    assert success['author'] == 'Jane Doe'
    assert 'This is the complete body content of the successful page.' in success['page_preview']
    assert 'fetch_failed' not in success

    for entry, data in ((render_error, SAMPLE_RESULTS[1]), (empty, SAMPLE_RESULTS[2])):
        assert entry['title'] == data['title']
        assert entry['link'] == data['link']
        assert entry['description'] == data['description']
        assert entry['fetch_failed'] is True
        assert entry['fetch_error'] == FETCH_FAILED_MESSAGE
        assert entry['page_preview'] == ''


def test_batch_keeps_results_when_fetcher_raises_for_whole_batch(monkeypatch):
    def explode(url, proxy=None):
        raise RuntimeError('browser crashed')

    monkeypatch.setattr('search_ai.search_result.get_page_sync', explode)
    results = SearchResults([SearchResult(**data) for data in SAMPLE_RESULTS])

    markdown = results.markdown(extend=True)
    payload = results.json(extend=True)

    assert len(_markdown_entries(markdown)) == 3
    assert markdown.count(f'**Fetch Failed:** {FETCH_FAILED_MESSAGE}') == 3

    assert len(payload) == 3
    assert all(entry['fetch_failed'] is True for entry in payload)


def test_batch_pads_missing_page_sources(monkeypatch):
    # A short response from the fetcher must not drop trailing results.
    monkeypatch.setattr('search_ai.search_result.get_page_sync', lambda url, proxy=None: [SUCCESS_HTML])

    results = SearchResults([SearchResult(**data) for data in SAMPLE_RESULTS])
    payload = results.json(extend=True)

    assert len(payload) == 3
    # Only the first URL had a source; the two trailing results are reported as
    # failed rather than being dropped.
    assert 'fetch_failed' not in payload[0]
    assert payload[1]['fetch_failed'] is True
    assert payload[2]['fetch_failed'] is True


def test_batch_all_pages_failing_does_not_raise(batch):
    failing_mapping = {url: '' for url in ALL_FAILED_URLS}
    results = batch(mapping=failing_mapping)

    markdown = results.markdown(extend=True)
    payload = results.json(extend=True)

    assert len(_markdown_entries(markdown)) == 3
    assert markdown.count(f'**Fetch Failed:** {FETCH_FAILED_MESSAGE}') == 3

    assert len(payload) == 3
    assert all(entry['fetch_failed'] is True for entry in payload)
    assert all(entry['page_preview'] == '' for entry in payload)


# ---------------------------------------------------------------------------
# Async output parity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_batch_matches_sync_batch(batch, async_batch):
    sync_markdown = batch().markdown(extend=True)
    async_markdown = await async_batch().markdown(extend=True)

    sync_json = batch().json(extend=True)
    async_json = await async_batch().json(extend=True)

    assert async_markdown == sync_markdown
    assert async_json == sync_json
    assert len(async_json) == 3
    assert async_json[0]['title'] == 'Successful Page'
    assert async_json[1]['fetch_failed'] is True
    assert async_json[2]['fetch_failed'] is True


@pytest.mark.asyncio
async def test_async_batch_all_pages_failing_does_not_raise(async_batch):
    failing_mapping = {url: '' for url in ALL_FAILED_URLS}
    results = async_batch(mapping=failing_mapping)

    markdown = await results.markdown(extend=True)
    payload = await results.json(extend=True)

    assert len(_markdown_entries(markdown)) == 3
    assert markdown.count(f'**Fetch Failed:** {FETCH_FAILED_MESSAGE}') == 3
    assert len(payload) == 3
    assert all(entry['fetch_failed'] is True for entry in payload)


# ---------------------------------------------------------------------------
# Single-result extended output follows the same rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('page_source', ['', '   ', None])
def test_single_result_marks_failure(monkeypatch, page_source):
    monkeypatch.setattr('search_ai.search_result.get_page_sync', lambda url, proxy=None: page_source)
    result = SearchResult(**SAMPLE_RESULTS[0])

    markdown = result.markdown(extend=True)
    payload = result.json(extend=True)

    assert '**Title:** Success Result' in markdown
    assert f'**Link:** {SUCCESS_URL}' in markdown
    assert f'**Description:** {SAMPLE_RESULTS[0]["description"]}' in markdown
    assert f'**Fetch Failed:** {FETCH_FAILED_MESSAGE}' in markdown

    assert payload['fetch_failed'] is True
    assert payload['fetch_error'] == FETCH_FAILED_MESSAGE
    assert payload['page_preview'] == ''
    assert payload['description'] == SAMPLE_RESULTS[0]['description']


@pytest.mark.asyncio
async def test_async_single_result_marks_failure(monkeypatch):
    async def fetch(url, proxy=None):
        return ''

    monkeypatch.setattr('search_ai.search_result.get_page', fetch)
    result = AsyncSearchResult(**SAMPLE_RESULTS[1])

    markdown = await result.markdown(extend=True)
    payload = await result.json(extend=True)

    assert f'**Fetch Failed:** {FETCH_FAILED_MESSAGE}' in markdown
    assert payload['fetch_failed'] is True
    assert payload['page_preview'] == ''


def test_single_result_success_is_unchanged(monkeypatch):
    monkeypatch.setattr('search_ai.search_result.get_page_sync', lambda url, proxy=None: SUCCESS_HTML)
    result = SearchResult(**SAMPLE_RESULTS[0])

    markdown = result.markdown(extend=True)
    payload = result.json(extend=True)

    assert '**Title:** Successful Page' in markdown
    assert '## Page Preview:' in markdown
    assert 'This is the complete body content of the successful page.' in markdown
    assert 'fetch_failed' not in payload
    assert 'This is the complete body content of the successful page.' in payload['page_preview']


def test_unsupported_file_type_is_not_a_failure(monkeypatch):
    calls = []

    def fetch(url, proxy=None):
        calls.append(url)
        return ''

    monkeypatch.setattr('search_ai.search_result.get_page_sync', fetch)
    result = SearchResult(title='A PDF', link='https://example.com/paper.pdf', description='A document.')

    markdown = result.markdown(extend=True)
    payload = result.json(extend=True)

    assert calls == []
    assert '**Fetch Failed:**' not in markdown
    assert markdown == result._basic_markdown()
    assert payload == result.model_dump()


def test_batch_unsupported_file_type_keeps_basic_output(monkeypatch):
    fetched_urls = []

    def fetch(url, proxy=None):
        urls = url if isinstance(url, list) else [url]
        fetched_urls.extend(urls)
        return [PAGE_SOURCES.get(item, '') for item in urls]

    monkeypatch.setattr('search_ai.search_result.get_page_sync', fetch)

    pdf_result = SearchResult(title='A PDF', link='https://example.com/paper.pdf', description='A document.')
    results = SearchResults([SearchResult(**SAMPLE_RESULTS[0]), pdf_result, SearchResult(**SAMPLE_RESULTS[2])])

    markdown = results.markdown(extend=True)
    payload = results.json(extend=True)

    # Only the supported HTML pages were fetched; the PDF was left alone.
    assert 'https://example.com/paper.pdf' not in fetched_urls

    entries = _markdown_entries(markdown)
    assert len(entries) == 3
    assert entries[1] == pdf_result._basic_markdown()
    assert '**Fetch Failed:**' not in entries[1]

    assert len(payload) == 3
    assert payload[1] == pdf_result.model_dump()
    assert 'fetch_failed' not in payload[1]


# ---------------------------------------------------------------------------
# Fetcher-level isolation (render exception / timeout / empty source)
# ---------------------------------------------------------------------------


class _FakePage:
    def __init__(self, mapping):
        self._mapping = mapping
        self._html = ''
        self.closed = False

    async def goto(self, url, **_kwargs):
        behaviour = self._mapping.get(url, 'ok')
        if behaviour == 'render_error':
            raise RuntimeError('render failed')
        if behaviour == 'timeout':
            raise TimeoutError('navigation timed out')
        self._html = SUCCESS_HTML if behaviour == 'ok' else ''

    async def wait_for_timeout(self, _ms):
        return None

    async def content(self):
        return self._html

    async def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self, mapping):
        self._mapping = mapping

    async def new_page(self):
        return _FakePage(self._mapping)

    async def close(self):
        return None


class _FakeBrowser:
    def __init__(self, mapping):
        self._mapping = mapping
        self.closed = False

    async def new_context(self, **_kwargs):
        return _FakeContext(self._mapping)

    async def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, mapping):
        self._mapping = mapping

    async def launch(self, **_kwargs):
        return _FakeBrowser(self._mapping)


class _FakePlaywright:
    def __init__(self, mapping):
        self.chromium = _FakeChromium(mapping)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc_info):
        return False


@pytest.mark.asyncio
async def test_get_page_isolates_failures_per_page(monkeypatch):
    mapping = {
        SUCCESS_URL: 'ok',
        RENDER_ERROR_URL: 'render_error',
        EMPTY_URL: 'empty',
        'https://example.com/timeout': 'timeout',
    }
    fake = _FakePlaywright(mapping)
    monkeypatch.setattr(extractor, 'async_playwright', lambda: fake)

    urls = [SUCCESS_URL, RENDER_ERROR_URL, EMPTY_URL, 'https://example.com/timeout']
    sources = await extractor.get_page(urls, None)

    assert len(sources) == 4
    assert sources[0] == SUCCESS_HTML
    assert sources[1] == ''
    assert sources[2] == ''
    assert sources[3] == ''


def test_get_page_sync_isolates_failures_per_page(monkeypatch):
    mapping = {SUCCESS_URL: 'ok', RENDER_ERROR_URL: 'render_error', EMPTY_URL: 'empty'}
    fake = _FakePlaywright(mapping)
    monkeypatch.setattr(extractor, 'async_playwright', lambda: fake)

    sources = extractor.get_page_sync([SUCCESS_URL, RENDER_ERROR_URL, EMPTY_URL], None)

    assert sources == [SUCCESS_HTML, '', '']


def test_safe_fetch_helpers_never_raise(monkeypatch):
    monkeypatch.setattr('search_ai.search_result.get_page_sync', lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))

    assert _safe_get_page_sync('https://example.com', None) == ''
    assert _safe_get_page_sync(['https://example.com', 'https://example.com'], None) == ['', '']


@pytest.mark.asyncio
async def test_async_safe_fetch_helper_never_raises(monkeypatch):
    async def explode(*args, **kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr('search_ai.search_result.get_page', explode)

    assert await _safe_get_page('https://example.com', None) == ''
    assert await _safe_get_page(['https://example.com', 'https://example.com'], None) == ['', '']


# ---------------------------------------------------------------------------
# End-to-end: real fetcher + batch output
# ---------------------------------------------------------------------------

E2E_MAPPING = {
    SUCCESS_URL: 'ok',
    RENDER_ERROR_URL: 'render_error',
    EMPTY_URL: 'empty',
}


def test_end_to_end_sync_batch_with_mixed_outcomes(monkeypatch):
    monkeypatch.setattr(extractor, 'async_playwright', lambda: _FakePlaywright(E2E_MAPPING))

    results = SearchResults([SearchResult(**data) for data in SAMPLE_RESULTS])
    markdown = results.markdown(extend=True)
    payload = results.json(extend=True)

    entries = _markdown_entries(markdown)
    assert len(entries) == 3

    assert '**Title:** Successful Page' in entries[0]
    assert 'This is the complete body content of the successful page.' in entries[0]
    assert '**Fetch Failed:**' not in entries[0]

    assert f'**Fetch Failed:** {FETCH_FAILED_MESSAGE}' in entries[1]
    assert f'**Fetch Failed:** {FETCH_FAILED_MESSAGE}' in entries[2]

    assert len(payload) == 3
    assert payload[0]['title'] == 'Successful Page'
    assert payload[1]['fetch_failed'] is True
    assert payload[2]['fetch_failed'] is True


@pytest.mark.asyncio
async def test_end_to_end_async_batch_matches_sync(monkeypatch):
    monkeypatch.setattr(extractor, 'async_playwright', lambda: _FakePlaywright(E2E_MAPPING))

    sync_results = SearchResults([SearchResult(**data) for data in SAMPLE_RESULTS])
    async_results = AsyncSearchResults([AsyncSearchResult(**data) for data in SAMPLE_RESULTS])

    sync_markdown = sync_results.markdown(extend=True)
    async_markdown = await async_results.markdown(extend=True)
    assert async_markdown == sync_markdown

    sync_json = sync_results.json(extend=True)
    async_json = await async_results.json(extend=True)
    assert async_json == sync_json

    assert len(async_json) == 3
    assert async_json[0]['title'] == 'Successful Page'
    assert async_json[1]['fetch_failed'] is True
    assert async_json[2]['fetch_failed'] is True
