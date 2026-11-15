import os
import re
import sys
from datetime import date
from enum import Enum
from pathlib import Path

# The browser/Excel libraries are only needed when an engine actually runs.  Importing
# them defensively keeps the record-handling logic (and its tests) usable on machines
# where the heavy RPA stack is not installed.
try:
    import Browser
except ImportError:  # pragma: no cover - depends on the environment
    Browser = None

try:
    import RPA.Browser.Selenium
    from RPA.Excel.Files import Files
except ImportError:  # pragma: no cover - depends on the environment
    RPA = None
    Files = None

try:
    from playwright import sync_playwright
except ImportError:  # pragma: no cover - depends on the environment
    sync_playwright = None


modify_dom_path = str((Path(".") / "modify_dom.js").absolute())
executable_path = str((Path(".") / "executable.js").absolute())

rpachallenge_site = "http://www.rpachallenge.com"

engines = {
    "robotframework-browser": "run_with_rfbrowser",
    "playwright": "run_with_playwright",
    "rpabrowser": "run_with_rpabrowser",
    "rpabrowser_js": "run_with_rpabrowser_js",
}


class RunOutcome(str, Enum):
    """How a single engine run ended."""

    NEW_RECORD = "new_record"
    NO_NEW_RECORD = "no_new_record"
    UNPARSEABLE_RESULT = "unparseable_result"
    PAGE_ERROR = "page_error"


def _rpa_browser_class():
    if RPA is None:
        return None
    return getattr(getattr(RPA, "Browser", None), "Browser", None)


def _rf_browser_class():
    if Browser is None:
        return None
    return getattr(Browser, "Browser", None)


class RecordHandler:
    REGEX_MATCHER = r".*in.(\d{1,4}).milliseconds"
    # Keep the historical pattern first, then accept any "<number> milliseconds"
    # wording so small changes to the result text do not silently break parsing.
    RESULT_PATTERNS = (
        re.compile(REGEX_MATCHER),
        re.compile(r"in\s+(\d{1,4})\s*milliseconds", re.IGNORECASE),
        re.compile(r"([\d,]+)\s*milliseconds", re.IGNORECASE),
        re.compile(r"\b([\d,]+)\s*ms\b", re.IGNORECASE),
    )
    record_time = None
    engine = None

    def __init__(self, base_dir="."):
        self.base_dir = Path(base_dir)
        self.record_time = None
        self.engine = None
        self.outcome = None

    @property
    def record_file(self):
        return self.base_dir / f"record_time_{self.engine}"

    def set_engine(self, name):
        self.engine = name
        self.outcome = None
        self.open_record_time()

    def open_record_time(self):
        """Read the historical best time, treating unusable files as "no record"."""
        self.record_time = None
        path = self.record_file
        try:
            record = path.read_text()
        except FileNotFoundError:
            print(
                f"[{self.engine}] Record file {path} not found; "
                "treating as no previous record"
            )
            return
        except OSError as exc:
            print(
                f"[{self.engine}] Could not read record file {path}: {exc}; "
                "treating as no previous record"
            )
            return

        record = record.strip()
        if not record:
            print(
                f"[{self.engine}] Record file {path} is empty; "
                "treating as no previous record"
            )
            return

        try:
            self.record_time = int(record)
        except ValueError:
            print(
                f"[{self.engine}] Record file {path} does not contain a whole "
                "number; treating as no previous record"
            )
            return

        print(f"[{self.engine}] Current record time is {self.record_time} ms")

    @classmethod
    def parse_result_time(cls, result):
        """Return the time in milliseconds, or None when it cannot be parsed."""
        if not isinstance(result, str) or not result.strip():
            return None
        for pattern in cls.RESULT_PATTERNS:
            match = pattern.search(result)
            if match:
                try:
                    return int(match.group(1).replace(",", ""))
                except ValueError:  # pragma: no cover - regex only yields digits
                    continue
        return None

    def check_for_new_record(self, page, result):
        task_time = self.parse_result_time(result)
        if task_time is None:
            self.outcome = RunOutcome.UNPARSEABLE_RESULT
            print(
                f"[{self.engine}] Could not parse a task time from the result "
                f"text: {result!r}. Record not updated and no screenshot taken."
            )
            return self.outcome

        print(f"Task execution time {task_time} ms")
        previous = self.record_time
        if previous is None or task_time < previous:
            self._save_new_record(page, task_time, previous)
            self.outcome = RunOutcome.NEW_RECORD
            return self.outcome

        self.outcome = RunOutcome.NO_NEW_RECORD
        print(
            f"[{self.engine}] No new record: {task_time} ms did not beat "
            f"{previous} ms. Record left unchanged."
        )
        return self.outcome

    def report_page_error(self, error):
        self.outcome = RunOutcome.PAGE_ERROR
        print(
            f"[{self.engine}] Page error: {type(error).__name__}: {error}. "
            "Record not updated and no screenshot taken."
        )
        return self.outcome

    def _save_new_record(self, page, task_time, previous):
        # Write through a temporary file so an interrupted write cannot leave a
        # half-written record behind.
        record_file = self.record_file
        tmp_file = record_file.parent / f"{record_file.name}.tmp"
        tmp_file.write_text(str(task_time))
        os.replace(tmp_file, record_file)

        if previous is None:
            print(
                f"[{self.engine}] New record is {task_time} ms! "
                "(there was no previous record)"
            )
        else:
            print(
                f"[{self.engine}] New record is {task_time} ms! "
                f"(previous record {previous} ms)"
            )

        today = date.today()
        screenshot_filename = (
            f"record_{self.engine}_{today.strftime('%d%m%Y')}_time{task_time}ms.png"
        )
        screenshot_path = self.base_dir / screenshot_filename
        try:
            self._take_screenshot(page, screenshot_path)
        except Exception as exc:  # a failed screenshot must not lose the new record
            print(
                f"[{self.engine}] Record updated but screenshot failed: "
                f"{type(exc).__name__}: {exc}"
            )

    @staticmethod
    def _take_screenshot(page, filename):
        rpa_browser = _rpa_browser_class()
        rf_browser = _rf_browser_class()
        filename = str(filename)
        if rpa_browser is not None and isinstance(page, rpa_browser):
            page.screenshot(filename=filename)
        elif rf_browser is not None and isinstance(page, rf_browser):
            page.take_screenshot(filename=filename)
        else:
            page.screenshot(path=filename)


def myeval(browser_object, xpath, elval):
    eval_str_pw = """([xpath, elval]) => {
        document.evaluate(xpath, document.body, null, 9, null).singleNodeValue.value = elval;}"""
    eval_str_sel = f"document.evaluate('{xpath}', document.body, null, 9, null).singleNodeValue.value = '{elval}';"
    rpa_browser = _rpa_browser_class()
    if rpa_browser is not None and isinstance(browser_object, rpa_browser):
        browser_object.execute_javascript(eval_str_sel)
    else:
        browser_object.evaluate(
            eval_str_pw,
            [xpath, elval],
        )


def run_with_playwright(rh, sheet):
    print("Running RPA Challenge with Playwright")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.newPage()
        page.goto(rpachallenge_site)
        page.click('text="Start"')
        for row in sheet:
            myeval(
                page,
                '//input[@ng-reflect-name="labelFirstName"]',
                row["First Name"],
            )
            myeval(page, '//input[@ng-reflect-name="labelLastName"]', row["Last Name"])
            myeval(
                page,
                '//input[@ng-reflect-name="labelCompanyName"]',
                row["Company Name"],
            )
            myeval(
                page,
                '//input[@ng-reflect-name="labelRole"]',
                row["Role in Company"],
            )
            myeval(page, '//input[@ng-reflect-name="labelAddress"]', row["Address"])
            myeval(page, '//input[@ng-reflect-name="labelEmail"]', row["Email"])
            myeval(
                page,
                '//input[@ng-reflect-name="labelPhone"]',
                str(row["Phone Number"]),
            )
            page.evaluate("""document.querySelector('input[type="submit"]').click();""")
        result = page.evaluate(
            """() => {return document.querySelector('.message2').textContent;}"""
        )
        outcome = rh.check_for_new_record(page, result)
        browser.close()
        return outcome


def run_with_rfbrowser(rh, sheet):
    print("Running RPA Challenge with RFBrowser (RobotFramework Playwright)")
    browser = Browser.Browser()
    browser.new_context()
    browser.set_browser_timeout("30s")
    browser.new_page(rpachallenge_site)
    browser.click('"Start"')
    for row in sheet:
        browser.fill_text(
            '//input[@ng-reflect-name="labelFirstName"]', row["First Name"]
        )
        browser.fill_text('//input[@ng-reflect-name="labelLastName"]', row["Last Name"])
        browser.fill_text(
            '//input[@ng-reflect-name="labelCompanyName"]', row["Company Name"]
        )
        browser.fill_text(
            '//input[@ng-reflect-name="labelRole"]', row["Role in Company"]
        )
        browser.fill_text('//input[@ng-reflect-name="labelAddress"]', row["Address"])
        browser.fill_text('//input[@ng-reflect-name="labelEmail"]', row["Email"])
        browser.fill_text(
            '//input[@ng-reflect-name="labelPhone"]', str(row["Phone Number"])
        )
        browser.click('input[type="submit"]')

    result = browser.get_text(".message2")

    outcome = rh.check_for_new_record(browser, result)
    browser.close_browser()
    return outcome


def run_with_rpabrowser(rh, sheet, js=False):
    print(f"Running RPA Challenge with RPA Browser (Selenium) - JS: {js}")
    browser = RPA.Browser.Selenium.Selenium()
    browser.open_available_browser(
        rpachallenge_site, browser_selection="chrome,firefox", headless=True
    )
    if js:
        modify_dom = ""
        tofile = "const taskdata = [\n"
        for row in sheet:
            tofile += "{"
            for key, value in row.items():
                tofile += f"'{key}': '{value}',"
            tofile = tofile[:-1]
            tofile += "},\n"
        tofile += "]"
        with open("modify_dom.js", "r") as f:
            modify_dom = f.read()
        with open("executable.js", "w") as f:
            f.write(tofile)
            f.write("\n\n" + modify_dom)
        browser.execute_javascript(executable_path)
    else:
        browser.click_button_when_visible("//button[text()='Start']")
        for row in sheet:
            myeval(
                browser, '//input[@ng-reflect-name="labelFirstName"]', row["First Name"]
            )
            myeval(
                browser, '//input[@ng-reflect-name="labelLastName"]', row["Last Name"]
            )
            myeval(
                browser,
                '//input[@ng-reflect-name="labelCompanyName"]',
                row["Company Name"],
            )
            myeval(
                browser, '//input[@ng-reflect-name="labelRole"]', row["Role in Company"]
            )
            myeval(browser, '//input[@ng-reflect-name="labelAddress"]', row["Address"])
            myeval(browser, '//input[@ng-reflect-name="labelEmail"]', row["Email"])
            myeval(
                browser,
                '//input[@ng-reflect-name="labelPhone"]',
                str(row["Phone Number"]),
            )
            browser.execute_javascript(
                """document.querySelector('input[type="submit"]').click();"""
            )
    result = browser.execute_javascript(
        """return document.querySelector('.message2').textContent;"""
    )
    outcome = rh.check_for_new_record(browser, result)
    browser.close_browser()
    return outcome


def run_with_rpabrowser_js(rh, sheet):
    return run_with_rpabrowser(rh, sheet, True)


def run_engine(engine, sheet, base_dir="."):
    """Run one engine and normalise every ending to a RunOutcome.

    Exceptions raised while driving the page end the run as PAGE_ERROR instead of
    propagating, so a broken run can never leave the record in an inconsistent
    state and never takes a screenshot.
    """
    if engine not in engines:
        print(
            f"[{engine}] Unknown engine; available engines: "
            f"{', '.join(engines.keys())}"
        )
        return RunOutcome.PAGE_ERROR

    rh = RecordHandler(base_dir)
    try:
        rh.set_engine(engine)
        outcome = globals()[engines[engine]](rh, sheet)
    except Exception as exc:
        if isinstance(rh.outcome, RunOutcome):
            # The outcome was already decided (e.g. record written and
            # screenshot taken) and the failure happened afterwards, usually
            # while tearing the browser down.  Keep the decided outcome.
            print(
                f"[{engine}] Failure after outcome {rh.outcome.value} was "
                f"decided: {type(exc).__name__}: {exc}"
            )
            return rh.outcome
        return rh.report_page_error(exc)

    if not isinstance(outcome, RunOutcome):
        rh.outcome = RunOutcome.UNPARSEABLE_RESULT
        print(
            f"[{engine}] Engine finished without reporting an outcome. "
            "Record not updated and no screenshot taken."
        )
        return rh.outcome

    print(f"[{engine}] Run finished: {outcome.value}")
    return outcome


def main(engine=None, base_dir=".", sheet=None):
    if sheet is None:
        if Files is None:
            raise RuntimeError(
                "RPA Excel support is not available; install rpaframework or "
                "pass a sheet explicitly"
            )
        excel = Files()
        excel.open_workbook("challenge.xlsx")
        sheet = excel.read_worksheet_as_table(header=True)

    engines_to_run = [engine] if engine in engines.keys() else list(engines.keys())
    outcomes = {}
    for engine_name in engines_to_run:
        outcomes[engine_name] = run_engine(engine_name, sheet, base_dir)

    print("Run summary:")
    for engine_name, outcome in outcomes.items():
        print(f"  {engine_name}: {outcome.value}")
    return outcomes


if __name__ == "__main__":
    engine = None
    if len(sys.argv) == 2:
        engine = sys.argv[1]
    main(engine)
