#!/usr/bin/env python
"""
ExploiterX Scanner Module - Enhanced Version

Advanced scanning engine for detecting multiple web vulnerabilities including:
- Cross-Site Scripting (XSS) - Multiple payload variants
- SQL Injection - Multiple payload types  
- CSRF vulnerabilities
- Security header misconfigurations
- Path traversal attempts

Author: ExploiterX Contributors
License: MIT
"""

import requests
import re
import html
import time
import urllib.parse as urlparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from fnmatch import fnmatch
from bs4 import BeautifulSoup
from typing import List, Optional, Dict, Any, Set, Tuple
from requests.models import Response
import logging
from threading import Lock, get_ident, local
from models.vulnerability import Vulnerability, VulnerabilityType, Severity
from models.scan_result import ScanResult
from config.constants import (
    XSS_PAYLOADS,
    SQLI_PAYLOADS,
    SQL_ERROR_PATTERNS,
    SECURE_HEADERS,
    CSRF_INDICATORS,
    XXE_PAYLOADS,
    XXE_ERROR_PATTERNS,
    PATH_TRAVERSAL_PAYLOADS,
    PATH_TRAVERSAL_PATTERNS,
    OPEN_REDIRECT_PAYLOADS,
    OPEN_REDIRECT_MARKER,
    REDIRECT_STATUS_CODES,
    COMMAND_INJECTION_PAYLOADS,
    COMMAND_INJECTION_PATTERNS,
    TIME_BASED_MARKERS,
)

logger = logging.getLogger(__name__)


class Scanner:
    """
    Advanced web vulnerability scanner with multi-threaded support.
    
    This class provides comprehensive scanning for various web vulnerabilities
    with support for multiple payload variants and concurrent testing.
    
    Attributes:
        session: Persistent requests session
        target_url: Base URL to scan
        target_links: Discovered endpoints
        links_to_ignore: URLs to exclude
        scan_result: Aggregated scan results
        timeout: HTTP request timeout
        results_lock: Thread lock for thread-safe result collection
        crawl_stats: Cumulative crawl outcome counters (see reset_crawl_stats)
    """
    
    def __init__(
        self, 
        url: str, 
        ignore_links: Optional[List[str]] = None,
        timeout: int = 10,
        max_retries: int = 2,
        delay: float = 0.0,
        max_depth: int = 10,
        max_links: int = 0,
        follow_redirects: bool = True,
        verify_ssl: bool = True,
        user_agent: Optional[str] = None,
        time_based_threshold: float = 3.0
    ) -> None:
        """
        Initialize the Scanner.
        
        Args:
            url: Target URL to scan
            ignore_links: List of URLs to ignore
            timeout: HTTP request timeout in seconds
            max_retries: Number of retries for transient request failures
            delay: Seconds to wait between requests (politeness/rate-limit avoidance)
            max_depth: Maximum crawl depth (0 disables recursion into links)
            max_links: Maximum number of links to discover (0 = unlimited)
            follow_redirects: Whether to follow HTTP redirects
            verify_ssl: Whether to verify SSL/TLS certificates
            user_agent: Custom User-Agent header
            time_based_threshold: Minimum seconds a time-based payload must delay
                the response before being flagged as vulnerable
            
        Raises:
            ValueError: If URL is invalid
        """
        if not url or not url.strip():
            raise ValueError("Target URL cannot be empty")
        
        normalized_url = self._normalize_url(url)

        if timeout < 1:
            raise ValueError("Timeout must be greater than 0")
        if max_retries < 0:
            raise ValueError("max_retries must be 0 or greater")
        if delay < 0:
            raise ValueError("delay must be 0 or greater")
        if max_depth < 0:
            raise ValueError("max_depth must be 0 or greater")
        if max_links < 0:
            raise ValueError("max_links must be 0 or greater")
        if time_based_threshold <= 0:
            raise ValueError("time_based_threshold must be greater than 0")

        self.session: requests.Session = requests.Session()
        # requests.Session is not thread-safe. The constructing thread keeps
        # using ``self.session`` directly (callers/tests depend on it being
        # the live object), while worker threads get their own clone seeded
        # from the master session's headers/verify/cookies.
        self._owner_thread: int = get_ident()
        self._thread_local: local = local()
        self.target_url: str = normalized_url
        self.target_links: List[str] = []
        self.links_to_ignore: Set[str] = {
            link.strip() for link in (ignore_links if ignore_links else []) if link and link.strip()
        }
        self.timeout: int = timeout
        self.max_retries: int = max_retries
        self.delay: float = delay
        self.max_depth: int = max_depth
        self.max_links: int = max_links
        self.follow_redirects: bool = follow_redirects
        self.verify_ssl: bool = verify_ssl
        self.time_based_threshold: float = time_based_threshold
        self.scan_result: ScanResult = ScanResult(normalized_url)
        self.results_lock: Lock = Lock()
        # Cumulative crawl outcome counters. Updated only under
        # ``_crawl_stats_lock`` so they stay consistent while the thread
        # pool scans; repeated crawl() calls accumulate until an explicit
        # reset_crawl_stats().
        self._crawl_stats_lock: Lock = Lock()
        self.crawl_stats: Dict[str, int] = {
            "pages_visited": 0,        # pages actually requested
            "pages_succeeded": 0,      # pages whose links were parsed
            "pages_failed": 0,         # pages whose request failed
            "links_ignored": 0,        # links skipped by ignore rules
            "links_truncated": 0,      # links dropped by the max_links cap
            "endpoints_discovered": 0, # links appended to target_links
        }
        self._target_parts = urlparse.urlsplit(normalized_url)
        self._seen_vulnerability_keys: Set[Tuple[str, str, str, str, str, str]] = set()
        
        # Set default headers
        self.session.headers.update({
            'User-Agent': user_agent or 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.session.verify = verify_ssl

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normalize URL for consistent comparisons and storage."""
        url = url.strip()
        parsed = urlparse.urlsplit(url)
        clean_path = parsed.path.rstrip("/")
        if clean_path == "/":
            clean_path = ""
        normalized = parsed._replace(path=clean_path, fragment="")
        return urlparse.urlunsplit(normalized)

    def _session(self) -> requests.Session:
        """Return a session that is safe to use from the current thread.

        The constructing thread returns ``self.session`` itself so existing
        callers and tests keep seeing the live session object. Any other
        thread (e.g. ``run_scanner(threads>1)`` workers) receives a lazily
        created clone seeded from the master session's headers, SSL verify
        setting and cookies at first use.
        """
        if get_ident() == self._owner_thread:
            return self.session
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(self.session.headers)
            session.verify = self.session.verify
            session.cookies.update(self.session.cookies)
            self._thread_local.session = session
        return session

    def _request(self, method: str, url: str, **kwargs: Any) -> Response:
        """
        Send HTTP request with bounded retries for transient network failures.

        Applies the configured redirect policy and SSL verification unless the
        caller explicitly overrides them. Optionally waits `self.delay` seconds
        after each successful request to avoid hammering the target.

        Raises the last request exception when retries are exhausted.
        """
        request_method = getattr(self._session(), method.lower())
        attempt = 0
        last_exc: Optional[Exception] = None

        kwargs.setdefault("allow_redirects", self.follow_redirects)
        kwargs.setdefault("verify", self.verify_ssl)

        while attempt <= self.max_retries:
            try:
                response = request_method(url, timeout=self.timeout, **kwargs)
                if self.delay > 0:
                    time.sleep(self.delay)
                return response
            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
            ) as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    raise
                logger.debug(
                    "Transient %s on %s %s (attempt %s/%s)",
                    type(exc).__name__,
                    method,
                    url,
                    attempt + 1,
                    self.max_retries + 1,
                )
                attempt += 1
            except requests.exceptions.RequestException:
                raise

        if last_exc:
            raise last_exc
        raise RuntimeError("Unexpected request retry flow")

    @staticmethod
    def _is_reflected(payload: str, content: str) -> bool:
        """
        Check whether a payload is reflected in a response body.

        Accounts for common server-side transformations such as HTML-entity
        encoding and URL encoding so encoded reflections are still detected.
        """
        if payload in content:
            return True

        # HTML-escaped reflection, e.g. <script> -> &lt;script&gt;
        escaped = html.escape(payload, quote=False)
        if escaped in content:
            return True

        # Content that HTML-unescapes back to the raw payload
        if payload in html.unescape(content):
            return True

        return False

    @staticmethod
    def _is_time_based(payload: str) -> bool:
        """Check whether a payload is designed to trigger a measurable delay."""
        upper = payload.upper()
        return any(marker in upper for marker in TIME_BASED_MARKERS)

    def _decode_response_content(self, response: Response) -> str:
        """Decode HTTP response content safely."""
        return response.content.decode(errors="ignore")

    def _is_internal_url(self, url: str) -> bool:
        """Check if URL belongs to the same target host."""
        try:
            parsed = urlparse.urlsplit(url)
            if parsed.scheme not in ("http", "https"):
                return False
            return parsed.hostname == self._target_parts.hostname
        except Exception:
            return False

    def _should_ignore_link(self, link: str) -> bool:
        """Check if a link should be ignored by exact/path/glob matching."""
        parsed_link = urlparse.urlsplit(link)
        path = parsed_link.path or "/"

        for pattern in self.links_to_ignore:
            if link == pattern:
                return True
            if fnmatch(link, pattern):
                return True
            if pattern.startswith("/") and fnmatch(path, pattern):
                return True
            if pattern in link or pattern in path:
                return True
        return False

    @staticmethod
    def _inject_payload_into_url(
        url: str, preferred_param: str, payload: str
    ) -> Optional[Tuple[str, str]]:
        """Inject a payload into an existing query parameter."""
        parsed = urlparse.urlsplit(url)
        query_pairs = urlparse.parse_qsl(parsed.query, keep_blank_values=True)
        if not query_pairs:
            return None

        target_param = preferred_param if preferred_param else query_pairs[0][0]
        mutated_pairs = []
        replaced = False

        for key, value in query_pairs:
            if key == target_param:
                mutated_pairs.append((key, payload))
                replaced = True
            else:
                mutated_pairs.append((key, value))

        if not replaced:
            mutated_pairs[0] = (mutated_pairs[0][0], payload)
            target_param = mutated_pairs[0][0]

        new_query = urlparse.urlencode(mutated_pairs, doseq=True)
        return urlparse.urlunsplit(parsed._replace(query=new_query)), target_param

    def _add_vulnerability_once(self, vuln: Vulnerability) -> None:
        """Add vulnerability if not already recorded."""
        key = (
            vuln.vuln_type,
            vuln.url,
            vuln.parameter or "",
            vuln.payload,
            vuln.method,
            vuln.severity,
        )

        with self.results_lock:
            if key in self._seen_vulnerability_keys:
                return
            self._seen_vulnerability_keys.add(key)
            self.scan_result.add_vulnerability(vuln)
    
    def _crawl_stat_add(self, key: str, amount: int = 1) -> None:
        """Increment a crawl statistic (thread-safe)."""
        with self._crawl_stats_lock:
            self.crawl_stats[key] += amount

    def _crawl_stat_value(self, key: str) -> int:
        """Read a single crawl statistic (thread-safe)."""
        with self._crawl_stats_lock:
            return self.crawl_stats[key]

    def get_crawl_stats(self) -> Dict[str, int]:
        """Return a consistent snapshot of the crawl statistics.

        Safe to call before or after run_scanner(); the returned dict is a
        copy, so callers cannot mutate the live counters.
        """
        with self._crawl_stats_lock:
            return dict(self.crawl_stats)

    def reset_crawl_stats(self) -> None:
        """Reset all crawl statistics to zero.

        crawl() accumulates across calls; use this to start a fresh
        measurement without creating a new Scanner.
        """
        with self._crawl_stats_lock:
            for key in self.crawl_stats:
                self.crawl_stats[key] = 0

    def extract_links_from(self, url: str) -> List[str]:
        """
        Extract hyperlinks from HTML content.
        
        Uses regex pattern matching with error handling for robust extraction.
        
        Args:
            url: URL to extract links from
            
        Returns:
            List of extracted and normalized links
        """
        try:
            response: Response = self._request("GET", url)
            response.raise_for_status()
            
            content: str = self._decode_response_content(response)
            
            # Enhanced regex for quoted and unquoted href/src attributes
            links: List[str] = re.findall(
                r'''(?:href|src)\s*=\s*["']([^"']+)["']|(?:href|src)\s*=\s*([^\s>"']+)''',
                content,
                re.IGNORECASE
            )
            # Flatten capture groups and drop empties
            links = [a or b for a, b in links if (a or b)]
            
            logger.debug(f"Extracted {len(links)} links from {url}")
            return links
            
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout extracting links from {url}")
            self._crawl_stat_add("pages_failed")
            return []
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to extract links from {url}: {e}")
            self._crawl_stat_add("pages_failed")
            return []
    
    def crawl(self, url: Optional[str] = None, depth: int = 0, max_depth: Optional[int] = None) -> None:
        """
        Breadth-first crawl of the website to discover endpoints.
        
        Iterative (queue-based) traversal avoids Python recursion limits on
        deep sites and tracks visited links for O(1) duplicate detection.
        
        Args:
            url: URL to crawl (defaults to target_url)
            depth: Starting depth for the crawl
            max_depth: Maximum crawl depth (defaults to the scanner's max_depth)
            
        Note:
            - Works best on local vulnerable applications (DVWA, WebGoat)
            - Production websites may block crawler due to security measures
            - JavaScript-rendered sites won't be crawled (requires headless browser)
        """
        if max_depth is None:
            max_depth = self.max_depth

        if depth > max_depth:
            return

        if url is None:
            url = self.target_url

        visited: Set[str] = set(self.target_links)
        queue: deque = deque([(url, depth)])

        while queue:
            current, current_depth = queue.popleft()

            if current_depth > max_depth:
                continue

            if self.max_links and len(self.target_links) >= self.max_links:
                # Links already enqueued but never processed because of the
                # cap count as truncated instead of being silently dropped.
                pending = 1 + sum(
                    1 for _, pending_depth in queue if pending_depth <= max_depth
                )
                self._crawl_stat_add("links_truncated", pending)
                logger.info(f"Reached max_links limit ({self.max_links})")
                break

            self._crawl_stat_add("pages_visited")
            failed_before = self._crawl_stat_value("pages_failed")
            try:
                href_links: List[str] = self.extract_links_from(current)
            except Exception as e:
                self._crawl_stat_add("pages_failed")
                logger.debug(f"Error extracting links from {current}: {e}")
                continue
            if self._crawl_stat_value("pages_failed") == failed_before:
                self._crawl_stat_add("pages_succeeded")

            # Provide feedback if no links found at initial URL
            if current == url and current_depth == depth and not href_links:
                logger.warning(
                    f"No links discovered at {url}. "
                    "This may indicate:\n"
                    "- Website blocks automated crawlers\n"
                    "- Links are rendered via JavaScript\n"
                    "- Static links not present in HTML\n"
                    "For testing, use local vulnerable apps (DVWA, WebGoat)"
                )
                print("[!] No links found. Consider testing with DVWA or similar vulnerable app.")
                print("[*] See README for DVWA setup guide.")

            for link in href_links:
                try:
                    # Normalize link
                    link = urlparse.urljoin(current, link)

                    # Remove fragments
                    if "#" in link:
                        link = link.split("#")[0]

                    # Skip empty links and non-HTTP schemes
                    if not link.strip():
                        continue
                    if not self._is_internal_url(link):
                        continue

                    # Skip already-visited links
                    if link in visited:
                        continue

                    # Skip ignored patterns (unchanged exact/path/glob semantics)
                    if self._should_ignore_link(link):
                        self._crawl_stat_add("links_ignored")
                        continue

                    # Respect the max_links limit per discovered link too.
                    # Candidates past the cap are counted as truncated rather
                    # than silently discarded; the crawl stops at the outer
                    # loop's cap check once this page's links are tallied.
                    if self.max_links and len(self.target_links) >= self.max_links:
                        self._crawl_stat_add("links_truncated")
                        continue

                    visited.add(link)
                    self.target_links.append(link)
                    self._crawl_stat_add("endpoints_discovered")
                    logger.info(f"Discovered: {link}")
                    print(f"[+] Discovered: {link}")

                    if current_depth + 1 <= max_depth:
                        queue.append((link, current_depth + 1))

                except Exception as e:
                    logger.debug(f"Error processing link {link}: {e}")
                    continue
    
    def extract_forms(self, url: str) -> List[Any]:
        """
        Extract HTML forms from a URL.
        
        Args:
            url: URL to extract forms from
            
        Returns:
            List of BeautifulSoup form elements
        """
        try:
            response: Response = self._request("GET", url)
            response.raise_for_status()
            
            parsed_html = BeautifulSoup(response.content, features="lxml")
            forms: List[Any] = parsed_html.find_all("form")
            
            logger.debug(f"Extracted {len(forms)} forms from {url}")
            return forms
            
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout extracting forms from {url}")
            return []
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to extract forms from {url}: {e}")
            return []
        except Exception as e:
            logger.error(f"Error parsing HTML from {url}: {e}")
            return []
    
    def submit_form(
        self, 
        form: Any, 
        value: str, 
        url: str
    ) -> Optional[Response]:
        """
        Submit an HTML form with test payload.
        
        Args:
            form: BeautifulSoup form element
            value: Payload to inject
            url: Base URL
            
        Returns:
            Response object or None on error
        """
        try:
            action: str = form.get("action") or ""
            post_url: str = urlparse.urljoin(url, action)
            method: str = form.get("method", "get").lower()
            
            # Extract input fields (inputs, textareas and selects)
            post_data: Dict[str, str] = {}
            
            for input_field in form.find_all("input"):
                field_name = input_field.get("name")
                if field_name:
                    post_data[field_name] = input_field.get("value") or value
            
            # Textarea fields carry their payload in the body
            for textarea in form.find_all("textarea"):
                field_name = textarea.get("name")
                if field_name:
                    post_data[field_name] = value
            
            # Select fields submit the selected (or first) option value
            for select in form.find_all("select"):
                field_name = select.get("name")
                if not field_name:
                    continue
                selected = select.find("option", selected=True)
                if selected is None:
                    selected = select.find("option")
                if selected is not None:
                    post_data[field_name] = selected.get("value") or value
                else:
                    post_data[field_name] = value
            
            # Submit form
            if method == "post":
                response: Response = self._request(
                    "POST",
                    post_url, 
                    data=post_data, 
                )
            else:
                response = self._request(
                    "GET",
                    post_url, 
                    params=post_data, 
                )
            
            logger.debug(f"Form submitted to {post_url}")
            return response
            
        except Exception as e:
            logger.error(f"Form submission error: {e}")
            return None
    
    # ========================================================================
    # XSS DETECTION METHODS
    # ========================================================================
    
    def test_xss_in_link(self, url: str) -> bool:
        """
        Test URL for XSS vulnerability using multiple payloads.
        
        Args:
            url: URL to test
            
        Returns:
            True if vulnerability found
        """
        parsed_url = urlparse.urlsplit(url)
        query_params = urlparse.parse_qsl(parsed_url.query, keep_blank_values=True)
        if not query_params:
            return False

        preferred_param = query_params[0][0]
        for payload in XSS_PAYLOADS[:5]:  # Test with first 5 payloads
            try:
                injected = self._inject_payload_into_url(url, preferred_param, payload)
                if not injected:
                    return False
                test_url, parameter = injected
                
                response = self._request("GET", test_url)
                response_content: str = self._decode_response_content(response)
                
                # Check if payload reflected (raw or HTML/URL encoded)
                if self._is_reflected(payload, response_content):
                    logger.warning(f"XSS found in URL: {url}")
                    vuln = Vulnerability(
                        vuln_type=VulnerabilityType.XSS,
                        url=url,
                        payload=payload,
                        severity=Severity.HIGH,
                        method="GET",
                        parameter=parameter,
                        response_code=response.status_code,
                        response_content=response_content[:200]
                    )
                    self._add_vulnerability_once(vuln)
                    
                    return True
                    
            except Exception as e:
                logger.debug(f"Error testing XSS payload {payload}: {e}")
                continue
        
        return False
    
    def _test_form_with_payloads(
        self, 
        form: Any, 
        url: str, 
        payloads: List[str],
        vuln_type: VulnerabilityType,
        severity: Severity,
        max_payloads: int = 5,
        check_patterns: Optional[List[str]] = None,
        check_payload: bool = False
    ) -> bool:
        """
        Generic method to test form for vulnerabilities.
        
        Reduces code duplication by providing a common interface for vulnerability testing.
        This method handles payload injection, response checking, and result recording.
        
        Args:
            form: BeautifulSoup form element to test
            url: Base URL of the form
            payloads: List of payloads to test
            vuln_type: Type of vulnerability being tested
            severity: Severity level of the vulnerability
            max_payloads: Maximum number of payloads to test (default: 5)
            check_patterns: Optional list of regex patterns to check in response
            check_payload: If True, check if payload is directly in response content
            
        Returns:
            True if vulnerability found and recorded, False otherwise
        """
        for payload in payloads[:max_payloads]:
            try:
                response: Optional[Response] = self.submit_form(form, payload, url)
                
                if response is None:
                    continue
                
                response_content: str = response.content.decode(errors='ignore')
                
                # Check vulnerability using patterns or payload reflection
                vulnerability_found: bool = False
                
                if check_payload:
                    vulnerability_found = self._is_reflected(payload, response_content)
                elif check_patterns:
                    # Ignore matches inside the reflected payload itself to
                    # avoid false positives from input echo
                    sanitized_content = response_content.replace(payload, "")
                    vulnerability_found = any(
                        re.search(pattern, sanitized_content, re.IGNORECASE) 
                        for pattern in check_patterns
                    )
                
                if vulnerability_found:
                    logger.warning(f"{vuln_type} found in form: {url}")
                    vuln = Vulnerability(
                        vuln_type=vuln_type,
                        url=url,
                        payload=payload,
                        severity=severity,
                        method="POST",
                        response_code=response.status_code,
                        response_content=response_content[:200]
                    )
                    self._add_vulnerability_once(vuln)
                    
                    return True
                    
            except Exception as e:
                logger.debug(f"Error testing {vuln_type} in form: {e}")
                continue
        
        return False
    
    def test_xss_in_form(self, form: Any, url: str, custom_payload: Optional[str] = None) -> bool:
        """
        Test form for XSS vulnerability.
        
        Args:
            form: BeautifulSoup form element
            url: Base URL
            
        Returns:
            True if vulnerability found
        """
        payloads = [custom_payload] if custom_payload else XSS_PAYLOADS
        return self._test_form_with_payloads(
            form=form,
            url=url,
            payloads=payloads,
            vuln_type=VulnerabilityType.XSS,
            severity=Severity.HIGH,
            max_payloads=1 if custom_payload else 5,
            check_payload=True
        )
    
    # ========================================================================
    # SQL INJECTION DETECTION METHODS
    # ========================================================================
    
    def test_sqli_in_link(self, url: str) -> bool:
        """
        Test URL for SQL injection vulnerability.
        
        Args:
            url: URL to test
            
        Returns:
            True if vulnerability found
        """
        parsed_url = urlparse.urlsplit(url)
        query_params = urlparse.parse_qsl(parsed_url.query, keep_blank_values=True)
        if not query_params:
            return False

        preferred_param = query_params[0][0]
        for payload in SQLI_PAYLOADS[:5]:
            try:
                injected = self._inject_payload_into_url(url, preferred_param, payload)
                if not injected:
                    return False
                test_url, parameter = injected
                
                start_time = time.monotonic()
                response = self._request("GET", test_url)
                elapsed = time.monotonic() - start_time
                # The politeness delay is applied inside _request AFTER the
                # response arrives; exclude it so a configured delay cannot
                # masquerade as a genuinely slow (vulnerable) response.
                if self.delay > 0:
                    elapsed = max(0.0, elapsed - self.delay)
                response_content: str = self._decode_response_content(response)
                
                # Check for SQL error patterns
                error_found = any(
                    error in response_content for error in SQL_ERROR_PATTERNS
                )
                # Time-based blind SQLi detection (e.g. SLEEP(5))
                time_found = (
                    self._is_time_based(payload)
                    and elapsed >= self.time_based_threshold
                )
                
                if error_found or time_found:
                    logger.warning(f"SQLi found in URL: {url}")
                    vuln = Vulnerability(
                        vuln_type=VulnerabilityType.SQL_INJECTION,
                        url=url,
                        payload=payload,
                        severity=Severity.CRITICAL,
                        method="GET",
                        parameter=parameter,
                        response_code=response.status_code,
                        response_content=response_content[:200],
                        metadata={
                            "detection": "time-based" if time_found else "error-based",
                            "response_time": round(elapsed, 3),
                        }
                    )
                    self._add_vulnerability_once(vuln)
                    
                    return True
                    
            except Exception as e:
                logger.debug(f"Error testing SQLi: {e}")
                continue
        
        return False
    
    def test_sqli_in_form(self, form: Any, url: str) -> bool:
        """
        Test form for SQL injection vulnerability.
        
        Args:
            form: BeautifulSoup form element
            url: Base URL
            
        Returns:
            True if vulnerability found
        """
        return self._test_form_with_payloads(
            form=form,
            url=url,
            payloads=SQLI_PAYLOADS,
            vuln_type=VulnerabilityType.SQL_INJECTION,
            severity=Severity.CRITICAL,
            max_payloads=5,
            check_patterns=SQL_ERROR_PATTERNS
        )
    
    # ========================================================================
    # CSRF DETECTION METHODS
    # ========================================================================
    
    def test_csrf(self, form: Any, url: str) -> bool:
        """
        Test form for CSRF vulnerabilities.
        
        Args:
            form: BeautifulSoup form element
            url: Base URL
            
        Returns:
            True if CSRF vulnerability found
        """
        try:
            # Get form method
            method: str = form.get("method", "get").lower()
            
            # Check for CSRF token
            has_token = False
            inputs = form.find_all("input")
            
            for input_field in inputs:
                field_name = input_field.get("name", "").lower()
                if any(csrf_indicator in field_name 
                       for csrf_indicator in CSRF_INDICATORS["missing_tokens"]):
                    has_token = True
                    break
            
            # POST/PUT/DELETE without CSRF token is vulnerability
            if method in ["post", "put", "delete"] and not has_token:
                logger.warning(f"Possible CSRF vulnerability in form at {url}")
                vuln = Vulnerability(
                    vuln_type=VulnerabilityType.CSRF,
                    url=url,
                    payload="Missing CSRF token",
                    severity=Severity.MEDIUM,
                    method=method.upper(),
                    response_code=200
                )
                self._add_vulnerability_once(vuln)
                
                return True
                
        except Exception as e:
            logger.debug(f"Error testing CSRF: {e}")
        
        return False
    
    # ========================================================================
    # SECURITY HEADER ANALYSIS
    # ========================================================================
    
    def _check_security_headers_on_response(
        self,
        response: Response,
        url: str,
        method_used: str,
        status_code: int
    ) -> None:
        """
        Analyze a response for missing or misconfigured security headers.

        Also inspects cookie flags (HttpOnly, Secure, SameSite) so weak session
        cookies are reported alongside other header misconfigurations.
        """
        headers = response.headers

        for header in SECURE_HEADERS.keys():
            if header not in headers:
                logger.warning(f"Missing security header: {header}")
                self._add_vulnerability_once(Vulnerability(
                    vuln_type=VulnerabilityType.INSECURE_HEADERS,
                    url=url,
                    payload=f"Missing {header}",
                    severity=Severity.LOW,
                    method=method_used,
                    response_code=status_code
                ))

        # Weak header value checks
        csp = headers.get("Content-Security-Policy", "")
        csp_lower = csp.lower()
        if csp and ("unsafe-inline" in csp_lower or "unsafe-eval" in csp_lower):
            logger.warning(f"Weak CSP on {url}: uses unsafe-inline/unsafe-eval")
            self._add_vulnerability_once(Vulnerability(
                vuln_type=VulnerabilityType.INSECURE_HEADERS,
                url=url,
                payload="Weak CSP: unsafe-inline/unsafe-eval present",
                severity=Severity.MEDIUM,
                method=method_used,
                response_code=status_code
            ))

        xfo = headers.get("X-Frame-Options", "")
        if xfo and xfo.upper() == "ALLOWALL":
            logger.warning(f"X-Frame-Options ALLOWALL on {url}")
            self._add_vulnerability_once(Vulnerability(
                vuln_type=VulnerabilityType.INSECURE_HEADERS,
                url=url,
                payload="X-Frame-Options set to ALLOWALL",
                severity=Severity.MEDIUM,
                method=method_used,
                response_code=status_code
            ))

        self._check_cookie_flags(response, url, method_used, status_code)

    def _check_cookie_flags(
        self,
        response: Response,
        url: str,
        method_used: str,
        status_code: int
    ) -> None:
        """Report cookies missing HttpOnly, Secure or SameSite flags."""
        cookie_strings: List[str] = []

        # Prefer the raw header list (handles multiple Set-Cookie correctly)
        raw = getattr(response, "raw", None)
        if raw is not None and hasattr(raw, "getlist"):
            cookie_strings = list(raw.getlist("Set-Cookie"))

        if not cookie_strings:
            headers = response.headers
            set_cookie = headers.get("Set-Cookie") if hasattr(headers, "get") else None
            if set_cookie and ", " not in set_cookie:
                cookie_strings = [set_cookie]

        for cookie_str in cookie_strings:
            name = cookie_str.split(";")[0].split("=")[0].strip()
            if not name:
                continue
            lowered = cookie_str.lower()
            for flag, severity in (
                ("httponly", Severity.LOW),
                ("secure", Severity.LOW),
                ("samesite", Severity.INFO),
            ):
                if flag not in lowered:
                    logger.warning(
                        f"Cookie '{name}' missing {flag.upper()} flag on {url}"
                    )
                    self._add_vulnerability_once(Vulnerability(
                        vuln_type=VulnerabilityType.INSECURE_HEADERS,
                        url=url,
                        payload=f"Cookie '{name}' missing {flag.upper()}",
                        severity=severity,
                        method=method_used,
                        response_code=status_code
                    ))

    def test_security_headers(self, url: str) -> None:
        """
        Check for missing or misconfigured security headers and weak cookies.
        
        Args:
            url: URL to test
        """
        try:
            response: Response = self._request("HEAD", url, allow_redirects=True)
            method_used = "HEAD"
            if response.status_code in (405, 501):
                response = self._request("GET", url, allow_redirects=True)
                method_used = "GET"
            self._check_security_headers_on_response(
                response, url, method_used, response.status_code
            )

        except requests.exceptions.RequestException as e:
            logger.debug(f"HEAD request failed for {url}, retrying with GET: {e}")
            try:
                response = self._request("GET", url, allow_redirects=True)
                self._check_security_headers_on_response(
                    response, url, "GET", response.status_code
                )
            except Exception as inner_exc:
                logger.debug(f"Error testing security headers: {inner_exc}")
        except Exception as e:
            logger.debug(f"Error testing security headers: {e}")
    
    # ========================================================================
    # XXE INJECTION DETECTION
    # ========================================================================
    
    def test_xxe_injection(self, url: str) -> bool:
        """
        Test URL for XXE (XML External Entity) vulnerability.
        
        Args:
            url: URL to test
            
        Returns:
            True if vulnerability found
        """
        parsed_url = urlparse.urlsplit(url)
        query_params = urlparse.parse_qsl(parsed_url.query, keep_blank_values=True)
        if not query_params:
            return False

        preferred_param = query_params[0][0]
        for payload in XXE_PAYLOADS[:3]:  # Test with first 3 XXE payloads
            try:
                injected = self._inject_payload_into_url(url, preferred_param, payload)
                if not injected:
                    return False
                test_url, parameter = injected
                
                response = self._request("GET", test_url)
                response_content: str = self._decode_response_content(response)
                
                # Check for XXE error patterns, ignoring the reflected payload
                # itself (error keywords like DOCTYPE/XML are part of the payload)
                sanitized_content = response_content.replace(payload, "")
                for pattern in XXE_ERROR_PATTERNS:
                    if re.search(pattern, sanitized_content, re.IGNORECASE):
                        logger.warning(f"XXE found in URL: {url}")
                        vuln = Vulnerability(
                            vuln_type=VulnerabilityType.XXE,
                            url=url,
                            payload=payload,
                            severity=Severity.CRITICAL,
                            method="GET",
                            parameter=parameter,
                            response_code=response.status_code,
                            response_content=response_content[:200]
                        )
                        self._add_vulnerability_once(vuln)
                        
                        return True
                        
            except Exception as e:
                logger.debug(f"Error testing XXE payload: {e}")
                continue
        
        return False
    
    def test_xxe_in_form(self, form: Any, url: str) -> bool:
        """
        Test form for XXE vulnerability.
        
        Args:
            form: BeautifulSoup form element
            url: Base URL
            
        Returns:
            True if vulnerability found
        """
        return self._test_form_with_payloads(
            form=form,
            url=url,
            payloads=XXE_PAYLOADS,
            vuln_type=VulnerabilityType.XXE,
            severity=Severity.CRITICAL,
            max_payloads=3,
            check_patterns=XXE_ERROR_PATTERNS
        )
    
    # ========================================================================
    # PATH TRAVERSAL DETECTION
    # ========================================================================
    
    def test_path_traversal(self, url: str) -> bool:
        """
        Test URL for Path Traversal vulnerability.
        
        Args:
            url: URL to test
            
        Returns:
            True if vulnerability found
        """
        parsed_url = urlparse.urlsplit(url)
        query_params = urlparse.parse_qsl(parsed_url.query, keep_blank_values=True)
        if not query_params:
            return False

        preferred_param = query_params[0][0]
        for payload in PATH_TRAVERSAL_PAYLOADS[:5]:  # Test with first 5 payloads
            try:
                injected = self._inject_payload_into_url(url, preferred_param, payload)
                if not injected:
                    return False
                test_url, parameter = injected
                
                response = self._request("GET", test_url)
                response_content: str = self._decode_response_content(response)
                
                # Check for path traversal patterns, ignoring the reflected
                # payload itself
                sanitized_content = response_content.replace(payload, "")
                for pattern in PATH_TRAVERSAL_PATTERNS:
                    if re.search(pattern, sanitized_content, re.IGNORECASE):
                        logger.warning(f"Path Traversal found in URL: {url}")
                        vuln = Vulnerability(
                            vuln_type=VulnerabilityType.PATH_TRAVERSAL,
                            url=url,
                            payload=payload,
                            severity=Severity.HIGH,
                            method="GET",
                            parameter=parameter,
                            response_code=response.status_code,
                            response_content=response_content[:200]
                        )
                        self._add_vulnerability_once(vuln)
                        
                        return True
                        
            except Exception as e:
                logger.debug(f"Error testing path traversal payload: {e}")
                continue
        
        return False
    
    def test_path_traversal_in_form(self, form: Any, url: str) -> bool:
        """
        Test form for Path Traversal vulnerability.
        
        Args:
            form: BeautifulSoup form element
            url: Base URL
            
        Returns:
            True if vulnerability found
        """
        return self._test_form_with_payloads(
            form=form,
            url=url,
            payloads=PATH_TRAVERSAL_PAYLOADS,
            vuln_type=VulnerabilityType.PATH_TRAVERSAL,
            severity=Severity.HIGH,
            max_payloads=5,
            check_patterns=PATH_TRAVERSAL_PATTERNS
        )
    
    # ========================================================================
    # OPEN REDIRECT DETECTION
    # ========================================================================

    def test_open_redirect(self, url: str) -> bool:
        """
        Test URL for Open Redirect vulnerability.
        
        Sends requests without following redirects and checks whether the
        Location header reflects an attacker-controlled payload or points
        to a different host.
        
        Args:
            url: URL to test
            
        Returns:
            True if vulnerability found
        """
        parsed_url = urlparse.urlsplit(url)
        query_params = urlparse.parse_qsl(parsed_url.query, keep_blank_values=True)
        if not query_params:
            return False

        preferred_param = query_params[0][0]
        for payload in OPEN_REDIRECT_PAYLOADS[:5]:
            try:
                injected = self._inject_payload_into_url(url, preferred_param, payload)
                if not injected:
                    return False
                test_url, parameter = injected

                response = self._request("GET", test_url, allow_redirects=False)

                if response.status_code not in REDIRECT_STATUS_CODES:
                    continue

                location = response.headers.get("Location", "")
                if not location:
                    continue

                # Confirm the redirect is attacker-controlled: either the marker
                # domain is reflected, or the target resolves to another host
                location_lower = location.lower()
                if OPEN_REDIRECT_MARKER in location_lower:
                    found = True
                else:
                    redirect_target = urlparse.urlsplit(
                        urlparse.urljoin(test_url, location)
                    )
                    # Compare hostnames (not netloc): netloc also carries the
                    # port and any userinfo, so a same-host redirect to an
                    # explicit port (http://host:8443/) would otherwise be
                    # reported as a false positive.
                    target_host = (redirect_target.hostname or "").lower()
                    found = bool(
                        target_host
                        and target_host != (self._target_parts.hostname or "").lower()
                    )

                if found:
                    logger.warning(f"Open Redirect found in URL: {url}")
                    vuln = Vulnerability(
                        vuln_type=VulnerabilityType.OPEN_REDIRECT,
                        url=url,
                        payload=payload,
                        severity=Severity.MEDIUM,
                        method="GET",
                        parameter=parameter,
                        response_code=response.status_code,
                        response_content=location[:200]
                    )
                    self._add_vulnerability_once(vuln)

                    return True

            except Exception as e:
                logger.debug(f"Error testing open redirect payload {payload}: {e}")
                continue

        return False

    # ========================================================================
    # COMMAND INJECTION DETECTION
    # ========================================================================

    def test_command_injection(self, url: str) -> bool:
        """
        Test URL for Command Injection vulnerability.
        
        Uses output-pattern detection for reflected command results and
        time-based detection for blind command injection (e.g. `sleep`).
        
        Args:
            url: URL to test
            
        Returns:
            True if vulnerability found
        """
        parsed_url = urlparse.urlsplit(url)
        query_params = urlparse.parse_qsl(parsed_url.query, keep_blank_values=True)
        if not query_params:
            return False

        preferred_param = query_params[0][0]
        for payload in COMMAND_INJECTION_PAYLOADS[:5]:
            try:
                injected = self._inject_payload_into_url(url, preferred_param, payload)
                if not injected:
                    return False
                test_url, parameter = injected

                start_time = time.monotonic()
                response = self._request("GET", test_url)
                elapsed = time.monotonic() - start_time
                # Exclude the post-response politeness delay (see test_sqli_in_link)
                if self.delay > 0:
                    elapsed = max(0.0, elapsed - self.delay)
                response_content: str = self._decode_response_content(response)

                # Ignore matches inside the reflected payload itself: a page
                # that merely echoes the request must not be mistaken for
                # command output (e.g. '| whoami' contains the word 'whoami').
                sanitized_content = response_content.replace(payload, "")
                pattern_found = any(
                    re.search(pattern, sanitized_content, re.IGNORECASE)
                    for pattern in COMMAND_INJECTION_PATTERNS
                )
                time_found = (
                    self._is_time_based(payload)
                    and elapsed >= self.time_based_threshold
                )

                if pattern_found or time_found:
                    logger.warning(f"Command Injection found in URL: {url}")
                    vuln = Vulnerability(
                        vuln_type=VulnerabilityType.COMMAND_INJECTION,
                        url=url,
                        payload=payload,
                        severity=Severity.CRITICAL,
                        method="GET",
                        parameter=parameter,
                        response_code=response.status_code,
                        response_content=response_content[:200],
                        metadata={
                            "detection": "time-based" if time_found else "output-based",
                            "response_time": round(elapsed, 3),
                        }
                    )
                    self._add_vulnerability_once(vuln)

                    return True

            except Exception as e:
                logger.debug(f"Error testing command injection payload {payload}: {e}")
                continue

        return False

    def test_command_injection_in_form(self, form: Any, url: str) -> bool:
        """
        Test form for Command Injection vulnerability.
        
        Args:
            form: BeautifulSoup form element
            url: Base URL
            
        Returns:
            True if vulnerability found
        """
        return self._test_form_with_payloads(
            form=form,
            url=url,
            payloads=COMMAND_INJECTION_PAYLOADS,
            vuln_type=VulnerabilityType.COMMAND_INJECTION,
            severity=Severity.CRITICAL,
            max_payloads=5,
            check_patterns=COMMAND_INJECTION_PATTERNS
        )

    # ========================================================================
    # MAIN SCANNING ORCHESTRATOR
    # ========================================================================
    
    def _scan_single_endpoint(self, link: str) -> None:
        """Run the full battery of checks against one endpoint."""
        # Extract and test forms
        forms = self.extract_forms(link)

        for form_idx, form in enumerate(forms):
            print(f"  [+] Testing form {form_idx + 1}")

            # XSS in form
            if self.test_xss_in_form(form, link):
                print("  [***] XSS FOUND in form")

            # SQLi in form
            if self.test_sqli_in_form(form, link):
                print("  [***] SQL INJECTION FOUND in form")

            # XXE in form
            if self.test_xxe_in_form(form, link):
                print("  [***] XXE FOUND in form")

            # Path Traversal in form
            if self.test_path_traversal_in_form(form, link):
                print("  [***] PATH TRAVERSAL FOUND in form")

            # Command Injection in form
            if self.test_command_injection_in_form(form, link):
                print("  [***] COMMAND INJECTION FOUND in form")

            # CSRF
            if self.test_csrf(form, link):
                print("  [***] CSRF VULNERABILITY FOUND")

        # URL-based tests
        if "=" in link:
            if self.test_xss_in_link(link):
                print("  [***] XSS FOUND in URL parameters")

            if self.test_sqli_in_link(link):
                print("  [***] SQL INJECTION FOUND in URL parameters")

            if self.test_xxe_injection(link):
                print("  [***] XXE FOUND in URL parameters")

            if self.test_path_traversal(link):
                print("  [***] PATH TRAVERSAL FOUND in URL parameters")

            if self.test_open_redirect(link):
                print("  [***] OPEN REDIRECT FOUND in URL parameters")

            if self.test_command_injection(link):
                print("  [***] COMMAND INJECTION FOUND in URL parameters")

        # Security headers and cookie flags
        self.test_security_headers(link)

    def run_scanner(self, threads: int = 1) -> ScanResult:
        """
        Execute comprehensive vulnerability scan.
        
        Tests all discovered endpoints for:
        - XSS vulnerabilities (URL and form-based)
        - SQL injection (URL and form-based, including time-based blind)
        - XXE (XML External Entity) injection
        - Path Traversal vulnerabilities
        - Open Redirect vulnerabilities
        - Command Injection vulnerabilities
        - CSRF vulnerabilities
        - Security headers and cookie flags
        
        Args:
            threads: Number of concurrent workers (1 = sequential)
        
        Returns:
            ScanResult with all findings
        """
        if threads < 1:
            raise ValueError("threads must be 1 or greater")

        # Scan the seed URL as well so forms/parameters on the entry page
        # (e.g. a login form) are never missed.
        unique_links = list(dict.fromkeys(self.target_links))
        scan_links = [self.target_url] + [
            link for link in unique_links if link != self.target_url
        ]
        self.scan_result = ScanResult(self.target_url)
        self._seen_vulnerability_keys.clear()

        logger.info(f"Starting scan on {len(scan_links)} endpoints")
        print(f"\n[*] Scanning {len(scan_links)} endpoints for vulnerabilities...\n")

        self.scan_result.endpoints_discovered = len(scan_links)

        def scan_one(link: str) -> None:
            try:
                with self.results_lock:
                    self.scan_result.endpoints_scanned += 1
                self._scan_single_endpoint(link)
            except Exception as e:
                logger.error(f"Error scanning {link}: {e}")

        if threads == 1:
            for i, link in enumerate(scan_links, 1):
                logger.info(f"Scanning [{i}/{len(scan_links)}] {link}")
                print(f"[*] [{i}/{len(scan_links)}] Scanning: {link}")
                scan_one(link)
        else:
            logger.info(f"Scanning {len(scan_links)} endpoints with {threads} threads")
            with ThreadPoolExecutor(max_workers=threads) as executor:
                futures = [executor.submit(scan_one, link) for link in scan_links]
                for future in as_completed(futures):
                    future.result()  # re-raise unexpected failures

        # Mark scan complete
        self.scan_result.finish()

        logger.info(
            f"Scan complete. Found {len(self.scan_result.vulnerabilities)} vulnerabilities"
        )
        print(f"\n[*] Scan complete. Found {len(self.scan_result.vulnerabilities)} vulnerabilities")

        return self.scan_result
