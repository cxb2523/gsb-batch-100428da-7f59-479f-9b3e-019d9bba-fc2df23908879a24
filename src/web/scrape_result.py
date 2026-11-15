from enum import Enum


class ScrapeOutcome(str, Enum):
    """Verifiable outcomes of collecting the grades page"""

    SUCCESS = "success"
    SESSION_UNAUTHORIZED = "session_unauthorized"
    PAGE_STRUCTURE_CHANGED = "page_structure_changed"
    HTTP_ERROR = "http_error"
    NETWORK_ERROR = "network_error"
    AUTH_FAILED = "auth_failed"
    UNEXPECTED_ERROR = "unexpected_error"


OUTCOME_LABELS: dict[ScrapeOutcome, str] = {
    ScrapeOutcome.SUCCESS: "Страница оценок успешно получена",
    ScrapeOutcome.SESSION_UNAUTHORIZED: "Сессия не авторизована: после входа открывается страница авторизации",
    ScrapeOutcome.PAGE_STRUCTURE_CHANGED: "Таблица оценок не найдена: структура страницы могла измениться",
    ScrapeOutcome.HTTP_ERROR: "Ошибка HTTP при запросе страницы оценок",
    ScrapeOutcome.NETWORK_ERROR: "Сетевая ошибка при запросе страницы оценок",
    ScrapeOutcome.AUTH_FAILED: "Ошибка авторизации",
    ScrapeOutcome.UNEXPECTED_ERROR: "Непредвиденная ошибка при загрузке данных",
}


class GradesScrapeResult:
    """Result of the login + grades page collection, carrying a verifiable outcome instead of a generic exception"""

    def __init__(
        self,
        outcome: ScrapeOutcome,
        html: str | None = None,
        reason: str | None = None,
        request_chain: list[str] | None = None,
        status_code: int | None = None
    ):
        self.outcome = outcome
        self.html = html
        self.reason = reason
        self.request_chain = request_chain or []
        self.status_code = status_code

    @property
    def ok(self) -> bool:
        return self.outcome is ScrapeOutcome.SUCCESS

    @property
    def label(self) -> str:
        return OUTCOME_LABELS[self.outcome]

    @classmethod
    def success(cls, html: str, request_chain: list[str] | None = None) -> "GradesScrapeResult":
        return cls(outcome=ScrapeOutcome.SUCCESS, html=html, request_chain=request_chain)

    @classmethod
    def failure(
        cls,
        outcome: ScrapeOutcome,
        reason: str,
        request_chain: list[str] | None = None,
        status_code: int | None = None
    ) -> "GradesScrapeResult":
        return cls(
            outcome=outcome,
            reason=reason,
            request_chain=request_chain,
            status_code=status_code
        )

    def format_details(self) -> str:
        """Builds a human-readable, checkable message describing the outcome and the request chain/failure reason"""
        lines = [self.label]

        if self.reason:
            lines.append(f"Причина: {self.reason}")

        if self.status_code is not None:
            lines.append(f"Код ответа HTTP: {self.status_code}")

        if self.request_chain:
            lines.append("Цепочка запросов: " + " -> ".join(self.request_chain))

        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"GradesScrapeResult(outcome={self.outcome.value!r}, reason={self.reason!r}, "
            f"status_code={self.status_code!r}, request_chain={self.request_chain!r}, "
            f"has_html={self.html is not None})"
        )
