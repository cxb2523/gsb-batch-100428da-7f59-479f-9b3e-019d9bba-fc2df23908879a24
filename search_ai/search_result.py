from typing import Any

from .proxy import Proxy
from .utils import extract_metadata, generate_markdown, valid_type
from .extractor import get_page_sync, get_page

from pydantic import BaseModel, HttpUrl


# Shown on any result whose page could not be fetched while extending output.
FETCH_FAILED_MESSAGE = 'Page content could not be retrieved.'


def _safe_get_page_sync(url: str | list[str], proxy: Proxy | None) -> str | list[str]:
    """
    Fetch page sources without letting a broken browser or batch abort the run.
    """
    try:
        return get_page_sync(url, proxy)
    except Exception:
        return '' if isinstance(url, str) else [''] * len(url)


async def _safe_get_page(url: str | list[str], proxy: Proxy | None) -> str | list[str]:
    try:
        return await get_page(url, proxy)
    except Exception:
        return '' if isinstance(url, str) else [''] * len(url)


def _normalize_page_sources(page_sources: str | list[str] | None, count: int) -> list[str | None]:
    """
    Guarantee one source slot per result so a short/garbled batch response cannot
    silently drop trailing results from the output.
    """
    if isinstance(page_sources, str):
        page_sources = [page_sources]

    normalized = list(page_sources or [])[:count]
    normalized.extend([None] * (count - len(normalized)))
    return normalized


def _fetch_supported_pages(supported: list[bool], fetched: list[str | None]) -> list[str | None]:
    """
    Assign fetched sources only to the results whose type supports extraction.

    ``None`` marks an unsupported file type, which keeps the existing basic
    output and must not be reported as a failed fetch.
    """
    sources = iter(fetched)
    return [next(sources, None) or '' if is_supported else None for is_supported in supported]


def _extended_page_sources_sync(results: list['BaseSearchResult'], proxy: Proxy | None) -> list[str | None]:
    """
    Fetch page sources for a batch.

    Each returned slot is either ``None`` (unsupported file type: keep the basic
    output, never a failure), ``''`` (fetch failed) or the page source.
    """
    supported = [valid_type(str(result.link)) for result in results]
    urls = [str(result.link) for result, is_supported in zip(results, supported) if is_supported]

    fetched = _normalize_page_sources(_safe_get_page_sync(urls, proxy), len(urls)) if urls else []
    return _fetch_supported_pages(supported, fetched)


async def _extended_page_sources_async(results: list['BaseSearchResult'], proxy: Proxy | None) -> list[str | None]:
    supported = [valid_type(str(result.link)) for result in results]
    urls = [str(result.link) for result, is_supported in zip(results, supported) if is_supported]

    fetched = _normalize_page_sources(await _safe_get_page(urls, proxy), len(urls)) if urls else []
    return _fetch_supported_pages(supported, fetched)


class BaseSearchResult(BaseModel):
    title: str
    link: HttpUrl
    description: str | None = None
    _proxy: Proxy | None = None

    def __str__(self):
        """
        This is here to hide _proxy, which is more of an implementation detail.
        """
        return f'{self.__class__.__name__}(title="{self.title}", link="{self.link}", description="{self.description}")'

    def __repr__(self):
        return self.__str__()

    def _basic_markdown(self) -> str:
        parts = [f'**Title:** {self.title}', f'**Link:** {self.link}']
        if self.description:
            parts.append(f'**Description:** {self.description}')
        return '\n'.join(parts)

    @staticmethod
    def _fetch_failed(page_source: str | None) -> bool:
        """
        Extended fetching treats a missing or empty page source as a failed scrape.
        """
        return not page_source or not page_source.strip()

    def _fetch_failed_markdown(self) -> str:
        parts = [f'**Title:** {self.title}', f'**Link:** {self.link}']
        if self.description:
            parts.append(f'**Description:** {self.description}')
        parts.append(f'**Fetch Failed:** {FETCH_FAILED_MESSAGE}')
        return '\n'.join(parts)

    def _fetch_failed_json(self) -> dict:
        combined_data = {
            'title': self.title,
            'link': str(self.link),
        }
        if self.description:
            combined_data['description'] = self.description
        # Keep the field present but empty so the schema matches successful entries.
        combined_data['page_preview'] = ''
        combined_data['fetch_failed'] = True
        combined_data['fetch_error'] = FETCH_FAILED_MESSAGE
        return combined_data

    def _format_extended_markdown(
        self,
        page_source: str | None,
        content_length: int,
        only_page_content: bool = False,
        ignore_links: bool = False,
        ignore_images: bool = True,
    ) -> str:
        """
        Never raise: a page that cannot be rendered is reported inline so one bad
        result cannot take down the rest of a batch.
        """
        try:
            return self._extended_markdown(
                page_source or '',
                content_length,
                only_page_content,
                ignore_links,
                ignore_images,
            )
        except Exception:
            return self._fetch_failed_markdown()

    def _format_extended_json(
        self,
        page_source: str | None,
        content_length: int,
        ignore_links: bool = False,
        ignore_images: bool = True,
    ) -> dict:
        """
        Never raise: a page that cannot be rendered is reported inline so one bad
        result cannot take down the rest of a batch.
        """
        try:
            return self._extended_json(
                page_source or '',
                content_length,
                ignore_links,
                ignore_images,
            )
        except Exception:
            return self._fetch_failed_json()

    def _extended_markdown(
        self,
        page_source: str,
        content_length: int,
        only_page_content: bool = False,
        ignore_links: bool = False,
        ignore_images: bool = True,
    ) -> str:
        if self._fetch_failed(page_source):
            # Keep the entry identifiable even when ``only_page_content`` would
            # normally drop metadata; an empty entry hides the failure entirely.
            return self._fetch_failed_markdown()

        markdown = generate_markdown(page_source, ignore_links, ignore_images)

        if only_page_content:
            return markdown[:content_length]

        metadata = extract_metadata(page_source)

        parts = [f'**Title:** {metadata.get("title") or self.title}', f'**Link:** {self.link}']

        if metadata.get('description'):
            parts.append(f'**Description:** {metadata["description"]}')
        elif self.description:
            parts.append(f'**Description:** {self.description}')

        if metadata.get('author'):
            parts.append(f'**Author:** {metadata["author"]}')
        if metadata.get('twitter'):
            parts.append(f'**Twitter:** {metadata["twitter"]}')

        if markdown:
            parts.append('')  # Extra break between metadata and page data
            parts.append('## Page Preview:\n')
            parts.append(markdown[:content_length].strip())

        return '\n'.join(parts)

    def _extended_json(
        self,
        page_source: str,
        content_length: int,
        ignore_links: bool = False,
        ignore_images: bool = True,
    ) -> dict:
        if self._fetch_failed(page_source):
            return self._fetch_failed_json()

        metadata = extract_metadata(page_source)
        markdown = generate_markdown(page_source, ignore_links, ignore_images)

        combined_data = {'title': metadata.get('title') or self.title, 'link': str(self.link)}

        if metadata.get('description'):
            combined_data['description'] = metadata['description']
        elif self.description:
            combined_data['description'] = self.description

        if metadata.get('author'):
            combined_data['author'] = metadata['author']
        if metadata.get('twitter'):
            combined_data['twitter'] = metadata['twitter']

        if markdown:
            combined_data['page_preview'] = markdown[:content_length]

        return combined_data


class SearchResult(BaseSearchResult):
    def markdown(
        self,
        extend: bool = False,
        content_length: int = 1_000,
        ignore_links: bool = False,
        ignore_images: bool = True,
        only_page_content: bool = False,
    ) -> str:
        if not extend or not valid_type(str(self.link)):
            return self._basic_markdown()

        page_source = _safe_get_page_sync(str(self.link), self._proxy)
        return self._format_extended_markdown(page_source, content_length, only_page_content, ignore_links, ignore_images)

    def json(
        self,
        extend: bool = False,
        content_length: int = 1_000,
        ignore_links: bool = False,
        ignore_images: bool = True,
        **kwargs: Any,
    ) -> dict:
        if not extend or not valid_type(str(self.link)):
            return super().model_dump(**kwargs)

        page_source = _safe_get_page_sync(str(self.link), self._proxy)
        return self._format_extended_json(page_source, content_length, ignore_links, ignore_images)


class AsyncSearchResult(BaseSearchResult):
    async def markdown(
        self,
        extend: bool = False,
        content_length: int = 1_000,
        ignore_links: bool = False,
        ignore_images: bool = True,
        only_page_content: bool = False,
    ) -> str:
        if not extend or not valid_type(str(self.link)):
            return self._basic_markdown()

        page_source = await _safe_get_page(str(self.link), self._proxy)
        return self._format_extended_markdown(page_source, content_length, only_page_content, ignore_links, ignore_images)

    async def json(
        self,
        extend: bool = False,
        content_length: int = 1_000,
        ignore_links: bool = False,
        ignore_images: bool = True,
        **kwargs: Any,
    ) -> dict:
        if not extend or not valid_type(str(self.link)):
            return super().model_dump(**kwargs)

        page_source = await _safe_get_page(str(self.link), self._proxy)
        return self._format_extended_json(page_source, content_length, ignore_links, ignore_images)


class SearchResults(list):
    def __init__(self, results: list[SearchResult], _proxy: Proxy | None = None):
        super().__init__(results)
        self._proxy = _proxy

    def markdown(self, extend: bool = False, content_length: int = 1_000, **kwargs) -> str:
        if not self:  # Edge case for no search results
            return ''

        if not extend:
            content = [result._basic_markdown() for result in self]

        else:
            page_sources = _extended_page_sources_sync(list(self), self._proxy)
            content = [
                result._basic_markdown()
                if page_source is None
                else result._format_extended_markdown(
                    page_source=page_source, content_length=content_length, **kwargs
                )
                for result, page_source in zip(self, page_sources)
            ]

        return '# Search Results\n\n' + '\n----------\n'.join(content)

    def json(self, extend: bool = False, content_length: int = 1_000, **kwargs) -> list[dict]:
        if not extend:
            return [result.model_dump() for result in self]

        page_sources = _extended_page_sources_sync(list(self), self._proxy)
        return [
            result.model_dump()
            if page_source is None
            else result._format_extended_json(page_source=page_source, content_length=content_length, **kwargs)
            for result, page_source in zip(self, page_sources)
        ]


class AsyncSearchResults(list):
    def __init__(self, results: list[AsyncSearchResult], _proxy: Proxy | None = None):
        super().__init__(results)
        self._proxy = _proxy

    async def markdown(self, extend: bool = False, content_length: int = 1_000, **kwargs) -> str:
        if not self:  # Edge case for no search results
            return ''

        if not extend:
            content = [result._basic_markdown() for result in self]

        else:
            page_sources = await _extended_page_sources_async(list(self), self._proxy)
            content = [
                result._basic_markdown()
                if page_source is None
                else result._format_extended_markdown(
                    page_source=page_source, content_length=content_length, **kwargs
                )
                for result, page_source in zip(self, page_sources)
            ]

        return '# Search Results\n\n' + '\n----------\n'.join(content)

    async def json(self, extend: bool = False, content_length: int = 1_000, **kwargs) -> list[dict]:
        if not extend:
            return [result.model_dump() for result in self]

        page_sources = await _extended_page_sources_async(list(self), self._proxy)
        return [
            result.model_dump()
            if page_source is None
            else result._format_extended_json(page_source=page_source, content_length=content_length, **kwargs)
            for result, page_source in zip(self, page_sources)
        ]
