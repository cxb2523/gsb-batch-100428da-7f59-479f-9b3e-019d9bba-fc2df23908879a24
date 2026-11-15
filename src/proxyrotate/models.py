"""Core data model for a single proxy server."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

SUPPORTED_SCHEMES = ("http", "https", "socks5")


@dataclass(slots=True)
class Proxy:
    """A parsed proxy URL.

    Supported forms:

        http://host:port
        https://host:port
        socks5://host:port
        http://user:pass@host:port
        socks5://user:pass@host:port
    """

    scheme: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    @classmethod
    def parse(cls, raw: str) -> Proxy:
        if "://" not in raw:
            raise ValueError("missing scheme (expected http:// or socks5://)")

        try:
            parsed = urlparse(raw)
        except ValueError as exc:
            raise ValueError(f"unparseable URL: {exc}") from exc

        scheme = parsed.scheme.lower()
        if scheme not in SUPPORTED_SCHEMES:
            raise ValueError(
                f"unsupported scheme {scheme!r} (supported: {', '.join(SUPPORTED_SCHEMES)})"
            )

        host = parsed.hostname
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"invalid port: {exc}") from exc

        if not host:
            raise ValueError("missing host")
        if port is None:
            raise ValueError("missing port")

        return cls(
            scheme=scheme,
            host=host,
            port=port,
            username=parsed.username,
            password=parsed.password,
        )

    def url(self) -> str:
        """Full URL including credentials."""
        return urlunparse((self.scheme, self._netloc(), "", "", "", ""))

    def redacted_url(self) -> str:
        """URL safe for logging: passwords are masked."""
        masked = Proxy(
            scheme=self.scheme,
            host=self.host,
            port=self.port,
            username=self.username,
            password="***" if self.password is not None else None,
        )
        return urlunparse((masked.scheme, masked._netloc(), "", "", "", ""))

    def _netloc(self) -> str:
        netloc = f"{self.host}:{self.port}"
        if self.username is not None:
            auth = self.username
            if self.password is not None:
                auth += f":{self.password}"
            netloc = f"{auth}@{netloc}"
        return netloc

    def dedupe_key(self) -> tuple[str, str, int, str | None, str | None]:
        return (self.scheme, self.host, self.port, self.username, self.password)

    def __str__(self) -> str:
        return self.redacted_url()
