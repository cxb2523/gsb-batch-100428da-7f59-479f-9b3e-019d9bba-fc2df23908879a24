# coding: utf-8
"""Metadata describing how a page was fetched and which parser handled it.

The objects in this module are attached to every extractor returned by
:func:`page_content_extractor.parser_factory`, so callers (and logs) can tell
which chain actually produced the article body without re-requesting anything.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


class ParserType:
    """Values used by :attr:`ParseResult.parser_type`."""

    EMBEDDABLE = 'embeddable'
    PDF = 'pdf'
    HTML = 'html'


class ContentFormat:
    """Values used by :attr:`ParseResult.sniffed_format`."""

    PDF = 'pdf'
    HTML = 'html'
    TEXT = 'text'
    # Bytes that are neither recognizable html nor printable text
    BINARY = 'binary'
    # Nothing at all was returned
    EMPTY = 'empty'


@dataclass
class ParseAttempt:
    """One network request plus the parser decision made for its response."""

    # URL that was requested (may be the jina proxy URL)
    url: str
    # URL the response really came from, after redirects (may be the proxy URL)
    response_url: str
    # Article URL, with any jina proxy prefix removed
    final_url: str
    via_jina: bool
    # Raw content-type header, None when the header is absent
    declared_content_type: Optional[str]
    # Format detected by inspecting the body, see ContentFormat
    sniffed_format: str
    # Parser chosen for this response, '' when no parser was available
    parser_type: str = ''
    notes: List[str] = field(default_factory=list)

    def to_dict(self):
        return {
            'url': self.url,
            'response_url': self.response_url,
            'final_url': self.final_url,
            'via_jina': self.via_jina,
            'declared_content_type': self.declared_content_type,
            'sniffed_format': self.sniffed_format,
            'parser_type': self.parser_type,
            'notes': list(self.notes),
        }


@dataclass
class ParseResult:
    """Outcome of :func:`page_content_extractor.parser_factory`.

    ``attempts`` keeps one entry per request, so a jina fallback is visible as
    two attempts: the original HTML request and the proxy request.
    """

    # Article URL as requested, with a scheme added when it was missing
    requested_url: str = ''
    # Final article URL after following redirects. Never contains the jina
    # proxy address, even when the body came through the proxy.
    final_url: str = ''
    # ParserType used to produce the body
    parser_type: str = ''
    # Whether the body was fetched through https://r.jina.ai/
    via_jina: bool = False
    # Actual response URL, which is the jina proxy URL on a proxy attempt
    response_url: str = ''
    # Raw content-type header of the response that supplied the body
    declared_content_type: Optional[str] = None
    # Format detected from the body itself, see ContentFormat
    sniffed_format: str = ''
    # True when the response had no content-type header at all
    content_type_missing: bool = False
    # True when the content-type header disagreed with the detected body format
    content_type_mismatch: bool = False
    # True when at least one redirect happened on the way to `response_url`
    redirected: bool = False
    # Urls that answered with a redirect before reaching `final_url`
    redirects: Tuple[str, ...] = ()
    # Human readable decisions made while dispatching
    notes: List[str] = field(default_factory=list)
    attempts: List[ParseAttempt] = field(default_factory=list)

    def to_dict(self):
        return {
            'requested_url': self.requested_url,
            'final_url': self.final_url,
            'parser_type': self.parser_type,
            'via_jina': self.via_jina,
            'response_url': self.response_url,
            'declared_content_type': self.declared_content_type,
            'sniffed_format': self.sniffed_format,
            'content_type_missing': self.content_type_missing,
            'content_type_mismatch': self.content_type_mismatch,
            'redirected': self.redirected,
            'redirects': list(self.redirects),
            'notes': list(self.notes),
            'attempts': [attempt.to_dict() for attempt in self.attempts],
        }
