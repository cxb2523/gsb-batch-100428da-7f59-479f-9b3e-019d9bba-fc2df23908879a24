import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from web import GradesScrapeResult, ScrapeOutcome
from web.exceptions import LoginRequestError
from services import analyze_from_web, analyze_from_html, WebAnalysisResult


GRADES_PAGE_HTML = """
<html><body>
<table>
    <tr data-key="1"><td>1</td><td>Математика</td><td>отл</td></tr>
    <tr data-key="2"><td>1</td><td>Физика</td><td>хор</td></tr>
    <tr data-key="3"><td>2</td><td>История</td><td>удов</td></tr>
</table>
</body></html>
"""

# Unknown faculty / empty program: parsing and averages still work, no data/in_diploma files needed
FACULTY = "faculty_other"
PROGRAM = ""


class AnalyzeFromWebOutcomeTests(unittest.TestCase):
    def _kwargs(self):
        return dict(
            login_url="https://lk.msu.ru/login",
            grades_url="https://lk.msu.ru/cabinet/marks",
            email="student@example.test",
            password="fake-password",
            faculty=FACULTY,
            study_program=PROGRAM
        )

    def test_success_propagates_html_and_analysis_equivalent_to_html_path(self):
        scrape_result = GradesScrapeResult.success(
            GRADES_PAGE_HTML,
            request_chain=["https://lk.msu.ru/cabinet/marks"]
        )

        with patch("services.grades_service.auth_lk", return_value=object()) as mock_auth, \
             patch("services.grades_service.scrape_grades", return_value=scrape_result) as mock_scrape:
            web_result = analyze_from_web(**self._kwargs())

        mock_auth.assert_called_once()
        mock_scrape.assert_called_once()

        self.assertIsInstance(web_result, WebAnalysisResult)
        self.assertTrue(web_result.ok)
        self.assertIs(web_result.outcome, ScrapeOutcome.SUCCESS)
        self.assertEqual(web_result.scrape_result.html, GRADES_PAGE_HTML)

        expected = analyze_from_html(
            GRADES_PAGE_HTML,
            faculty=FACULTY,
            study_program=PROGRAM
        )
        self.assertIsNotNone(web_result.analysis)
        self.assertEqual(web_result.analysis.avg_grade, expected.avg_grade)
        self.assertEqual(web_result.analysis.avg_grade_wo_retake, expected.avg_grade_wo_retake)
        self.assertEqual(web_result.analysis.diploma_grade_counts, expected.diploma_grade_counts)
        self.assertEqual(web_result.analysis.distinction.messages, expected.distinction.messages)

    def test_session_unauthorized_outcome_is_propagated_without_analysis(self):
        failure = GradesScrapeResult.failure(
            ScrapeOutcome.SESSION_UNAUTHORIZED,
            reason="login page returned",
            request_chain=["https://lk.msu.ru/cabinet/marks", "https://lk.msu.ru/login"]
        )

        with patch("services.grades_service.auth_lk", return_value=object()), \
             patch("services.grades_service.scrape_grades", return_value=failure):
            result = analyze_from_web(**self._kwargs())

        self.assertFalse(result.ok)
        self.assertIs(result.outcome, ScrapeOutcome.SESSION_UNAUTHORIZED)
        self.assertIsNone(result.analysis)
        self.assertIn("login", result.format_details())
        self.assertEqual(len(result.request_chain), 2)

    def test_page_structure_changed_outcome_is_propagated(self):
        failure = GradesScrapeResult.failure(
            ScrapeOutcome.PAGE_STRUCTURE_CHANGED,
            reason="no tr[data-key]",
            request_chain=["https://lk.msu.ru/cabinet/marks"]
        )

        with patch("services.grades_service.auth_lk", return_value=object()), \
             patch("services.grades_service.scrape_grades", return_value=failure):
            result = analyze_from_web(**self._kwargs())

        self.assertIs(result.outcome, ScrapeOutcome.PAGE_STRUCTURE_CHANGED)
        self.assertIsNone(result.analysis)

    def test_http_and_network_outcomes_are_propagated(self):
        for outcome, reason in (
            (ScrapeOutcome.HTTP_ERROR, "500 Server Error"),
            (ScrapeOutcome.NETWORK_ERROR, "Connection aborted"),
        ):
            with self.subTest(outcome=outcome):
                failure = GradesScrapeResult.failure(
                    outcome,
                    reason=reason,
                    request_chain=["https://lk.msu.ru/cabinet/marks"]
                )

                with patch("services.grades_service.auth_lk", return_value=object()), \
                     patch("services.grades_service.scrape_grades", return_value=failure):
                    result = analyze_from_web(**self._kwargs())

                self.assertIs(result.outcome, outcome)
                self.assertFalse(result.ok)
                self.assertIsNone(result.analysis)
                self.assertEqual(result.reason, reason)

    def test_login_failure_becomes_auth_failed_outcome(self):
        with patch(
            "services.grades_service.auth_lk",
            side_effect=LoginRequestError("Авторизация не выполнена: неверный пароль")
        ) as mock_auth, patch("services.grades_service.scrape_grades") as mock_scrape:
            result = analyze_from_web(**self._kwargs())

        mock_auth.assert_called_once()
        mock_scrape.assert_not_called()

        self.assertFalse(result.ok)
        self.assertIs(result.outcome, ScrapeOutcome.AUTH_FAILED)
        self.assertIsNone(result.analysis)
        self.assertIn("неверный пароль", result.reason)


class AnalyzeFromHtmlUnchangedTests(unittest.TestCase):
    """The pure parsing/calculation path must keep its previous behavior and signature"""

    def test_grades_calculation_on_constructed_html(self):
        result = analyze_from_html(
            GRADES_PAGE_HTML,
            faculty=FACULTY,
            study_program=PROGRAM,
            retakes=1,
            semesters_shown=0
        )

        self.assertEqual(len(result.grades), 3)
        # Without retakes: (5 + 4 + 3) / 3 = 4.0
        self.assertEqual(result.avg_grade_wo_retake, 4.0)
        # With one retake counted as 2: (12 + 2) / 4 = 3.5
        self.assertEqual(result.avg_grade, 3.5)

    def test_semester_filter_and_distinction_structure(self):
        result = analyze_from_html(
            GRADES_PAGE_HTML,
            faculty=FACULTY,
            study_program=PROGRAM,
            semesters_shown=1
        )

        self.assertEqual(len(result.grades), 3)  # filtering happens inside get_avg_grade
        # Only semester 1 grades: 5 and 4 -> 4.5
        self.assertEqual(result.avg_grade_wo_retake, 4.5)
        self.assertIsInstance(result.distinction.messages, list)
        self.assertEqual(result.diploma_grade_counts, {3: 0, 4: 0, 5: 0})


if __name__ == "__main__":
    unittest.main()
