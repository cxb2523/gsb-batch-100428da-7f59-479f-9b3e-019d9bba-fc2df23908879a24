from web import auth_lk, scrape_grades, WebClientError, GradesScrapeResult, ScrapeOutcome
from core import (
    DiplomaSubjectsInfo,
    GradesAnalysisResult,
    parse_grades,
    preprocess_grades,
    get_avg_grade,
    get_diploma_grades_stats,
    diploma_with_distinction_check
)


class WebAnalysisResult:
    """Outcome of the full web collection + analysis pipeline

    On success `analysis` holds the computed GradesAnalysisResult and `scrape_result` carries
    the raw grades HTML. On collection failure `analysis` is None and the scrape result explains
    which verifiable outcome occurred (unauthorized session, changed page, HTTP/network error...).
    """

    def __init__(self, scrape_result: GradesScrapeResult, analysis: GradesAnalysisResult | None = None):
        self.scrape_result = scrape_result
        self.analysis = analysis

    @property
    def ok(self) -> bool:
        return self.scrape_result.ok and self.analysis is not None

    @property
    def outcome(self) -> ScrapeOutcome:
        return self.scrape_result.outcome

    @property
    def reason(self) -> str | None:
        return self.scrape_result.reason

    @property
    def request_chain(self) -> list[str]:
        return self.scrape_result.request_chain

    def format_details(self) -> str:
        return self.scrape_result.format_details()


def analyze_from_web(
    login_url: str,
    grades_url: str,
    email: str,
    password: str,
    faculty: str,
    study_program: str,
    department: str | None = None,
    retakes: int = 0,
    semesters_shown: int = 0
) -> WebAnalysisResult:
    """Run the full web collection and analysis, propagating the collection outcome

    Collection failures (login or grades page) are returned as a WebAnalysisResult with the
    concrete outcome instead of a generic exception. The grades calculation and red-diploma
    rules themselves are the same as in analyze_from_html.
    """
    try:
        session = auth_lk(login_url=login_url, email=email, password=password)

    except WebClientError as error:
        return WebAnalysisResult(
            GradesScrapeResult.failure(
                outcome=ScrapeOutcome.AUTH_FAILED,
                reason=str(error)
            )
        )

    scrape_result = scrape_grades(session=session, grades_url=grades_url)

    if not scrape_result.ok:
        return WebAnalysisResult(scrape_result)

    result = analyze_from_html(
        html_content=scrape_result.html,
        faculty=faculty,
        study_program=study_program,
        department=department,
        retakes=retakes,
        semesters_shown=semesters_shown
    )

    return WebAnalysisResult(scrape_result, analysis=result)


def analyze_from_html(
    html_content: str,
    faculty: str,
    study_program: str,
    department: str | None = None,
    retakes: int = 0,
    semesters_shown: int = 0
) -> GradesAnalysisResult:
    """Analyze grades from loaded HTML. Is useful for tests"""
    diploma_subjects = DiplomaSubjectsInfo()

    raw_grades = parse_grades(html_content)

    processed_grades = preprocess_grades(
        grades_data=raw_grades,
        diploma_subjects=diploma_subjects,
        faculty=faculty,
        study_program=study_program,
        department=department
    )

    avg_grade, avg_grade_wo_retake = get_avg_grade(grades_data=processed_grades, retakes=retakes, semesters_shown=semesters_shown)

    diploma_grade_counts, avg_diploma_grade = get_diploma_grades_stats(processed_grades)
    diploma_subjects_count = diploma_subjects.count_subjects(faculty=faculty, study_program=study_program, department=department)

    distinction = diploma_with_distinction_check(diploma_grade_counts=diploma_grade_counts, diploma_subjects_count=diploma_subjects_count)

    result = GradesAnalysisResult(
        grades=processed_grades,
        avg_grade=avg_grade,
        avg_grade_wo_retake=avg_grade_wo_retake,
        avg_diploma_grade=avg_diploma_grade,
        diploma_grade_counts=diploma_grade_counts,
        distinction=distinction
    )

    return result
