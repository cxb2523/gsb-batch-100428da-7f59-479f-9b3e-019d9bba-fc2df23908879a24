"""Cloudflare Worker temp-mail (cloudflare_temp_email) via grok_register_ttk."""

from __future__ import annotations

import re
import time as _time
from typing import Any

from register_core.contracts import Mailbox, OtpCode, OtpWaitDiagnostics
from register_core.email.outcome import (
    MailListError,
    list_error_notes,
    poll_until_code,
)
from register_core.errors import FailFastError, ProviderError

# Message fields the worker has used across cloudflare_temp_email versions.
_TEXT_FIELDS = ("text", "raw", "content", "intro", "body", "snippet")


class CloudflareSource:
    """Allocate + poll product OTP through the project's Cloudflare Worker.

    Uses the same list/detail helpers as Grok ``email_provider=cloudflare``
    (POST /api/new_address → JWT, GET /messages), but polls them here so the
    ending can be classified with the canonical vocabulary shared by
    one_mail / cf_temp (see :mod:`register_core.email.outcome`).
    """

    name = "cloudflare"

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.last_wait_diagnostics: OtpWaitDiagnostics | None = None

    def _reg(self):
        try:
            import grok_register_ttk as reg  # type: ignore
        except Exception as exc:
            raise FailFastError(f"cloudflare requires grok_register_ttk: {exc}") from exc
        # Ensure config.json / .env overlays are applied (import may have been stubbed).
        try:
            if hasattr(reg, "load_config"):
                reg.load_config()
        except Exception:
            pass
        return reg

    def _api_base(self, reg: Any) -> str:
        base = ""
        try:
            if hasattr(reg, "get_cloudflare_api_base"):
                base = str(reg.get_cloudflare_api_base() or "").strip()
            if not base:
                base = str(reg.config.get("cloudflare_api_base", "") or "").strip()
        except Exception:
            base = ""
        return base

    def allocate(self) -> Mailbox:
        reg = self._reg()
        base = self._api_base(reg)
        if not base:
            raise FailFastError(
                "cloudflare email source requires cloudflare_api_base "
                "(config.json or CLOUDFLARE_API_BASE)"
            )
        prev = None
        try:
            prev = reg.config.get("email_provider")
            reg.config["email_provider"] = "cloudflare"
            address, token = reg.get_email_and_token()
        except FailFastError:
            raise
        except Exception as exc:
            raise ProviderError(f"cloudflare allocate failed: {exc}") from exc
        finally:
            if prev is not None:
                reg.config["email_provider"] = prev
        return Mailbox(address=address, token=token or "", provider=self.name)

    # ------------------------------------------------------------------
    # polling
    # ------------------------------------------------------------------

    @staticmethod
    def _row_text(reg: Any, row: dict[str, Any], api_base: str, token: str) -> str:
        """Join list-item content with any fetchable detail body.

        A failing detail request is not fatal: the list item usually carries
        enough content, and the next poll round retries.  Only content — no
        headers or identifiers — is returned.
        """
        parts = [
            str(row[field])
            for field in _TEXT_FIELDS
            if isinstance(row.get(field), str) and str(row.get(field)).strip()
        ]
        html_list = row.get("html") or []
        if isinstance(html_list, str):
            html_list = [html_list]
        for h in html_list:
            parts.append(re.sub(r"<[^>]+>", " ", str(h)))
        if hasattr(reg, "cloudflare_get_message_detail"):
            msg_id = row.get("id") or row.get("msgid")
            if msg_id:
                try:
                    detail = reg.cloudflare_get_message_detail(api_base, token, msg_id)
                except Exception:
                    detail = None
                if isinstance(detail, dict):
                    for field in _TEXT_FIELDS:
                        value = detail.get(field)
                        if isinstance(value, str) and value.strip():
                            parts.append(value)
                    detail_html = detail.get("html") or []
                    if isinstance(detail_html, str):
                        detail_html = [detail_html]
                    for h in detail_html:
                        parts.append(re.sub(r"<[^>]+>", " ", str(h)))
        return "\n".join(parts)

    @staticmethod
    def _row_subject(row: dict[str, Any]) -> str:
        return str(row.get("subject") or "")

    def _fetch_messages(self, reg: Any, api_base: str, mailbox: Mailbox) -> list[dict[str, Any]]:
        try:
            rows = reg.cloudflare_get_messages(api_base, mailbox.token)
        except Exception as exc:
            raise MailListError(
                f"cloudflare messages failed: {type(exc).__name__}",
                notes=list_error_notes(exc),
            ) from exc
        if not isinstance(rows, list):
            raise MailListError("cloudflare messages payload not a list", notes="bad_payload")
        return [row for row in rows if isinstance(row, dict)]

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
        diag = OtpWaitDiagnostics(
            timeout_s=float(timeout_s),
            provider=self.name,
            sender_hint=(sender_hint or ""),
            notes="cloudflare messages poll",
        )
        self.last_wait_diagnostics = diag
        reg = self._reg()
        base = self._api_base(reg)
        if not base:
            raise FailFastError(
                "cloudflare email source requires cloudflare_api_base "
                "(config.json or CLOUDFLARE_API_BASE)"
            )
        if not hasattr(reg, "cloudflare_get_messages"):
            # The engine must expose the list helper: its opaque get_oai_code
            # wrapper cannot report per-round evidence, so it could not honour
            # newer_than_epoch or produce the shared failure classes.
            raise FailFastError(
                "cloudflare requires grok_register_ttk.cloudflare_get_messages"
            )

        def extract(row: dict[str, Any]) -> str:
            text = self._row_text(reg, row, base, mailbox.token)
            try:
                code = reg.extract_otp_code(text, self._row_subject(row))
            except TypeError:
                # Stub/older signature without ``subject``.
                code = reg.extract_otp_code(text)
            return str(code or "").strip()

        return poll_until_code(
            provider=self.name,
            address=mailbox.address,
            diag=diag,
            fetch=lambda: self._fetch_messages(reg, base, mailbox),
            extract_code=extract,
            sender_key=lambda row: (
                f"{row.get('source', '')}{row.get('from', '')}"
                f"{row.get('address', '')}"
            ),
            timeout_s=float(timeout_s),
            poll_interval_s=float(poll_interval_s),
            used_codes=used_codes,
            newer_than_epoch=newer_than_epoch,
            sender_hint=sender_hint,
            clock=_time.monotonic,
            sleep=lambda seconds: _time.sleep(seconds),
        )

    def release(self, mailbox: Mailbox, *, success: bool) -> None:
        return
