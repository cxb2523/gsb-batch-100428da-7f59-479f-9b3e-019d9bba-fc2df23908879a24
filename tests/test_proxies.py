import pytest

from proxyrotate.models import Proxy
from proxyrotate.proxies import load_proxies


class TestParse:
    def test_http(self):
        proxy = Proxy.parse("http://1.2.3.4:8080")
        assert proxy.scheme == "http"
        assert proxy.host == "1.2.3.4"
        assert proxy.port == 8080
        assert proxy.username is None
        assert proxy.password is None

    def test_https(self):
        proxy = Proxy.parse("https://proxy.example.com:8443")
        assert proxy.scheme == "https"
        assert proxy.port == 8443

    def test_socks5(self):
        proxy = Proxy.parse("socks5://5.6.7.8:1080")
        assert proxy.scheme == "socks5"
        assert proxy.host == "5.6.7.8"
        assert proxy.port == 1080

    def test_with_auth(self):
        proxy = Proxy.parse("http://user:secret@9.9.9.9:3128")
        assert proxy.username == "user"
        assert proxy.password == "secret"

    def test_scheme_case_insensitive(self):
        assert Proxy.parse("SOCKS5://h:1").scheme == "socks5"

    @pytest.mark.parametrize(
        "raw,reason",
        [
            ("1.2.3.4:8080", "scheme"),
            ("ftp://1.2.3.4:21", "unsupported scheme"),
            ("http://", "host"),
            ("http://host-only", "port"),
            ("http://h:notaport", "port"),
            ("socks5://h:99999", "port"),
            ("", "scheme"),
        ],
    )
    def test_invalid(self, raw, reason):
        with pytest.raises(ValueError, match=reason):
            Proxy.parse(raw)

    def test_url_round_trip(self):
        raw = "socks5://bob:hunter2@10.0.0.1:1080"
        proxy = Proxy.parse(raw)
        rebuilt = Proxy.parse(proxy.url())
        assert rebuilt.dedupe_key() == proxy.dedupe_key()


class TestRedaction:
    def test_password_masked(self):
        proxy = Proxy.parse("http://alice:wonderland@example.com:8080")
        redacted = proxy.redacted_url()
        assert "wonderland" not in redacted
        assert "***" in redacted
        assert "alice" in redacted
        assert "example.com:8080" in redacted

    def test_without_credentials_unchanged(self):
        proxy = Proxy.parse("http://example.com:8080")
        assert proxy.redacted_url() == "http://example.com:8080"

    def test_str_is_redacted(self):
        assert str(Proxy.parse("http://u:p@h:1")) == "http://u:***@h:1"


class TestLoadProxies:
    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_proxies(tmp_path / "nope.txt")

    def test_comments_and_blanks_preserve_order(self, proxy_file):
        path = proxy_file(
            "# Europe\n"
            "\n"
            "http://1.1.1.1:80\n"
            "   \n"
            "# US\n"
            "socks5://2.2.2.2:1080\n"
        )
        result = load_proxies(path)
        assert [p.host for p in result.proxies] == ["1.1.1.1", "2.2.2.2"]
        assert result.invalid == []
        assert result.duplicates == 0

    def test_duplicates_dropped_first_wins(self, proxy_file):
        path = proxy_file(
            "http://1.1.1.1:80\n"
            "http://1.1.1.1:80\n"
            "http://1.1.1.1:80\n"
            "socks5://1.1.1.1:80\n"  # different scheme -> kept
        )
        result = load_proxies(path)
        assert len(result.proxies) == 2
        assert result.duplicates == 2

    def test_invalid_lines_reported_not_fatal(self, proxy_file):
        path = proxy_file(
            "http://good.example:1\n"
            "garbage-line\n"
            "ftp://bad-scheme:2\n"
            "http://also-good.example:3\n"
        )
        result = load_proxies(path)
        assert [p.host for p in result.proxies] == [
            "good.example",
            "also-good.example",
        ]
        assert [(lineno, reason) for lineno, _raw, reason in result.invalid] == [
            (2, "missing scheme (expected http:// or socks5://)"),
            (3, "unsupported scheme 'ftp' (supported: http, https, socks5)"),
        ]

    def test_empty_file_yields_no_proxies(self, proxy_file):
        result = load_proxies(proxy_file("# nothing here\n"))
        assert result.proxies == []

    def test_inline_whitespace_trimmed(self, proxy_file):
        result = load_proxies(proxy_file("  http://1.1.1.1:80  \n"))
        assert result.proxies[0].port == 80
