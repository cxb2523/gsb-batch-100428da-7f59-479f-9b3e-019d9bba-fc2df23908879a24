import dataclasses
import os
import pathlib
import threading
import typing
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import bs4
import pdfkit
import scrapy


@dataclasses.dataclass
class URL:
    index: int
    url: str


@dataclasses.dataclass
class ConversionResult:
    """Outcome of a single HTML -> PDF conversion attempt."""

    filename: str
    success: bool
    error: typing.Optional[str] = None


class LearncppSpider(scrapy.Spider):
    name = "learncpp"
    allowed_domains = ["learncpp.com"]

    # wkhtmltopdf occasionally emits this teardown message even though the PDF
    # was produced; the original implementation deliberately ignores it.
    BENIGN_WKHTMLTOPDF_ERROR = (
        "QNetworkReplyImplPrivate::error: Internal problem, this method must only be called once."
    )

    # Define ThreadPoolExecutor
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executor = ThreadPoolExecutor(
            max_workers=192
        )  # Limit to 192 concurrent PDF conversions

        # Bookkeeping for PDF conversion outcomes. Worker threads and the
        # reactor thread touch these structures, so guard them with a lock.
        self._conversion_lock = threading.Lock()
        self._executor_closed = False
        self._conversion_total = 0
        self._conversion_succeeded = 0
        self._conversion_failures: typing.List[ConversionResult] = []

    def create_working_directory(self) -> None:
        # Create a directory named "learncpp" if it doesn't exist
        if not os.path.exists(self.name):
            os.mkdir(self.name)
        self.log(f"Created working directory: {self.name}")

    def start_requests(self) -> typing.Union[scrapy.Request, None]:
        # Create a working directory
        self.create_working_directory()

        # Use get_urls function to dynamically generate start_urls
        urls = self.get_urls()
        self.log(f"Found {len(urls)} URLs to scrape.")

        for url in urls:
            self.log(f"Processing URL: {url.url}")

            # Yield a Request object for each URL
            yield scrapy.Request(
                url=url.url,
                callback=self.parse,
                cb_kwargs={"page_index": str(url.index)},
            )

    def parse(
        self,
        response: scrapy.http.response.Response,
        page_index: int,
    ) -> None:
        # Modify the filename to include the index
        parsed_url = pathlib.Path(response.url)
        filename = f"{page_index}-{parsed_url.parts[-1]}.html"
        self.log(f"Processing HTML for {filename}")

        # Save the HTML file
        with open(os.path.join(self.name, filename), "wb") as f:
            f.write(response.body)
        self.log(f"Saved HTML file: {filename}")

        # Clean the HTML file
        self.clean(filename)
        self.log(f"Cleaned HTML for {filename}")

        # Submit the PDF conversion; rejection and per-page failures are
        # recorded here and must not interrupt the rest of the crawl.
        self.submit_conversion(filename)

    def clean(self, filename: str) -> None:
        # Read the HTML file
        with open(os.path.join(self.name, filename), "r", encoding="utf-8") as file:
            html_content = file.read()

        # Parse the HTML content
        soup = bs4.BeautifulSoup(html_content, "html.parser")

        # List of div IDs to remove
        div_ids_to_remove = [
            "comments",
            "site-header-main",
            "header-image-main",
            "colophon-inside",
        ]

        # Class name to remove
        classes_to_remove = ["code-block code-block-10", "cf_monitor"]

        # Remove elements by tag, attribute, and values
        self.remove_elements_by_attribute(
            soup,
            "div",
            "id",
            div_ids_to_remove,
        )
        self.remove_elements_by_attribute(
            soup,
            "div",
            "class",
            classes_to_remove,
        )

        # Remove the footer
        self.remove_elements_by_attribute(
            soup, "footer", "class", ["entry-meta entry-utility"]
        )

        # Save the modified HTML content
        with open(os.path.join(self.name, filename), "w", encoding="utf-8") as file:
            file.write(str(soup))
        self.log(f"Saved cleaned HTML for {filename}")

    def remove_elements_by_attribute(
        self,
        soup: bs4.BeautifulSoup,
        tag: str,
        attribute: str,
        values: typing.List[str],
    ) -> None:
        for value in values:
            elements = soup.find_all(tag, {attribute: value})
            for element in elements:
                element.decompose()

    def submit_conversion(self, filename: str) -> None:
        """Submit one conversion task to the thread pool.

        Every page reaching this point is counted in the conversion total.
        If the pool has already been shut down (or rejects the task for any
        other reason), the page is recorded as a failure instead of letting
        a RuntimeError escape and crash the crawl shutdown.
        """
        with self._conversion_lock:
            self._conversion_total += 1
            pool_closed = self._executor_closed

        if pool_closed:
            error = "PDF conversion not started: thread pool is shut down"
            self._record_failure(filename, error)
            self.log(f"PDF conversion failed for {filename}: {error}")
            return

        try:
            self.executor.submit(self._run_conversion, filename)
        except RuntimeError as exc:
            # Thrown when the executor was shut down concurrently.
            error = f"PDF conversion task rejected by thread pool: {exc}"
            self._record_failure(filename, error)
            self.log(f"PDF conversion failed for {filename}: {error}")
            return

        self.log(f"Started PDF conversion for {filename}")

    def _run_conversion(self, filename: str) -> ConversionResult:
        """Worker-thread wrapper: never lets an exception escape the future."""
        try:
            result = self.convert_to_pdf(filename)
        except Exception as exc:  # pragma: no cover - defensive safety net
            result = ConversionResult(
                filename, False, f"{type(exc).__name__}: {exc}"
            )

        self._record_result(result)

        pdf_filename = pathlib.Path(filename).with_suffix(".pdf")
        if result.success:
            self.log(f"Converted {filename} to PDF: {pdf_filename}")
        else:
            self.log(
                f"PDF conversion failed for {filename}: {result.error}"
            )
        return result

    def convert_to_pdf(self, filename: str) -> ConversionResult:
        """Convert one cleaned HTML file to PDF.

        Returns a ConversionResult describing the outcome. Success is only
        reported when pdfkit actually completes without error; the message
        about a converted PDF is therefore never logged unconditionally.
        """
        # Original file path
        pdf_filename = pathlib.Path(filename).with_suffix(".pdf")

        # Convert the modified HTML file to PDF
        try:
            pdfkit.from_file(
                os.path.join(self.name, filename),
                os.path.join(self.name, pdf_filename),
                options={"enable-local-file-access": ""},
            )
        except OSError as exc:
            if self.BENIGN_WKHTMLTOPDF_ERROR in str(exc):
                # If so, suppress the error: wkhtmltopdf still wrote the PDF.
                return ConversionResult(filename, True)
            return ConversionResult(
                filename, False, f"{type(exc).__name__}: {exc}"
            )
        except Exception as exc:
            return ConversionResult(
                filename, False, f"{type(exc).__name__}: {exc}"
            )

        return ConversionResult(filename, True)

    def _record_result(self, result: ConversionResult) -> None:
        with self._conversion_lock:
            if result.success:
                self._conversion_succeeded += 1
            else:
                self._conversion_failures.append(result)

    def _record_failure(self, filename: str, error: str) -> None:
        with self._conversion_lock:
            self._conversion_failures.append(
                ConversionResult(filename, False, error)
            )

    def closed(self, reason: str) -> None:
        # Scrapy hook invoked when the spider has finished crawling.
        self.shutdown_conversions()

    def shutdown_conversions(
        self,
    ) -> typing.Tuple[int, int, typing.List[ConversionResult]]:
        """Wait for submitted conversions and log the final tally.

        Safe to call more than once. Returns (total, succeeded, failures).
        """
        with self._conversion_lock:
            already_closed = self._executor_closed
            self._executor_closed = True

        if not already_closed:
            # wait=True: every task already accepted by the pool runs to
            # completion before the crawl process exits.
            self.executor.shutdown(wait=True)

        with self._conversion_lock:
            total = self._conversion_total
            succeeded = self._conversion_succeeded
            failures = list(self._conversion_failures)

        failed = len(failures)
        summary_lines = [
            "PDF conversion summary:",
            f"  Total pages: {total}",
            f"  Succeeded:   {succeeded}",
            f"  Failed:      {failed}",
        ]
        if failures:
            summary_lines.append("  Failed pages:")
            for result in failures:
                summary_lines.append(
                    f"    - {result.filename}: {result.error}"
                )
        else:
            summary_lines.append("  Failed pages: none")

        for line in summary_lines:
            self.log(line)

        return total, succeeded, failures

    def get_urls(self) -> typing.List[URL]:
        try:
            # Parse the HTML content with BeautifulSoup
            html_content = urllib.request.urlopen(
                urllib.request.Request(
                    "http://www.learncpp.com",
                    headers={"User-Agent": "Mozilla/5.0"},
                )
            ).read()
        except urllib.error.URLError as e:
            self.log(f"Error accessing the website: {e}")
            return []

        try:
            soup = bs4.BeautifulSoup(html_content, "html.parser")

            # Find all divs with class "lessontable-row-title"
            all_divs = soup.find_all("div", class_="lessontable-row-title")

            # List to store the URLs
            fetched_urls = []

            # Variable to store indices
            page_index = 1

            # Iterate through each div and find all <a> tags inside
            for div in all_divs:
                all_a_tags = div.find_all("a")

                for a_tag in all_a_tags:
                    # Append the URL to the list
                    fetched_urls.append(URL(page_index, a_tag["href"]))

                    # Increment the index
                    page_index += 1

            self.log(f"Successfully fetched {len(fetched_urls)} URLs")
            return fetched_urls

        except Exception as e:
            self.log(f"Error parsing HTML content: {e}")
            return []
