"""Canonical outcomes for one OTP polling window, shared by every source.

Every ``EmailSource`` implementation reports the same four user-visible
failure classes on ``OtpWaitDiagnostics.failure_class``:

``no_mail``      no message at all reached the mailbox during the window;
``parse_fail``   message(s) arrived, but no usable verification code was parsed;
``stale_code``   a code was parsed, yet it was an old one — already consumed via
                 ``used_codes`` or received before ``newer_than_epoch``;
``imap_error``   the mail-list request itself failed on the round that ended the
                 window (transport, HTTP status, auth, malformed payload).  The
                 name is historical; it also covers HTTP-based sources such as
                 cf_temp / cloudflare / one_mail.

``aborted`` is retained for external cancellation.

Success is not represented here: callers return ``OtpCode(code=..., source=...)``
and record ``matched_*`` timings on the diagnostics.  The invariant that matters
operationally is that a mailbox which *did* receive mail older than the watermark
is never reported as ``no_mail``: it becomes ``stale_code`` when that old mail
carried a code, else ``parse_fail``.

Diagnostics must stay secret-free: these helpers only look at timestamps and
count messages, never at mailbox JWTs, admin keys, or message bodies.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from register_core.contracts import OtpCode, OtpWaitDiagnostics
from register_core.errors import MailMissError

NO_MAIL = "no_mail"
PARSE_FAIL = "parse_fail"
STALE_CODE = "stale_code"
IMAP_ERROR = "imap_error"
ABORTED = "aborted"

FAILURE_CLASSES: frozenset[str] = frozenset(
    {NO_MAIL, PARSE_FAIL, STALE_CODE, IMAP_ERROR, ABORTED}
)

# Legacy / provider-local spellings mapped onto the canonical set.  Keeping these
# means a diagnostic already persisted in a sink can still be normalized without
# inventing a second taxonomy.
_FAILURE_CLASS_ALIASES: dict[str, str] = {
    # list / transport failures
    "list_error": IMAP_ERROR,
    "list_request_error": IMAP_ERROR,
    "mail_list_error": IMAP_ERROR,
    "http_error": IMAP_ERROR,
    "transport_error": IMAP_ERROR,
    "auth_error": IMAP_ERROR,
    "network_error": IMAP_ERROR,
    "imap": IMAP_ERROR,
    # empty window
    "empty": NO_MAIL,
    "no_messages": NO_MAIL,
    "nomail": NO_MAIL,
    "timeout": NO_MAIL,
    "mailbox_empty": NO_MAIL,
    # received but unparsable
    "parse": PARSE_FAIL,
    "parse_error": PARSE_FAIL,
    "parse_failed": PARSE_FAIL,
    "unparsed": PARSE_FAIL,
    "no_code": PARSE_FAIL,
    # old code (used or below the watermark)
    "used": STALE_CODE,
    "used_code": STALE_CODE,
    "used_codes": STALE_CODE,
    "old_code": STALE_CODE,
    "stale": STALE_CODE,
    "watermark": STALE_CODE,
    "before_watermark": STALE_CODE,
    # cancellation
    "abort": ABORTED,
    "cancel": ABORTED,
    "canceled": ABORTED,
    "cancelled": ABORTED,
}

# Timestamp fields seen across one-mail / cf_temp / Cloudflare list payloads.
_MAIL_TIME_KEYS = (
    "timestamp",
    "created_at",
    "createdAt",
    "date",
    "received_at",
    "receivedAt",
    "received",
    "time",
)

_MAX_NOTES_CHARS = 300


class MailListError(Exception):
    """Internal marker: the mail-list request itself failed.

    Carries only a short machine-readable ``notes`` token (``transport:...``,
    ``auth_fail:http_401``, ``http_500``, ``bad_payload``) so neither a mailbox
    JWT nor an admin key can leak into the user-visible ``MailMissError``.
    """

    def __init__(self, message: str = "", *, notes: str = "") -> None:
        super().__init__(message)
        self.notes = notes


def normalize_failure_class(value: str | None) -> str:
    """Return a canonical failure class (``""`` stays ``""`` on success)."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return ""
    if text in FAILURE_CLASSES:
        return text
    return _FAILURE_CLASS_ALIASES.get(text, text)


def mail_epoch(mail: Mapping[str, Any]) -> float | None:
    """Best-effort epoch seconds for one mail row; ``None`` when unknown.

    Numeric values above ``1e11`` are treated as milliseconds, matching
    cf_temp / Cloudflare deployments that report ``timestamp`` in ms.  An
    unknown timestamp is deliberately *not* treated as stale: the server-side
    ``fresh`` window may already have filtered the list.
    """
    for key in _MAIL_TIME_KEYS:
        raw = mail.get(key)
        if raw in (None, ""):
            continue
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            value = float(raw)
            return value / 1000.0 if value > 1e11 else value
        text = str(raw).strip()
        try:
            value = float(text)
        except ValueError:
            pass
        else:
            return value / 1000.0 if value > 1e11 else value
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return None


def mail_is_before_watermark(epoch: float | None, watermark: float | None) -> bool:
    """True when a message provably predates ``newer_than_epoch``.

    A missing timestamp cannot be proven old; such a message is treated as
    fresh and left to the caller's server-side freshness filter.
    """
    if watermark is None or epoch is None:
        return False
    return epoch < float(watermark)


def classify_legacy_failure(message: str) -> tuple[str, str]:
    """Classify an opaque helper error message into the canonical vocabulary.

    ``gmail_imap`` / ``duckmail`` / ``legacy_grok`` call ``grok_register_ttk``
    helpers that only expose a free-form string.  Mapping it here — rather than
    with per-source substring tests and per-source spellings — keeps those
    bridges on the same four classes.  Returns ``(failure_class, notes_token)``.
    """
    text = str(message or "")
    low = text.lower()
    if "auth" in low or "401" in low or "403" in low or "credential" in low:
        return IMAP_ERROR, "auth_fail"
    if "code" in low and ("parse" in text.lower() or "extract" in low or "解析" in text):
        return PARSE_FAIL, ""
    if (
        "timeout" in low
        or "timed out" in low
        or "未收到" in text
        or "没有收到" in text
        or "no mail" in low
        or "empty" in low
    ):
        return NO_MAIL, ""
    return IMAP_ERROR, "transport"


def status_notes(status: int | None) -> str:
    """Notes token for a non-200 mail-list response (no body/headers read)."""
    if not isinstance(status, int):
        return "http_error"
    if status in (401, 403):
        return f"auth_fail:http_{status}"
    return f"http_{status}"


def list_error_notes(exc: BaseException) -> str:
    """Short, secret-free token describing why a mail-list request failed.

    Only an explicit ``notes`` token, the HTTP status, or the exception type is
    used — never a URL query string, request headers, response body, mailbox
    JWT, or admin key.
    """
    explicit = getattr(exc, "notes", "")
    if isinstance(explicit, str) and explicit:
        return explicit
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status_notes(status)
    if isinstance(exc, (ValueError, TypeError)):
        return "bad_payload"
    return f"transport:{type(exc).__name__}"


@dataclass(slots=True)
class PollEvidence:
    """Accumulated facts for one poll window; ``finalize`` picks the class.

    Sources only record observations — they must not pre-decide a class per
    message, otherwise the same situation would again produce different strings
    per source.
    """

    rounds: int = 0
    list_failures: int = 0
    final_round_list_failed: bool = False
    fresh_messages: int = 0
    stale_messages: int = 0
    used_code_hits: int = 0
    stale_code_hits: int = 0

    def finalize(self) -> str:
        """Classify how the window ended (called after the deadline)."""
        if self.used_code_hits:
            # A used code is definitive: the mailbox received mail, but only a
            # code this run had already consumed.
            return STALE_CODE
        if self.fresh_messages:
            # Fresh mail exists but no usable code came out of it.
            return PARSE_FAIL
        if self.stale_code_hits:
            # Only pre-watermark mail, and that mail carried a code: stale,
            # not absent.
            return STALE_CODE
        if self.stale_messages:
            # Only pre-watermark mail without a parsable code.
            return PARSE_FAIL
        if self.list_failures and (
            self.final_round_list_failed or self.list_failures >= self.rounds
        ):
            # "Nothing arrived" is not supportable when the list request itself
            # is what ended the window.
            return IMAP_ERROR
        return NO_MAIL

    def snapshot_into(self, diag: OtpWaitDiagnostics) -> None:
        """Copy counters onto ``OtpWaitDiagnostics`` (class left untouched)."""
        diag.poll_count = self.rounds
        diag.list_error_count = self.list_failures
        diag.stale_message_count = self.stale_messages
        diag.stale_code_count = self.stale_code_hits
        diag.used_code_count = self.used_code_hits

    def apply_to(self, diag: OtpWaitDiagnostics) -> str:
        """Copy counters and set ``failure_class`` to the final classification."""
        self.snapshot_into(diag)
        diag.failure_class = normalize_failure_class(self.finalize())
        return diag.failure_class


def _mail_identity(mail: Mapping[str, Any]) -> Any:
    """Stable key for de-duplicating a mail row across poll rounds."""
    for key in ("id", "message_id", "msgid", "uid"):
        value = mail.get(key)
        if value not in (None, ""):
            return (key, str(value))
    return (
        str(mail.get("subject") or ""),
        mail_epoch(mail),
        str(mail.get("text") or mail.get("body") or ""),
    )


def _append_note(tokens: list[str], token: str) -> None:
    token = str(token or "").strip()
    if token and token not in tokens:
        tokens.append(token)


def _apply_sender_hint(
    mails: list[Mapping[str, Any]],
    sender_hint: str | None,
    sender_key: Callable[[Mapping[str, Any]], str] | None,
    must_match: bool,
) -> list[Mapping[str, Any]]:
    """Order hint-matching mail first; optionally drop the rest.

    ``must_match`` preserves a source's historical behaviour for mailboxes that
    can hold codes from several senders (one_mail).  Either way, the caller has
    already counted every message as delivered, so a hint that filters
    everything out ends as ``parse_fail`` — never as ``no_mail``.
    """
    hint = (sender_hint or "").strip().lower()
    if not hint or sender_key is None:
        return list(mails)
    preferred: list[Mapping[str, Any]] = []
    rest: list[Mapping[str, Any]] = []
    for mail in mails:
        target = preferred if hint in str(sender_key(mail) or "").lower() else rest
        target.append(mail)
    if must_match:
        return preferred
    return (preferred + rest) if preferred else list(mails)


def poll_until_code(
    *,
    provider: str,
    address: str,
    diag: OtpWaitDiagnostics,
    fetch: Callable[[], Iterable[Mapping[str, Any]]],
    extract_code: Callable[[Mapping[str, Any]], str],
    timeout_s: float,
    poll_interval_s: float,
    used_codes: set[str] | None = None,
    newer_than_epoch: float | None = None,
    sender_hint: str | None = None,
    sender_key: Callable[[Mapping[str, Any]], str] | None = None,
    sender_must_match: bool = False,
    note_prefix: str = "",
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> OtpCode:
    """Shared OTP poll loop; returns an :class:`OtpCode` or raises MailMissError.

    ``fetch`` must raise on a failed list request — :class:`MailListError` (or
    anything carrying a ``notes``/``response.status_code``) is reduced to a
    short notes token and the round is retried while the window is open.  An
    error on the final round makes the ending ``imap_error`` rather than
    ``no_mail``; any other exception type propagates unchanged so sources keep
    their existing failure contract.
    """
    started = clock()
    deadline = started + max(0.0, float(timeout_s))
    used = used_codes or set()
    evidence = PollEvidence()
    seen_stale: set[Any] = set()
    note_tokens: list[str] = []
    # Seed with any descriptive note the caller set at construction so flushing
    # evidence tokens extends it instead of erasing it.
    if str(diag.notes or "").strip():
        _append_note(note_tokens, str(diag.notes))
    if note_prefix:
        _append_note(note_tokens, note_prefix)
    poll_sec = max(0.1, float(poll_interval_s))
    # Safety net: if an injected clock does not advance, stop instead of
    # spinning.  Real sources always advance via sleep().
    max_rounds = max(1, int(max(0.0, float(timeout_s)) / poll_sec) + 2)

    def _flush_notes() -> None:
        diag.notes = " ".join(note_tokens)[:_MAX_NOTES_CHARS]

    while True:
        evidence.rounds += 1
        final_round = clock() >= deadline
        evidence.final_round_list_failed = False
        try:
            raw_mails = list(fetch())
        except MailListError as exc:
            # A list request failure is a *classified* ending, not a crash: the
            # round is retried while the window is open, and if the window ends
            # on such a round the ending is imap_error.  Auth failures break
            # immediately — they cannot heal inside one poll window — but still
            # report as imap_error.  Any other exception type (bad login,
            # misconfiguration) propagates unchanged so sources keep their
            # existing failure contract.
            evidence.list_failures += 1
            evidence.final_round_list_failed = True
            note = list_error_notes(exc)
            _append_note(note_tokens, note)
            _flush_notes()
            if final_round or note.startswith("auth_fail"):
                break
            sleep(poll_sec)
            continue

        fresh: list[Mapping[str, Any]] = []
        stale: list[Mapping[str, Any]] = []
        for mail in raw_mails:
            if not isinstance(mail, Mapping):
                continue
            if mail_is_before_watermark(mail_epoch(mail), newer_than_epoch):
                stale.append(mail)
            else:
                fresh.append(mail)

        if stale:
            # Count each pre-watermark message once per window: the same row is
            # re-listed every round, and inflated counters make the note lie
            # about how much old mail the mailbox actually holds.
            new_stale = 0
            for mail in stale:
                key = _mail_identity(mail)
                if key in seen_stale:
                    continue
                seen_stale.add(key)
                new_stale += 1
                try:
                    if extract_code(mail):
                        evidence.stale_code_hits += 1
                except Exception:
                    continue
            evidence.stale_messages += new_stale
            if new_stale:
                _append_note(note_tokens, f"watermark_dropped:{new_stale}")

        if fresh:
            diag.message_scan_count += len(fresh)
            evidence.fresh_messages += len(fresh)
            if diag.first_message_seen_at is None:
                diag.first_message_seen_at = clock()
                diag.first_seen_after_seconds = diag.first_message_seen_at - started
        else:
            diag.empty_rounds += 1

        for mail in _apply_sender_hint(
            fresh, sender_hint, sender_key, sender_must_match
        ):
            try:
                code = str(extract_code(mail) or "").strip()
            except Exception:
                code = ""
            if not code:
                continue
            if code in used:
                evidence.used_code_hits += 1
                _append_note(note_tokens, "used_code")
                continue
            evidence.snapshot_into(diag)
            _flush_notes()
            diag.failure_class = ""
            diag.matched_at = clock()
            diag.matched_after_seconds = diag.matched_at - started
            diag.elapsed_seconds = diag.matched_after_seconds
            return OtpCode(code=code, source=provider)

        _flush_notes()
        if final_round or evidence.rounds >= max_rounds:
            break
        sleep(poll_sec)

    diag.elapsed_seconds = clock() - started
    evidence.apply_to(diag)
    _flush_notes()
    raise MailMissError(
        f"{provider} OTP miss for {address} after {timeout_s:.0f}s "
        f"({diag.failure_class})",
        diagnostics=diag,
    )
