import requests
from bs4 import BeautifulSoup
from requests.exceptions import HTTPError, RequestException

from .scrape_result import GradesScrapeResult, ScrapeOutcome


def _request_chain(response: requests.Response) -> list[str]:
    return [item.url for item in response.history] + [response.url]


def scrape_grades(session: requests.Session, grades_url: str) -> GradesScrapeResult:
    """Access the grades page using an authenticated session and return a verifiable collection result

    Never raises for HTTP/network failures, an unauthorized session or a changed page structure:
    every such case is returned as a GradesScrapeResult with a distinct outcome, the failure
    reason and the request chain (or the attempted URL when no response was received).
    """
    try:
        get_response = session.get(grades_url, timeout=10)
        get_response.raise_for_status()

    except HTTPError as error:
        response = error.response
        chain = _request_chain(response) if response is not None else [grades_url]
        status_code = response.status_code if response is not None else None

        return GradesScrapeResult.failure(
            outcome=ScrapeOutcome.HTTP_ERROR,
            reason=str(error),
            request_chain=chain,
            status_code=status_code
        )

    except RequestException as error:
        response = getattr(error, "response", None)
        chain = _request_chain(response) if response is not None else [grades_url]
        status_code = response.status_code if response is not None else None

        return GradesScrapeResult.failure(
            outcome=ScrapeOutcome.NETWORK_ERROR,
            reason=str(error),
            request_chain=chain,
            status_code=status_code
        )

    chain = _request_chain(get_response)
    soup = BeautifulSoup(get_response.text, "html.parser")

    if soup.select_one("form#login-form"):
        return GradesScrapeResult.failure(
            outcome=ScrapeOutcome.SESSION_UNAUTHORIZED,
            reason="Вместо страницы оценок получена страница авторизации",
            request_chain=chain
        )

    if not soup.select_one("tr[data-key]"):
        return GradesScrapeResult.failure(
            outcome=ScrapeOutcome.PAGE_STRUCTURE_CHANGED,
            reason="Страница была загружена, но таблица оценок (tr[data-key]) не найдена",
            request_chain=chain
        )

    return GradesScrapeResult.success(html=get_response.text, request_chain=chain)
