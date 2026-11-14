"""Cloudflare Temp Mail source (``cf_temp``) behind the EmailSource protocol.

Adapted from daimon3332/OutlookRegister (MIT):
https://github.com/daimon3332/OutlookRegister

This source is isolated from ``mail_assets`` / Hotmail Graph REST: it allocates
brand-new throwaway addresses for Outlook recovery binding and OAuth proof, and
never acts as a fallback for the existing pooled sources.

Security: the admin password is read from a named environment variable at call
time. Neither the password, the mailbox JWT, nor raw message bodies are logged
or stored on diagnostics.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

import httpx

from register_core.contracts import Mailbox, OtpCode, OtpWaitDiagnostics
from register_core.email.outcome import (
    MailListError,
    list_error_notes,
    mail_epoch,
    poll_until_code,
)
from register_core.errors import ProviderError

_CODE_PATTERNS = (
    re.compile(r"(?i)(?:code|验证码|security code)[^0-9]{0,20}(\d{6})"),
    re.compile(r"\b(\d{6})\b"),
)

# Re-exported for callers that referenced the helper while it lived here; the
# implementation now lives in register_core.email.outcome so every source uses
# one date normalization.
__all__ = ["TempMailClient", "TempMailSource", "_mail_epoch", "mail_epoch"]
_mail_epoch = mail_epoch


class TempMailClient:
    """Thin HTTP client for a self-hosted CF Temp Mail deployment."""

    def __init__(
        self,
        *,
        base_url: str,
        admin_password_env: str,
        domain: str,
        name_prefix: str = "orx",
        enable_prefix: bool = True,
        timeout: float = 30,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.admin_password_env = admin_password_env
        self.domain = domain
        self.name_prefix = name_prefix
        self.enable_prefix = enable_prefix
        self.timeout = float(timeout)
        self.last_wait_diagnostics: OtpWaitDiagnostics | None = None

    @staticmethod
    def extract_code_from_text(text: str) -> str | None:
        for pattern in _CODE_PATTERNS:
            match = pattern.search(text or "")
            if match:
                return match.group(1)
        return None

    @staticmethod
    def mailbox_from_payload(payload: dict[str, Any]) -> Mailbox:
        """Build a Mailbox from ``/admin/new_address`` JSON (no meta, no secrets)."""
        address = str(payload.get("address") or "").strip()
        if not address:
            raise ProviderError("temp_mail_address_missing")
        return Mailbox(
            address=address,
            token=str(payload.get("jwt") or payload.get("token") or ""),
            provider="cf_temp",
        )

    def _admin_password(self) -> str:
        password = os.environ.get(self.admin_password_env, "")
        if not password:
            raise ProviderError("temp_mail_admin_env_missing")
        return password

    def create_address(self) -> Mailbox:
        if not self.base_url:
            raise ProviderError("temp_mail_base_url_missing")
        try:
            response = httpx.post(
                f"{self.base_url}/admin/new_address",
                headers={"x-admin-auth": self._admin_password()},
                json={
                    "domain": self.domain,
                    "prefix": self.name_prefix if self.enable_prefix else "",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"temp_mail_allocate_failed: {type(exc).__name__}") from exc
        return self.mailbox_from_payload(payload)

    def list_mails(self, mailbox: Mailbox) -> list[dict[str, Any]]:
        """GET one mailbox's message list.

        Failures become :class:`MailListError` carrying only a short notes
        token — the JWT header, URL, and response body never travel with it.
        """
        try:
            response = httpx.get(
                f"{self.base_url}/api/mails",
                headers={"Authorization": f"Bearer {mailbox.token}"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise MailListError(
                f"cf_temp list failed: {type(exc).__name__}",
                notes=list_error_notes(exc),
            ) from exc
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if not isinstance(payload, dict):
            raise MailListError("cf_temp list payload not a list/object", notes="bad_payload")
        rows = payload.get("mails") or payload.get("results") or []
        if not isinstance(rows, list):
            raise MailListError("cf_temp list rows not a list", notes="bad_payload")
        return [row for row in rows if isinstance(row, dict)]

    def wait_for_code(
        self,
        mailbox: Mailbox,
        *,
        timeout_sec: float,
        poll_sec: float,
        after_ts: float | None = None,
        used_codes: set[str] | None = None,
        sender_hint: str | None = None,
    ) -> OtpCode:
        """Poll until a fresh code arrives, else raise a classified MailMissError.

        Outcome classes come from :mod:`register_core.email.outcome`, so this
        source, ``one_mail`` and ``cloudflare`` answer the same way to the same
        situation.  A list request that fails does not end the window early;
        the ending is ``imap_error`` only when the list itself is what ended it,
        and "no mail" is never reported for a mailbox that the list kept failing
        to read.
        """
        diag = OtpWaitDiagnostics(
            timeout_s=float(timeout_sec),
            provider="cf_temp",
            sender_hint=sender_hint or "",
            notes="cf_temp wait_for_code",
        )
        self.last_wait_diagnostics = diag
        return poll_until_code(
            provider="cf_temp",
            address=mailbox.address,
            diag=diag,
            fetch=lambda: self.list_mails(mailbox),
            extract_code=lambda mail: self.extract_code_from_text(
                " ".join(
                    str(mail.get(key, "") or "")
                    for key in ("subject", "text", "body")
                )
            ),
            sender_key=lambda mail: f"{mail.get('source', '')}{mail.get('from', '')}",
            timeout_s=float(timeout_sec),
            poll_interval_s=float(poll_sec),
            used_codes=used_codes,
            newer_than_epoch=after_ts,
            sender_hint=sender_hint,
            clock=time.monotonic,
            sleep=lambda seconds: time.sleep(seconds),
        )

    def release(self, mailbox: Mailbox, *, success: bool) -> None:
        """CF Temp Mail addresses are disposable; nothing to return to a pool."""
        return None


class TempMailSource:
    """EmailSource adapter over TempMailClient, registered as temp_mail/cf_temp."""

    name = "cf_temp"

    def __init__(self, client: TempMailClient | None = None, **options: Any) -> None:
        if client is not None and options:
            raise ProviderError("temp_mail_ambiguous_construction")
        self.client = client if client is not None else _build_client(options)
        self.last_wait_diagnostics: OtpWaitDiagnostics | None = None

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> "TempMailSource":
        return cls(_build_client(options or {}))

    def _mailbox_from_payload(self, payload: dict[str, Any]) -> Mailbox:
        return self.client.mailbox_from_payload(payload)

    def allocate(self) -> Mailbox:
        return self.client.create_address()

    def poll_otp(
        self,
        mailbox: Mailbox,
        *,
        timeout_s: float = 180,
        poll_interval_s: float = 3,
        used_codes: set[str] | None = None,
        newer_than_epoch: float | None = None,
        sender_hint: str | None = None,
    ) -> OtpCode:
        try:
            return self.client.wait_for_code(
                mailbox,
                timeout_sec=timeout_s,
                poll_sec=poll_interval_s,
                after_ts=newer_than_epoch,
                used_codes=used_codes,
                sender_hint=sender_hint,
            )
        finally:
            self.last_wait_diagnostics = self.client.last_wait_diagnostics

    def release(self, mailbox: Mailbox, *, success: bool) -> None:
        self.client.release(mailbox, success=success)


def _build_client(options: dict[str, Any]) -> TempMailClient:
    return TempMailClient(
        base_url=str(options.get("base_url") or ""),
        admin_password_env=str(options.get("admin_password_env") or "CF_TEMP_ADMIN_ENV"),
        domain=str(options.get("domain") or ""),
        name_prefix=str(options.get("name_prefix") or "orx"),
        enable_prefix=bool(options.get("enable_prefix", True)),
        timeout=float(options.get("timeout", 30)),
    )
