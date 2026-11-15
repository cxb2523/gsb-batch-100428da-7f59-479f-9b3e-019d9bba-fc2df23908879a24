import os
import json
import logging
import sys
import re
import tempfile
import time
from dotenv import load_dotenv
from datetime import datetime, timedelta
import argparse

from selenium import webdriver
from selenium_stealth import stealth

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException

# A transaction amount is a standalone currency value ("$54.50 CAD",
# "US$1,234.56", "54.50 CAD"). Composite strings like the DRIP panel's
# "1.1444 shares x $47.62 CAD" (Filled quantity) must not match.
MONEY_RE = re.compile(r'^[+-]?(?:(?:US|C)?\$[\d,]+(?:\.\d+)?(?:\s+[A-Z]{3})?|[\d,]+(?:\.\d+)?\s+[A-Z]{3})$')

# Panel details that hold the transaction total, most specific first.
AMOUNT_LABELS = ('Total cost', 'Total', 'Amount', 'Amount invested', 'Total value', 'Value', 'Dividend')
NON_AMOUNT_LABEL_RE = re.compile(r'price|share|rate|quantity|fx', re.IGNORECASE)
INACTIVE_STATUSES = {'cancelled', 'canceled', 'rejected', 'expired', 'failed'}

# Element locators for the Wealthsimple activity page.
XPATH_ROWS = "//main//button[span[1]/span[3]]"
XPATH_LOAD_MORE = "//button[.//span[text()='Load more']]"
XPATH_HEADERS = "//main//h3"
XPATH_ROW_DESCRIPTION = "./span[1]/span[2]/span[1]"
XPATH_ROW_TYPE = "./span[1]/span[2]/span[2]/span[1]/span[1]/span[1]"
XPATH_ROW_AMOUNT = "./span[1]/span[3]/span[last()]/span[1]/span[1]"
XPATH_SECTION_HEADER = "ancestor::div[.//h3][1]//h3"

# Wait/paging tuning, kept as module constants so tests can shrink them.
PANEL_WAIT_TIMEOUT = 10
DETAILS_WAIT_TIMEOUT = 5
LOAD_MORE_MAX_CLICKS = 60
PAGE_LOAD_WAIT_SECONDS = 2
DEFAULT_AFTER_DAYS = 45


class SkipTransaction(Exception):
    """Raised when one transaction cannot be scraped and must be skipped."""


class CutoffReached(Exception):
    """Raised when a scraped transaction is older than the --after cutoff."""

    def __init__(self, date):
        super().__init__(f"cutoff reached at {date.isoformat()}")
        self.date = date


# Wealthsimple renders negatives with a Unicode minus ("− $47.23 CAD").
# Normalize only for matching: the original string is kept in the output so
# myResultParser.js keeps its existing type-based sign handling.
def normalize_money(value):
    text = str(value or '').replace('−', '-').replace('–', '-').replace('—', '-').replace(' ', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    return re.sub(r'^([+-])\s+', r'\1', text)

def is_money(value):
    return bool(MONEY_RE.match(normalize_money(value)))

def one_line(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()

# Pick the pure money value out of an expanded activity panel. Labels alone
# are not trusted: Wealthsimple sometimes labels the share quantity "Total",
# so only currency-shaped values are accepted, and price/share rows skipped.
def pick_amount(details):
    for label in AMOUNT_LABELS:
        value = (details.get(label) or '').strip()
        if is_money(value):
            return value
    for label, value in details.items():
        if NON_AMOUNT_LABEL_RE.search(label):
            continue
        value = (value or '').strip()
        if is_money(value):
            return value
    return None

# Convert a date string from the activity page ('Today', 'Yesterday',
# 'January 30, 2024', or 'August 30, 2026 4:00 pm') to a datetime.
def convert_datetime(input_string):
    text = re.sub(r'\s+', ' ', input_string).strip()
    today = datetime.now()
    if text.lower() == 'today':
        return today.replace(hour=0, minute=0, second=0, microsecond=0)
    if text.lower() == 'yesterday':
        return (today - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    for fmt in ('%B %d, %Y %I:%M %p', '%B %d, %Y'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {input_string!r}")

# Click an element after centering it in the viewport: the activity page has a
# sticky header that would otherwise intercept clicks on rows scrolled to the top.
def click_visible(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'}); arguments[0].click();", element)


def _transaction_signature(transaction):
    # Stable fingerprint of a full transaction record, used for resume dedup.
    return json.dumps(transaction, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def atomic_write_json(path, records):
    """Serialize records as a JSON array and swap it into place atomically.

    The temp file is created in the same directory as the target so os.replace
    is atomic on both Linux and Windows. A crash or failed write therefore
    leaves the previous file intact and parseable — never a half-written one.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix='.wealthsimpleton-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(records, handle, indent=4)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class IncrementalWriter:
    """Keeps the output file as a valid JSON array after every transaction.

    With no path the writer simply accumulates records in memory for the final
    stdout print. On startup an existing file is loaded so an interrupted run
    can be resumed without duplicating already-scraped transactions. Write
    failures are counted and logged; scraping always continues.
    """

    def __init__(self, path, log, persist_fn=atomic_write_json):
        self.path = path
        self.log = log
        self._persist_fn = persist_fn
        self.records = []
        self._signatures = set()
        self.write_errors = 0
        if path:
            self._load_existing()

    def _load_existing(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding='utf-8') as handle:
                data = json.load(handle)
            if not isinstance(data, list):
                raise ValueError('top-level JSON value is not an array')
        except (OSError, ValueError) as error:
            # Never silently destroy an unreadable output file: move it aside
            # and start a fresh array, then keep scraping.
            backup = f"{self.path}.corrupt-{os.getpid()}"
            try:
                os.replace(self.path, backup)
            except OSError:
                backup = '(could not be moved)'
            self.log.warning("Existing output %s is not a readable JSON array (%s); "
                             "moved it to %s and starting a new file", self.path, error, backup)
            return
        self.records = data
        self._signatures = {_transaction_signature(record) for record in data}
        self.log.info("Resuming: loaded %d existing record(s) from %s", len(data), self.path)

    def add(self, transaction):
        """Append one transaction. Returns True if it was already on file.

        Resumed duplicates are counted but not appended again, so the on-disk
        array stays identical to the previous run instead of growing doubles.
        """
        signature = _transaction_signature(transaction)
        if signature in self._signatures:
            return True
        self.records.append(transaction)
        self._signatures.add(signature)
        if self.path:
            try:
                self._persist_fn(self.path, self.records)
            except Exception as error:  # noqa: BLE001 - scraping must survive disk errors
                self.write_errors += 1
                self.log.warning("Failed to incrementally write %s after %d record(s): %s. "
                                 "Keeping the data in memory and continuing",
                                 self.path, len(self.records), error)
        return False

    def flush(self):
        """Final best-effort retry if any incremental write failed earlier."""
        if not self.path or self.write_errors == 0:
            return
        try:
            self._persist_fn(self.path, self.records)
        except Exception as error:  # noqa: BLE001
            self.log.warning("Final retry writing %s also failed: %s. "
                             "All %d record(s) are still printed to stdout",
                             self.path, error, len(self.records))
        else:
            self.log.info("Retry succeeded: %d record(s) saved to %s", len(self.records), self.path)
            self.write_errors = 0


# Keep clicking "Load more" until the button disappears or the oldest visible
# section is confirmed older than the cutoff. An unparseable section header is
# never fatal: log a warning and load the next page instead of stopping early.
def load_all_activity(driver, cutoff, log):
    cutoff_midnight = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
    clicks = 0
    for _ in range(LOAD_MORE_MAX_CLICKS):
        try:
            load_more = driver.find_element(By.XPATH, XPATH_LOAD_MORE)
        except NoSuchElementException:
            log.info("No 'Load more' button found, reached the end of the activity list")
            break
        headers = driver.find_elements(By.XPATH, XPATH_HEADERS)
        if headers:
            oldest_header_text = headers[-1].text
            try:
                oldest = convert_datetime(oldest_header_text)
            except ValueError:
                log.warning("Oldest section header %r could not be parsed as a date; "
                            "loading the next page anyway instead of stopping pagination",
                            oldest_header_text)
            else:
                if oldest < cutoff_midnight:
                    log.info("Reached cutoff %s, stopping 'Load more'", cutoff.date().isoformat())
                    break
        click_visible(driver, load_more)
        clicks += 1
        time.sleep(PAGE_LOAD_WAIT_SECONDS)
    return clicks


def _read_panel_details(driver, panel):
    try:
        WebDriverWait(driver, DETAILS_WAIT_TIMEOUT).until(
            lambda _: panel.find_elements(By.TAG_NAME, 'dt'))
    except TimeoutException:
        pass
    # Pair each label with its own value; zipping the dt and dd lists
    # globally mispairs every row after an unpaired dd.
    details = {}
    for dt in panel.find_elements(By.TAG_NAME, 'dt'):
        dd = dt.find_elements(By.XPATH, './following-sibling::dd[1]')
        if dd:
            details[dt.text] = dd[0].text
    return details


# Scrape a single activity row into the unchanged output schema, or raise
# SkipTransaction with a human-readable reason. Any exception type other than
# SkipTransaction/CutoffReached is treated as an unexpected skip by the caller.
def scrape_one(driver, index, cutoff, log):
    ticker = driver.find_elements(By.XPATH, XPATH_ROWS)[index]
    description = ticker.find_element(By.XPATH, XPATH_ROW_DESCRIPTION).text
    transaction_type = ticker.find_element(By.XPATH, XPATH_ROW_TYPE).text

    click_visible(driver, ticker)
    panel_xpath = f"({XPATH_ROWS})[{index + 1}]/following-sibling::div[1]"
    try:
        panel = WebDriverWait(driver, PANEL_WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.XPATH, panel_xpath)))
    except TimeoutException:
        raise SkipTransaction(f"activity panel did not open within {PANEL_WAIT_TIMEOUT}s")

    details = _read_panel_details(driver, panel)
    click_visible(driver, ticker)  # collapse

    status = (details.get('Status') or '').strip()
    if status.lower() in INACTIVE_STATUSES:
        raise SkipTransaction(f"inactive status: {status}")

    row_amount_matches = ticker.find_elements(By.XPATH, XPATH_ROW_AMOUNT)
    row_amount = row_amount_matches[0].text.strip() if row_amount_matches else ''
    amount = pick_amount(details)
    if amount is None:
        if row_amount and is_money(row_amount):
            amount = row_amount
            log.debug("  using row amount for %d: %s | %s", index + 1, description, transaction_type)
        else:
            suffix = f" (status: {status})" if status else ""
            raise SkipTransaction(f"no transaction amount found in panel or row{suffix}")

    date_str = details.get('Date')
    if not date_str:
        date_str = ticker.find_element(By.XPATH, XPATH_SECTION_HEADER).text
    try:
        date_dt = convert_datetime(date_str)
    except ValueError:
        raise SkipTransaction(f"unparseable date: {date_str!r}")

    if date_dt < cutoff:
        raise CutoffReached(date_dt)

    return {
        "description": description,
        "type": transaction_type,
        "amount": amount,
        "date": date_dt.isoformat(),
        "details": details,
    }


def _log_skip(log, stats, skipped, index, total, description, transaction_type, reason):
    label = one_line(description) or f"index {index + 1}"
    type_part = f" | {transaction_type}" if transaction_type else ""
    log.warning("Skipped %d/%d: %s%s — %s", index + 1, total, label, type_part, reason)
    stats['skipped'] += 1
    skipped.append({
        'index': index + 1,
        'total': total,
        'description': label,
        'type': transaction_type,
        'reason': reason,
    })


# Iterate over every loaded activity row. Successful transactions go to the
# incremental writer immediately; every skip is logged with its position,
# readable description and reason, and counted for the final summary.
def scrape_all(driver, cutoff, writer, log):
    tickers = driver.find_elements(By.XPATH, XPATH_ROWS)
    total = len(tickers)
    log.info("Found %d transactions, scraping...", total)
    stats = {'succeeded': 0, 'written': 0, 'resumed': 0, 'skipped': 0, 'write_errors': 0}
    skipped = []

    for index in range(total):
        description = f"index {index + 1}"
        transaction_type = ""
        ticker = None
        try:
            ticker = driver.find_elements(By.XPATH, XPATH_ROWS)[index]
            description = ticker.find_element(By.XPATH, XPATH_ROW_DESCRIPTION).text
            transaction_type = ticker.find_element(By.XPATH, XPATH_ROW_TYPE).text
        except Exception as error:  # noqa: BLE001
            _log_skip(log, stats, skipped, index, total, description, transaction_type,
                      f"row header unreadable: {type(error).__name__}: {error}")
            continue

        try:
            transaction = scrape_one(driver, index, cutoff, log)
        except CutoffReached:
            log.info("Cutoff %s reached, stopping", cutoff.date().isoformat())
            break
        except SkipTransaction as skip:
            _log_skip(log, stats, skipped, index, total, description, transaction_type, str(skip))
            continue
        except Exception as error:  # noqa: BLE001
            log.debug("  scrape error for %d/%d", index + 1, total, exc_info=True)
            _log_skip(log, stats, skipped, index, total, description, transaction_type,
                      f"{type(error).__name__}: {error}")
            continue

        resumed = writer.add(transaction)
        stats['succeeded'] += 1
        if resumed:
            stats['resumed'] += 1
            log.info("  %d/%d already present in output file, kept as-is: %s",
                     index + 1, total, description)
        else:
            stats['written'] += 1
            log.debug("  %d/%d: %s | %s | %s | %s", index + 1, total,
                      description, transaction_type, transaction['amount'], transaction['date'])

    stats['write_errors'] = writer.write_errors
    return stats, skipped


# Full scrape against an injected driver (real Selenium or a test fake).
def run(driver, output_path, cutoff, log, persist_fn=atomic_write_json):
    load_all_activity(driver, cutoff, log)
    writer = IncrementalWriter(output_path or None, log, persist_fn=persist_fn)
    stats, skipped = scrape_all(driver, cutoff, writer, log)
    writer.flush()

    # stdout stays a pure JSON array, exactly as myResultParser.js expects;
    # all human-facing reporting (including the summary) goes to stderr.
    print(json.dumps(writer.records, indent=4))

    summary = (f"Scrape complete: {stats['succeeded']} succeeded "
               f"({stats['written']} newly written, {stats['resumed']} already on file), "
               f"{stats['skipped']} skipped")
    if stats['write_errors']:
        summary += f", {stats['write_errors']} file write error(s)"
    log.info(summary)
    for item in skipped:
        log.info("  skipped #%d/%d: %s — %s",
                 item['index'], item['total'], item['description'], item['reason'])
    return stats, skipped, writer


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--file', help='Where to save the output (can be an existing file for incremental scraping)')
    parser.add_argument('--after', help='A \'Y-m-d H:M\' string to filter out orders older than a certain date/time')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose/debug logging to stderr')
    args = parser.parse_args(argv)

    output_path = args.file or ""
    after_str = args.after

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(message)s',
        datefmt='%H:%M:%S',
        stream=sys.stderr,
    )
    log = logging.getLogger('wealthsimpleton')

    log.info("Wealthsimpleton starting")
    load_dotenv()
    options = webdriver.ChromeOptions()
    dataDir = os.path.expanduser("~/.config/wealthsimpleton-selenium")
    remote = os.environ.get("SELENIUM_REMOTE_URL")
    if remote and not os.environ.get("SELENIUM_PROFILE_DIR"):
        # The profile lives on the Selenium sidecar (it runs Chrome as
        # `seluser`); the path is resolved there, so never touch it locally.
        dataDir = "/home/seluser/.config/" + os.path.basename(dataDir)
    dataDir = os.environ.get("SELENIUM_PROFILE_DIR", dataDir)
    options.add_argument(f"--user-data-dir={dataDir}")
    options.add_argument("--profile-directory=Default")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    os.environ["TMPDIR"] = "/tmp"
    driver = None
    for attempt in range(3):
        try:
            if remote:
                options.add_argument("--no-sandbox")
                driver = webdriver.Remote(command_executor=remote, options=options)
            else:
                import glob
                os.makedirs(dataDir, exist_ok=True)
                for f in glob.glob(os.path.join(dataDir, "Singleton*")):
                    os.remove(f)
                driver = webdriver.Chrome(options=options)
            break
        except Exception as e:
            log.warning("Chrome launch failed (attempt %d/3): %s", attempt + 1, e)
            time.sleep(5)
    if driver is None:
        raise RuntimeError("Chrome failed to launch after 3 attempts")
    if remote:
        # selenium-stealth only accepts a local Chrome instance; the remote
        # driver speaks the same CDP, so present it as Chrome for that check.
        driver.__class__ = webdriver.Chrome
    stealth(driver,
        languages=["en-US", "en"],
        vendor="Google Inc.",
        platform="Win32",
        webgl_vendor="Intel Inc.",
        renderer="Intel Iris OpenGL Engine",
        fix_hairline=True,
        )

    driver.get("https://my.wealthsimple.com/login")
    log.info("Waiting for login/2FA (timeout: 3600s)...")
    WebDriverWait(driver, 3600).until(EC.url_changes(driver.current_url)) # Long timeout needed for manual login or 2FA
    log.info("Logged in, loading activity page")
    driver.get("https://my.wealthsimple.com/app/activity")
    WebDriverWait(driver, 300).until(EC.presence_of_element_located((By.XPATH, XPATH_ROWS)))
    time.sleep(2)
    if after_str is not None:
        cutoff = datetime.strptime(after_str, '%Y-%m-%d %H:%M')
    else:
        cutoff = datetime.now() - timedelta(days=DEFAULT_AFTER_DAYS)  # ~1.5 months

    run(driver, output_path, cutoff, log)


# Main function
if __name__ == "__main__":
    main()
