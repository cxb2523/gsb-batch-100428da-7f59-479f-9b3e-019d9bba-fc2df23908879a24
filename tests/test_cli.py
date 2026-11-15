"""Tests for the ExploiterX command line interface."""

import os
import pytest
from unittest.mock import Mock, patch, MagicMock

from exploiterX import (
    _looks_like_cli,
    parse_args,
    cli_main,
    save_reports,
    main,
)


class TestCliDetection:
    """Tests for CLI mode detection."""

    def test_empty_argv_not_cli(self):
        assert _looks_like_cli([]) is False

    def test_url_flag_detected(self):
        assert _looks_like_cli(["--url", "http://example.com"]) is True
        assert _looks_like_cli(["-u", "http://example.com"]) is True
        assert _looks_like_cli(["--url=http://example.com"]) is True

    def test_other_exploiterx_flags_detected(self):
        assert _looks_like_cli(["--threads", "5"]) is True
        assert _looks_like_cli(["--format", "json"]) is True
        assert _looks_like_cli(["--no-banner"]) is True

    def test_unrelated_argv_not_cli(self):
        """Arguments from other tools (e.g. pytest) must not trigger CLI mode."""
        assert _looks_like_cli(["tests", "-v", "--tb=short"]) is False
        assert _looks_like_cli(["-c", "pyproject.toml", "-q"]) is False


class TestParseArgs:
    """Tests for argument parsing."""

    def test_parse_basic(self):
        args = parse_args(["--url", "http://example.com"])
        assert args.url == "http://example.com"
        assert args.threads is None

    def test_parse_all_options(self):
        args = parse_args([
            "--url", "http://example.com",
            "--ignore", "/logout", "/admin",
            "--auth-url", "http://example.com/login",
            "--auth-username", "user",
            "--auth-password", "pass",
            "--threads", "8",
            "--timeout", "20",
            "--retries", "5",
            "--max-depth", "3",
            "--max-links", "100",
            "--delay", "1.5",
            "--time-based-threshold", "2.0",
            "--format", "json", "html",
            "--output", "out",
            "--log-level", "DEBUG",
            "--no-banner",
        ])
        assert args.url == "http://example.com"
        assert args.ignore == ["/logout", "/admin"]
        assert args.threads == 8
        assert args.timeout == 20
        assert args.retries == 5
        assert args.max_depth == 3
        assert args.max_links == 100
        assert args.delay == 1.5
        assert args.time_based_threshold == 2.0
        assert args.format == ["json", "html"]
        assert args.output == "out"
        assert args.log_level == "DEBUG"
        assert args.no_banner is True

    def test_parse_invalid_format_rejected(self):
        with pytest.raises(SystemExit):
            parse_args(["--format", "xml"])


def _mock_scan_result():
    """Build a scan result mock that satisfies print_summary and reporters."""
    result = MagicMock()
    result.target_url = "http://example.com"
    result.endpoints_discovered = 1
    result.endpoints_scanned = 1
    result.scan_duration = 1.0
    result.vulnerabilities = []
    result.get_severity_distribution.return_value = {
        "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0
    }
    result.count_by_type.return_value = {}
    result.get_critical_vulnerabilities.return_value = []
    result.get_high_vulnerabilities.return_value = []
    return result


def _mock_crawl_stats():
    """Build a crawl stats dict that satisfies print_crawl_summary."""
    return {
        "pages_visited": 1,
        "pages_succeeded": 1,
        "pages_failed": 0,
        "links_ignored": 0,
        "links_truncated": 0,
        "endpoints_discovered": 1,
    }


class TestCliMain:
    """Tests for the non-interactive CLI scan flow."""

    @patch("exploiterX.display_banner")
    @patch("exploiterX.Scanner")
    def test_cli_main_full_flow(self, mock_scanner_class, mock_banner, tmp_path):
        """cli_main runs crawl + scan and saves the requested report."""
        mock_instance = Mock()
        mock_instance.target_links = ["http://example.com/page1"]
        mock_instance.run_scanner.return_value = _mock_scan_result()
        mock_instance.get_crawl_stats.return_value = _mock_crawl_stats()
        mock_scanner_class.return_value = mock_instance

        output_dir = str(tmp_path / "reports")
        args = parse_args([
            "--url", "http://example.com",
            "--format", "json",
            "--output", output_dir,
        ])

        cli_main(args)

        mock_banner.assert_called_once()
        mock_scanner_class.assert_called_once()
        mock_instance.crawl.assert_called_once()
        mock_instance.run_scanner.assert_called_once_with(threads=5)
        assert os.path.exists(os.path.join(output_dir, "exploiterx.json"))

    @patch("exploiterX.display_banner")
    @patch("exploiterX.Scanner")
    def test_cli_main_threads_override(self, mock_scanner_class, mock_banner):
        """--threads overrides the default scan concurrency."""
        mock_instance = Mock()
        mock_instance.target_links = ["http://example.com/page1"]
        mock_instance.run_scanner.return_value = _mock_scan_result()
        mock_instance.get_crawl_stats.return_value = _mock_crawl_stats()
        mock_scanner_class.return_value = mock_instance

        args = parse_args([
            "--url", "http://example.com",
            "--threads", "12",
            "--no-banner",
        ])

        cli_main(args)

        mock_banner.assert_not_called()
        mock_instance.run_scanner.assert_called_once_with(threads=12)

    @patch("exploiterX.display_banner")
    @patch("exploiterX.Scanner")
    def test_cli_main_time_based_threshold(self, mock_scanner_class, mock_banner):
        """--time-based-threshold is passed through to the Scanner."""
        mock_instance = Mock()
        mock_instance.target_links = ["http://example.com/page1"]
        mock_instance.run_scanner.return_value = _mock_scan_result()
        mock_instance.get_crawl_stats.return_value = _mock_crawl_stats()
        mock_scanner_class.return_value = mock_instance

        args = parse_args([
            "--url", "http://example.com",
            "--time-based-threshold", "1.5",
            "--no-banner",
        ])

        cli_main(args)

        call_kwargs = mock_scanner_class.call_args[1]
        assert call_kwargs["time_based_threshold"] == 1.5

    def test_cli_main_invalid_threshold_rejected(self):
        """Out-of-range thresholds are rejected with a configuration error."""
        args = parse_args(["--url", "http://example.com", "--time-based-threshold", "-1", "--no-banner"])

        with pytest.raises(SystemExit) as exc_info:
            cli_main(args)

        assert exc_info.value.code == 1

    @patch("exploiterX.display_banner")
    @patch("exploiterX.Scanner")
    @patch("exploiterX.perform_login")
    def test_cli_main_with_auth(self, mock_login, mock_scanner_class, mock_banner):
        """Authenticated scans call perform_login with the supplied credentials."""
        mock_login.return_value = True
        mock_instance = Mock()
        mock_instance.target_links = ["http://example.com/page1"]
        mock_instance.run_scanner.return_value = _mock_scan_result()
        mock_instance.get_crawl_stats.return_value = _mock_crawl_stats()
        mock_scanner_class.return_value = mock_instance

        args = parse_args([
            "--url", "http://example.com",
            "--auth-url", "http://example.com/login",
            "--auth-username", "user",
            "--auth-password", "pass",
            "--no-banner",
        ])

        cli_main(args)

        mock_login.assert_called_once()
        call_kwargs = mock_login.call_args
        assert call_kwargs[0][1] == {
            "login_url": "http://example.com/login",
            "username": "user",
            "password": "pass",
        }

    @patch("exploiterX.display_banner")
    def test_cli_main_requires_url(self, mock_banner):
        """cli_main exits when no target URL is configured."""
        args = parse_args(["--no-banner"])

        with pytest.raises(SystemExit) as exc_info:
            cli_main(args)

        assert exc_info.value.code == 1

    @patch("exploiterX.Scanner")
    def test_cli_main_no_endpoints_exits(self, mock_scanner_class):
        """cli_main exits cleanly when the crawl discovers nothing."""
        mock_instance = Mock()
        mock_instance.target_links = []
        mock_instance.get_crawl_stats.return_value = _mock_crawl_stats()
        mock_scanner_class.return_value = mock_instance

        args = parse_args(["--url", "http://example.com", "--no-banner"])

        with pytest.raises(SystemExit) as exc_info:
            cli_main(args)

        assert exc_info.value.code == 0
        mock_instance.run_scanner.assert_not_called()


class TestMainDispatch:
    """Tests for main() CLI/interactive dispatch."""

    @patch("exploiterX.display_banner")
    @patch("exploiterX.Scanner")
    def test_main_dispatches_to_cli_with_url(self, mock_scanner_class, mock_banner):
        """main([--url ...]) takes the non-interactive CLI path."""
        mock_instance = Mock()
        mock_instance.target_links = ["http://example.com/page1"]
        mock_instance.run_scanner.return_value = _mock_scan_result()
        mock_instance.get_crawl_stats.return_value = _mock_crawl_stats()
        mock_scanner_class.return_value = mock_instance

        main(["--url", "http://example.com", "--no-banner"])

        mock_instance.crawl.assert_called_once()
        mock_instance.run_scanner.assert_called_once()

    def test_main_requires_url_for_cli_flags(self):
        """CLI flags without --url produce an error exit."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--threads", "5"])

        assert exc_info.value.code == 1

    @patch("exploiterX.get_target_url")
    @patch("exploiterX.get_ignore_links")
    @patch("exploiterX.get_authentication_info")
    @patch("exploiterX.Scanner")
    def test_main_interactive_when_argv_is_unrelated(
        self, mock_scanner_class, mock_auth, mock_ignore, mock_url
    ):
        """Unrelated argv (e.g. pytest's) falls back to interactive mode."""
        mock_url.return_value = "http://example.com"
        mock_ignore.return_value = []
        mock_auth.return_value = None

        mock_instance = Mock()
        mock_instance.target_links = ["http://example.com/page1"]
        mock_instance.run_scanner.return_value = _mock_scan_result()
        mock_instance.get_crawl_stats.return_value = _mock_crawl_stats()
        mock_scanner_class.return_value = mock_instance

        with patch("exploiterX.display_banner"):
            main(["tests", "-v"])

        mock_url.assert_called_once()
        mock_instance.crawl.assert_called_once()


class TestSaveReports:
    """Tests for report generation helpers."""

    def test_save_reports_single_format(self, tmp_path):
        result = MagicMock()
        result.target_url = "http://example.com"
        result.start_time.isoformat.return_value = "2026-01-01T00:00:00"
        result.end_time.isoformat.return_value = "2026-01-01T00:01:00"
        result.scan_duration = 60.0
        result.endpoints_discovered = 1
        result.endpoints_scanned = 1
        result.vulnerabilities = []
        result.get_severity_distribution.return_value = {
            "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0
        }
        result.count_by_type.return_value = {}

        output_file = str(tmp_path / "scan.json")
        saved = save_reports(result, ["json"], output_file)

        assert saved == [output_file]
        assert os.path.exists(output_file)

    def test_save_reports_multiple_formats(self, tmp_path):
        result = MagicMock()
        result.target_url = "http://example.com"
        result.start_time.isoformat.return_value = "2026-01-01T00:00:00"
        result.end_time.isoformat.return_value = "2026-01-01T00:01:00"
        result.scan_duration = 60.0
        result.endpoints_discovered = 1
        result.endpoints_scanned = 1
        result.vulnerabilities = []
        result.get_severity_distribution.return_value = {
            "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0
        }
        result.count_by_type.return_value = {}

        output_dir = str(tmp_path / "reports")
        saved = save_reports(result, ["json", "html"], output_dir)

        assert len(saved) == 2
        assert os.path.exists(os.path.join(output_dir, "exploiterx.json"))
        assert os.path.exists(os.path.join(output_dir, "exploiterx.html"))

    def test_save_reports_console_only_returns_empty(self, tmp_path):
        saved = save_reports(MagicMock(), ["console"], str(tmp_path))
        assert saved == []
