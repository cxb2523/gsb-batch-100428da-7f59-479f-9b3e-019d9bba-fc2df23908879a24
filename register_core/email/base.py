"""Email source protocol: allocate mailbox + poll OTP.

Convention (optional, not part of Protocol signature):
  After ``poll_otp`` succeeds or raises ``MailMissError``, implementations may
  set ``self.last_wait_diagnostics`` to an ``OtpWaitDiagnostics`` instance so
  adapters can attach ``artifacts["otp_wait"]`` without changing call sites.

Outcome contract:
  Every implementation reports the same endings on
  ``OtpWaitDiagnostics.failure_class`` —
  ``no_mail`` / ``parse_fail`` / ``stale_code`` / ``imap_error`` (plus
  ``aborted``), with ``""`` on success.  Use
  :mod:`register_core.email.outcome` (``PollEvidence``, ``poll_until_code``,
  ``normalize_failure_class``) instead of inventing per-source strings, so the
  same mailbox state never produces different diagnoses.  Diagnostics carry
  counters and timestamps only — never mailbox JWTs, admin keys, or mail bodies.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from register_core.contracts import Mailbox, OtpCode


@runtime_checkable
class EmailSource(Protocol):
    """One mailbox provider (duckmail, gmail_imap, tinyhost, hotmail, …)."""

    name: str

    def allocate(self) -> Mailbox:
        """Create or reserve one address for a single attempt."""
        ...

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
        """Block until a fresh OTP arrives or raise MailMissError."""
        ...

    def release(self, mailbox: Mailbox, *, success: bool) -> None:
        """Optional cleanup / return alias to pool. Default no-op OK."""
        ...
