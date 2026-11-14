"""Gmail IMAP catch-all source via existing grok_register_ttk helpers."""

from __future__ import annotations

import time as _time
from typing import Any

from register_core.contracts import Mailbox, OtpCode, OtpWaitDiagnostics
from register_core.email.outcome import (
    NO_MAIL,
    STALE_CODE,
    classify_legacy_failure,
    normalize_failure_class,
)
from register_core.errors import FailFastError, MailMissError, ProviderError


class GmailImapSource:
    name = "gmail_imap"

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.last_wait_diagnostics = None

    def _reg(self):
        try:
            import grok_register_ttk as reg  # type: ignore
        except Exception as exc:
            raise FailFastError(f"gmail_imap requires grok_register_ttk: {exc}") from exc
        return reg

    def allocate(self) -> Mailbox:
        reg = self._reg()
        prev = None
        try:
            prev = reg.config.get("email_provider")
            reg.config["email_provider"] = "gmail"
            address, token = reg.get_email_and_token()
        except Exception as exc:
            raise ProviderError(f"gmail allocate failed: {exc}") from exc
        finally:
            if prev is not None:
                reg.config["email_provider"] = prev
        return Mailbox(address=address, token=token or "", provider=self.name)

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
        started = _time.time()
        diag = OtpWaitDiagnostics(
            timeout_s=float(timeout_s),
            provider=self.name,
            sender_hint=(sender_hint or ""),
            notes="wraps grok_register_ttk.get_oai_code",
        )
        self.last_wait_diagnostics = diag
        reg = self._reg()
        prev = None
        code = None
        try:
            prev = reg.config.get("email_provider")
            reg.config["email_provider"] = "gmail"
            diag.poll_count = 1
            code = reg.get_oai_code(
                mailbox.token,
                mailbox.address,
                timeout=int(timeout_s),
                poll_interval=int(poll_interval_s),
            )
        except Exception as exc:
            # The helper only exposes a free-form message; classify it into the
            # same four outcomes every source uses (register_core.email.outcome)
            # instead of a gmail-private spelling.
            failure, note = classify_legacy_failure(str(exc))
            diag.failure_class = normalize_failure_class(failure)
            diag.list_error_count = 1
            if note:
                diag.notes = ((diag.notes or "") + f" {note}").strip()
            diag.elapsed_seconds = _time.time() - started
            self.last_wait_diagnostics = diag
            raise MailMissError(
                f"gmail OTP failed ({diag.failure_class})", diagnostics=diag
            ) from exc
        finally:
            if prev is not None:
                reg.config["email_provider"] = prev
        diag.elapsed_seconds = _time.time() - started
        if not code:
            diag.failure_class = NO_MAIL
            self.last_wait_diagnostics = diag
            raise MailMissError(
                f"gmail empty OTP for {mailbox.address}",
                diagnostics=diag,
            )
        if used_codes and code in used_codes:
            diag.failure_class = STALE_CODE
            self.last_wait_diagnostics = diag
            # Do not put the raw OTP into the error string (log/sink surface).
            raise MailMissError(
                f"gmail OTP already used for {mailbox.address}",
                diagnostics=diag,
            )
        diag.matched_at = _time.time()
        diag.matched_after_seconds = diag.matched_at - started
        diag.message_scan_count = 1
        self.last_wait_diagnostics = diag
        return OtpCode(code=str(code), source=self.name)

    def release(self, mailbox: Mailbox, *, success: bool) -> None:
        try:
            reg = self._reg()
            if hasattr(reg, "_gmail_cleanup_email"):
                reg._gmail_cleanup_email(mailbox.address)  # type: ignore[attr-defined]
        except Exception:
            return
