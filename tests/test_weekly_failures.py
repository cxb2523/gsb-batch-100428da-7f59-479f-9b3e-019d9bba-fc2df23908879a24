"""Offline tests for per-week failure isolation in the forexfactory-html provider."""

from datetime import datetime

import pandas as pd
import pytest
from dateutil.tz import gettz

from src.forexfactory.incremental import scrape_incremental
from src.forexfactory.main import main
from src.forexfactory.providers import ProviderError
from src.forexfactory.providers import forexfactory_html as html_module
from src.forexfactory.providers.forexfactory_html import (
    ForexFactoryHtmlProvider,
    WeeklyFetchFailure,
    format_weekly_failures,
)
from src.forexfactory.providers.http import HttpResult

TZNAME = "Asia/Tehran"


def _calendar_html(day_label: str, event_name: str, currency: str = "USD") -> str:
    return f"""
    <table class="calendar__table">
    <tr class="calendar__row calendar__row--day-breaker"><td class="calendar__date">{day_label}</td></tr>
    <tr class="calendar__row" data-event-id="1">
      <td class="calendar__time">8:30am</td>
      <td class="calendar__currency">{currency}</td>
      <td class="calendar__impact"><span title="High Impact Expected"></span></td>
      <td class="calendar__event"><a href="/calendar/1-test-event">{event_name}</a></td>
      <td class="calendar__actual">1.0%</td>
      <td class="calendar__forecast">0.8%</td>
      <td class="calendar__previous">0.7%</td>
    </tr>
    </table>
    """


SECURITY_HTML = """
<html><head><title>Performing security verification</title></head>
<body>This website uses a security service to protect against malicious bots.</body></html>
"""

BLANK_HTML = "<html><body>nothing useful here</body></html>"


class FakeFetcher:
    """Replacement for fetch_url keyed by the week token embedded in the URL."""

    def __init__(self, responses):
        # responses maps the "week=..." token to either an HttpResult or an Exception.
        self.responses = responses
        self.calls = []

    def __call__(self, url, timeout=30):
        self.calls.append(url)
        token = url.split("week=", 1)[1]
        response = self.responses[token]
        if isinstance(response, Exception):
            raise response
        return response


def _result(html: str) -> HttpResult:
    return HttpResult(
        url="https://www.forexfactory.com/calendar",
        status_code=200,
        final_url="https://www.forexfactory.com/calendar",
        content_type="text/html",
        text=html,
    )


def _three_week_range():
    tz = gettz(TZNAME)
    # 2025-04-07 is a Monday, so iter_weeks yields Apr 7, Apr 14, Apr 21.
    return datetime(2025, 4, 7, tzinfo=tz), datetime(2025, 4, 23, tzinfo=tz)


def test_middle_week_failure_is_isolated_and_others_survive(monkeypatch):
    start, end = _three_week_range()
    responses = {
        "apr07.2025": _result(_calendar_html("Mon Apr 7", "Week One Event")),
        "apr14.2025": _result(SECURITY_HTML),
        "apr21.2025": _result(_calendar_html("Mon Apr 21", "Week Three Event")),
    }
    fetcher = FakeFetcher(responses)
    monkeypatch.setattr(html_module, "fetch_url", fetcher)

    provider = ForexFactoryHtmlProvider()
    events = provider.fetch_events(start, end, TZNAME)

    # Every week was still attempted.
    assert len(fetcher.calls) == 3

    # Events from the successful weeks survive and stay inside the requested range.
    event_names = {event["event"] for event in events}
    assert event_names == {"Week One Event", "Week Three Event"}
    assert all(start.date().isoformat() <= event["date"] <= end.date().isoformat() for event in events)
    assert all(event.get("datetime_local") for event in events)

    # The failed week is reported with week start, existing reason and raw message.
    assert len(provider.weekly_failures) == 1
    failure = provider.weekly_failures[0]
    assert isinstance(failure, WeeklyFetchFailure)
    assert failure.week_start.date().isoformat() == "2025-04-14"
    assert failure.reason == "security_verification"
    assert "security_verification" in failure.error
    assert "apr14.2025" in failure.url

    summary = format_weekly_failures(provider.weekly_failures)
    assert "2025-04-14" in summary
    assert "security_verification" in summary


def test_events_outside_requested_range_are_still_filtered(monkeypatch):
    start, end = _three_week_range()
    # The first week's page also contains an event dated before the requested range.
    week_one_html = (
        _calendar_html("Mon Apr 7", "In Range Event")
        + '<tr class="calendar__row calendar__row--day-breaker"><td class="calendar__date">Sat Apr 5</td></tr>'
        + """
        <tr class="calendar__row" data-event-id="2">
          <td class="calendar__time">9:00am</td>
          <td class="calendar__currency">EUR</td>
          <td class="calendar__impact"><span title="Low Impact Expected"></span></td>
          <td class="calendar__event"><a href="/calendar/2-other">Out Of Range Event</a></td>
        </tr>
        """
    )
    responses = {
        "apr07.2025": _result(week_one_html),
        "apr14.2025": _result(_calendar_html("Mon Apr 14", "Middle Event")),
        "apr21.2025": _result(_calendar_html("Mon Apr 21", "Last Event")),
    }
    monkeypatch.setattr(html_module, "fetch_url", FakeFetcher(responses))

    provider = ForexFactoryHtmlProvider()
    events = provider.fetch_events(start, end, TZNAME)

    assert {event["event"] for event in events} == {"In Range Event", "Middle Event", "Last Event"}
    assert provider.weekly_failures == []


def test_all_weeks_failing_raises_provider_error(monkeypatch):
    start, end = _three_week_range()
    responses = {
        "apr07.2025": _result(SECURITY_HTML),
        "apr14.2025": _result(BLANK_HTML),
        "apr21.2025": RuntimeError("Could not fetch https://www.forexfactory.com/calendar?week=apr21.2025: connection reset"),
    }
    fetcher = FakeFetcher(responses)
    monkeypatch.setattr(html_module, "fetch_url", fetcher)

    provider = ForexFactoryHtmlProvider()
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_events(start, end, TZNAME)

    assert exc_info.value.reason == "all_weeks_failed"
    message = str(exc_info.value)
    assert "all_weeks_failed" in message
    assert "2025-04-07" in message
    assert "2025-04-21" in message

    # All three weeks were attempted and each failure detail is retained.
    assert len(fetcher.calls) == 3
    assert [failure.reason for failure in provider.weekly_failures] == [
        "security_verification",
        "calendar_selector_missing",
        "request_failed",
    ]
    assert [failure.week_start.date().isoformat() for failure in provider.weekly_failures] == [
        "2025-04-07",
        "2025-04-14",
        "2025-04-21",
    ]
    assert "connection reset" in provider.weekly_failures[2].error
    assert all(failure.url for failure in provider.weekly_failures)

    failure_dicts = [failure.as_dict() for failure in provider.weekly_failures]
    assert failure_dicts[0] == {
        "week_start": "2025-04-07",
        "reason": "security_verification",
        "error": failure_dicts[0]["error"],
        "url": failure_dicts[0]["url"],
    }


def test_scrape_incremental_returns_failures_and_merges_successful_weeks(monkeypatch, tmp_path):
    start, end = _three_week_range()
    responses = {
        "apr07.2025": _result(_calendar_html("Mon Apr 7", "Week One Event")),
        "apr14.2025": _result(BLANK_HTML),
        "apr21.2025": _result(_calendar_html("Mon Apr 21", "Week Three Event")),
    }
    monkeypatch.setattr(html_module, "fetch_url", FakeFetcher(responses))

    output = tmp_path / "cache.csv"
    provider = ForexFactoryHtmlProvider()
    failures = scrape_incremental(start, end, str(output), tzname=TZNAME, provider=provider)

    assert len(failures) == 1
    assert failures[0].week_start.date().isoformat() == "2025-04-14"
    assert failures[0].reason == "calendar_selector_missing"

    df = pd.read_csv(output, dtype=str)
    assert set(df["Event"]) == {"Week One Event", "Week Three Event"}


def test_main_partial_failure_prints_summary_and_exits_zero(monkeypatch, tmp_path, capsys):
    start, end = _three_week_range()
    responses = {
        "apr07.2025": _result(_calendar_html("Mon Apr 7", "Week One Event")),
        "apr14.2025": _result(SECURITY_HTML),
        "apr21.2025": _result(_calendar_html("Mon Apr 21", "Week Three Event")),
    }
    monkeypatch.setattr(html_module, "fetch_url", FakeFetcher(responses))

    output = tmp_path / "cache.csv"
    argv = [
        "--start", "2025-04-07",
        "--end", "2025-04-23",
        "--csv", str(output),
        "--tz", TZNAME,
    ]

    assert main(argv) is None

    captured = capsys.readouterr()
    assert "Failed to fetch 1 calendar week(s):" in captured.err
    assert "2025-04-14" in captured.err
    assert "security_verification" in captured.err

    df = pd.read_csv(output, dtype=str)
    assert set(df["Event"]) == {"Week One Event", "Week Three Event"}


def test_main_all_weeks_fail_exits_non_zero(monkeypatch, tmp_path, capsys):
    responses = {
        "apr07.2025": _result(SECURITY_HTML),
        "apr14.2025": _result(SECURITY_HTML),
        "apr21.2025": _result(SECURITY_HTML),
    }
    monkeypatch.setattr(html_module, "fetch_url", FakeFetcher(responses))

    output = tmp_path / "cache.csv"
    argv = [
        "--start", "2025-04-07",
        "--end", "2025-04-23",
        "--csv", str(output),
        "--tz", TZNAME,
    ]

    with pytest.raises(SystemExit) as exc_info:
        main(argv)

    assert exc_info.value.code == 4
    captured = capsys.readouterr()
    assert "all_weeks_failed" in captured.err
    assert "2025-04-07" in captured.err

    # The CSV header is still created, but no event rows are written.
    df = pd.read_csv(output, dtype=str)
    assert df.empty
