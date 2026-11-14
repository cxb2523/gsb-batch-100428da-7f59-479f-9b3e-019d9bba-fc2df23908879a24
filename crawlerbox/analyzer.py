"""Batch analysis of reported emails.

This module owns the fetch -> download -> parse pipeline.  It deliberately
imports only lightweight dependencies so that the inbox contract and the
per-email outcome handling can be used (and tested) without the heavy parsing
stack (PDF/QR/OCR/...).  ``phishparser`` is imported lazily, only when an email
actually has to be parsed.

Inbox contract
--------------
An inbox is an iterable of mappings.  Every mapping must contain:

* ``"phish_id"``: the identifier of the reported email (also passed to the
  parser and used as the deduplication key);
* ``"rawUrl"``: the URL from which the raw EML is downloaded.

The same ``phish_id`` is used for fetching, in-batch deduplication, outcome
reporting and parsing.  There is no second identifier such as ``"id"``.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Optional, Union

import requests

from .personalized_config import INBOX_ID_KEY, INBOX_RAW_URL_KEY
from .phish_logger import Phish_Logger

logger = Phish_Logger.get_phish_logger('phish_logs')

# Finite connect/read timeouts (seconds) so that one stalled download cannot
# block the whole batch.
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 30.0
DEFAULT_DOWNLOAD_TIMEOUT = (DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT)

TimeoutType = Union[float, tuple]


class OutcomeStatus:
    """Possible endings of a single inbox entry."""

    SUCCESS = "success"
    DOWNLOAD_FAILED = "download_failed"
    DUPLICATE_SKIPPED = "duplicate_skipped"
    PARSE_FAILED = "parse_failed"
    INVALID_ENTRY = "invalid_entry"

    ALL = (
        SUCCESS,
        DOWNLOAD_FAILED,
        DUPLICATE_SKIPPED,
        PARSE_FAILED,
        INVALID_ENTRY,
    )


@dataclass(frozen=True)
class EmailOutcome:
    """The ending of one inbox entry.

    Callers receive one ``EmailOutcome`` per inbox entry, in inbox order.
    ``status`` is one of :class:`OutcomeStatus`; ``reason`` is a human readable
    explanation for anything that is not a success.
    """

    phish_id: Optional[str]
    raw_url: Optional[str]
    status: str
    reason: Optional[str] = None

    def to_dict(self):
        return {
            'phish_id': self.phish_id,
            'raw_url': self.raw_url,
            'status': self.status,
            'reason': self.reason,
        }

    # Mapping-style access keeps the outcomes easy to consume/serialize for
    # callers that prefer dictionaries.
    def __getitem__(self, key):
        data = self.to_dict()
        if key in data:
            return data[key]
        raise KeyError(key)

    def get(self, key, default=None):
        return self.to_dict().get(key, default)


class EmailDownloadError(Exception):
    """Raised when the raw EML of a single email cannot be downloaded."""


def _format_exception(exc: BaseException) -> str:
    text = ' '.join(str(exc).split())
    if len(text) > 500:
        text = text[:497] + '...'
    return f'{type(exc).__name__}: {text}' if text else type(exc).__name__


def _format_timeout(timeout: TimeoutType) -> str:
    if isinstance(timeout, (tuple, list)) and len(timeout) == 2:
        return f'connect={timeout[0]}s, read={timeout[1]}s'
    return f'{timeout}s'


def download_eml(raw_url, timeout: TimeoutType = DEFAULT_DOWNLOAD_TIMEOUT):
    """Download one raw EML.

    Raises :class:`EmailDownloadError` for connection errors, timeouts,
    non-2xx responses and empty response bodies.  The error message is meant to
    be shown to the caller as the outcome reason.
    """
    try:
        response = requests.get(raw_url, allow_redirects=True, timeout=timeout)
    except requests.Timeout as exc:
        raise EmailDownloadError(
            f'request timed out ({_format_timeout(timeout)}) {_format_exception(exc)}'
        ) from exc
    except requests.ConnectionError as exc:
        raise EmailDownloadError(f'connection error {_format_exception(exc)}') from exc
    except requests.RequestException as exc:
        raise EmailDownloadError(f'download error {_format_exception(exc)}') from exc
    except Exception as exc:  # a malformed rawUrl must not stop the batch either
        raise EmailDownloadError(f'invalid download {_format_exception(exc)}') from exc

    if not 200 <= response.status_code < 300:
        status = f'HTTP {response.status_code}'
        if response.reason:
            status += f' {response.reason}'
        raise EmailDownloadError(status)

    content = response.content
    if not content:
        raise EmailDownloadError('empty response body')
    return content


def parse_data(phish_id, rawemail_inbytes):
    """Lazily load the real parser.

    Kept as an indirection so callers/tests can inject a parser and so that
    importing the batch pipeline does not require the full parsing stack.
    """
    from .phishparser import parse_data as _parse_data

    return _parse_data(phish_id, rawemail_inbytes)


def _coerce_inbox(inbox) -> list:
    if inbox is None:
        return []
    if isinstance(inbox, Mapping):
        # Tolerate a single entry being passed instead of a list of entries.
        return [inbox]
    if isinstance(inbox, (str, bytes)):
        logger.error('Inbox must be a list of mappings, got %s; nothing to analyze', type(inbox).__name__)
        return []
    try:
        return list(inbox)
    except TypeError:
        logger.error('Inbox is not iterable (got %s); nothing to analyze', type(inbox).__name__)
        return []


def _read_entry(entry):
    """Return ``(phish_id, raw_url, problem)`` for one inbox entry."""
    if not isinstance(entry, Mapping):
        return None, None, (
            f'inbox entry is {type(entry).__name__}, expected a mapping with keys '
            f'{INBOX_ID_KEY!r} and {INBOX_RAW_URL_KEY!r}'
        )

    if INBOX_ID_KEY not in entry:
        hint = ''
        if 'id' in entry:
            hint = f"; the inbox contract uses {INBOX_ID_KEY!r}, not 'id'"
        keys = sorted(str(key) for key in entry.keys())
        return None, None, f'missing required inbox key {INBOX_ID_KEY!r} (keys found: {keys}){hint}'

    id_value = entry[INBOX_ID_KEY]
    if id_value is None or not str(id_value).strip():
        return None, None, f'inbox key {INBOX_ID_KEY!r} is empty'
    phish_id = str(id_value)

    raw_url = entry.get(INBOX_RAW_URL_KEY)
    if not isinstance(raw_url, str) or not raw_url.strip():
        return phish_id, None, (
            f'missing or invalid required inbox key {INBOX_RAW_URL_KEY!r} for phish_id {phish_id!r}'
        )

    return phish_id, raw_url, None


def count_outcomes(outcomes: Sequence[EmailOutcome]):
    """Count outcomes per status (all statuses are always present)."""
    counts = dict.fromkeys(OutcomeStatus.ALL, 0)
    for outcome in outcomes:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
    return counts


def summarize_text(outcomes: Sequence[EmailOutcome]) -> str:
    """Readable one-line summary of a batch."""
    counts = count_outcomes(outcomes)
    return (
        f'Analysis outcomes for {len(outcomes)} inbox entries: '
        f"{counts[OutcomeStatus.SUCCESS]} succeeded, "
        f"{counts[OutcomeStatus.DOWNLOAD_FAILED]} failed to download, "
        f"{counts[OutcomeStatus.PARSE_FAILED]} failed to parse, "
        f"{counts[OutcomeStatus.DUPLICATE_SKIPPED]} duplicate(s) skipped, "
        f"{counts[OutcomeStatus.INVALID_ENTRY]} invalid entr(ies)"
    )


def format_report(outcomes: Sequence[EmailOutcome]) -> str:
    """Human readable, per-email report: one line per inbox entry + summary."""
    lines = []
    for position, outcome in enumerate(outcomes, start=1):
        line = f'[{position}] phish_id={outcome.phish_id} status={outcome.status}'
        if outcome.reason:
            line += f' reason={outcome.reason}'
        lines.append(line)
    lines.append(summarize_text(outcomes))
    return '\n'.join(lines)


def analyze(
    inbox,
    timeout: TimeoutType = DEFAULT_DOWNLOAD_TIMEOUT,
    parser: Optional[Callable[[str, bytes], object]] = None,
) -> list:
    """Download and parse every email of ``inbox``, one outcome per entry.

    Download/parse problems are contained to the affected email: the rest of
    the batch keeps going.  Entries sharing the same ``phish_id`` are parsed
    once per batch; later occurrences are reported as
    ``OutcomeStatus.DUPLICATE_SKIPPED`` (deduplication is keyed on the
    identifier and applies whatever the first occurrence's ending was).
    Cross-batch deduplication is not attempted.

    Returns the list of :class:`EmailOutcome`, in inbox order.
    """
    if parser is None:
        parser = parse_data

    entries = _coerce_inbox(inbox)
    total = len(entries)
    outcomes = []
    first_seen = {}

    if not entries:
        logger.warning('No reported emails to analyze')

    for position, entry in enumerate(entries, start=1):
        phish_id, raw_url, problem = _read_entry(entry)

        if problem:
            logger.warning('[%d//%d] Skipping invalid inbox entry: %s', position, total, problem)
            outcomes.append(EmailOutcome(phish_id, raw_url, OutcomeStatus.INVALID_ENTRY, problem))
            continue

        if phish_id in first_seen:
            reason = (
                f"duplicate {INBOX_ID_KEY} {phish_id!r} in this batch "
                f"(first occurrence at position {first_seen[phish_id]}); parsed only once per batch"
            )
            logger.info('[%d//%d] Skipping duplicate phish email id: %s', position, total, phish_id)
            outcomes.append(
                EmailOutcome(phish_id, raw_url, OutcomeStatus.DUPLICATE_SKIPPED, reason)
            )
            continue
        first_seen[phish_id] = position

        logger.info('[%d//%d] Parsing phish email id: %s', position, total, phish_id)
        try:
            rawemail_inbytes = download_eml(raw_url, timeout=timeout)
        except EmailDownloadError as exc:
            reason = str(exc)
            logger.error(
                '[%d//%d] Download failed for phish email id %s (%s): %s',
                position, total, phish_id, raw_url, reason,
            )
            outcomes.append(
                EmailOutcome(phish_id, raw_url, OutcomeStatus.DOWNLOAD_FAILED, reason)
            )
            continue

        try:
            parser_result = parser(phish_id, rawemail_inbytes)
        except Exception as exc:
            reason = _format_exception(exc)
            logger.error(
                '[%d//%d] Parsing failed for phish email id %s: %s',
                position, total, phish_id, reason,
            )
            outcomes.append(EmailOutcome(phish_id, raw_url, OutcomeStatus.PARSE_FAILED, reason))
            continue

        # The parser reports failure explicitly (e.g. database error) without
        # raising; surface that instead of claiming success.
        if parser_result is False:
            reason = 'parser reported failure, see the crawler logs for details'
            logger.error(
                '[%d//%d] Parsing reported failure for phish email id %s',
                position, total, phish_id,
            )
            outcomes.append(EmailOutcome(phish_id, raw_url, OutcomeStatus.PARSE_FAILED, reason))
            continue

        logger.info(
            '[%d//%d] Parsed phish email id: %s (%d bytes)',
            position, total, phish_id, len(rawemail_inbytes),
        )
        outcomes.append(EmailOutcome(phish_id, raw_url, OutcomeStatus.SUCCESS))

    logger.info('%s', summarize_text(outcomes))
    return outcomes
