#!/usr/bin/env python3
"""Offline tests: canonical OTP poll outcomes + per-source parity.

Everything here is fake — synthetic mail rows, fake HTTP responses, and an
injected clock.  No socket is opened and no real provider is contacted.
"""

from __future__ import annotations

import dataclasses
import unittest
from unittest import mock

import pytest

from register_core.contracts import Mailbox, OtpWaitDiagnostics
from register_core.email.outcome import (
    ABORTED,
    FAILURE_CLASSES,
    IMAP_ERROR,
    NO_MAIL,
    PARSE_FAIL,
    STALE_CODE,
    MailListError,
    PollEvidence,
    list_error_notes,
    mail_epoch,
    mail_is_before_watermark,
    normalize_failure_class,
    poll_until_code,
    status_notes,
)
from register_core.email.sources.cloudflare import CloudflareSource
from register_core.email.sources.one_mail import OneMailSource
from register_core.email.sources.temp_mail import TempMailClient, TempMailSource
from register_core.errors import MailMissError, ProviderError


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fake_clock(start: float = 1000.0, step: float = 0.5):
    """Return (clock, sleep) advancing a synthetic clock; sleep is instant."""
    state = {"now": start}

    def clock() -> float:
        return state["now"]

    def sleep(seconds: float) -> None:
        state["now"] += max(float(seconds), step)

    return clock, sleep


def _run_poll(fetch, *, extract=None, used=None, watermark=None, timeout_s=2.0):
    diag = OtpWaitDiagnostics(provider="test", timeout_s=timeout_s)
    clock, sleep = _fake_clock()
    try:
        otp = poll_until_code(
            provider="test",
            address="a@invalid",
            diag=diag,
            fetch=fetch,
            extract_code=extract or (lambda mail: str(mail.get("code") or "")),
            timeout_s=timeout_s,
            poll_interval_s=0.5,
            used_codes=used,
            newer_than_epoch=watermark,
            clock=clock,
            sleep=sleep,
        )
    except MailMissError as exc:
        return None, exc.diagnostics
    return otp, diag


def _miss_class(fetch, **kw) -> str:
    otp, diag = _run_poll(fetch, **kw)
    assert otp is None, "expected a miss, got a code"
    return diag.failure_class


# ---------------------------------------------------------------------------
# taxonomy + normalization
# ---------------------------------------------------------------------------


class TestTaxonomy(unittest.TestCase):
    def test_four_failure_classes_are_distinct(self) -> None:
        assert {NO_MAIL, PARSE_FAIL, STALE_CODE, IMAP_ERROR} <= FAILURE_CLASSES
        assert len({NO_MAIL, PARSE_FAIL, STALE_CODE, IMAP_ERROR}) == 4
        assert ABORTED in FAILURE_CLASSES

    def test_normalize_keeps_canonical_and_maps_legacy(self) -> None:
        assert normalize_failure_class("no_mail") == NO_MAIL
        assert normalize_failure_class("") == ""
        assert normalize_failure_class(None) == ""
        assert normalize_failure_class("List-Error") == IMAP_ERROR
        assert normalize_failure_class("parse_failed") == PARSE_FAIL
        assert normalize_failure_class("old_code") == STALE_CODE
        # Unknown values are passed through rather than silently becoming no_mail.
        assert normalize_failure_class("quarantined") == "quarantined"

    def test_status_notes_never_include_bodies(self) -> None:
        assert status_notes(401) == "auth_fail:http_401"
        assert status_notes(503) == "http_503"

    def test_list_error_notes_uses_type_or_status_only(self) -> None:
        assert list_error_notes(MailListError("x", notes="http_500")) == "http_500"
        assert list_error_notes(TimeoutError()) == "transport:TimeoutError"
        resp = mock.Mock(status_code=403)
        exc = mock.Mock(response=resp)
        exc.__class__ = type("FakeHttpError", (Exception,), {})
        assert list_error_notes(exc) == "auth_fail:http_403"


class TestMailEpoch(unittest.TestCase):
    def test_reads_epoch_seconds_and_milliseconds(self) -> None:
        assert mail_epoch({"timestamp": 1_700_000_000}) == 1_700_000_000.0
        assert mail_epoch({"timestamp": 1_700_000_000_000}) == 1_700_000_000.0
        assert mail_epoch({"created_at": "1700000000"}) == 1_700_000_000.0

    def test_iso_and_missing(self) -> None:
        assert mail_epoch({"date": "2026-01-01T00:00:00Z"}) is not None
        assert mail_epoch({}) is None
        assert mail_epoch({"timestamp": "not-a-date"}) is None

    def test_watermark_never_treats_unknown_as_old(self) -> None:
        assert mail_is_before_watermark(100.0, 200.0) is True
        assert mail_is_before_watermark(300.0, 200.0) is False
        assert mail_is_before_watermark(200.0, 200.0) is False  # strictly older
        assert mail_is_before_watermark(None, 200.0) is False
        assert mail_is_before_watermark(100.0, None) is False


class TestPollEvidence(unittest.TestCase):
    def test_no_mail(self) -> None:
        assert PollEvidence(rounds=3, fresh_messages=0, stale_messages=0).finalize() == NO_MAIL

    def test_parse_fail_for_fresh_mail_without_code(self) -> None:
        assert PollEvidence(rounds=3, fresh_messages=2).finalize() == PARSE_FAIL

    def test_stale_for_used_code(self) -> None:
        assert PollEvidence(rounds=1, fresh_messages=1, used_code_hits=1).finalize() == STALE_CODE

    def test_stale_for_only_pre_watermark_mail_with_code(self) -> None:
        ev = PollEvidence(rounds=2, stale_messages=1, stale_code_hits=1)
        assert ev.finalize() == STALE_CODE

    def test_pre_watermark_mail_without_code_is_parse_fail_not_no_mail(self) -> None:
        ev = PollEvidence(rounds=2, stale_messages=1, stale_code_hits=0)
        assert ev.finalize() == PARSE_FAIL

    def test_imap_error_when_list_is_what_ended_the_window(self) -> None:
        ev = PollEvidence(rounds=2, list_failures=2, final_round_list_failed=True)
        assert ev.finalize() == IMAP_ERROR

    def test_mail_beats_list_errors(self) -> None:
        # A transient list failure must not hide delivered mail.
        ev = PollEvidence(rounds=2, list_failures=1, fresh_messages=1)
        assert ev.finalize() == PARSE_FAIL


# ---------------------------------------------------------------------------
# shared poll loop
# ---------------------------------------------------------------------------


class TestPollUntilCode(unittest.TestCase):
    def test_success_returns_code_and_provider(self) -> None:
        otp, diag = _run_poll(lambda: [{"code": "123456"}])
        assert otp is not None
        assert otp.code == "123456"
        assert otp.source == "test"
        assert diag.failure_class == ""
        assert diag.matched_after_seconds is not None

    def test_no_mail(self) -> None:
        assert _miss_class(lambda: []) == NO_MAIL

    def test_parse_fail(self) -> None:
        assert _miss_class(lambda: [{"subject": "hello"}]) == PARSE_FAIL

    def test_stale_code_from_used_codes(self) -> None:
        assert (
            _miss_class(lambda: [{"code": "111111"}], used={"111111"}) == STALE_CODE
        )

    def test_watermark_old_mail_with_code_is_stale_not_no_mail(self) -> None:
        fetcher = lambda: [{"code": "222222", "timestamp": 100}]  # noqa: E731
        assert _miss_class(fetcher, watermark=500) == STALE_CODE

    def test_watermark_old_mail_without_code_is_parse_fail(self) -> None:
        fetcher = lambda: [{"subject": "old", "timestamp": 100}]  # noqa: E731
        assert _miss_class(fetcher, watermark=500) == PARSE_FAIL

    def test_watermark_never_returns_old_code_as_new(self) -> None:
        # The only mail is older than the watermark → must be a miss, not a hit.
        fetcher = lambda: [{"code": "333333", "timestamp": 100}]  # noqa: E731
        otp, diag = _run_poll(fetcher, watermark=500)
        assert otp is None
        assert diag.failure_class == STALE_CODE
        assert diag.stale_message_count == 1

    def test_list_error_is_imap_error(self) -> None:
        def boom():
            raise MailListError("nope", notes="transport:ConnectError")

        assert _miss_class(boom) == IMAP_ERROR

    def test_auth_list_error_ends_window_immediately(self) -> None:
        calls = {"n": 0}

        def auth_boom():
            calls["n"] += 1
            raise MailListError("auth", notes="auth_fail:http_401")

        assert _miss_class(auth_boom) == IMAP_ERROR
        assert calls["n"] == 1  # no point re-hitting a rejected credential

    def test_transient_list_error_then_code(self) -> None:
        rounds = iter(
            [
                MailListError("blip", notes="transport:Timeout"),
                [{"code": "444444"}],
            ]
        )

        def fetch():
            item = next(rounds)
            if isinstance(item, Exception):
                raise item
            return item

        otp, _ = _run_poll(fetch)
        assert otp is not None and otp.code == "444444"

    def test_transient_list_error_then_empty_ends_no_mail(self) -> None:
        # The list worked on the final round, so "no mail" IS supportable.
        first = {"done": False}

        def fetch():
            if not first["done"]:
                first["done"] = True
                raise MailListError("blip", notes="transport:Timeout")
            return []

        assert _miss_class(fetch) == NO_MAIL

    def test_unexpected_exception_propagates(self) -> None:
        def boom():
            raise ProviderError("bad credentials")

        with pytest.raises(ProviderError):
            _run_poll(boom)


# ---------------------------------------------------------------------------
# per-source parity: cf_temp / one_mail / cloudflare
# ---------------------------------------------------------------------------


class _HttpResp:
    """Minimal fake HTTP response (no network)."""

    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise _HttpStatusError(self.status_code)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _HttpStatusError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"http {status_code}")
        self.response = mock.Mock(status_code=status_code)


class _FakeCloudflareReg:
    """Stand-in for grok_register_ttk's cloudflare helpers."""

    def __init__(self, rows=None, error: Exception | None = None):
        self.config = {"cloudflare_api_base": "https://cf.invalid"}
        self.rows = rows if rows is not None else []
        self.error = error
        self.list_calls = 0

    def get_cloudflare_api_base(self) -> str:
        return self.config["cloudflare_api_base"]

    def cloudflare_get_messages(self, api_base, token):
        self.list_calls += 1
        if self.error is not None:
            raise self.error
        return self.rows

    def cloudflare_get_message_detail(self, api_base, token, message_id):
        return {}

    def extract_otp_code(self, text, subject=""):
        # The fake rows carry the code directly; keep decode trivial/offline.
        for line in str(text).splitlines():
            if line.startswith("CODE:"):
                return line.split(":", 1)[1].strip()
        return ""


def _cf_temp_source(monkeypatch, rows=None, error: Exception | None = None) -> TempMailSource:
    source = TempMailSource(
        base_url="https://mail.invalid",
        admin_password_env="TEST_TEMP_MAIL_ADMIN",
        domain="invalid",
    )

    def fake_list(self, mailbox):
        if error is not None:
            raise error
        return list(rows or [])

    monkeypatch.setattr(TempMailClient, "list_mails", fake_list)
    monkeypatch.setattr(
        "register_core.email.sources.temp_mail.time.sleep", lambda _s: None
    )
    return source


def _one_mail_source(monkeypatch, rows=None, error: Exception | None = None, admin=True):
    source = OneMailSource(
        base_url="https://m.invalid",
        auth_mode="admin" if admin else "password",
        admin_key="synthetic-admin-key",
        domains="a.invalid",
    ) if admin else OneMailSource(
        base_url="https://m.invalid", emails="pool@x.invalid:pw"
    )
    mailbox = source.allocate()

    def fake_fetch(*args, **kwargs):
        if error is not None:
            raise error
        return list(rows or [])

    if admin:
        monkeypatch.setattr(source, "_fetch_codes_admin", fake_fetch)
    else:
        monkeypatch.setattr(source, "_login", lambda *a, **k: "synthetic-jwt")
        monkeypatch.setattr(source, "_fetch_codes", fake_fetch)
    monkeypatch.setattr(
        "register_core.email.sources.one_mail._time.sleep", lambda _s: None
    )
    return source, mailbox


def _cloudflare_source(monkeypatch, rows=None, error: Exception | None = None):
    reg = _FakeCloudflareReg(rows=rows, error=error)
    source = CloudflareSource()
    monkeypatch.setattr(source, "_reg", lambda: reg)
    monkeypatch.setattr(
        "register_core.email.sources.cloudflare._time.sleep", lambda _s: None
    )
    mailbox = Mailbox(address="a@invalid", token="synthetic-jwt", provider="cloudflare")
    return source, mailbox


def _poll_source(source, mailbox, **kw):
    try:
        otp = source.poll_otp(mailbox, timeout_s=kw.pop("timeout_s", 1.0), **kw)
    except MailMissError as exc:
        return None, exc.diagnostics, exc
    return otp, source.last_wait_diagnostics, None


# Scenario → expected class, expressed once and reused for all three sources.
_SCENARIOS = {
    NO_MAIL: [],
    PARSE_FAIL: [{"subject": "welcome", "text": "no digits here"}],
    STALE_CODE: [{"code": "111111", "text": "CODE:111111"}],
}


@pytest.mark.parametrize("expected", [NO_MAIL, PARSE_FAIL, STALE_CODE])
def test_three_sources_agree_on_failure_class(monkeypatch, expected):
    rows = _SCENARIOS[expected]
    used = {"111111"} if expected == STALE_CODE else None

    cf = _cf_temp_source(monkeypatch, rows=rows)
    cf_mb = Mailbox(address="a@invalid", token="synthetic-jwt", provider="cf_temp")
    _, cf_diag, cf_exc = _poll_source(cf, cf_mb, used_codes=used)

    om, om_mb = _one_mail_source(monkeypatch, rows=_one_mail_rows(rows))
    _, om_diag, om_exc = _poll_source(om, om_mb, used_codes=used)

    cfs, cfs_mb = _cloudflare_source(monkeypatch, rows=_cloudflare_rows(rows))
    _, cfs_diag, cfs_exc = _poll_source(cfs, cfs_mb, used_codes=used)

    assert cf_diag.failure_class == expected
    assert om_diag.failure_class == expected
    assert cfs_diag.failure_class == expected
    for exc in (cf_exc, om_exc, cfs_exc):
        assert exc is not None
        assert exc.diagnostics.failure_class == expected
        # No secret material may leak into the user-visible message.
        assert "synthetic-jwt" not in str(exc)
        assert "synthetic-admin-key" not in str(exc)


def _one_mail_rows(rows):
    """Translate the neutral scenario rows into one-mail's verifcodes shape."""
    out = []
    for row in rows:
        item = {"subject": row.get("subject", ""), "from_addr": "noreply@x.invalid"}
        if "code" in row:
            item["code"] = row["code"]
        elif row.get("text"):
            item["code"] = ""  # arrived mail, no server-extracted code
        out.append(item)
    return out


def _cloudflare_rows(rows):
    """Translate into the worker's message-list shape (CODE: NN inside text)."""
    out = []
    for row in rows:
        item = {"id": "m1", "subject": row.get("subject", ""), "from": "noreply@x.invalid"}
        text = row.get("text", "")
        if "code" in row:
            text = f"{text}\nCODE:{row['code']}"
        item["text"] = text
        out.append(item)
    return out


@pytest.mark.parametrize("expected", [NO_MAIL, PARSE_FAIL, STALE_CODE, IMAP_ERROR])
def test_three_sources_agree_with_watermark(monkeypatch, expected):
    """Old-only mail must never be a hit, nor be reported as no_mail."""
    if expected == NO_MAIL:
        rows = []
    elif expected == PARSE_FAIL:
        rows = [{"subject": "old", "timestamp": 100}]
    elif expected == STALE_CODE:
        rows = [{"code": "555555", "text": "CODE:555555", "timestamp": 100}]
    else:
        rows = None  # list request itself fails

    # cf_temp
    cf = _cf_temp_source(
        monkeypatch,
        rows=rows,
        error=MailListError("down", notes="http_500") if rows is None else None,
    )
    cf_mb = Mailbox(address="a@invalid", token="synthetic-jwt", provider="cf_temp")
    _, cf_diag, _ = _poll_source(cf, cf_mb, newer_than_epoch=500)

    # one_mail: timestamp comes from created_at in server rows
    om_rows = _one_mail_rows(rows or [])
    for row, src in zip(om_rows, rows or []):
        row["created_at"] = src.get("timestamp")
    om, om_mb = _one_mail_source(
        monkeypatch, rows=om_rows,
        error=MailListError("down", notes="http_500") if rows is None else None,
    )
    _, om_diag, _ = _poll_source(om, om_mb, newer_than_epoch=500)

    # cloudflare
    cf_rows = _cloudflare_rows(rows or [])
    for row, src in zip(cf_rows, rows or []):
        row["created_at"] = src.get("timestamp")
    cfs, cfs_mb = _cloudflare_source(
        monkeypatch, rows=cf_rows,
        error=MailListError("down", notes="http_500") if rows is None else None,
    )
    _, cfs_diag, _ = _poll_source(cfs, cfs_mb, newer_than_epoch=500)

    assert cf_diag.failure_class == expected
    assert om_diag.failure_class == expected
    assert cfs_diag.failure_class == expected


# ---------------------------------------------------------------------------
# per-source success paths + HTTP-boundary behaviour
# ---------------------------------------------------------------------------


def test_cf_temp_success_and_fake_http_list(monkeypatch):
    client = TempMailClient(
        base_url="https://mail.invalid",
        admin_password_env="TEST_TEMP_MAIL_ADMIN",
        domain="invalid",
    )
    calls = {"n": 0}

    def fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        return _HttpResp(200, [{"subject": "Security code", "text": "code 123456"}])

    monkeypatch.setattr("register_core.email.sources.temp_mail.httpx.get", fake_get)
    source = TempMailSource(client=client)
    mailbox = Mailbox(address="a@invalid", token="synthetic-jwt", provider="cf_temp")
    otp = source.poll_otp(mailbox, timeout_s=1.0, poll_interval_s=0.01)
    assert otp.code == "123456"
    assert otp.source == "cf_temp"
    assert source.last_wait_diagnostics.failure_class == ""
    assert calls["n"] == 1


def test_cf_temp_http_500_is_imap_error_without_secrets(monkeypatch):
    client = TempMailClient(
        base_url="https://mail.invalid",
        admin_password_env="TEST_TEMP_MAIL_ADMIN",
        domain="invalid",
    )
    monkeypatch.setattr(
        "register_core.email.sources.temp_mail.httpx.get",
        lambda url, headers=None, timeout=None: _HttpResp(500, {}),
    )
    monkeypatch.setattr(
        "register_core.email.sources.temp_mail.time.sleep", lambda _s: None
    )
    source = TempMailSource(client=client)
    mailbox = Mailbox(address="a@invalid", token="synthetic-jwt", provider="cf_temp")
    with pytest.raises(MailMissError) as ctx:
        source.poll_otp(mailbox, timeout_s=0.3, poll_interval_s=0.01)
    diag = ctx.value.diagnostics
    assert diag.failure_class == IMAP_ERROR
    assert "http_500" in diag.notes
    assert "synthetic-jwt" not in str(ctx.value)
    assert "synthetic-jwt" not in diag.notes


def test_cf_temp_bad_json_is_imap_error(monkeypatch):
    client = TempMailClient(
        base_url="https://mail.invalid",
        admin_password_env="TEST_TEMP_MAIL_ADMIN",
        domain="invalid",
    )
    monkeypatch.setattr(
        "register_core.email.sources.temp_mail.httpx.get",
        lambda url, headers=None, timeout=None: _HttpResp(200, ValueError("no json")),
    )
    monkeypatch.setattr(
        "register_core.email.sources.temp_mail.time.sleep", lambda _s: None
    )
    source = TempMailSource(client=client)
    mailbox = Mailbox(address="a@invalid", token="synthetic-jwt", provider="cf_temp")
    with pytest.raises(MailMissError) as ctx:
        source.poll_otp(mailbox, timeout_s=0.3, poll_interval_s=0.01)
    assert ctx.value.diagnostics.failure_class == IMAP_ERROR
    assert "bad_payload" in ctx.value.diagnostics.notes


def test_one_mail_success_with_fake_http(monkeypatch):
    source = OneMailSource(base_url="https://m.invalid", emails="pool@x.invalid:pw")
    mailbox = source.allocate()
    monkeypatch.setattr(source, "_login", lambda *a, **k: "synthetic-jwt")
    monkeypatch.setattr(
        "register_core.email.sources.one_mail.requests.get",
        lambda url, headers=None, params=None, timeout=None: _HttpResp(
            200, {"results": [{"code": "654321", "from_addr": "noreply@x.invalid"}]}
        ),
    )
    otp = source.poll_otp(mailbox, timeout_s=1.0, poll_interval_s=0.01)
    assert otp.code == "654321"
    assert otp.source == "one_mail"
    assert source.last_wait_diagnostics.failure_class == ""


def test_one_mail_http_500_is_imap_error(monkeypatch):
    source = OneMailSource(base_url="https://m.invalid", emails="pool@x.invalid:pw")
    mailbox = source.allocate()
    monkeypatch.setattr(source, "_login", lambda *a, **k: "synthetic-jwt")
    monkeypatch.setattr(
        "register_core.email.sources.one_mail.requests.get",
        lambda url, headers=None, params=None, timeout=None: _HttpResp(500, {}),
    )
    monkeypatch.setattr(
        "register_core.email.sources.one_mail._time.sleep", lambda _s: None
    )
    with pytest.raises(MailMissError) as ctx:
        source.poll_otp(mailbox, timeout_s=0.3, poll_interval_s=0.01)
    assert ctx.value.diagnostics.failure_class == IMAP_ERROR
    assert "http_500" in ctx.value.diagnostics.notes


def test_cloudflare_success(monkeypatch):
    source, mailbox = _cloudflare_source(
        monkeypatch,
        rows=[{"id": "m1", "text": "CODE:987654", "subject": "verification code"}],
    )
    otp = source.poll_otp(mailbox, timeout_s=1.0, poll_interval_s=0.01)
    assert otp.code == "987654"
    assert otp.source == "cloudflare"
    assert source.last_wait_diagnostics.failure_class == ""


def test_cloudflare_list_error_is_imap_error(monkeypatch):
    source, mailbox = _cloudflare_source(
        monkeypatch, error=TimeoutError("read timed out")
    )
    with pytest.raises(MailMissError) as ctx:
        source.poll_otp(mailbox, timeout_s=0.3, poll_interval_s=0.01)
    diag = ctx.value.diagnostics
    assert diag.failure_class == IMAP_ERROR
    assert diag.list_error_count >= 1
    assert "synthetic-jwt" not in str(ctx.value)


def test_registry_sources_expose_protocol_shape():
    # Sanity: all three still implement the EmailSource convention.
    for cls in (TempMailSource, OneMailSource, CloudflareSource):
        assert hasattr(cls, "allocate")
        assert hasattr(cls, "poll_otp")
        assert hasattr(cls, "release")


def test_sender_hint_mismatch_is_parse_fail_not_no_mail(monkeypatch):
    """Delivered mail that the hint filters out was still delivered.

    one_mail keeps its sender filter (a shared pool can hold several
    products' codes), but the ending must say ``parse_fail`` — the operator
    should fix the hint/decoder, not conclude that no mail arrived.
    """
    rows = [{"from_addr": "someone-else@x.invalid", "code": "123456", "subject": "x"}]
    source, mailbox = _one_mail_source(monkeypatch, rows=rows)
    _, diag, exc = _poll_source(source, mailbox, sender_hint="accountprotection")
    assert exc is not None
    assert diag.failure_class == PARSE_FAIL
    assert diag.message_scan_count >= 1


def test_cf_temp_sender_hint_prefers_but_does_not_hide(monkeypatch):
    """cf_temp/cloudflare prefer hint matches yet still accept other senders."""
    rows = [
        {"from": "other@x.invalid", "subject": "hi", "text": "code 111111"},
        {"from": "noreply@openai.com", "subject": "code", "text": "code 222222"},
    ]
    source = _cf_temp_source(monkeypatch, rows=rows)
    mailbox = Mailbox(address="a@invalid", token="synthetic-jwt", provider="cf_temp")
    otp, diag, exc = _poll_source(source, mailbox, sender_hint="openai")
    assert exc is None
    assert otp is not None and otp.code == "222222"
    assert diag.failure_class == ""


def test_success_reports_code_and_source_for_all_three(monkeypatch):
    cf = _cf_temp_source(monkeypatch, rows=[{"subject": "code", "text": "code 101010"}])
    cf_mb = Mailbox(address="a@invalid", token="synthetic-jwt", provider="cf_temp")
    cf_otp, cf_diag, _ = _poll_source(cf, cf_mb)
    assert (cf_otp.code, cf_otp.source, cf_diag.failure_class) == (
        "101010", "cf_temp", "",
    )

    om, om_mb = _one_mail_source(
        monkeypatch, rows=[{"code": "202020", "from_addr": "noreply@x.invalid"}]
    )
    om_otp, om_diag, _ = _poll_source(om, om_mb)
    assert (om_otp.code, om_otp.source, om_diag.failure_class) == (
        "202020", "one_mail", "",
    )

    cfs, cfs_mb = _cloudflare_source(
        monkeypatch, rows=[{"id": "m1", "subject": "code", "text": "CODE:303030"}]
    )
    cfs_otp, cfs_diag, _ = _poll_source(cfs, cfs_mb)
    assert (cfs_otp.code, cfs_otp.source, cfs_diag.failure_class) == (
        "303030", "cloudflare", "",
    )


def test_diagnostics_never_carry_secret_material(monkeypatch):
    """Every ending's diagnostics must be safe to persist as an artifact."""
    cf = _cf_temp_source(monkeypatch, rows=[])
    cf_mb = Mailbox(address="a@invalid", token="synthetic-jwt", provider="cf_temp")
    _, cf_diag, _ = _poll_source(cf, cf_mb)

    om, om_mb = _one_mail_source(monkeypatch, rows=[])
    _, om_diag, _ = _poll_source(om, om_mb)

    cfs, cfs_mb = _cloudflare_source(monkeypatch, rows=[])
    _, cfs_diag, _ = _poll_source(cfs, cfs_mb)

    for diag in (cf_diag, om_diag, cfs_diag):
        blob = repr(dataclasses.asdict(diag))
        assert "synthetic-jwt" not in blob
        assert "synthetic-admin-key" not in blob
        # Only counters, timings, and short tokens are persisted.
        assert diag.failure_class in FAILURE_CLASSES


if __name__ == "__main__":
    raise SystemExit(unittest.main())
