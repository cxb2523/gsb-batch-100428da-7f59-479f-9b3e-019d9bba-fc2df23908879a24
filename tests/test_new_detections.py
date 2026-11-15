"""Tests for the new detection engines and scanner hardening."""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock
from scanner import Scanner
from models.vulnerability import VulnerabilityType, Severity


@pytest.fixture
def scanner():
    """Scanner with a short time-based threshold for fast tests."""
    return Scanner("http://example.com", timeout=5, time_based_threshold=0.05)


class TestOpenRedirect:
    """Tests for open redirect detection."""

    def test_open_redirect_marker_reflected(self, scanner):
        """Detect redirect when the marker domain is reflected in Location."""
        def fake_get(url, **kwargs):
            response = Mock()
            response.status_code = 302
            response.headers = {"Location": "https://evil.com"}
            return response

        with patch.object(scanner.session, "get", side_effect=fake_get):
            result = scanner.test_open_redirect("http://example.com/go?url=test")

        assert result is True
        vulns = scanner.scan_result.get_vulnerabilities_by_type(
            VulnerabilityType.OPEN_REDIRECT
        )
        assert len(vulns) == 1
        assert vulns[0].severity == Severity.MEDIUM
        assert vulns[0].parameter == "url"

    def test_open_redirect_external_host(self, scanner):
        """Detect redirect to a different host even without the marker."""
        def fake_get(url, **kwargs):
            assert kwargs.get("allow_redirects") is False
            response = Mock()
            response.status_code = 301
            response.headers = {"Location": "http://other-site.com/landing"}
            return response

        with patch.object(scanner.session, "get", side_effect=fake_get):
            result = scanner.test_open_redirect("http://example.com/r?next=test")

        assert result is True

    def test_open_redirect_same_host_not_flagged(self, scanner):
        """A redirect to the same host is not a vulnerability."""
        def fake_get(url, **kwargs):
            response = Mock()
            response.status_code = 302
            response.headers = {"Location": "/login"}
            return response

        with patch.object(scanner.session, "get", side_effect=fake_get):
            result = scanner.test_open_redirect("http://example.com/r?next=test")

        assert result is False

    def test_open_redirect_same_host_explicit_port_not_flagged(self, scanner):
        """A same-host redirect that only adds a port is not a vulnerability."""
        def fake_get(url, **kwargs):
            response = Mock()
            response.status_code = 302
            response.headers = {"Location": "http://example.com:8443/login"}
            return response

        with patch.object(scanner.session, "get", side_effect=fake_get):
            result = scanner.test_open_redirect("http://example.com/r?next=test")

        assert result is False

    def test_open_redirect_no_redirect_status(self, scanner):
        """A normal 200 response is not flagged."""
        with patch.object(scanner.session, "get") as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.headers = {}
            mock_get.return_value = mock_response

            result = scanner.test_open_redirect("http://example.com/r?next=test")

        assert result is False

    def test_open_redirect_no_parameters(self, scanner):
        """URLs without query parameters are skipped."""
        assert scanner.test_open_redirect("http://example.com/page") is False


class TestCommandInjection:
    """Tests for command injection detection."""

    def test_command_injection_output_pattern(self, scanner):
        """Detect command injection via reflected command output."""
        with patch.object(scanner.session, "get") as mock_get:
            mock_response = Mock()
            mock_response.content = b"uid=1000(test) gid=1000(test)"
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            result = scanner.test_command_injection("http://example.com/ping?host=test")

        assert result is True
        vulns = scanner.scan_result.get_vulnerabilities_by_type(
            VulnerabilityType.COMMAND_INJECTION
        )
        assert len(vulns) == 1
        assert vulns[0].severity == Severity.CRITICAL

    def test_command_injection_time_based(self, scanner):
        """Detect blind command injection via timing."""
        def fake_get(url, **kwargs):
            time.sleep(0.1)  # longer than the 0.05s threshold
            response = Mock()
            response.content = b"<html>ok</html>"
            response.status_code = 200
            return response

        with patch.object(scanner.session, "get", side_effect=fake_get):
            result = scanner.test_command_injection("http://example.com/ping?host=test")

        assert result is True
        vulns = scanner.scan_result.get_vulnerabilities_by_type(
            VulnerabilityType.COMMAND_INJECTION
        )
        assert vulns[0].metadata.get("detection") == "time-based"

    def test_command_injection_safe(self, scanner):
        """Safe responses are not flagged."""
        with patch.object(scanner.session, "get") as mock_get:
            mock_response = Mock()
            mock_response.content = b"<html>pong</html>"
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            result = scanner.test_command_injection("http://example.com/ping?host=test")

        assert result is False

    def test_command_injection_in_form(self, scanner):
        """Detect command injection through form submission."""
        from bs4 import BeautifulSoup

        html = """
        <form action="/ping" method="post">
            <input name="host" type="text">
        </form>
        """

        form = BeautifulSoup(html, 'lxml').find('form')

        with patch.object(scanner, "submit_form") as mock_submit:
            mock_response = Mock()
            mock_response.content = b"uid=1000(test) gid=1000(test)"
            mock_response.status_code = 200
            mock_submit.return_value = mock_response

            result = scanner.test_command_injection_in_form(form, "http://example.com")

        assert result is True


class TestTimeBasedSQLi:
    """Tests for time-based blind SQL injection detection."""

    def test_sqli_time_based_detected(self, scanner):
        """A delayed response to a SLEEP payload is flagged as SQLi."""
        def fake_get(url, **kwargs):
            time.sleep(0.1)
            response = Mock()
            response.content = b"<html>page</html>"
            response.status_code = 200
            return response

        with patch.object(scanner.session, "get", side_effect=fake_get):
            result = scanner.test_sqli_in_link("http://example.com/user?id=1")

        assert result is True
        vulns = scanner.scan_result.get_vulnerabilities_by_type(
            VulnerabilityType.SQL_INJECTION
        )
        assert vulns
        assert vulns[0].metadata.get("detection") == "time-based"

    def test_sqli_fast_response_not_time_flagged(self, scanner):
        """Fast responses to time-based payloads are not flagged by timing."""
        with patch.object(scanner.session, "get") as mock_get:
            mock_response = Mock()
            mock_response.content = b"<html>page</html>"
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            result = scanner.test_sqli_in_link("http://example.com/user?id=1")

        assert result is False


class TestEncodedXSS:
    """Tests for XSS detection across HTML-encoded reflections."""

    def test_xss_html_escaped_reflection(self, scanner):
        """Detect XSS when the server HTML-encodes the reflected payload."""
        payload = "<script>alert('XSS')</script>"
        escaped = "&lt;script&gt;alert('XSS')&lt;/script&gt;"

        def fake_get(url, **kwargs):
            response = Mock()
            response.content = f"Search: {escaped}".encode()
            response.status_code = 200
            return response

        with patch.object(scanner.session, "get", side_effect=fake_get):
            result = scanner.test_xss_in_link("http://example.com/search?q=test")

        assert result is True

    def test_xss_encoded_reflection_in_form(self, scanner):
        """Detect XSS in forms when the payload is reflected HTML-encoded."""
        from bs4 import BeautifulSoup

        html = """
        <form action="/search" method="get">
            <input name="q" type="text">
        </form>
        """
        form = BeautifulSoup(html, 'lxml').find('form')

        with patch.object(scanner, "submit_form") as mock_submit:
            mock_response = Mock()
            mock_response.content = b"Result: &lt;script&gt;alert(&#x27;XSS&#x27;)&lt;/script&gt;"
            mock_response.status_code = 200
            mock_submit.return_value = mock_response

            result = scanner.test_xss_in_form(form, "http://example.com")

        assert result is True


class TestFormFieldHandling:
    """Tests for select/textarea handling in form submission."""

    def test_submit_form_handles_textarea(self, scanner):
        """Textarea contents are populated with the payload."""
        from bs4 import BeautifulSoup

        html = """
        <form action="/post" method="post">
            <textarea name="comment"></textarea>
        </form>
        """
        form = BeautifulSoup(html, 'lxml').find('form')

        with patch.object(scanner.session, "post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.content = b"ok"
            mock_post.return_value = mock_response

            scanner.submit_form(form, "PAYLOAD", "http://example.com")
            submitted = mock_post.call_args[1]["data"]
            assert submitted["comment"] == "PAYLOAD"

    def test_submit_form_handles_select(self, scanner):
        """Select fields submit the selected option's value."""
        from bs4 import BeautifulSoup

        html = """
        <form action="/filter" method="post">
            <select name="category">
                <option value="a">A</option>
                <option value="b" selected>B</option>
            </select>
        </form>
        """
        form = BeautifulSoup(html, 'lxml').find('form')

        with patch.object(scanner.session, "post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.content = b"ok"
            mock_post.return_value = mock_response

            scanner.submit_form(form, "PAYLOAD", "http://example.com")
            submitted = mock_post.call_args[1]["data"]
            assert submitted["category"] == "b"

    def test_submit_form_select_falls_back_to_first_option(self, scanner):
        """Select without a selected option uses the first option value."""
        from bs4 import BeautifulSoup

        html = """
        <form action="/filter" method="post">
            <select name="category">
                <option value="a">A</option>
            </select>
        </form>
        """
        form = BeautifulSoup(html, 'lxml').find('form')

        with patch.object(scanner.session, "post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.content = b"ok"
            mock_post.return_value = mock_response

            scanner.submit_form(form, "PAYLOAD", "http://example.com")
            submitted = mock_post.call_args[1]["data"]
            assert submitted["category"] == "a"


class TestCrawlerLimits:
    """Tests for crawler limits and link parsing."""

    def test_crawl_respects_max_links(self, scanner):
        """Crawling stops once max_links is reached."""
        scanner = Scanner("http://example.com", max_links=2)

        with patch.object(scanner, "extract_links_from", return_value=[
            "http://example.com/p1",
            "http://example.com/p2",
            "http://example.com/p3",
            "http://example.com/p4",
        ]):
            scanner.crawl(url="http://example.com", depth=0, max_depth=1)

        assert len(scanner.target_links) == 2

    def test_crawl_max_links_zero_is_unlimited(self, scanner):
        """max_links=0 means no limit."""
        with patch.object(scanner, "extract_links_from", return_value=[
            "http://example.com/p1",
            "http://example.com/p2",
        ]):
            scanner.crawl(url="http://example.com", depth=0, max_depth=1)

        assert len(scanner.target_links) == 2

    def test_crawl_skips_external_and_ignored(self):
        """External links and ignored patterns are not collected."""
        scanner = Scanner("http://example.com", ignore_links=["/logout"])

        with patch.object(scanner, "extract_links_from", return_value=[
            "http://external.com/page",
            "http://example.com/logout",
            "http://example.com/home",
            "javascript:void(0)",
        ]):
            scanner.crawl(url="http://example.com", depth=0, max_depth=1)

        assert scanner.target_links == ["http://example.com/home"]

    def test_extract_links_unquoted_attributes(self, scanner):
        """Link extraction catches unquoted href attributes."""
        with patch.object(scanner.session, "get") as mock_get:
            mock_response = Mock()
            mock_response.content = b'<a href=/plain>Plain</a><a href="/quoted">Quoted</a>'
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            links = scanner.extract_links_from("http://example.com")

        assert "/plain" in links
        assert "/quoted" in links


class TestSecurityHeaderHardening:
    """Tests for weak header values and cookie flag analysis."""

    def test_weak_csp_detected(self, scanner):
        """CSP with unsafe-inline is reported as a misconfiguration."""
        with patch.object(scanner.session, "head") as mock_head:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.headers = {
                "Content-Security-Policy": "default-src 'self' 'unsafe-inline'"
            }
            mock_head.return_value = mock_response

            scanner.test_security_headers("http://example.com")

        weak = [v for v in scanner.scan_result.vulnerabilities
                if v.payload.startswith("Weak CSP")]
        assert weak
        assert weak[0].severity == Severity.MEDIUM

    def test_x_frame_options_allowall_detected(self, scanner):
        """X-Frame-Options ALLOWALL is reported as a misconfiguration."""
        with patch.object(scanner.session, "head") as mock_head:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.headers = {"X-Frame-Options": "ALLOWALL"}
            mock_head.return_value = mock_response

            scanner.test_security_headers("http://example.com")

        weak = [v for v in scanner.scan_result.vulnerabilities
                if "ALLOWALL" in v.payload]
        assert weak

    def test_weak_csp_not_reported_for_strong_csp(self, scanner):
        """A strong CSP does not trigger the weak-CSP finding."""
        with patch.object(scanner.session, "head") as mock_head:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.headers = {
                "Content-Security-Policy": "default-src 'self'; script-src 'self'"
            }
            mock_head.return_value = mock_response

            scanner.test_security_headers("http://example.com")

        weak = [v for v in scanner.scan_result.vulnerabilities
                if v.payload.startswith("Weak CSP")]
        assert not weak

    def test_cookie_flags_analyzed(self, scanner):
        """Cookies missing HttpOnly/Secure/SameSite are reported."""
        raw = MagicMock()
        raw.getlist.return_value = [
            "sessionid=abc123; Path=/",
            "csrf=xyz; Path=/; HttpOnly; Secure; SameSite=Lax",
        ]

        with patch.object(scanner.session, "head") as mock_head:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.headers = {}
            mock_response.raw = raw
            mock_head.return_value = mock_response

            scanner.test_security_headers("http://example.com")

        cookie_vulns = [v for v in scanner.scan_result.vulnerabilities
                        if v.payload.startswith("Cookie '")]
        payloads = {v.payload for v in cookie_vulns}
        assert "Cookie 'sessionid' missing HTTPONLY" in payloads
        assert "Cookie 'sessionid' missing SECURE" in payloads
        assert "Cookie 'sessionid' missing SAMESITE" in payloads
        # The well-configured cookie must not be reported
        assert all("csrf" not in v.payload for v in cookie_vulns)


class TestParallelScanning:
    """Tests for threaded endpoint scanning."""

    def test_run_scanner_with_threads(self, scanner):
        """Parallel scanning scans every endpoint exactly once."""
        scanner.target_links = [
            "http://example.com/page1?id=1",
            "http://example.com/page2?id=2",
            "http://example.com/page3?id=3",
        ]

        with patch.object(scanner, "extract_forms", return_value=[]):
            with patch.object(scanner, "test_xss_in_link", return_value=False):
                with patch.object(scanner, "test_sqli_in_link", return_value=False):
                    with patch.object(scanner, "test_xxe_injection", return_value=False):
                        with patch.object(scanner, "test_path_traversal", return_value=False):
                            with patch.object(scanner, "test_open_redirect", return_value=False):
                                with patch.object(scanner, "test_command_injection", return_value=False):
                                    with patch.object(scanner, "test_security_headers", return_value=None):
                                        result = scanner.run_scanner(threads=3)

        # Seed URL + 3 discovered links are all scanned exactly once
        assert result.endpoints_scanned == 4

    def test_run_scanner_rejects_invalid_threads(self, scanner):
        """threads must be at least 1."""
        with pytest.raises(ValueError):
            scanner.run_scanner(threads=0)


class TestPathTraversalAccuracy:
    """False-positive / false-negative regression tests for path traversal.

    The original pattern list also matched generic strings (``403``, ``404``,
    ``not found``, ``Access denied``, ``permission denied``, ``No such file``)
    that occur on ordinary pages, so basically every scanned page produced a
    false Path Traversal (HIGH) finding. Detection now relies only on the
    file-content signatures that actually prove a traversal.
    """

    @staticmethod
    def _response(body: str):
        response = Mock()
        response.content = body.encode()
        response.status_code = 200
        return response

    def test_benign_404_page_not_flagged(self, scanner):
        """A normal '404 Not Found' page is not path traversal."""
        with patch.object(scanner.session, "get",
                          return_value=self._response("<html><h1>404 Not Found</h1></html>")):
            result = scanner.test_path_traversal("http://example.com/page?file=test")

        assert result is False
        assert not scanner.scan_result.get_vulnerabilities_by_type(
            VulnerabilityType.PATH_TRAVERSAL
        )

    def test_benign_not_found_text_not_flagged(self, scanner):
        """A page that merely mentions 'not found' is not path traversal."""
        with patch.object(scanner.session, "get",
                          return_value=self._response("No results not found for your query")):
            result = scanner.test_path_traversal("http://example.com/page?file=test")

        assert result is False

    def test_benign_permission_denied_not_flagged(self, scanner):
        """An 'access denied' message is not path traversal."""
        with patch.object(scanner.session, "get",
                          return_value=self._response("Access denied - permission denied")):
            result = scanner.test_path_traversal("http://example.com/page?file=test")

        assert result is False

    def test_etc_passwd_content_still_detected(self, scanner):
        """Real /etc/passwd content is still detected (no false negative)."""
        with patch.object(scanner.session, "get",
                          return_value=self._response("root:x:0:0:root:/root:/bin/bash")):
            result = scanner.test_path_traversal("http://example.com/page?file=test")

        assert result is True
        vulns = scanner.scan_result.get_vulnerabilities_by_type(
            VulnerabilityType.PATH_TRAVERSAL
        )
        assert vulns and vulns[0].severity == Severity.HIGH

    def test_windows_ini_content_still_detected(self, scanner):
        """win.ini content is still detected."""
        with patch.object(scanner.session, "get",
                          return_value=self._response("[fonts]\n[mail]\nMAPI=1")):
            result = scanner.test_path_traversal("http://example.com/page?file=test")

        assert result is True


class TestCommandInjectionAccuracy:
    """False-positive regression tests for command injection.

    A response that merely echoes the injected payload back must not be
    reported as command execution. The reflected payload is stripped before
    pattern matching, matching what the form-based engine already did.
    """

    def test_reflected_payload_not_flagged_as_injection(self, scanner, monkeypatch):
        """Reflecting a payload whose text matches a pattern is not RCE."""
        import scanner as scanner_module
        # '| whoami' contains the indicator word 'whoami'; its mere reflection
        # must not be mistaken for command output.
        monkeypatch.setattr(scanner_module, "COMMAND_INJECTION_PAYLOADS", ["| whoami"])

        def fake_get(url, **kwargs):
            response = Mock()
            response.content = b"<html>you searched for | whoami</html>"
            response.status_code = 200
            return response

        with patch.object(scanner.session, "get", side_effect=fake_get):
            result = scanner.test_command_injection("http://example.com/ping?host=test")

        assert result is False

    def test_real_command_output_still_detected(self, scanner):
        """Actual 'id' output is still flagged (no false negative)."""
        with patch.object(scanner.session, "get") as mock_get:
            mock_response = Mock()
            mock_response.content = b"uid=0(root) gid=0(root) groups=0(root)"
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            result = scanner.test_command_injection("http://example.com/ping?host=test")

        assert result is True


class TestXXEAccuracy:
    """False-positive regression tests for XXE.

    The generic patterns "parse error", "fatal error", "No such file" and
    "Permission denied" match ordinary error pages (e.g. a PHP 'Fatal error'),
    so they were removed; only XXE-specific parser messages and file contents
    are matched now.
    """

    def test_benign_php_fatal_error_not_flagged(self, scanner):
        """A normal 'Fatal error' page is not XXE."""
        with patch.object(scanner.session, "get") as mock_get:
            mock_response = Mock()
            mock_response.content = (
                b"<html>Fatal error: Uncaught exception in /var/www/app.php</html>"
            )
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            result = scanner.test_xxe_injection("http://example.com/page?xml=test")

        assert result is False

    def test_benign_no_such_file_not_flagged(self, scanner):
        """A plain 'No such file' message is not XXE."""
        with patch.object(scanner.session, "get") as mock_get:
            mock_response = Mock()
            mock_response.content = b"<html>No such file or directory</html>"
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            result = scanner.test_xxe_injection("http://example.com/page?xml=test")

        assert result is False

    def test_xxe_doctype_rejection_still_detected(self, scanner):
        """An XML parser rejecting DOCTYPE is still detected (no false negative)."""
        with patch.object(scanner.session, "get") as mock_get:
            mock_response = Mock()
            mock_response.content = b"XML parser error: DOCTYPE is disallowed"
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            result = scanner.test_xxe_injection("http://example.com/page?xml=test")

        assert result is True
