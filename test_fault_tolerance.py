"""Acceptance tests for fault-tolerant supplier collection.

Simulates, with locally built HTML and stubbed HTTP requests:
  1. A company page request raising an exception mid-batch.
  2. A company page without a contact-supplier link.
  3. All suppliers succeeding.
Plus the overall-failure cases (search page request error / no results),
and verifies Excel export and the auto-contact candidate list only
contain valid records.

Run: python test_fault_tolerance.py
"""

import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

import pandas as pd

# Stub PySimpleGUI before importing sellers_data (not needed for logic tests).
sg_stub = types.ModuleType("PySimpleGUI")
sg_stub.popup = lambda *a, **k: None
sg_stub.popup_auto_close = lambda *a, **k: None
sg_stub.WIN_CLOSED = "__WIN_CLOSED__"
sys.modules["PySimpleGUI"] = sg_stub

from data_to_excel import data_to_excel  # noqa: E402
from sellers_data import sellers_data  # noqa: E402

SEARCH_URL = "https://www.alibaba.com/corporations/widgets.html"

COMPANIES = ["Company A", "Company B", "Company C"]


def search_html(names):
    cards = ""
    for name in names:
        slug = name.lower().replace(" ", "")
        cards += f"""
        <div class="factory-card">
          <ul class="capability"><li>{name} products</li></ul>
          <a class="button csp" href="//{slug}.example.com/dir">dir</a>
          <a target="_blank" href="//{slug}.example.com">{name}</a>
        </div>"""
    return f"<html><body>{cards}</body></html>"


def company_page_html(name, with_contact_link=True):
    slug = name.lower().replace(" ", "")
    link = (
        f'<a class="shop-component-KeyButton ContactSupplier message" '
        f'href="//contact.{slug}.example.com">Contact Supplier</a>'
        if with_contact_link
        else ""
    )
    return f"<html><body><h1>{name}</h1>{link}</body></html>"


class FakeResponse:
    def __init__(self, content: str):
        self.content = content.encode("utf-8")


def make_fake_get(page_behavior):
    """page_behavior: dict company name -> 'ok' | 'raise' | 'no_link'."""

    def fake_get(url, *args, **kwargs):
        if url == SEARCH_URL:
            return FakeResponse(search_html(COMPANIES))
        for name in COMPANIES:
            slug = name.lower().replace(" ", "")
            if slug in url:
                behavior = page_behavior[name]
                if behavior == "raise":
                    raise ConnectionError(f"Connection refused for {name}")
                return FakeResponse(
                    company_page_html(name, with_contact_link=behavior != "no_link")
                )
        raise AssertionError(f"Unexpected URL requested: {url}")

    return fake_get


def run_case(title, page_behavior, expected_ok, expected_failed):
    with mock.patch("sellers_data.requests.get", make_fake_get(page_behavior)):
        result = sellers_data("widgets", 5)
    ok_names = [r["company_name"] for r in result["success"]]
    failed = result["failed"]
    failed_names = [f["company_name"] for f in failed]
    assert ok_names == expected_ok, f"{title}: success {ok_names} != {expected_ok}"
    assert failed_names == expected_failed, (
        f"{title}: failed {failed_names} != {expected_failed}"
    )
    for item in failed:
        assert item["error"], f"{title}: failure record missing reason: {item}"
    print(f"[PASS] {title}")
    print(f"       success: {ok_names}")
    for item in failed:
        print(f"       failed : {item['company_name']} -> {item['error']}")
    return result


def test_middle_request_raises():
    return run_case(
        "middle company page request raises",
        {"Company A": "ok", "Company B": "raise", "Company C": "ok"},
        expected_ok=["Company A", "Company C"],
        expected_failed=["Company B"],
    )


def test_missing_contact_link():
    return run_case(
        "company page without contact link",
        {"Company A": "ok", "Company B": "no_link", "Company C": "ok"},
        expected_ok=["Company A", "Company C"],
        expected_failed=["Company B"],
    )


def test_all_success():
    return run_case(
        "all suppliers succeed",
        {"Company A": "ok", "Company B": "ok", "Company C": "ok"},
        expected_ok=COMPANIES,
        expected_failed=[],
    )


def test_search_page_overall_failures():
    # No results on the search page -> overall failure (SystemExit).
    with mock.patch(
        "sellers_data.requests.get",
        lambda *a, **k: FakeResponse("<html><body></body></html>"),
    ):
        try:
            sellers_data("widgets", 5)
        except SystemExit:
            print("[PASS] empty search results -> overall failure (exit)")
        else:
            raise AssertionError("empty search results did not exit")
    # Search page request error -> overall failure (SystemExit).
    with mock.patch(
        "sellers_data.requests.get",
        mock.Mock(side_effect=ConnectionError("network down")),
    ):
        try:
            sellers_data("widgets", 5)
        except SystemExit:
            print("[PASS] search page request error -> overall failure (exit)")
        else:
            raise AssertionError("search page request error did not exit")


def test_excel_and_contact_list(result, title):
    success = result["success"]
    with tempfile.TemporaryDirectory() as tmp:
        data_to_excel(tmp, "widgets", success)
        xlsx = Path(tmp) / "Widgets Sellers.xlsx"
        assert xlsx.exists(), f"{title}: Excel file not written"
        df = pd.read_excel(xlsx)
        assert list(df.columns) == ["company_name", "main_prods", "company_website"], (
            f"{title}: unexpected columns {list(df.columns)}"
        )
        assert list(df["company_name"]) == [r["company_name"] for r in success], (
            f"{title}: Excel rows do not match success records"
        )
    # Auto-contact candidates: only successful records with a non-empty link.
    contacted = [r["dir_cont_link"] for r in success if r["dir_cont_link"]]
    assert len(contacted) == len(success), f"{title}: bad contact link filtered in"
    assert all(link.startswith("//contact.") for link in contacted)
    print(f"[PASS] {title}: Excel rows and contact list contain only valid records")


def test_all_failed_excel():
    result = run_case(
        "all company pages fail",
        {"Company A": "raise", "Company B": "no_link", "Company C": "raise"},
        expected_ok=[],
        expected_failed=COMPANIES,
    )
    with tempfile.TemporaryDirectory() as tmp:
        data_to_excel(tmp, "widgets", result["success"])
        df = pd.read_excel(Path(tmp) / "Widgets Sellers.xlsx")
        assert list(df.columns) == ["company_name", "main_prods", "company_website"]
        assert len(df) == 0
    print("[PASS] all failed -> Excel still written with headers only, no crash")


if __name__ == "__main__":
    r1 = test_middle_request_raises()
    r2 = test_missing_contact_link()
    r3 = test_all_success()
    test_search_page_overall_failures()
    test_excel_and_contact_list(r1, "middle request raises")
    test_excel_and_contact_list(r2, "missing contact link")
    test_excel_and_contact_list(r3, "all success")
    test_all_failed_excel()
    print("\nAll acceptance tests passed.")
