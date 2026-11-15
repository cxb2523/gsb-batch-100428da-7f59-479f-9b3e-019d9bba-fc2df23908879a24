# -*- coding: utf-8 -*-
"""Local verification with a fake Selenium driver and fake HTML-shaped data.

No real browser, no network. Run from the repo root:

    "C:/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe" -I tests/test_audible_fake.py
"""
import csv
import os
import re
import sys
import tempfile
import types

# ---------------------------------------------------------------------------
# Stub out the selenium packages BEFORE importing the project modules.
# ---------------------------------------------------------------------------
selenium_pkg = types.ModuleType("selenium")
selenium_webdriver_pkg = types.ModuleType("selenium.webdriver")
selenium_common_pkg = types.ModuleType("selenium.common")
selenium_exc_pkg = types.ModuleType("selenium.common.exceptions")
keys_pkg = types.ModuleType("selenium.webdriver.common.keys")
chrome_pkg = types.ModuleType("selenium.webdriver.chrome")
chrome_options_pkg = types.ModuleType("selenium.webdriver.chrome.options")


class Keys:
    ENTER = "\n"
    CONTROL = ""


class Options:
    def __init__(self):
        self.args = []

    def add_extension(self, path):
        self.args.append(path)


class NoSuchElementException(Exception):
    pass


keys_pkg.Keys = Keys
chrome_options_pkg.Options = Options
selenium_exc_pkg.NoSuchElementException = NoSuchElementException
selenium_pkg.webdriver = selenium_webdriver_pkg
selenium_pkg.common = selenium_common_pkg
selenium_common_pkg.exceptions = selenium_exc_pkg
selenium_webdriver_pkg.Chrome = lambda *a, **k: None

sys.modules.update({
    "selenium": selenium_pkg,
    "selenium.webdriver": selenium_webdriver_pkg,
    "selenium.common": selenium_common_pkg,
    "selenium.common.exceptions": selenium_exc_pkg,
    "selenium.webdriver.common.keys": keys_pkg,
    "selenium.webdriver.chrome": chrome_pkg,
    "selenium.webdriver.chrome.options": chrome_options_pkg,
})

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

import audible_data_handling as adh          # noqa: E402
import audible_webpage_automation as awa     # noqa: E402

# No real sleeps in tests.
awa.time.sleep = lambda *_a, **_k: None


# ---------------------------------------------------------------------------
# Fake DOM elements and driver.
# ---------------------------------------------------------------------------
class FakeElement:
    def __init__(self, text=""):
        self.text = text

    def click(self):
        pass


class FakeNextButton(FakeElement):
    def __init__(self, driver):
        super().__init__("Next")
        self._driver = driver

    def is_enabled(self):
        return self._driver.page_index < len(self._driver.pages) - 1

    def click(self):
        if self.is_enabled():
            self._driver.page_index += 1
            self._driver.active_handle = "list"


class FakeLinkElement(FakeElement):
    def __init__(self, text, driver, card):
        super().__init__(text)
        self._driver = driver
        self._card = card

    def send_keys(self, _keys):
        # ctrl+enter opens the detail page in a new tab.
        self._driver.window_handles.append("detail")
        self._driver.active_handle = "detail"
        self._driver.current_card = self._card


class FakeDriver:
    """Routes xpath calls to in-memory list cards and detail pages."""

    def __init__(self, pages):
        self.pages = pages
        self.page_index = 0
        self.window_handles = ["list"]
        self.active_handle = "list"
        self.current_card = None
        self.showmore_clicks = 0

    # -- tab management -----------------------------------------------------
    def close(self):
        if self.active_handle == "detail":
            self.window_handles.remove("detail")
            self.active_handle = "list"
            self.current_card = None

    class _SwitchTo:
        def __init__(self, driver):
            self._driver = driver

        def window(self, handle):
            self._driver.active_handle = handle if handle == "list" else "detail"

    @property
    def switch_to(self):
        return FakeDriver._SwitchTo(self)

    def execute_script(self, _script):
        return None

    # -- helpers ------------------------------------------------------------
    @property
    def cards(self):
        return self.pages[self.page_index]

    def _missing(self):
        raise NoSuchElementException("element not found")

    # -- plural lookups -----------------------------------------------------
    def find_elements_by_xpath(self, xpath):
        if "productList')]/li" in xpath and xpath.endswith("/li"):
            return [FakeElement("") for _ in self.cards]
        if "ReviewsTabUS" in xpath and xpath.endswith("/div/div[2]/p[1]"):
            card = self.current_card
            reviews = (card or {}).get("detail", {}).get("reviews", [])
            return [FakeElement(r) for r in reviews]
        return []

    # -- singular lookups ---------------------------------------------------
    def find_element_by_xpath(self, xpath):
        if self.active_handle == "list":
            return self._list_lookup(xpath)
        return self._detail_lookup(xpath)

    def _list_lookup(self, xpath):
        idx_match = re.search(r"li\[(\d+)\]", xpath)
        idx = int(idx_match.group(1)) if idx_match else 1
        if idx < 1 or idx > len(self.cards):
            self._missing()
        card = self.cards[idx - 1]

        if xpath.endswith("//li[1]//a"):
            return FakeLinkElement(card.get("title", ""), self, card)
        if "nextButton" in xpath:
            return FakeNextButton(self)

        field_map = [
            ("h3[contains(@class,'heading')]", "title"),
            ("li[contains(@class,'subtitle')]", "subtitle"),
            ("li[contains(@class,'authorLabel')]//a", "author"),
            ("li[contains(@class,'narratorLabel')]//a", "narrator"),
            ("li[contains(@class,'runtimeLabel')]", "runtime"),
            ("li[contains(@class,'ratingsLabel')]/span[1]", "rating"),
            ("div[contains(@class,'BuyBox')]/p[1]", "price"),
        ]
        for fragment, key in field_map:
            if fragment in xpath:
                value = card.get(key, "")
                if value == "" or value is None:
                    self._missing()
                return FakeElement(value)
        self._missing()

    def _detail_lookup(self, xpath):
        card = self.current_card
        if card.get("detail_fail"):
            # Simulate a broken detail page: every required lookup fails.
            self._missing()
        detail = card["detail"]

        if "showMoreReviews" in xpath:
            self.showmore_clicks += 1
            return FakeElement("show more")
        review_match = re.search(r"ReviewsTabUS'\)\]/div\[(\d+)\]/div\[2\]/p\[1\]", xpath)
        if review_match:
            n = int(review_match.group(1))
            reviews = detail.get("reviews", [])
            if n < 1 or n > len(reviews):
                self._missing()
            return FakeElement(reviews[n - 1])
        if "centerSlot" in xpath:
            if xpath.endswith("//li[1]"):
                return FakeElement(detail["title"])
            if xpath.endswith("//li[2]"):
                return FakeElement(detail.get("subtitle", ""))
            if "authorLabel" in xpath:
                return FakeElement(detail["author"])
            if "narratorLabel" in xpath:
                return FakeElement(detail["narrator"])
            if "runtimeLabel" in xpath:
                return FakeElement(detail["runtime"])
            if "contains(@class,'format')" in xpath:
                return FakeElement(detail["type"])
            if "categoriesLabel" in xpath:
                return FakeElement(detail.get("category", ""))
            if "ratingsLabel')]/span[2]" in xpath:
                return FakeElement(detail["rating"])
            if "ratingsLabel')]" in xpath:
                return FakeElement(detail["ratings_label"])
        if "contains(@title," in xpath:
            return FakeElement(detail["price"])
        self._missing()


# ---------------------------------------------------------------------------
# Fake data (HTML-shaped fields, in memory).
# ---------------------------------------------------------------------------
def make_card(title, detail_fail=False, missing_card_fields=()):
    card = {
        "title": title,
        "subtitle": title + " Subtitle",
        "author": "Author of " + title,
        "narrator": "Narrator of " + title,
        "runtime": "Length: 5 hrs and 3 mins",
        "rating": "4.5",
        "price": "Buy for $19.99",
        "detail": {
            "title": title,
            "subtitle": title + " Subtitle",
            "author": "Author of " + title,
            "narrator": "Narrator of " + title,
            "runtime": "Length: 5 hrs and 3 mins",
            "type": "Unabridged",
            "category": "Fiction",
            "rating": "4.5",
            "ratings_label": "4.5 out of 5 stars (1,234 ratings)",
            "price": "Buy for $19.99",
            "reviews": ["review one of " + title, "review two of " + title, "review three of " + title],
        },
    }
    if detail_fail:
        card["detail_fail"] = True
    for field in missing_card_fields:
        card[field] = ""
    return card


def reset_globals():
    adh.final_column_data = []
    adh.final_data_list = []
    awa.showmore_open_times = 0


def read_csv(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (name, detail))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_happy_path_and_pagination():
    print("test_happy_path_and_pagination")
    reset_globals()
    pages = [
        [make_card("P1 Book 1"), make_card("P1 Book 2")],
        [make_card("P2 Book 1"), make_card("P2 Book 2")],
    ]
    driver = FakeDriver(pages)
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            awa.showmore_open_times = 2
            awa.click_element(driver)
            rows = read_csv("output_encoded.csv")
        finally:
            os.chdir(cwd)

    header, data = rows[0], rows[1:]
    titles = [r[0] for r in data]
    check("all four cards processed across two pages", len(data) == 4, str(titles))
    check("first card of page 1 not skipped", "P1 Book 1" in titles)
    check("first card of page 2 not skipped", "P2 Book 1" in titles)
    check("all rows marked detail_page", all(r[-1] == adh.SOURCE_DETAIL for r in data))
    check("review columns == showmore_open_times * 10 (20)",
          len(header) == len(adh.book_data_columns) + 20 + 1)
    check("every row matches header width", all(len(r) == len(header) for r in data))
    check("detail tabs closed, back on list tab",
          driver.window_handles == ["list"] and driver.active_handle == "list")
    check("pagination advanced to page 2", driver.page_index == 1)
    check("showmore clicked (times-1) per book", driver.showmore_clicks == 4)


def test_fallback_on_broken_detail_page():
    print("test_fallback_on_broken_detail_page")
    reset_globals()
    pages = [[
        make_card("Good Book"),
        make_card("Broken Book", detail_fail=True),
        make_card("Broken Sparse Book", detail_fail=True,
                  missing_card_fields=("author", "narrator", "runtime", "price", "rating")),
    ]]
    driver = FakeDriver(pages)
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            awa.showmore_open_times = 2
            awa.click_element(driver)
            rows = read_csv("output_encoded.csv")
        finally:
            os.chdir(cwd)

    header, data = rows[0], rows[1:]
    check("all three books produced rows (local failure did not stop the loop)", len(data) == 3,
          str([r[0] for r in data]))
    good, broken, sparse = data
    check("good row from detail page", good[-1] == adh.SOURCE_DETAIL)
    check("broken row marked list_fallback", broken[-1] == adh.SOURCE_FALLBACK)
    check("fallback keeps list-card title", broken[0] == "Broken Book")
    check("fallback keeps list-card author", broken[2] == "Author of Broken Book")
    check("fallback keeps list-card runtime (cleaned)", broken[4] == "5 hrs and 3 mins")
    check("fallback keeps list-card price (cleaned)", broken[9] == "$19.99")
    check("detail-only columns blank in fallback (Audiobook_Type/Categories)",
          broken[5] == "" and broken[6] == "")
    check("fallback review slots are blank but present",
          broken[10:30] == [""] * 20)
    check("sparse card missing fields are blank, not an exception",
          sparse[-1] == adh.SOURCE_FALLBACK and sparse[0] == "Broken Sparse Book"
          and sparse[2] == "" and sparse[4] == "" and sparse[9] == "")
    check("columns stay aligned for every row", all(len(r) == len(header) for r in data))
    check("driver back on list tab with single handle",
          driver.window_handles == ["list"] and driver.active_handle == "list")


def test_single_card_list_is_not_skipped():
    print("test_single_card_list_is_not_skipped")
    reset_globals()
    driver = FakeDriver([[make_card("Lonely Book")]])
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            awa.showmore_open_times = 1
            awa.click_element(driver)
            rows = read_csv("output_encoded.csv")
        finally:
            os.chdir(cwd)
    check("single first item is processed", len(rows) == 2 and rows[1][0] == "Lonely Book")
    check("10 review columns when showmore_open_times=1",
          len(rows[0]) == len(adh.book_data_columns) + 10 + 1)


def test_duplicate_remover():
    print("test_duplicate_remover")
    reset_globals()
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            header = adh.book_data_columns + ["Review 1", adh.SOURCE_COLUMN]
            with open("output_encoded.csv", "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(header)
                w.writerow(["A"] + [""] * (len(header) - 1))
                w.writerow(header)  # repeated header must not survive as data
                w.writerow(["A"] + [""] * (len(header) - 1))  # exact duplicate
                w.writerow(["B"] + [""] * (len(header) - 1))
            adh.duplicate_remover()
            rows = read_csv("Audible_Dataset_final.csv")
            check("final file exists", os.path.exists("Audible_Dataset_final.csv"))
            check("exactly one header row", rows.count(header) == 1)
            check("duplicate data rows removed", len(rows) == 3, str(rows))
            check("unique data rows kept", [r[0] for r in rows[1:]] == ["A", "B"])

            os.remove("output_encoded.csv")
            adh.duplicate_remover()  # must print a message, not raise
            check("missing input does not crash", True)
        finally:
            os.chdir(cwd)


def test_review_crawler_always_returns_full_width():
    print("test_review_crawler_always_returns_full_width")
    reset_globals()
    awa.showmore_open_times = 3  # 30 review columns
    card = make_card("Review Book")
    driver = FakeDriver([[card]])
    driver.window_handles.append("detail")
    driver.active_handle = "detail"
    driver.current_card = card
    reviews = awa.reviews_crawler(driver)
    check("returns exactly showmore_open_times*10 entries", len(reviews) == 30)
    check("captured reviews kept and rest blank-padded",
          reviews[0].startswith("review one") and reviews[3] == "")

    # A detail page whose showmore button disappears mid-crawl must not raise.
    class BrokenShowMore(FakeDriver):
        def find_element_by_xpath(self, xpath):
            if "showMoreReviews" in xpath:
                raise NoSuchElementException("showmore gone")
            return super().find_element_by_xpath(xpath)

    broken = BrokenShowMore([[card]])
    broken.window_handles.append("detail")
    broken.active_handle = "detail"
    broken.current_card = card
    reviews2 = awa.reviews_crawler(broken)
    check("crawler survives showmore failure and pads", len(reviews2) == 30)


if __name__ == "__main__":
    test_happy_path_and_pagination()
    test_fallback_on_broken_detail_page()
    test_single_card_list_is_not_skipped()
    test_duplicate_remover()
    test_review_crawler_always_returns_full_width()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
