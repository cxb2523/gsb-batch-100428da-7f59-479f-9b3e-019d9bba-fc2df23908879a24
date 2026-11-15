import apprise
import yaml
import shutil
import os
import schedule
import time
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
from os import path
from loguru import logger
from selenium import webdriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

SCHEDULES = os.getenv("SCHEDULES")

# Counter names, in the order they appear in the notification body.
COUNTER_FIELDS = ("todo", "tofix", "checked", "waiting")

COUNTER_XPATHS = {
    "todo": '/html/body/div[2]/div/div/div[2]/main/div[3]/div[2]/div[1]/div[1]/div[1]/span',
    "tofix": '/html/body/div[2]/div/div/div[2]/main/div[3]/div[2]/div[1]/div[2]/div/span',
    "checked": '/html/body/div[2]/div/div/div[2]/main/div[3]/div[2]/div[1]/div[3]/div/span',
    "waiting": '/html/body/div[2]/div/div/div[2]/main/div[3]/div[2]/div[1]/div[4]/div/span',
}

REQUIRED_KID_FIELDS = ("name", "username", "password")


class CrawlError(RuntimeError):
    """Raised when a kid's Ofek session cannot be opened or scraped."""


class CountParseError(ValueError):
    """Raised when one of the four task counters cannot be parsed."""


def describe_exception(exc: BaseException) -> str:
    """Return a non-empty, locatable description of an exception."""
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def parse_counter(counter_name: str, text: Any) -> int:
    """Extract the first number from a counter's text."""
    if text is None:
        raise CountParseError(
            f"missing {counter_name} counter (no value returned)"
        )
    match = re.search(r"\d+", str(text))
    if not match:
        raise CountParseError(
            f"cannot parse {counter_name} counter from {str(text)!r}"
        )
    return int(match.group())


def parse_counts(raw_counts: Any) -> Dict[str, int]:
    """Parse all four counters of one kid.

    The result is deliberately built from this kid's raw values only, so a
    failed scrape can never fall back to another kid's counters.
    """
    if not isinstance(raw_counts, dict):
        raise CountParseError(f"invalid counter payload: {raw_counts!r}")
    return {
        counter_name: parse_counter(counter_name, raw_counts.get(counter_name))
        for counter_name in COUNTER_FIELDS
    }


def has_tasks(todo, tofix) -> bool:
    """Return True when the 'to do' or 'to fix' counter is above zero."""
    return parse_counter("todo", todo) > 0 or parse_counter("tofix", tofix) > 0


def kid_display_name(kid: Any, index: int) -> str:
    """A locatable name for a kid entry, even when its name is missing."""
    if isinstance(kid, dict):
        name = kid.get("name")
        if name is not None and str(name).strip():
            return str(name).strip()
    return f"#{index}"


def validate_kid(kid: Any) -> Optional[str]:
    """Return a failure reason when a kid entry is unusable, else None."""
    if not isinstance(kid, dict):
        return "invalid config: entry is not a mapping"
    missing = [
        key
        for key in REQUIRED_KID_FIELDS
        if kid.get(key) is None or not str(kid.get(key)).strip()
    ]
    if missing:
        return "invalid config: missing " + ", ".join(missing)
    return None


@dataclass
class KidOutcome:
    """The result of checking one kid in one round."""

    name: str
    success: bool
    with_tasks: bool = False
    reason: str = ""
    counts: Optional[Dict[str, int]] = None


@dataclass
class RoundSummary:
    """Aggregated result of one round over all configured kids."""

    total: int
    succeeded: int
    failed: int
    with_tasks: int
    without_tasks: int
    failures: List[Tuple[str, str]] = field(default_factory=list)

    def message(self) -> str:
        summary = (
            f"Round summary: {self.succeeded} succeeded, {self.failed} failed, "
            f"{self.with_tasks} with tasks, {self.without_tasks} without tasks "
            f"({self.total} kids total)"
        )
        if self.failures:
            details = "; ".join(f"{name}: {reason}" for name, reason in self.failures)
            summary += "; failures: " + details
        return summary


def build_summary(outcomes: Sequence[KidOutcome]) -> RoundSummary:
    succeeded = sum(1 for outcome in outcomes if outcome.success)
    with_tasks = sum(
        1 for outcome in outcomes if outcome.success and outcome.with_tasks
    )
    without_tasks = succeeded - with_tasks
    failures = [
        (outcome.name, outcome.reason) for outcome in outcomes if not outcome.success
    ]
    return RoundSummary(
        total=len(outcomes),
        succeeded=succeeded,
        failed=len(failures),
        with_tasks=with_tasks,
        without_tasks=without_tasks,
        failures=failures,
    )


class Crawler:
    def __init__(self):
        self.kids = []
        self.delay = 5
        self.ofek_url = "https://myofek.cet.ac.il/he"
        self.user_field = '//*[@id="HIN_USERID"]'
        self.password_field = '//*[@id="Ecom_Password"]'
        self.edu_login_btn = '//*[@id="loginButton2"]'
        self.browser = None
        self.apobj = apprise.Apprise()
        self.notifires = os.getenv("NOTIFIERS") or ""
        self.config_path = 'config/config.yaml'
        self.get_kids()

    #### Setting ChromeOptions ####
    def init_browser(self):
        logger.info("Initializing browser")
        self.options = webdriver.ChromeOptions()
        self.options.add_argument("-incognito")
        self.options.add_argument("--headless")
        self.options.add_argument("disable-gpu")
        self.options.add_argument("--no-sandbox")
        self.options.add_argument('--start-maximized')
        self.options.add_argument("--disable-dev-shm-usage")
        self.browser = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()), options=self.options
        )
        return self.browser

    def close_browser(self, browser=None):
        """Close a browser session, whether the round succeeded or failed."""
        driver = browser if browser is not None else self.browser
        if driver is None:
            return
        logger.debug("Closing browser")
        try:
            driver.quit()
        finally:
            if driver is self.browser:
                self.browser = None

    def init_notifires(self):
        if len(self.notifires) != 0:
            logger.debug("Setting Apprise notification channels")
            jobs = self.notifires.split()
            for job in jobs:
                logger.debug("Adding: " + job)
                self.apobj.add(job)

    # Correctly spelled alias for the historical method name.
    init_notifiers = init_notifires

    def send_notification(self, title, message):
        if len(self.notifires) != 0:
            self.apobj.notify(
                body=message,
                title=title,
            )

    def get_kids(self):
        """Load the kids list once, tolerating missing or malformed config."""
        self.kids = []
        try:
            logger.info("Loading kids list")
            if not path.exists(self.config_path):
                os.makedirs(path.dirname(self.config_path), exist_ok=True)
                shutil.copy('config.yaml', self.config_path)
            with open(self.config_path, 'r', encoding='utf-8') as stream:
                config = yaml.safe_load(stream)
            if isinstance(config, list):
                self.kids = config
            elif isinstance(config, dict):
                kids = config.get("kids")
                if isinstance(kids, list):
                    self.kids = kids
                elif kids is not None:
                    logger.error("Invalid config: 'kids' must be a list")
            else:
                logger.error("Invalid config: expected a mapping with a 'kids' list")
        except Exception as e:
            logger.error(str(e))

    def crowl(self, username, password):
        """Log in and read the four counters of one kid.

        Returns the raw counter texts for this kid only and raises CrawlError
        naming the failed step when anything goes wrong.
        """
        logger.info("Crowling")
        browser = self.browser
        if browser is None:
            raise CrawlError("browser was not initialized")

        stage = "open Ofek website"
        try:
            # Open Ofek website
            browser.get(self.ofek_url)

            # Click the login button
            stage = "click login button"
            logger.debug("Click the login button")
            WebDriverWait(browser, self.delay).until(
                EC.presence_of_element_located(
                    ("xpath", '/html/body/div[2]/div/div/header/div[1]/div[1]/button')
                )
            ).click()

            # Click edu.gov.il SSO button
            stage = "click edu.gov.il SSO button"
            logger.debug("Click edu.gov.il SSO button")
            WebDriverWait(browser, self.delay).until(
                EC.presence_of_element_located(
                    (
                        "xpath",
                        '/html/body/div[2]/div/div/header/div[1]/div[1]/div/div/dialog/div/section/div/div/button/div',
                    )
                )
            ).click()

            # Change to User/Pass auth type
            stage = "switch to username/password login"
            browser.get(
                browser.current_url.replace(
                    'EduCombinedAuthSms', 'EduCombinedAuthUidPwd'
                )
            )

            # Enter Username
            stage = "enter username"
            WebDriverWait(browser, self.delay).until(
                EC.presence_of_element_located(("xpath", self.user_field))
            ).send_keys(username)

            # Enter Password
            stage = "enter password"
            WebDriverWait(browser, self.delay).until(
                EC.presence_of_element_located(("xpath", self.password_field))
            ).send_keys(password)

            # Click the login button
            stage = "submit login"
            browser.find_element("xpath", self.edu_login_btn).click()

            # Scrap tasks status
            logger.info("Scrapping...")
            raw_counts = {}
            for counter_name in COUNTER_FIELDS:
                stage = f"read {counter_name} counter"
                raw_counts[counter_name] = WebDriverWait(browser, self.delay).until(
                    EC.presence_of_element_located(
                        ("xpath", COUNTER_XPATHS[counter_name])
                    )
                ).text
            return raw_counts
        except CrawlError:
            raise
        except Exception as e:
            raise CrawlError(f"{stage} failed: {describe_exception(e)}") from e


def check_kid(crawler, kid: Any, index: int, notifier) -> KidOutcome:
    """Check one kid in isolation and return its outcome.

    Never raises for a bad config entry, a failed login/scrape or unparseable
    counters: those become a failed outcome, with the browser always closed.
    """
    display_name = kid_display_name(kid, index)
    problem = validate_kid(kid)
    if problem:
        logger.warning(f"Skipping kid {display_name} (#{index}): {problem}")
        return KidOutcome(display_name, False, reason=problem)

    # Keep the raw name for the notification title, exactly as configured.
    name = str(kid["name"])
    username = str(kid["username"])
    password = str(kid["password"])

    logger.info("Getting tasks for: " + display_name)

    raw_counts = None
    browser = None
    browser_started = False
    try:
        browser = crawler.init_browser()
        browser_started = True
        raw_counts = crawler.crowl(username, password)
    except Exception as e:
        if isinstance(e, CrawlError):
            reason = f"scrape failed: {e}"
        else:
            reason = f"scrape failed: {describe_exception(e)}"
        logger.error(f"Kid {display_name} (#{index}) failed: {reason}")
        if not browser_started:
            # A browser may have been created by init_browser before it raised.
            browser = getattr(crawler, "browser", None)
            browser_started = browser is not None
        return KidOutcome(display_name, False, reason=reason)
    finally:
        if browser_started:
            try:
                crawler.close_browser(browser)
            except Exception as e:
                logger.error(
                    f"Failed to close browser for {name} (#{index}): "
                    f"{describe_exception(e)}"
                )

    try:
        counts = parse_counts(raw_counts)
    except CountParseError as e:
        reason = f"counter parse failed: {e}"
        logger.error(f"Kid {display_name} (#{index}) failed: {reason}")
        return KidOutcome(display_name, False, reason=reason)

    if has_tasks(raw_counts["todo"], raw_counts["tofix"]):
        title = "מצב משימות אופק של " + name
        message = "\n".join(
            str(raw_counts[counter_name]) for counter_name in COUNTER_FIELDS
        )
        try:
            notifier.send_notification(title, message)
        except Exception as e:
            # The scrape itself succeeded; a notification channel failure must
            # not mask the kid's result nor affect the other kids.
            logger.error(
                f"Failed to send notification for {name} (#{index}): "
                f"{describe_exception(e)}"
            )
        return KidOutcome(name, True, with_tasks=True, counts=counts)

    logger.info("No tasks tbd for " + display_name)
    return KidOutcome(name, True, with_tasks=False, counts=counts)


def check_all(crawler, kids: Sequence[Any], notifier=None) -> RoundSummary:
    """Check every kid, isolating failures, then log one round summary."""
    if notifier is None:
        notifier = crawler

    outcomes = []
    try:
        for index, kid in enumerate(kids or [], start=1):
            try:
                outcome = check_kid(crawler, kid, index, notifier)
            except Exception as e:
                # Last-resort guard: one kid must never abort the rest of the round.
                display_name = kid_display_name(kid, index)
                reason = f"unexpected error: {describe_exception(e)}"
                logger.error(f"Kid {display_name} (#{index}) failed: {reason}")
                outcome = KidOutcome(display_name, False, reason=reason)
            outcomes.append(outcome)
    finally:
        # No browser may outlive the round, even after an unexpected error.
        leftover = getattr(crawler, "browser", None)
        if leftover is not None:
            try:
                crawler.close_browser(leftover)
            except Exception as e:
                logger.error(
                    "Failed to close leftover browser: " + describe_exception(e)
                )

    summary = build_summary(outcomes)
    logger.info(summary.message())
    return summary


def main(crawler=None, notifier=None, kids=None):
    if crawler is None:
        crawler = Crawler()
    if notifier is None:
        notifier = crawler
    if kids is None:
        kids = crawler.kids
    if isinstance(kids, dict):
        kids = kids.get("kids") or []
    return check_all(crawler, kids, notifier)


if __name__ == "__main__":
    try:
        crawler = Crawler()
        crawler.init_notifires()
        if not SCHEDULES:
            logger.debug("Setting default schedule to 16:00")
            schedule.every().day.at("16:00").do(main, crawler=crawler)
        else:
            for _schedule in SCHEDULES.split(','):
                logger.debug("Setting schedule to everyday at " + _schedule)
                schedule.every().day.at(_schedule).do(main, crawler=crawler)
        while True:
            schedule.run_pending()
            time.sleep(1)
    except Exception as e:
        logger.error(str(e))
