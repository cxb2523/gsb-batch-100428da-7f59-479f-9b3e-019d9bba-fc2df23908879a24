# coding: utf-8
import logging

import humanize

from page_content_extractor.http import session
from .embeddable import EmbeddableExtractor
from .exceptions import ParseError
from .html import HtmlContentExtractor
from .pdf import PdfExtractor
from .result import ContentFormat, ParseAttempt, ParseResult, ParserType

__all__ = ['ParseError', 'parser_factory', 'ParseResult', 'ParseAttempt',
           'ParserType', 'ContentFormat']

logger = logging.getLogger(__name__)
jina_prefix = 'https://r.jina.ai/'

# Markers good enough to recognize an html/xml document without a content-type header
_html_markers = (b'<!doctype html', b'<html', b'<head', b'<body', b'<meta',
                 b'<article', b'<div', b'<p>', b'<p ', b'<rss', b'<feed', b'<?xml')


def _ensure_scheme(url):
    """Existing behaviour: urls without a scheme are treated as plain http."""
    if not url.startswith('http'):
        url = 'http://' + url
    return url


def _strip_jina(url):
    """Remove the proxy prefix, so proxy addresses never leak downstream."""
    if url and url.startswith(jina_prefix):
        return url[len(jina_prefix):]
    return url


def _response_bytes(resp):
    try:
        content = resp.content
    except Exception:  # hand-made responses in tests may only expose text
        content = None
    if isinstance(content, str):
        return content.encode('utf-8', 'replace')
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)
    return _response_text(resp).encode('utf-8', 'replace')


def _response_text(resp):
    try:
        text = resp.text
    except Exception:
        text = None
    return text if isinstance(text, str) else ''


def _response_url(resp, default=''):
    url = getattr(resp, 'url', None)
    return url if isinstance(url, str) and url else default


def _header(resp, name):
    headers = getattr(resp, 'headers', None)
    if not hasattr(headers, 'get'):
        return None
    value = headers.get(name)
    if value is None:  # header names are case-insensitive in requests, not in plain dicts
        try:
            items = headers.items()
        except AttributeError:
            return None
        for key, val in items:
            if isinstance(key, str) and key.lower() == name.lower():
                return val
    return value if isinstance(value, str) else None


def _content_type_family(ct):
    """Map a content-type header onto the parser family, mirroring the old checks.

    Returns None when the header is absent and 'other' for headers we cannot use.
    """
    if ct is None:
        return None
    ct = ct.strip().lower()
    if ct.startswith('application/pdf'):  # may carry a charset suffix
        return ParserType.PDF
    if ct.startswith('text') or 'html' in ct or 'xml' in ct or 'charset' in ct:
        return ParserType.HTML
    return 'other'


def _looks_like_text(raw):
    """Byte-level check, so a non utf-8 text body (gbk, latin-1, ...) still counts."""
    sample = raw[:4096]
    if not sample.strip():
        return False
    printable = sum(1 for byte in sample if byte >= 0x20 or byte in (0x09, 0x0a, 0x0d))
    return printable / len(sample) >= .95


def _sniff_format(raw, text):
    """Detect the body format from the bytes themselves.

    This only lets us notice a missing or wrong content-type header, it never
    replaces the content-type based dispatch on its own.
    """
    if not raw.strip() and not text.strip():
        return ContentFormat.EMPTY
    if b'%PDF-' in raw[:1024]:
        return ContentFormat.PDF
    lowered = raw[:4096].lower()
    if any(marker in lowered for marker in _html_markers):
        return ContentFormat.HTML
    if _looks_like_text(raw):
        return ContentFormat.TEXT
    return ContentFormat.BINARY


def _compatible(family, sniffed):
    """Whether a sniffed body format is consistent with the declared family."""
    if sniffed == ContentFormat.EMPTY:
        # An empty body does not contradict the header; empty html is a normal
        # case that triggers the jina retry, not a mismatch.
        return True
    if family == ParserType.PDF:
        # A pdf payload is binary by nature; only recognizable html/text
        # contradicts the header
        return sniffed in (ContentFormat.PDF, ContentFormat.BINARY)
    if family == ParserType.HTML:
        # Plain text is served through the html extractor as well; bytes we
        # cannot recognize as either are worth flagging.
        return sniffed in (ContentFormat.HTML, ContentFormat.TEXT)
    return False


def _pick_primary_family(declared_family, sniffed):
    """Pick the first parser to try, keeping the historical pdf-before-html order.

    A body that really looks like a pdf is still attempted as a pdf, because pdf
    used to be checked before html. Everything else keeps the old html default,
    including a missing content-type header (which used to be treated as
    'text'), so that the empty-body jina retry still happens exactly as before.
    An unusable header over a body that cannot be recognized still raises.
    """
    if declared_family == ParserType.PDF or sniffed == ContentFormat.PDF:
        return ParserType.PDF
    if declared_family == ParserType.HTML:
        return ParserType.HTML
    if declared_family is None:
        return ParserType.HTML  # old code defaulted to 'text', i.e. the html parser
    if sniffed in (ContentFormat.HTML, ContentFormat.TEXT):
        # An unusable header over a body that is plainly html or text still gets
        # the html branch, so the mismatch is recorded instead of failing.
        return ParserType.HTML
    return None


def _redirects_of(resp):
    """Hops that answered with a redirect before reaching the final response."""
    history = getattr(resp, 'history', None)
    if not isinstance(history, (list, tuple)):
        return ()
    return tuple(_strip_jina(r.url) for r in history
                 if isinstance(getattr(r, 'url', None), str) and r.url)


def _attach(parser, result):
    """Make the dispatch decision inspectable on the returned page object."""
    parser.parse_result = result
    # Convenience aliases for callers that do not want to unwrap parse_result
    parser.final_url = result.final_url
    parser.parser_type = result.parser_type
    parser.via_jina = result.via_jina
    logger.info('Parsed %s with the %s parser%s, response url: %s, sniffed format: %s%s',
                result.final_url, result.parser_type,
                ' (via jina proxy)' if result.via_jina else '',
                result.response_url, result.sniffed_format,
                ''.join('; %s' % note for note in result.notes))
    return parser


def _pdf_has_content(parser):
    """Whether a pdf parser can produce text, only used when the header lied."""
    try:
        return bool(parser.get_content())
    except Exception as e:
        logger.info('Failed to extract text from %s, %s', parser.url, e)
        return False


# dispatcher
def parser_factory(url, use_jina=False):
    """
        Returns the extracted object, which should have at least two
        methods `get_content` and `get_illustration`

        The returned object also exposes `parse_result` (a `ParseResult`) with
        the redirect-resolved final url, the parser type that was used, whether
        the jina proxy served the body, and why the dispatch went that way.
    """
    article_url = _ensure_scheme(url)
    if article_url.startswith(jina_prefix):
        # A proxy url handed in by the caller is still a proxy request
        use_jina = True
    if use_jina:
        # `url` points at the proxy, the article url lives underneath it
        article_url = _strip_jina(article_url)
        request_url = jina_prefix + article_url
        headers = {'x-respond-with': 'html'}
    else:
        request_url = article_url
        headers = None

    resp = session.get(request_url, headers=headers)
    if use_jina:
        resp.raise_for_status()

    response_url = _response_url(resp, request_url)
    # The body came through the proxy if we asked for it, or if a redirect led there
    via_jina = use_jina or response_url.startswith(jina_prefix)
    if via_jina:
        # Restore the original article address: the proxy must never leak into
        # favicon/image resolution, cache keys or anything downstream.
        final_url = article_url
    else:
        # Follow the redirects that actually happened
        final_url = _strip_jina(response_url)

    raw, text = _response_bytes(resp), _response_text(resp)
    declared_ct = _header(resp, 'content-type')
    declared_family = _content_type_family(declared_ct)
    sniffed = _sniff_format(raw, text)
    content_type_missing = declared_ct is None
    content_type_mismatch = declared_family is not None \
        and not _compatible(declared_family, sniffed)
    redirects = _redirects_of(resp)

    notes = []
    if content_type_missing:
        notes.append('content-type header is missing, sniffed body format: %s' % sniffed)
    if content_type_mismatch:
        notes.append('content-type %r disagrees with the sniffed body format %r, '
                     'still trying in the pdf-before-html order'
                     % (declared_ct, sniffed))
    for redirect in redirects:
        notes.append('followed redirect %s' % redirect)

    primary = _pick_primary_family(declared_family, sniffed)
    attempt = ParseAttempt(
        url=request_url,
        response_url=response_url,
        final_url=final_url,
        via_jina=via_jina,
        declared_content_type=declared_ct,
        sniffed_format=sniffed,
        # Shared on purpose: every decision recorded below also belongs to this
        # attempt, so the two views cannot drift apart.
        notes=notes,
    )

    def build_result(parser_type):
        return ParseResult(
            requested_url=article_url,
            final_url=final_url,
            parser_type=parser_type,
            via_jina=via_jina,
            response_url=response_url,
            declared_content_type=declared_ct,
            sniffed_format=sniffed,
            content_type_missing=content_type_missing,
            content_type_mismatch=content_type_mismatch,
            redirected=bool(redirects),
            redirects=redirects,
            notes=list(notes),
            attempts=[attempt],
        )

    # Embeddable detection keeps using the requested article url, exactly as before.
    # The extractor gets the redirect-resolved url so its favicon and relative
    # links point at the page that really served the body; on a proxied request
    # final_url is the restored article url, never the proxy address.
    if EmbeddableExtractor.is_embeddable(article_url):
        logger.info('Get an embeddable to parse(%s), sniffed: %s, via jina: %s',
                    final_url, sniffed, via_jina)
        try:
            parser = EmbeddableExtractor(text, final_url)
            attempt.parser_type = ParserType.EMBEDDABLE
            return _attach(parser, build_result(ParserType.EMBEDDABLE))
        except Exception as e:
            logger.info('%s is not an embeddable, try another(%s)', final_url, e)
            attempt.notes.append('embeddable parser failed, falling back: %s' % e)

    def parse_html(allow_jina=True):
        parser = HtmlContentExtractor(text, final_url)
        attempt.parser_type = ParserType.HTML
        result = build_result(ParserType.HTML)
        if not allow_jina or via_jina or not parser.is_empty():
            return _attach(parser, result)

        logger.info('%s is empty? switch to jina', final_url)
        notes.append('html parser produced an empty body, which is the only '
                     'condition that triggers the jina retry')
        try:
            proxied = parser_factory(jina_prefix + article_url, use_jina=True)
        except Exception as e:
            logger.warning('jina %s throws an error: %s', jina_prefix + article_url, e)
            notes.append('jina retry failed: %s' % e)
            # Refresh the copy taken by the result before the retry was attempted
            result.notes = list(notes)
            return _attach(parser, result)

        proxied_result = proxied.parse_result
        # Keep the proxy address out of the combined result: it reports the
        # article url, while the attempt list still shows both requests.
        result = ParseResult(
            requested_url=article_url,
            final_url=proxied_result.final_url or article_url,
            parser_type=proxied_result.parser_type,
            via_jina=True,
            response_url=proxied_result.response_url,
            declared_content_type=proxied_result.declared_content_type,
            sniffed_format=proxied_result.sniffed_format,
            content_type_missing=proxied_result.content_type_missing,
            content_type_mismatch=proxied_result.content_type_mismatch,
            redirected=bool(redirects) or proxied_result.redirected,
            redirects=redirects + proxied_result.redirects,
            notes=list(notes) + list(proxied_result.notes),
            attempts=[attempt] + proxied_result.attempts,
        )
        result.notes.append('original html body was empty, retried through %s; '
                            'article url restored to %s' % (jina_prefix, result.final_url))
        return _attach(proxied, result)

    if primary == ParserType.PDF:
        length = _header(resp, 'content-length') or '-1'
        try:
            size = humanize.naturalsize(length, binary=True)
        except Exception:
            size = length
        logger.info('Get a pdf to parse, %s, size: %s', final_url, size)
        parser = None
        try:
            parser = PdfExtractor(raw, final_url)
        except ParseError:
            logger.exception('Failed to parse this pdf file, %s', final_url)
            notes.append('pdf parser failed')
        except Exception as e:  # pdfminer raises its own errors on non-pdf input
            logger.info('Failed to parse this pdf file, %s, %s', final_url, e)
            notes.append('pdf parser failed: %s' % e)

        if parser is not None:
            usable = True
            if sniffed in (ContentFormat.HTML, ContentFormat.TEXT):
                # Pdf still went first, as it always did, but the body plainly
                # contradicts the pdf header, so only keep the pdf parser when
                # it can actually produce a text body.
                usable = _pdf_has_content(parser)
                if not usable:
                    notes.append('pdf parser produced no content for a body that '
                                 'looks like %s' % sniffed)
            if usable:
                attempt.parser_type = ParserType.PDF
                return _attach(parser, build_result(ParserType.PDF))

        # Keep the html parser where the old content-type check would have picked
        # it (header says html/text or is missing), and also where the body itself
        # plainly looks like html/text despite a pdf header. Otherwise a response
        # typed as an unusable pdf is still a hard error, as before.
        if declared_family in (ParserType.HTML, None):
            logger.info('%s is not usable as a pdf, continuing with the html branch', final_url)
            notes.append('pdf parser failed, continued with the html branch as the '
                         'content-type %r would have done' % declared_ct)
            return parse_html()
        if sniffed in (ContentFormat.HTML, ContentFormat.TEXT):
            logger.info('%s looks like %s, falling back to the html parser', final_url, sniffed)
            notes.append('pdf parser could not use the body (declared %r, sniffed %r), '
                         'fell back to the html parser' % (declared_ct, sniffed))
            return parse_html(allow_jina=False)
        raise TypeError('I have no idea how the %s is formatted' % declared_ct)

    if primary == ParserType.HTML:
        logger.info('Get a %s to parse, %s', declared_ct or sniffed, final_url)
        return parse_html()

    raise TypeError('I have no idea how the %s is formatted' % declared_ct)
