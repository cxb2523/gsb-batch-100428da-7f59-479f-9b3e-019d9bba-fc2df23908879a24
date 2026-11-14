"""
    Created by howie.hu at 2021-12-18.
    Description: Magic google search.
    Changelog: all notable changes to this file will be documented
"""

import random
import sys
import time

import cchardet
import requests

from pyquery import PyQuery as pq

from magic_google.config import Config
from magic_google.utils import get_data, get_logger

if sys.version_info[0] > 2:
    from urllib.parse import parse_qs, quote_plus, urlparse

    STRING_TYPES = (str,)
    INTEGER_TYPES = (int,)
else:
    from urllib import quote_plus

    from urlparse import parse_qs, urlparse

    STRING_TYPES = (str, unicode)  # noqa: F821 pylint: disable=undefined-variable
    INTEGER_TYPES = (int, long)  # noqa: F821 pylint: disable=undefined-variable


requests.packages.urllib3.disable_warnings(
    requests.packages.urllib3.exceptions.InsecureRequestWarning
)

# Terminal statuses of one keyword inside a batch collection.
STATUS_COMPLETED = "completed"
STATUS_BLOCKED = "blocked"
STATUS_NETWORK_ERROR = "network_error"
STATUS_PAGE_LIMIT = "page_limit"

# Google uses these HTTP statuses when it refuses automated traffic.
BLOCKING_STATUS_CODES = (403, 429)

# HTTP redirects; the target tells a verification page from a plain redirect.
REDIRECT_STATUS_CODES = (301, 302, 303, 307, 308)

# Redirect targets that mean Google wants the caller to pass a verification page.
VERIFICATION_URL_MARKERS = (
    "/sorry/",
    "google.com/sorry",
    "ipv4.google.com/sorry",
    "recaptcha",
    "consent.google.com",
    "accounts.google.com",
)

# Markers of an interstitial verification/captcha page returned with HTTP 200.
VERIFICATION_HTML_MARKERS = (
    "/sorry/",
    "our systems have detected unusual traffic",
    'id="captcha"',
    "id='captcha'",
    "g-recaptcha",
    "recaptcha/api",
    "www.google.com/recaptcha",
)


class MagicGoogle:
    """
    Magic google search.
    """

    def __init__(self, proxies=None):
        self.config = Config
        self.logger = get_logger()
        self.domain_list = get_data(file_path=self.config.DOMAIN_PATH)
        self.ua_list = get_data(file_path=self.config.UA_PATH)
        self.proxies = random.choice(proxies) if proxies else None

    def search(self, query, language=None, num=None, start=0, pause=2, country=None):
        """Get the results you want, such as title,description,url

        Args:
            query (str): query content
            language (str, optional): query language. Defaults to None.
            num ([type], optional): Number of results per page. Defaults to None.
            start (int, optional): start result. Defaults to 0.
            pause (int, optional): pause time(s). Defaults to 2.]
            country (str, optional): query country, Defaults to None example: countryCN or cn or CN
        Yields:
            [type]: {}
        """
        content = self.search_page(query, language, num, start, pause, country)
        for result in self._parse_results(content):
            yield result

    def search_url(self, query, language=None, num=None, start=0, pause=2, country=None):
        """search url from page

        Args:
            query (str): query content
            language (str, optional): query language. Defaults to None.
            num ([type], optional): Number of results per page. Defaults to None.
            start (int, optional): start result. Defaults to 0.
            pause (int, optional): pause time(s). Defaults to 2.
            country (str, optional): query country, Defaults to None example: countryCN or cn or CN

        Yields:
            [type]: [url]
        """
        content = self.search_page(query, language, num, start, pause, country)
        for url in self._parse_urls(content):
            yield url

    def search_page(self, query, language=None, num=None, start=0, pause=2, country=None):
        """Google search

        Args:
            query (str): query content
            language (str, optional): query language. Defaults to None.
            num ([type], optional): Number of results per page. Defaults to None.
            start (int, optional): start result. Defaults to 0.
            pause (int, optional): pause time(s). Defaults to 2.
            country (str, optional): query country, Defaults to None example: countryCN or cn or CN

        Returns:
            [str]: Content
        """
        url = self._build_search_url(
            query, language=language, num=num, start=start, country=country
        )
        try:
            response = self.fetch_page(url=url, pause=pause)
            text = self._decode_content(response.content)
            self.logger.info(url)
            return text
        except Exception as e:
            self.logger.exception(e)
            return None

    def search_batch(
        self,
        queries,
        language=None,
        num=None,
        start=0,
        pause=2,
        country=None,
        step=None,
        max_pages=None,
        previous=None,
    ):
        """Collect result urls for several queries, page by page.

        Every query starts at ``start`` (0 by default) and advances by ``step``
        until a page yields no parsed result, Google returns a verification
        page, the request fails, or ``max_pages`` is reached. A problem with one
        query never stops the remaining queries.

        Args:
            queries (iterable[str] | str): query content, a single query is also accepted.
            language (str, optional): query language. Defaults to None.
            num ([type], optional): Number of results per page. Defaults to None.
            start (int, optional): first result offset for new queries. Defaults to 0.
            pause (int, optional): pause time(s) before each request. Defaults to 2.
            country (str, optional): query country, Defaults to None example: countryCN or cn or CN
            step (int, optional): result offset added per page. Defaults to ``num`` or 10.
            max_pages (int, optional): page cap for each keyword in this call.
                Defaults to None (keep paging until an empty page or a failure).
                ``pages`` in the result still accumulates across resumed runs.
            previous (dict | list, optional): a previous ``search_batch`` result (or
                its ``results`` list). Completed queries are not requested again;
                other queries continue from their ``next_start``.

        Returns:
            dict: ``{"results": [...], "finished": bool}``. Each item contains
            ``query``, ``status`` (completed/blocked/network_error/page_limit),
            ``reason``, ``pages`` (successfully fetched pages, including the
            terminal empty page), ``urls`` (deduplicated, accumulated),
            ``stopped_at`` (offset of the page where it stopped),
            ``next_start`` (offset for the next run, None when completed) and
            ``finished``.
        """
        query_list = self._normalize_queries(queries)
        if step is None:
            step = num if num else 10
        self._validate_batch_options(start, step, max_pages)
        previous_map = self._previous_map(previous)

        results = []
        for query in query_list:
            results.append(
                self._collect_query(
                    query=query,
                    previous_entry=previous_map.get(query),
                    language=language,
                    num=num,
                    start=start,
                    pause=pause,
                    country=country,
                    step=step,
                    max_pages=max_pages,
                )
            )
        return {
            "results": results,
            "finished": all(item["status"] == STATUS_COMPLETED for item in results),
        }

    def fetch_page(self, url, pause=2):
        """Fetch one Google page and return the raw response.

        Exceptions are left to the caller: ``search_page`` keeps its historical
        ``None`` return value on failure while ``search_batch`` needs the actual
        failure in order to report a reason.

        Args:
            url (str): page url
            pause (int, optional): pause time(s). Defaults to 2.

        Returns:
            requests.Response: response object
        """
        headers = {"user-agent": self.get_random_user_agent()}
        time.sleep(pause)
        return requests.get(
            url=url,
            proxies=self.proxies,
            headers=headers,
            allow_redirects=False,
            verify=False,
            timeout=30,
        )

    def get_random_user_agent(self):
        """Get a random user agent string.

        Returns:
            str: Random user agent string.
        """
        return random.choice(self.ua_list)

    def get_random_domain(self):
        """Get a random user agent string.

        Returns:
            str: Random domain string.
        """
        domain = random.choice(self.domain_list)
        if domain in self.config.BLACK_DOMAIN:
            self.get_random_domain()
        else:
            return domain

    def filter_link(self, link):
        """
        Returns None if the link doesn't yield a valid result.
        Token from https://github.com/MarioVilas/google

        Args:
            link ([str]): search href

        Returns:
            [str]: valid result
        """
        try:
            # Valid results are absolute URLs not pointing to a Google domain
            # like images.google.com or googleusercontent.com
            o = urlparse(link, "http")
            if o.netloc:
                return link
            # Decode hidden URLs.
            if link.startswith("/url?"):
                link = parse_qs(o.query)["q"][0]
                # Valid results are absolute URLs not pointing to a Google domain
                # like images.google.com or googleusercontent.com
                o = urlparse(link, "http")
                if o.netloc:
                    return link
        # Otherwise, or on error, return None.
        except Exception as e:
            self.logger.exception(e)
            return None

    def _build_search_url(self, query, language=None, num=None, start=0, country=None):
        """Build the Google search url for one page."""
        query, domain = quote_plus(query), self.get_random_domain()
        country = (
            "country" + country.replace("country", "").upper() if country else None
        )
        if start > 0:
            url = self.config.URL_NEXT
            url = url.format(
                domain=domain,
                language=language,
                query=query,
                num=num,
                start=start,
                country=country,
            )
        else:
            if num is None:
                url = self.config.URL_SEARCH
                url = url.format(
                    domain=domain, language=language, query=query, country=country
                )
            else:
                url = self.config.URL_NUM
                url = url.format(
                    domain=domain,
                    language=language,
                    query=query,
                    num=num,
                    country=country,
                )
        url = url.replace("hl=None&", "") if language is None else url
        url = url.replace("&cr=None", "") if country is None else url
        url = url.replace("&num=None", "") if num is None else url
        return url

    def _parse_results(self, content):
        """Parse one page into ``{"title", "url", "text"}`` dicts."""
        if not content:
            return
        pq_content = pq(content)
        for p in pq_content.items("a"):
            href = p.attr("href")
            if not href or not href.startswith("/url?q="):
                continue
            pa = p.parent()
            if not pa.is_("div"):
                continue
            ppa = pa.parent()
            if ppa.attr("class") is None:
                continue
            result = {}
            result["title"] = p("h3").eq(0).text()
            result["url_path"] = p("div").eq(1).text()
            result["url"] = self.filter_link(href)
            result["text"] = ppa("div").eq(0).text()
            yield result

    def _parse_urls(self, content):
        """Parse one page into result urls, skipping malformed anchors."""
        if not content:
            return
        pq_content = pq(content)
        for p in pq_content.items("a"):
            href = p.attr("href")
            if not href or not href.startswith("/url?q="):
                continue
            pa = p.parent()
            if not pa.is_("div"):
                continue
            ppa = pa.parent()
            if ppa.attr("class") is None:
                continue
            url = self.filter_link(href)
            if url:
                yield url

    def _collect_query(
        self,
        query,
        previous_entry,
        language,
        num,
        start,
        pause,
        country,
        step,
        max_pages,
    ):
        """Run one query through all of its allowed pages."""
        pages, urls, seen_urls, next_start, finished_entry = self._resume_state(
            query, previous_entry, start, step
        )
        if finished_entry is not None:
            return finished_entry

        # ``max_pages`` limits this call only; ``pages`` keeps accumulating across runs.
        new_pages = 0

        while max_pages is None or new_pages < max_pages:
            status, reason, page_urls, url = self._fetch_page_once(
                query=query,
                language=language,
                num=num,
                start=next_start,
                pause=pause,
                country=country,
            )
            if status is not None:
                return self._terminal_entry(
                    query, status, reason, pages, urls, next_start
                )

            pages += 1
            new_pages += 1
            self.logger.info(url)

            if not page_urls:
                return self._terminal_entry(
                    query,
                    STATUS_COMPLETED,
                    "no results on page start={}".format(next_start),
                    pages,
                    urls,
                    next_start,
                )

            for page_url in page_urls:
                if page_url not in seen_urls:
                    seen_urls.add(page_url)
                    urls.append(page_url)

            next_start += step
            if max_pages is not None and new_pages >= max_pages:
                return self._terminal_entry(
                    query,
                    STATUS_PAGE_LIMIT,
                    "reached max_pages={}".format(max_pages),
                    pages,
                    urls,
                    next_start,
                )

        # Only reachable when max_pages is exhausted before any request is made.
        return self._terminal_entry(
            query,
            STATUS_PAGE_LIMIT,
            "reached max_pages={}".format(max_pages),
            pages,
            urls,
            next_start,
        )

    def _resume_state(self, query, previous_entry, start, step):
        """Rebuild the counters of a query from a previous batch result.

        Returns:
            tuple: ``(pages, urls, seen_urls, next_start, finished_entry)``,
            where ``finished_entry`` is the ready-made result item when the
            previous run already completed this query.
        """
        pages = 0
        urls = []
        seen_urls = set()
        next_start = start

        if not previous_entry:
            return pages, urls, seen_urls, next_start, None

        pages = self._safe_int(previous_entry.get("pages", 0), "pages")
        if pages < 0:
            raise ValueError("previous result pages must not be negative")
        urls = self._merge_urls(previous_entry.get("urls"), seen_urls)

        previous_status = previous_entry.get("status")
        if previous_status is None and previous_entry.get("finished") is True:
            previous_status = STATUS_COMPLETED
        if previous_status == STATUS_COMPLETED:
            finished_entry = self._batch_entry(
                query=query,
                status=STATUS_COMPLETED,
                reason=previous_entry.get("reason"),
                pages=pages,
                urls=urls,
                stopped_at=previous_entry.get("stopped_at"),
            )
            return pages, urls, seen_urls, next_start, finished_entry

        resumed_from = previous_entry.get("next_start")
        if resumed_from is None:
            resumed_from = previous_entry.get("stopped_at")
        if resumed_from is not None:
            next_start = self._safe_int(resumed_from, "next_start")
        else:
            next_start = start + pages * step
        return pages, urls, seen_urls, next_start, None

    def _fetch_page_once(self, query, language, num, start, pause, country):
        """Fetch and parse a single result page.

        Returns:
            tuple: ``(status, reason, page_urls, url)``. ``status`` is None when
            the page was fetched and parsed, otherwise it is a terminal status.
        """
        url = self._build_search_url(
            query, language=language, num=num, start=start, country=country
        )
        try:
            response = self.fetch_page(url=url, pause=pause)
        except Exception as e:
            return STATUS_NETWORK_ERROR, "{}: {}".format(type(e).__name__, e), None, url

        status, reason, text = self._inspect_response(response)
        if status is not None:
            return status, reason, None, url

        try:
            page_urls = list(self._parse_urls(text))
        except Exception as e:
            return (
                STATUS_NETWORK_ERROR,
                "failed to parse response: {}".format(e),
                None,
                url,
            )

        # A verification page carries no result anchors. Checking the markers
        # only when parsing found nothing avoids flagging ordinary results
        # whose snippets happen to mention captchas.
        if not page_urls and self._looks_like_verification_page(text):
            return (
                STATUS_BLOCKED,
                "Google verification page returned for start={}".format(start),
                None,
                url,
            )
        return None, None, page_urls, url

    def _inspect_response(self, response):
        """Classify one response before parsing it.

        Args:
            response (requests.Response): fetched page.

        Returns:
            tuple: ``(status, reason, text)``. ``status`` is None when the page
            can be parsed, otherwise it is a terminal status and ``text`` is None.
        """
        if response is None:
            return STATUS_NETWORK_ERROR, "no response returned", None

        status_code = getattr(response, "status_code", None)
        if status_code in BLOCKING_STATUS_CODES:
            return (
                STATUS_BLOCKED,
                "Google blocked the request with HTTP {}".format(status_code),
                None,
            )
        if status_code in REDIRECT_STATUS_CODES:
            location = self._response_header(response, "Location")
            if self._is_verification_url(location):
                return (
                    STATUS_BLOCKED,
                    "redirected to Google verification page: {}".format(location),
                    None,
                )
            return (
                STATUS_NETWORK_ERROR,
                "unexpected redirect to: {}".format(location),
                None,
            )
        if status_code is not None and status_code >= 400:
            return (
                STATUS_NETWORK_ERROR,
                "unexpected HTTP status {}".format(status_code),
                None,
            )

        try:
            text = self._decode_content(response.content)
        except Exception as e:
            return (
                STATUS_NETWORK_ERROR,
                "failed to decode response: {}".format(e),
                None,
            )

        # Verification markers are checked after parsing, so a normal page whose
        # snippets mention captchas is not mistaken for a block.
        return None, None, text

    def _decode_content(self, content):
        """Decode raw response bytes using ``cchardet`` with an utf-8 fallback."""
        charset = cchardet.detect(content)
        encoding = charset.get("encoding") if charset else None
        return content.decode(encoding or "utf-8")

    @staticmethod
    def _normalize_queries(queries):
        """Validate and de-duplicate query keywords while keeping their order."""
        if isinstance(queries, STRING_TYPES):
            queries = [queries]
        try:
            query_list = list(queries)
        except TypeError:
            raise ValueError("queries must be a string or an iterable of strings")
        ordered = []
        seen = set()
        for query in query_list:
            if not isinstance(query, STRING_TYPES) or not query.strip():
                raise ValueError("every query must be a non-empty string")
            if query not in seen:
                seen.add(query)
                ordered.append(query)
        if not ordered:
            raise ValueError("queries must contain at least one keyword")
        return ordered

    @staticmethod
    def _validate_batch_options(start, step, max_pages):
        """Validate pagination options of :meth:`search_batch`."""
        if isinstance(start, bool) or not isinstance(start, INTEGER_TYPES) or start < 0:
            raise ValueError("start must be a non-negative integer")
        if isinstance(step, bool) or not isinstance(step, INTEGER_TYPES) or step <= 0:
            raise ValueError("step must be a positive integer")
        if max_pages is None:
            return
        invalid = isinstance(max_pages, bool) or not isinstance(
            max_pages, INTEGER_TYPES
        )
        if invalid or max_pages <= 0:
            raise ValueError("max_pages must be a positive integer or None")

    @staticmethod
    def _previous_map(previous):
        """Index a previous batch result by query keyword."""
        if previous is None:
            return {}
        if isinstance(previous, dict):
            entries = previous.get("results")
            if entries is None:
                raise ValueError("previous result must contain a 'results' list")
        elif isinstance(previous, (list, tuple)):
            entries = previous
        else:
            raise ValueError("previous result must be a batch result dict or a list")
        if not isinstance(entries, (list, tuple)):
            raise ValueError("previous 'results' must be a list")

        previous_map = {}
        for entry in entries:
            if not isinstance(entry, dict) or "query" not in entry:
                raise ValueError("every previous result item must contain a 'query'")
            previous_map.setdefault(entry["query"], entry)
        return previous_map

    @staticmethod
    def _safe_int(value, field_name):
        """Convert a previous-result integer field, raising a clear error."""
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError("previous result '{}' must be an integer".format(field_name))

    @staticmethod
    def _merge_urls(urls, seen_urls):
        """Carry previously collected urls over into the current batch."""
        if urls is None:
            return []
        if not isinstance(urls, (list, tuple)):
            raise ValueError("previous result 'urls' must be a list")
        merged = []
        for url in urls:
            if not isinstance(url, STRING_TYPES):
                raise ValueError("previous result 'urls' must only contain strings")
            if url not in seen_urls:
                seen_urls.add(url)
                merged.append(url)
        return merged

    @staticmethod
    def _response_header(response, name):
        """Read a response header case-insensitively from dict-like headers."""
        headers = getattr(response, "headers", None) or {}
        value = headers.get(name)
        if value is None:
            value = headers.get(name.lower())
        return value

    @staticmethod
    def _is_verification_url(url):
        """Tell whether a redirect target is a Google verification page."""
        if not url:
            return False
        normalized = url.lower()
        return any(marker in normalized for marker in VERIFICATION_URL_MARKERS)

    @staticmethod
    def _looks_like_verification_page(text):
        """Tell whether an HTTP 200 body is a Google verification/captcha page."""
        if not text:
            return False
        normalized = text.lower()
        return any(marker in normalized for marker in VERIFICATION_HTML_MARKERS)

    @staticmethod
    def _batch_entry(query, status, reason, pages, urls, stopped_at, next_start=None):
        """Build one ``search_batch`` result item.

        Args:
            next_start (int, optional): offset for the next run; ignored when the
                status is terminal for the query (completed).
        """
        if status == STATUS_COMPLETED:
            next_start = None
        return {
            "query": query,
            "status": status,
            "reason": reason,
            "pages": pages,
            "urls": urls,
            "stopped_at": stopped_at,
            "next_start": next_start,
            "finished": status == STATUS_COMPLETED,
        }

    def _terminal_entry(self, query, status, reason, pages, urls, stopped_at):
        """Build a terminal entry that resumes from the page it stopped on."""
        return self._batch_entry(
            query=query,
            status=status,
            reason=reason,
            pages=pages,
            urls=urls,
            stopped_at=stopped_at,
            next_start=stopped_at,
        )
