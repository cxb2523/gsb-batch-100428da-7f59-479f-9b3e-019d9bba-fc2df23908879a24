"""Full flow example: stealth + a random user agent + proxy failover.

The flow is:

    pick a random proxy order -> for each proxy:
        open a fresh browser context with that proxy and a random UA
        navigate to the target
        on proxy/network/HTTP failure: close the context, record the failure,
        continue with the next proxy
    -> on success return the HTML, the proxy/UA actually used and the failures
    -> when every proxy failed raise ``ProxyRotationError``

``scrape_with_proxy_rotation`` is the public entry point and mirrors the
Node.js implementation in ``NodeJS/full_flow_example.js`` (same function name,
same result field names).

Configuration precedence:

    explicit function argument > environment variable > constant below

Environment variables
---------------------
FULL_FLOW_PROXIES       Proxy pool, newline- or comma-separated (JSON array
                        also accepted). Example:
                        FULL_FLOW_PROXIES="http://127.0.0.1:1
                        http://127.0.0.1:2"
FULL_FLOW_USER_AGENTS   UA pool, newline-separated (JSON array also accepted;
                        do not use commas, UA strings contain commas).
FULL_FLOW_TARGET_URL    Target URL (default ``https://example.com``).
FULL_FLOW_PROXY_BYPASS  Optional proxy bypass list, e.g. ``<-loopback>``.
FULL_FLOW_HEADLESS      ``1``/``0`` for the command line entry point.
FULL_FLOW_TIMEOUT_MS    Navigation timeout in milliseconds (default 60000).
FULL_FLOW_PRINT_JSON    ``1`` prints the full result as JSON instead of the
                        first 500 characters of the HTML.

Running this script directly performs one failover run, prints the first 500
characters of the page (or the result JSON) and exits non-zero when every
proxy failed (1) or the configuration is invalid (2).

Note: running this against local mock proxies only proves the rotation and
error-handling logic.  It is not evidence that a real proxy + real target
combination works; validate that separately.
"""

import asyncio
import json
import os
import random
import re
import sys
from urllib.parse import unquote, urlsplit

from playwright.async_api import async_playwright

# Proxy pool and UA pool are still plain in-script constants.  The placeholders
# below are NOT reachable; inject a real/working pool through the function
# arguments or the FULL_FLOW_* environment variables.
PROXIES = [
    "http://username:password@proxy1.example.com:8000",
    "http://username:password@proxy2.example.com:8000",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.1 Safari/605.1.15",
]

DEFAULT_TARGET_URL = "https://example.com"
DEFAULT_TIMEOUT_MS = 60000
MAX_ERROR_CHARS = 500

ENV_PROXIES = "FULL_FLOW_PROXIES"
ENV_USER_AGENTS = "FULL_FLOW_USER_AGENTS"
ENV_TARGET_URL = "FULL_FLOW_TARGET_URL"
ENV_PROXY_BYPASS = "FULL_FLOW_PROXY_BYPASS"
ENV_HEADLESS = "FULL_FLOW_HEADLESS"
ENV_TIMEOUT_MS = "FULL_FLOW_TIMEOUT_MS"
ENV_PRINT_JSON = "FULL_FLOW_PRINT_JSON"

# playwright-stealth changed its API between major versions:
#   2.x: Stealth().apply_stealth_async(page_or_context)
#   1.x: stealth_async(page)
# Support both; if neither is importable the flow still works, just without
# the stealth patches.
try:
    from playwright_stealth import Stealth as _Stealth

    _STEALTH = _Stealth()

    async def _apply_stealth(page):
        await _STEALTH.apply_stealth_async(page)

except ImportError:
    try:
        from playwright_stealth import stealth_async as _stealth_async

        async def _apply_stealth(page):
            await _stealth_async(page)

    except ImportError:
        _apply_stealth = None


class ProxyRotationError(RuntimeError):
    """Raised when every proxy in the pool failed.

    ``failed_attempts`` holds one record per proxy, in attempt order.
    ``attempted_count`` is the number of proxies actually tried.
    """

    def __init__(self, target_url, failed_attempts):
        self.target_url = target_url
        self.failed_attempts = list(failed_attempts)
        self.attempted_count = len(self.failed_attempts)

        message = (
            f"All {self.attempted_count} proxy attempt(s) failed for "
            f"{target_url}."
        )
        if self.failed_attempts:
            details = "; ".join(
                f"[{index}] {attempt['proxy']} -> "
                f"{attempt['error_type']}: {attempt['error']}"
                for index, attempt in enumerate(self.failed_attempts, start=1)
            )
            message = f"{message} Attempts: {details}"
        super().__init__(message)


class HttpStatusError(RuntimeError):
    """The proxy answered, but the navigation ended with an HTTP error."""

    def __init__(self, status, target_url):
        self.status = status
        super().__init__(f"HTTP {status} for {target_url}")


class EmptyContentError(RuntimeError):
    """Navigation "succeeded" but produced no HTML; treat it as a failure."""


async def human_like_interaction(page):
    """Small, human-ish mouse/scroll animation (same spirit as the other demos)."""
    await page.mouse.move(100, 100)
    await asyncio.sleep(random.uniform(0.5, 1.5))
    await page.mouse.move(200, 300)
    await asyncio.sleep(random.uniform(0.5, 1.5))
    await page.evaluate("window.scrollBy(0, window.innerHeight / 2)")
    await asyncio.sleep(random.uniform(1, 2))


def _parse_pool_env(value, *, allow_commas):
    """Parse a pool from an environment variable.

    Accepts a JSON array or a newline-separated list.  Comma separation is
    additionally accepted for proxies, but not for User-Agent strings (which
    contain commas themselves).
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.startswith("["):
        parsed = json.loads(text)
        if not isinstance(parsed, list) or not all(
            isinstance(item, str) for item in parsed
        ):
            raise ValueError("pool environment variable must be a JSON array of strings")
        return parsed
    separator = r"[\r\n,]+" if allow_commas else r"[\r\n]+"
    return [item.strip() for item in re.split(separator, text)]


def _resolve_pool(value, env_name, fallback, *, allow_commas):
    """Resolve a pool from an argument, an environment variable or a constant.

    A bare string is split the same way the environment variable would be,
    so ``proxies="a,b"`` behaves like ``FULL_FLOW_PROXIES="a,b"``.
    """
    if value is not None:
        if isinstance(value, str):
            parsed = _parse_pool_env(value, allow_commas=allow_commas)
            return fallback if parsed is None else parsed
        return value
    parsed = _parse_pool_env(os.environ.get(env_name), allow_commas=allow_commas)
    return fallback if parsed is None else parsed


def _clean_pool(items, *, name):
    """Trim, drop empty entries and remove duplicates while preserving order."""
    cleaned = []
    seen = set()
    for item in items:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        cleaned.append(value)
    if not cleaned:
        raise ValueError(f"{name} must contain at least one non-empty entry")
    return cleaned


def _proxy_config(proxy):
    """Split a proxy URL into Playwright settings plus a masked identifier.

    Playwright takes ``server``/``username``/``password`` separately, so the
    credential part of ``http://user:pass@host:port`` is moved into its own
    fields here.  The returned identifier masks the password but keeps the
    username and host:port; together with ``proxy_index`` it remains possible
    to match a record back to the exact pool entry.
    """
    raw = str(proxy).strip()
    if not raw:
        raise ValueError("empty proxy URL")
    if "://" not in raw:
        raw = "http://" + raw

    parts = urlsplit(raw)
    if not parts.hostname:
        raise ValueError(f"invalid proxy URL: {proxy!r}")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"invalid proxy port in {proxy!r}: {exc}") from exc

    scheme = parts.scheme or "http"
    host = parts.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    server = f"{scheme}://{host}"
    if port is not None:
        server += f":{port}"

    settings = {"server": server}
    has_credentials = parts.username is not None
    if has_credentials:
        username = unquote(parts.username or "")
        settings["username"] = username
        password = unquote(parts.password) if parts.password is not None else None
        if password is not None:
            settings["password"] = password
        credentials = username if password is None else f"{username}:***"
        masked = f"{scheme}://{credentials}@{host}"
        if port is not None:
            masked += f":{port}"
    else:
        masked = server

    return settings, masked


def _fallback_mask(proxy):
    """Best-effort masking for a proxy URL that could not be parsed."""
    raw = str(proxy).strip()
    if "@" in raw:
        prefix, suffix = raw.rsplit("@", 1)
        scheme = prefix.split("://", 1)[0] + "://" if "://" in prefix else ""
        return f"{scheme}***@{suffix}"
    return raw[:200]


def _error_text(error):
    text = str(error).strip().replace("\r", " ").replace("\n", " ")
    return text[:MAX_ERROR_CHARS]


def _error_type(error):
    return type(error).__name__ or "Error"


def _boolean_env(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def _int_env(name, default):
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"{name} must be an integer number of milliseconds, got {value!r}"
        ) from exc


async def scrape_with_proxy_rotation(
    proxies=None,
    user_agents=None,
    target_url=None,
    headless=None,
    timeout_ms=None,
    proxy_bypass=None,
):
    """Load one page, rotating proxies on failure.

    Parameters
    ----------
    proxies:
        Proxy pool.  ``None`` reads ``FULL_FLOW_PROXIES`` (falling back to the
        ``PROXIES`` constant).  Each proxy is tried at most once, in random
        order; duplicates are removed.
    user_agents:
        UA pool.  ``None`` reads ``FULL_FLOW_USER_AGENTS`` (falling back to the
        ``USER_AGENTS`` constant).  One random UA is picked per attempt.
    target_url:
        URL to load.  ``None`` reads ``FULL_FLOW_TARGET_URL`` (falling back to
        ``https://example.com``).
    headless:
        Run Chromium headless.  ``None`` reads ``FULL_FLOW_HEADLESS``
        (default ``True``).
    timeout_ms:
        Navigation timeout in milliseconds.  ``None`` reads
        ``FULL_FLOW_TIMEOUT_MS`` (default 60000).
    proxy_bypass:
        Optional Playwright proxy bypass list (e.g. ``<-loopback>``).  ``None``
        reads ``FULL_FLOW_PROXY_BYPASS``.

    Returns
    -------
    dict with the keys ``success``, ``url``, ``html``, ``proxy``,
    ``proxy_index``, ``user_agent``, ``failed_attempts`` and
    ``attempt_count``.  Field names match the Node.js implementation.

    Raises
    ------
    ProxyRotationError
        When every proxy failed.  ``attempted_count`` and ``failed_attempts``
        describe exactly how many proxies were tried and why each failed.
    ValueError
        When the proxy/UA pool is empty or misconfigured.
    """
    proxy_pool = _resolve_pool(
        proxies, ENV_PROXIES, PROXIES, allow_commas=True
    )
    ua_pool = _resolve_pool(
        user_agents, ENV_USER_AGENTS, USER_AGENTS, allow_commas=False
    )

    proxy_pool = _clean_pool(proxy_pool, name="proxy pool")
    ua_pool = _clean_pool(ua_pool, name="user agent pool")

    url = target_url or os.environ.get(ENV_TARGET_URL) or DEFAULT_TARGET_URL
    if headless is None:
        headless = _boolean_env(ENV_HEADLESS, True)
    if timeout_ms is None:
        timeout_ms = _int_env(ENV_TIMEOUT_MS, DEFAULT_TIMEOUT_MS)
    if proxy_bypass is None:
        proxy_bypass = os.environ.get(ENV_PROXY_BYPASS) or None

    # Keep the original pool position for reporting, then shuffle so every
    # proxy is attempted exactly once in random order.
    indexed_pool = list(enumerate(proxy_pool))
    random.shuffle(indexed_pool)

    failed_attempts = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        try:
            for position, (proxy_index, proxy) in enumerate(indexed_pool, start=1):
                masked_proxy = _fallback_mask(proxy)
                user_agent = random.choice(ua_pool)
                context = None
                try:
                    proxy_settings, masked_proxy = _proxy_config(proxy)
                    if proxy_bypass:
                        proxy_settings["bypass"] = proxy_bypass

                    context = await browser.new_context(
                        user_agent=user_agent,
                        proxy=proxy_settings,
                    )
                    page = await context.new_page()
                    if _apply_stealth is not None:
                        await _apply_stealth(page)

                    response = await page.goto(
                        url,
                        timeout=timeout_ms,
                        wait_until="load",
                    )
                    status = response.status if response is not None else None
                    if status is None:
                        raise RuntimeError(f"navigation returned no response for {url}")
                    if status >= 400:
                        raise HttpStatusError(status, url)

                    await human_like_interaction(page)
                    html = await page.content()
                    if not html or not html.strip():
                        raise EmptyContentError(
                            f"page returned empty HTML for {url} (proxy {masked_proxy})"
                        )

                    return {
                        "success": True,
                        "url": url,
                        "html": html,
                        "proxy": masked_proxy,
                        "proxy_index": proxy_index,
                        "user_agent": user_agent,
                        "failed_attempts": failed_attempts,
                        "attempt_count": position,
                    }
                except Exception as error:  # noqa: BLE001 - one bad exit must not abort rotation
                    failed_attempts.append(
                        {
                            "proxy": masked_proxy,
                            "proxy_index": proxy_index,
                            "user_agent": user_agent,
                            "error_type": _error_type(error),
                            "error": _error_text(error),
                        }
                    )
                finally:
                    if context is not None:
                        try:
                            await context.close()
                        except Exception:
                            # The context may already be gone after a crash;
                            # never hide the navigation error we are reporting.
                            pass
        finally:
            try:
                await browser.close()
            except Exception:
                # Never let a close failure mask ProxyRotationError, which
                # carries the attempt count the caller needs.
                pass

    raise ProxyRotationError(url, failed_attempts)


async def main():
    """Command line entry point: one failover run and a short report."""
    result = await scrape_with_proxy_rotation()

    if os.environ.get(ENV_PRINT_JSON, "").strip() == "1":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["html"][:500])  # print first 500 chars

    print(
        "[full-flow] success: "
        f"proxy={result['proxy']} (index {result['proxy_index']}), "
        f"user_agent={result['user_agent']!r}, "
        f"failed_attempts={len(result['failed_attempts'])}, "
        f"attempt_count={result['attempt_count']}",
        file=sys.stderr,
    )
    return result


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ProxyRotationError as error:
        print(f"[full-flow] ERROR: {error}", file=sys.stderr)
        for position, attempt in enumerate(error.failed_attempts, start=1):
            print(
                f"[full-flow]   attempt {position}: proxy={attempt['proxy']} "
                f"(index {attempt['proxy_index']}) -> "
                f"{attempt['error_type']}: {attempt['error']}",
                file=sys.stderr,
            )
        raise SystemExit(1)
    except ValueError as error:  # misconfigured pool, timeout, URL, ...
        print(f"[full-flow] CONFIG ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
