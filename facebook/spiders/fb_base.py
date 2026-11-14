"""Shared spider base: session handling and page fan-out."""

from typing import Any, ClassVar

from scrapy import Request, Spider, signals
from scrapy.exceptions import CloseSpider
from scrapy.http import Response
from scrapy_playwright.page import PageMethod

from ..outcomes import DEFAULT_OUTPUT, SESSION_EXPIRED, OutcomeRecorder, PageOutcome

# Saved by save_auth.py after one manual login. Holds live
# session cookies.
AUTH_FILE = "auth.json"


class FbBaseSpider(Spider):
    """Base spider holding the Playwright session."""

    base_url = 'https://www.facebook.com/'

    allowed_domains: ClassVar[list] = ["facebook.com", "localhost"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        page_id_arg = kwargs.get("page_id")

        if not page_id_arg:
            raise CloseSpider("Please provide page_id.")

        self.page_id: list = []
        if ',' in page_id_arg:
            self.page_id = page_id_arg.split(',')
        else:
            self.page_id = [page_id_arg]

        # One verifiable outcome per page id, persisted after each change.
        self.outcome_recorder = OutcomeRecorder(
            kwargs.get("outcome_output") or DEFAULT_OUTPUT
        )
        # Set when the login form is seen; once true no further request
        # for a later page is yielded.
        self.session_expired = False

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        # Safety net: outcomes are written incrementally already, this
        # guarantees the final report exists even if the run is stopped.
        crawler.signals.connect(
            spider.spider_closed,  # pylint: disable=no-member
            signal=signals.spider_closed,
        )
        return spider

    def spider_closed(self, _spider: Spider) -> None:
        """Flush the outcome report when the run ends."""
        self.outcome_recorder.write()

    async def start(self) -> Any:
        """Open the landing page to establish the session."""
        yield Request(
            url=self.base_url, callback=self.parse,
            meta={
                "playwright": True,
                "playwright_context_kwargs": {"storage_state": AUTH_FILE},
            },
        )

    def parse_page(self, response: Response, **_kwargs: Any) -> Any:
        """Parse one timeline page. Implemented by subclasses."""
        raise NotImplementedError

    def parse(self, response: Response, **_kwargs: Any) -> Any:
        """Fan out timeline requests, one page id at a time."""
        if response.xpath("//form[@id='login_form']").get():
            # The session is already dead before any page was requested.
            # Record the stop cause and one outcome per requested page
            # instead of closing the spider silently.
            detail = (
                "session expired on landing page "
                "(login form shown); no timeline request was sent"
            )
            self._record_expiry(
                remaining_ids=list(self.page_id),
                expiry_page_url=response.url,
                detail=detail,
            )
            return
        yield self._page_request(0)

    def _page_request(self, index: int) -> Request:
        """Build the timeline request for page id at ``index``."""
        page_id = self.page_id[index]
        return Request(
            url=self.base_url + page_id,
            callback=self.parse_page,
            meta={
                "page_id": page_id,
                "page_index": index,
                "playwright": True,
                "playwright_context_kwargs": {"storage_state": AUTH_FILE},
                "playwright_page_methods": [
                    PageMethod("wait_for_timeout", 2000),
                ],
            },
        )

    def next_page_request(self, current_index: int) -> Request | None:
        """Return the request for the next page, unless the run stopped."""
        next_index = current_index + 1
        if self.session_expired or next_index >= len(self.page_id):
            return None
        return self._page_request(next_index)

    def _record_expiry(
        self, remaining_ids: list, expiry_page_url: str, detail: str
    ) -> None:
        """Record the expiry stop cause and every page left unprocessed."""
        self.session_expired = True
        for page_id in remaining_ids:
            self.outcome_recorder.record(
                PageOutcome(
                    page_id=page_id,
                    page_url=self.base_url + page_id,
                    status=SESSION_EXPIRED,
                    detail=detail,
                )
            )
        self.outcome_recorder.stop(
            f"session expired at {expiry_page_url}: rerun python save_auth.py"
        )
        self.logger.error(
            "Session expired at %s; %d page(s) not requested, outcomes recorded",
            expiry_page_url,
            len(remaining_ids),
        )
