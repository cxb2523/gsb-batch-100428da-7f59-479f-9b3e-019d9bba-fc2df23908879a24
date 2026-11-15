import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cal_dining_dashboard import app as dashboard_app
from cal_dining_dashboard import local_scheduler

FIXTURE = Path(__file__).parent / "fixtures" / "cal_menu_sample.html"


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class TestScanDashboardTests(unittest.TestCase):
    def _run_scan(
        self, tmpdir: str, html: str, locations: list[str], *, expect_status: int = 200
    ) -> dict:
        config_dir = Path(tmpdir)
        (config_dir / "config.yaml").write_text(
            "email: friend@example.com\n"
            "locations:\n"
            + "".join(f"  - {loc}\n" for loc in locations)
            + "meals:\n  - Breakfast\n  - Lunch\n  - Dinner\n"
            "keywords: [clam chowder, tofu]\n"
            "schedule:\n"
            "  timezone: America/Los_Angeles\n"
            "  times: ['07:00']\n"
            "  days: [mon, tue, wed, thu, fri, sat, sun]\n"
            "  window_minutes: 20\n",
            encoding="utf-8",
        )
        with (
            patch("cal_dining_scanner.user_config_dir", return_value=config_dir),
            patch(
                "cal_dining_scanner.requests.get",
                return_value=_FakeResponse(html),
            ),
        ):
            app = dashboard_app.create_app()
            client = app.test_client()
            resp = client.post(
                "/test-scan",
                headers={"X-Setup-Token": app.config["SETUP_CSRF_TOKEN"]},
            )
        self.assertEqual(resp.status_code, expect_status, resp.get_data(as_text=True))
        return resp.get_json()

    def test_normal_scan_reports_matches_and_no_notices(self):
        html = FIXTURE.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmpdir:
            data = self._run_scan(
                tmpdir, html, ["Crossroads", "Foothill", "Clark Kerr"]
            )

        self.assertTrue(data["ok"])
        self.assertEqual(data["notices"], [])
        self.assertIn("New England Clam Chowder", data["matches"])

    def test_partial_failure_is_shown_as_notices_not_a_bare_error(self):
        html = FIXTURE.read_text(encoding="utf-8").replace(
            '<div class="location-title">\n          <span class="cafe-title">Foothill</span>\n        </div>',
            '<div class="location-title"></div>',
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            data = self._run_scan(
                tmpdir, html, ["Crossroads", "Foothill", "Clark Kerr"]
            )

        self.assertTrue(data["ok"])
        self.assertIn("New England Clam Chowder", data["matches"])
        self.assertTrue(
            any("Foothill" in notice for notice in data["notices"]),
            data["notices"],
        )

    def test_zero_items_page_still_returns_an_error(self):
        html = re.sub(
            r'<li class="recip[^"]*">.*?</li>',
            "",
            FIXTURE.read_text(encoding="utf-8"),
            flags=re.DOTALL,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            data = self._run_scan(
                tmpdir,
                html,
                ["Crossroads", "Foothill", "Clark Kerr"],
                expect_status=400,
            )

        self.assertFalse(data["ok"])
        self.assertIn("zero menu items", data["error"])


class DashboardSecurityTests(unittest.TestCase):
    def test_post_rejects_missing_setup_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("cal_dining_scanner.user_config_dir", return_value=Path(tmpdir)):
                app = dashboard_app.create_app()
                client = app.test_client()

                resp = client.post("/save", data={"email": "friend@example.com"})

                self.assertEqual(resp.status_code, 403)
                self.assertFalse((Path(tmpdir) / "config.yaml").exists())

    def test_post_accepts_setup_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("cal_dining_scanner.user_config_dir", return_value=Path(tmpdir)):
                app = dashboard_app.create_app()
                token = app.config["SETUP_CSRF_TOKEN"]
                client = app.test_client()

                resp = client.post(
                    "/save",
                    headers={"X-Setup-Token": token},
                    data={
                        "email": "friend@example.com",
                        "keywords": "pizza\nsoup",
                        "locations": ["Crossroads"],
                        "meals": ["Lunch"],
                        "timezone": "America/Los_Angeles",
                        "times": "07:00",
                        "days": ["mon"],
                    },
                )

                self.assertEqual(resp.status_code, 200)
                self.assertTrue((Path(tmpdir) / "config.yaml").exists())


@patch(
    "cal_dining_dashboard.local_scheduler.status",
    return_value={"installed": False, "details": "test"},
)
class DashboardHostCheckTests(unittest.TestCase):
    """DNS rebinding: a page on attacker.example that re-resolves to 127.0.0.1 is
    same-origin with the dashboard, but its requests still carry its own Host."""

    REBOUND = "http://attacker.example:8765"

    def test_rebound_host_cannot_read_setup_token(self, _status):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("cal_dining_scanner.user_config_dir", return_value=Path(tmpdir)):
                app = dashboard_app.create_app()
                client = app.test_client()

                resp = client.get("/", base_url=self.REBOUND)

                self.assertEqual(resp.status_code, 403)
                self.assertNotIn(
                    app.config["SETUP_CSRF_TOKEN"], resp.get_data(as_text=True)
                )

    def test_rebound_host_cannot_send_stored_password_to_github(self, _status):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".env").write_text('GMAIL_APP_PASSWORD="stored-secret"\n')
            with (
                patch("cal_dining_scanner.user_config_dir", return_value=Path(tmpdir)),
                patch(
                    "cal_dining_dashboard.github_setup.configure",
                    return_value={"repo_url": "x", "actions_url": "x"},
                ) as configure,
            ):
                app = dashboard_app.create_app()
                client = app.test_client()

                resp = client.post(
                    "/github/configure",
                    base_url=self.REBOUND,
                    headers={"X-Setup-Token": app.config["SETUP_CSRF_TOKEN"]},
                    data={"email": "friend@example.com", "github_token": "attacker"},
                )

                self.assertEqual(resp.status_code, 403)
                configure.assert_not_called()
                self.assertFalse((Path(tmpdir) / "config.yaml").exists())

    def test_loopback_hosts_are_allowed(self, _status):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("cal_dining_scanner.user_config_dir", return_value=Path(tmpdir)):
                app = dashboard_app.create_app()
                client = app.test_client()

                for base_url in (
                    "http://127.0.0.1:8765",
                    "http://localhost:8765",
                    "http://[::1]:8765",
                ):
                    with self.subTest(base_url=base_url):
                        resp = client.get("/", base_url=base_url)
                        self.assertEqual(resp.status_code, 200)
                        self.assertIn(
                            app.config["SETUP_CSRF_TOKEN"],
                            resp.get_data(as_text=True),
                        )

    def test_explicit_bind_host_is_allowed(self, _status):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("cal_dining_scanner.user_config_dir", return_value=Path(tmpdir)):
                app = dashboard_app.create_app(bind_host="192.168.1.20")
                client = app.test_client()

                ok = client.get("/", base_url="http://192.168.1.20:8765")
                rebound = client.get("/", base_url=self.REBOUND)

                self.assertEqual(ok.status_code, 200)
                self.assertEqual(rebound.status_code, 403)


class LocalSchedulerTests(unittest.TestCase):
    def test_launchd_plist_keeps_spaced_paths_as_single_arguments(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "Application Support" / "cal & dining"
            config_path = config_dir / "config.yaml"
            with (
                patch(
                    "cal_dining_dashboard.local_scheduler.scanner_executable",
                    return_value=["/Users/Test User/.local/bin/cal-dining-scanner"],
                ),
                patch(
                    "cal_dining_dashboard.config_io.config_path",
                    return_value=config_path,
                ),
                patch(
                    "cal_dining_dashboard.config_io.config_dir",
                    return_value=config_dir,
                ),
            ):
                body = local_scheduler._launchd_plist_body()

        self.assertIn(
            "<string>/Users/Test User/.local/bin/cal-dining-scanner</string>",
            body,
        )
        self.assertIn("<string>--config</string>", body)
        self.assertIn(
            "<string>"
            + str(config_path).replace("&", "&amp;")
            + "</string>",
            body,
        )


if __name__ == "__main__":
    unittest.main()
