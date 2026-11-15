import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from requests.exceptions import ConnectionError as RequestsConnectionError, HTTPError

from web import scrape_grades, GradesScrapeResult, ScrapeOutcome


LOGIN_PAGE_HTML = """
<html><body>
<form id="login-form">
    <input type="hidden" name="_csrf-frontend" value="token">
    <input name="LoginForm[email]">
</form>
</body></html>
"""

GRADES_PAGE_HTML = """
<html><body>
<table>
    <tr data-key="1"><td>1</td><td>Математика</td><td>отл</td></tr>
    <tr data-key="2"><td>1</td><td>Физика</td><td>хор</td></tr>
</table>
</body></html>
"""

EMPTY_PAGE_HTML = "<html><body><h1>Личный кабинет</h1></body></html>"


class FakeResponse:
    def __init__(self, url: str, text: str, status_code: int = 200, history=None):
        self.url = url
        self.text = text
        self.status_code = status_code
        self.history = history or []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError(
                f"{self.status_code} Client Error",
                response=self
            )


class FakeSession:
    def __init__(self, response=None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.requested_urls = []

    def get(self, url, timeout=None):
        self.requested_urls.append(url)

        if self._error is not None:
            raise self._error

        return self._response


class ScrapeGradesOutcomeTests(unittest.TestCase):
    GRADES_URL = "https://lk.msu.ru/cabinet/marks"
    LOGIN_URL = "https://lk.msu.ru/login"

    def test_success_returns_html_and_success_outcome(self):
        response = FakeResponse(self.GRADES_URL, GRADES_PAGE_HTML)
        session = FakeSession(response=response)

        result = scrape_grades(session, self.GRADES_URL)

        self.assertIsInstance(result, GradesScrapeResult)
        self.assertTrue(result.ok)
        self.assertIs(result.outcome, ScrapeOutcome.SUCCESS)
        self.assertEqual(result.html, GRADES_PAGE_HTML)
        self.assertEqual(result.request_chain, [self.GRADES_URL])
        self.assertIsNone(result.reason)

    def test_login_form_after_redirect_is_session_unauthorized(self):
        redirect = FakeResponse(self.LOGIN_URL, LOGIN_PAGE_HTML)
        final = FakeResponse(self.LOGIN_URL, LOGIN_PAGE_HTML, history=[redirect])
        session = FakeSession(response=final)

        result = scrape_grades(session, self.GRADES_URL)

        self.assertFalse(result.ok)
        self.assertIs(result.outcome, ScrapeOutcome.SESSION_UNAUTHORIZED)
        self.assertIsNone(result.html)
        self.assertEqual(result.request_chain, [self.LOGIN_URL, self.LOGIN_URL])
        self.assertTrue(result.reason)
        message = result.format_details()
        self.assertIn("авториз", message.lower())
        self.assertIn("Цепочка запросов", message)
        self.assertIn(self.LOGIN_URL, message)

    def test_missing_grades_table_is_page_structure_changed(self):
        response = FakeResponse(self.GRADES_URL, EMPTY_PAGE_HTML)
        session = FakeSession(response=response)

        result = scrape_grades(session, self.GRADES_URL)

        self.assertFalse(result.ok)
        self.assertIs(result.outcome, ScrapeOutcome.PAGE_STRUCTURE_CHANGED)
        self.assertIsNone(result.html)
        self.assertEqual(result.request_chain, [self.GRADES_URL])
        self.assertIn("tr[data-key]", result.reason)

    def test_http_error_is_distinct_outcome_with_status_code(self):
        response = FakeResponse(self.GRADES_URL, "error", status_code=500)
        session = FakeSession(response=response)

        result = scrape_grades(session, self.GRADES_URL)

        self.assertFalse(result.ok)
        self.assertIs(result.outcome, ScrapeOutcome.HTTP_ERROR)
        self.assertEqual(result.status_code, 500)
        self.assertEqual(result.request_chain, [self.GRADES_URL])
        self.assertIn("500", result.format_details())

    def test_network_error_is_distinct_outcome_and_keeps_attempted_url(self):
        error = RequestsConnectionError("Connection aborted")
        session = FakeSession(error=error)

        result = scrape_grades(session, self.GRADES_URL)

        self.assertFalse(result.ok)
        self.assertIs(result.outcome, ScrapeOutcome.NETWORK_ERROR)
        self.assertNotIn(result.outcome, (ScrapeOutcome.HTTP_ERROR, ScrapeOutcome.SESSION_UNAUTHORIZED))
        self.assertIn("Connection aborted", result.reason)
        self.assertEqual(result.request_chain, [self.GRADES_URL])
        self.assertIsNone(result.status_code)

    def test_scrape_never_raises_on_failure(self):
        for session in (
            FakeSession(error=RequestsConnectionError("down")),
            FakeSession(response=FakeResponse(self.GRADES_URL, "x", status_code=404)),
            FakeSession(response=FakeResponse(self.LOGIN_URL, LOGIN_PAGE_HTML)),
            FakeSession(response=FakeResponse(self.GRADES_URL, EMPTY_PAGE_HTML)),
        ):
            result = scrape_grades(session, self.GRADES_URL)
            self.assertIsInstance(result, GradesScrapeResult)
            self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
