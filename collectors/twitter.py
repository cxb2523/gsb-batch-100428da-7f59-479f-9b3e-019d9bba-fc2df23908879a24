"""Twitter/X collector: twikit (cookies from Camoufox login) + Google News RSS fallback.

Every collection run ends with a diagnostic outcome exposed via the
module-level ``last_*`` variables:

* ``last_method``   -- method that actually produced the returned items
                       (``twikit`` or ``google_news_rss``);
* ``last_reason``   -- machine-readable outcome reason (see ``REASON_*``);
* ``last_fallback`` -- ``True`` when the Google News RSS fallback was used;
* ``last_warning``  -- human-readable warning (empty on a healthy twikit run).

This lets the dashboard distinguish an expected fallback (no cookies) from a
broken twikit session (cookies present but requests failing, e.g. rate limit).
"""

import json
import logging
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
import httpx

from config import settings
from storage.models import RawItem, SourceType

logger = logging.getLogger(__name__)

MAX_CONTENT_LEN = 2000
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

# ---------------------------------------------------------------------------
# Diagnostic outcome reasons
# ---------------------------------------------------------------------------
# twikit worked (possibly with some keywords failing -- see run logs).
REASON_TWIKIT_OK = "twikit_ok"
# No usable twikit session: fall back before any request is made.
REASON_COOKIES_MISSING = "cookies_missing"
REASON_COOKIES_INVALID = "cookies_invalid"
REASON_COOKIES_UNREADABLE = "cookies_unreadable"
REASON_TWIKIT_NOT_INSTALLED = "twikit_not_installed"
REASON_LOGIN_UNAVAILABLE = "login_unavailable"
REASON_LOGIN_FAILED = "login_failed"
# Cookies/session existed but every twikit search request failed (rate limit,
# network error, expired session, ...).
REASON_TWIKIT_REQUEST_FAILED = "twikit_request_failed"

# Human-readable warnings for the fallback outcomes.
_FALLBACK_WARNINGS: dict[str, str] = {
    REASON_COOKIES_MISSING: "Cookies missing — upload cookies to enable real tweets",
    REASON_COOKIES_INVALID: (
        "Twitter cookies invalid (auth_token/ct0 missing) — "
        "re-login to enable real tweets"
    ),
    REASON_COOKIES_UNREADABLE: (
        "Twitter cookies file unreadable — re-login to enable real tweets"
    ),
    REASON_TWIKIT_NOT_INSTALLED: (
        "twikit not installed — using Google News fallback"
    ),
    REASON_LOGIN_UNAVAILABLE: (
        "No valid cookies and auto-login not configured — "
        "using Google News fallback"
    ),
    REASON_LOGIN_FAILED: (
        "Camoufox re-login failed — using Google News fallback"
    ),
    REASON_TWIKIT_REQUEST_FAILED: (
        "twikit failed (rate limit?) — using Google News fallback"
    ),
}


async def _load_twikit_client() -> "tuple[twikit.Client | None, str]":
    """Load twikit client with cookies from Camoufox login.

    Returns ``(client, reason)``. On success ``reason`` is empty; otherwise
    ``client`` is ``None`` and ``reason`` is one of the ``REASON_*``
    constants describing why no session could be built.
    """
    try:
        import twikit
    except ImportError:
        logger.warning("twikit not installed, skipping Twitter API")
        return None, REASON_TWIKIT_NOT_INSTALLED

    cookies_path = Path(settings.twitter_cookies_file)
    if not cookies_path.exists():
        logger.info("No Twitter cookies file, will try Camoufox login")
        return None, REASON_COOKIES_MISSING

    try:
        cookies = json.loads(cookies_path.read_text(encoding="utf-8"))
        if "auth_token" not in cookies or "ct0" not in cookies:
            logger.warning("Twitter cookies missing auth_token/ct0")
            return None, REASON_COOKIES_INVALID

        client = twikit.Client(language="en")
        client.set_cookies(cookies)
        logger.info("Twitter: twikit loaded %d cookies from file", len(cookies))
        return client, ""

    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Error loading Twitter cookies: %s", exc)
        return None, REASON_COOKIES_UNREADABLE


async def _try_camoufox_login() -> "tuple[twikit.Client | None, str]":
    """Try to re-login via Camoufox and return ``(client, reason)``."""
    if not settings.twitter_username or not settings.twitter_password:
        return None, REASON_LOGIN_UNAVAILABLE

    try:
        from collectors.twitter_login import ensure_valid_cookies
    except ImportError:
        logger.warning("twitter_login module not available (camoufox not installed?)")
        return None, REASON_LOGIN_UNAVAILABLE

    success = await ensure_valid_cookies()
    if not success:
        return None, REASON_LOGIN_FAILED

    client, reason = await _load_twikit_client()
    if client:
        return client, ""
    # Re-login reported success but the cookies still can't be loaded --
    # keep the concrete load failure reason.
    return None, reason or REASON_LOGIN_FAILED


async def _collect_twikit() -> "tuple[list[RawItem], str, str]":
    """Collect tweets through twikit (cookies from Camoufox).

    Returns ``(items, reason, detail)``:

    * ``reason == ""`` -- twikit was available and the search loop ran (items
      may still be empty if no tweets matched the keywords);
    * otherwise a ``REASON_*`` value explaining why no twikit request could
      succeed, with ``detail`` carrying extra context (e.g. the last error).
    """
    # Try loading existing cookies
    client, reason = await _load_twikit_client()

    # If no valid cookies, try Camoufox login
    if not client:
        client, login_reason = await _try_camoufox_login()
        if not client:
            # Keep the more specific reason from the cookie load phase.
            return [], reason or login_reason, ""

    items: list[RawItem] = []
    seen_ids: set[str] = set()
    failed_keywords: list[str] = []
    last_error: Exception | None = None

    try:
        for keyword in settings.twitter_keywords:
            results = None
            last_product_error: Exception | None = None
            for product in ("Latest", "Top"):
                try:
                    results = await client.search_tweet(
                        keyword, product=product, count=20
                    )
                    break
                except Exception as exc:
                    last_product_error = exc
                    logger.debug(
                        "Twitter search '%s' (%s) failed: %s", keyword, product, exc
                    )

            if last_product_error is not None and results is None:
                # Every product failed for this keyword.
                last_error = last_product_error
                failed_keywords.append(keyword)
                logger.warning(
                    "Twitter search '%s' failed on all products: %s",
                    keyword,
                    last_product_error,
                )
                continue

            # An empty result list is a legitimate answer (no matching
            # tweets), not a failure -- only a raised exception is.
            for tweet in results or []:
                if tweet.id in seen_ids:
                    continue
                seen_ids.add(tweet.id)

                text = tweet.text or ""
                screen_name = tweet.user.screen_name if tweet.user else ""

                items.append(
                    RawItem(
                        source=SourceType.TWITTER,
                        url=f"https://x.com/{screen_name}/status/{tweet.id}",
                        title=text[:150],
                        content=text[:MAX_CONTENT_LEN],
                        author=f"@{screen_name}",
                        score=tweet.favorite_count or 0,
                        metadata={
                            "keyword": keyword,
                            "retweet_count": tweet.retweet_count or 0,
                            "reply_count": tweet.reply_count or 0,
                            "method": "twikit",
                        },
                    ),
                )

        if failed_keywords:
            if len(failed_keywords) == len(settings.twitter_keywords):
                detail = str(last_error) if last_error else ""
                logger.error(
                    "Twitter twikit requests failed for every keyword%s",
                    f": {detail}" if detail else "",
                )
                return [], REASON_TWIKIT_REQUEST_FAILED, detail
            logger.warning(
                "Twitter twikit: %d/%d keywords failed, keeping partial results",
                len(failed_keywords),
                len(settings.twitter_keywords),
            )

        logger.info("Twitter (twikit): collected %d tweets", len(items))
        return items, "", ""
    except Exception as exc:
        # Failure outside the per-keyword search loop (session-level error).
        logger.error("Twitter twikit collector failed: %s", exc)
        return [], REASON_TWIKIT_REQUEST_FAILED, str(exc)


async def _collect_google_news() -> list[RawItem]:
    """Fallback: Google News RSS по trading keywords."""
    items: list[RawItem] = []
    seen_urls: set[str] = set()

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for keyword in settings.twitter_keywords:
                url = f"{GOOGLE_NEWS_RSS}?q={quote_plus(keyword)}&hl=en-US&gl=US&ceid=US:en"
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue
                except httpx.HTTPError:
                    continue

                feed = feedparser.parse(resp.text)
                for entry in feed.entries[:15]:
                    entry_url = entry.get("link", "")
                    if not entry_url or entry_url in seen_urls:
                        continue
                    seen_urls.add(entry_url)

                    title = entry.get("title", "")
                    summary = entry.get("summary", "")[:MAX_CONTENT_LEN]
                    source_name = entry.get("source", {})
                    if isinstance(source_name, dict):
                        source_name = source_name.get("title", "")

                    items.append(
                        RawItem(
                            source=SourceType.RSS,
                            url=entry_url,
                            title=title,
                            content=summary or title,
                            author=str(source_name),
                            metadata={
                                "keyword": keyword,
                                "published": entry.get("published", ""),
                                "method": "google_news_rss",
                            },
                        ),
                    )

        logger.info("Twitter/News (Google RSS): collected %d items", len(items))
        return items
    except httpx.HTTPError as exc:
        logger.error("Google News RSS failed: %s", exc)
        return []


# Track collection outcome for UI status
last_method: str = ""
last_reason: str = ""
last_warning: str = ""
last_fallback: bool = False


async def collect() -> list[RawItem]:
    """Собрать контент: twikit (Camoufox cookies) -> Google News RSS fallback.

    Always records a diagnostic outcome in ``last_method`` / ``last_reason`` /
    ``last_fallback`` / ``last_warning`` before returning. A successful twikit
    run clears any warning left by a previous run.
    """
    global last_method, last_reason, last_warning, last_fallback

    if not settings.twitter_keywords:
        logger.warning("Twitter keywords not configured, skipping")
        return []

    # Попробовать twikit сначала
    items, reason, detail = await _collect_twikit()

    if not reason:
        # twikit ran successfully (even if it found zero tweets) -- no
        # fallback and never keep a stale warning from a previous run.
        last_method = "twikit"
        last_reason = REASON_TWIKIT_OK
        last_fallback = False
        last_warning = ""
        return items

    # Fallback на Google News RSS. The RSS mapping/params are untouched.
    items = await _collect_google_news()
    last_method = "google_news_rss"
    last_reason = reason
    last_fallback = True
    last_warning = _FALLBACK_WARNINGS.get(
        reason, "twikit unavailable — using Google News fallback"
    )
    if detail:
        logger.warning("Twitter: %s (detail: %s)", last_warning, detail)
    else:
        logger.warning("Twitter: %s", last_warning)

    return items
