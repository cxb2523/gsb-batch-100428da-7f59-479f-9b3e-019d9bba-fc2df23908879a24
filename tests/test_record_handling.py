import datetime
from pathlib import Path

import pytest

import task
from task import RecordHandler, RunOutcome, main, run_engine


class FakePage:
    """Minimal stand-in for any browser wrapper object."""

    def __init__(self):
        self.screenshots = []

    def screenshot(self, path=None, filename=None):
        target = path if path is not None else filename
        self.screenshots.append(str(target))
        # Deliberately do not create the file: the test only cares that the
        # handler asked for a screenshot with the documented name.
        return target


@pytest.fixture
def record_dir(tmp_path):
    return tmp_path


def make_today():
    return datetime.date.today().strftime("%d%m%Y")


def expected_screenshot(engine, task_time):
    return f"record_{engine}_{make_today()}_time{task_time}ms.png"


def screenshot_names(page):
    return [Path(name).name for name in page.screenshots]


def test_new_record_updates_file_and_takes_screenshot(record_dir, capsys):
    engine = "demo-engine"
    (record_dir / f"record_time_{engine}").write_text("200")

    rh = RecordHandler(record_dir)
    page = FakePage()
    rh.set_engine(engine)
    outcome = rh.check_for_new_record(page, "You completed the challenge in 150 milliseconds")

    assert outcome == RunOutcome.NEW_RECORD
    assert (record_dir / f"record_time_{engine}").read_text() == "150"
    assert page.screenshots and screenshot_names(page) == [expected_screenshot(engine, 150)]
    printed = capsys.readouterr().out
    assert "150" in printed


def test_no_new_record_leaves_file_and_screenshot_alone(record_dir, capsys):
    engine = "demo-engine"
    record_file = record_dir / f"record_time_{engine}"
    record_file.write_text("100")

    rh = RecordHandler(record_dir)
    page = FakePage()
    rh.set_engine(engine)
    outcome = rh.check_for_new_record(page, "You completed the challenge in 250 milliseconds")

    assert outcome == RunOutcome.NO_NEW_RECORD
    assert record_file.read_text() == "100"
    assert page.screenshots == []
    printed = capsys.readouterr().out
    assert engine in printed
    assert "250" in printed


@pytest.mark.parametrize(
    "result",
    [
        "Something went wrong on the page",
        "",
        None,
        "You completed the challenge",
        "no timing information here",
    ],
)
def test_unparseable_result_is_reported_without_side_effects(record_dir, capsys, result):
    engine = "demo-engine"
    record_file = record_dir / f"record_time_{engine}"
    record_file.write_text("100")

    rh = RecordHandler(record_dir)
    page = FakePage()
    rh.set_engine(engine)
    outcome = rh.check_for_new_record(page, result)

    assert outcome == RunOutcome.UNPARSEABLE_RESULT
    assert record_file.read_text() == "100"
    assert page.screenshots == []
    printed = capsys.readouterr().out
    assert engine in printed
    assert "parse" in printed.lower()


def test_missing_record_file_is_treated_as_no_history(record_dir, capsys):
    engine = "demo-engine"

    rh = RecordHandler(record_dir)
    page = FakePage()
    rh.set_engine(engine)
    outcome = rh.check_for_new_record(page, "You completed the challenge in 400 milliseconds")

    assert outcome == RunOutcome.NEW_RECORD
    assert (record_dir / f"record_time_{engine}").read_text() == "400"
    assert page.screenshots and screenshot_names(page) == [expected_screenshot(engine, 400)]
    printed = capsys.readouterr().out
    assert engine in printed


def test_empty_record_file_is_treated_as_no_history(record_dir):
    engine = "demo-engine"
    (record_dir / f"record_time_{engine}").write_text("   \n")

    rh = RecordHandler(record_dir)
    rh.set_engine(engine)
    assert rh.record_time is None

    outcome = rh.check_for_new_record(FakePage(), "in 500 milliseconds")
    assert outcome == RunOutcome.NEW_RECORD
    assert (record_dir / f"record_time_{engine}").read_text() == "500"


def test_page_error_is_reported_without_side_effects(record_dir, monkeypatch, capsys):
    engine = "demo-engine"
    record_file = record_dir / f"record_time_{engine}"
    record_file.write_text("100")

    def exploding_engine(rh, sheet):
        raise RuntimeError("browser crashed")

    fake_engines = {engine: "exploding_engine"}
    monkeypatch.setattr(task, "engines", fake_engines)
    monkeypatch.setattr(task, "exploding_engine", exploding_engine, raising=False)

    outcome = run_engine(engine, [], record_dir)

    assert outcome == RunOutcome.PAGE_ERROR
    assert record_file.read_text() == "100"
    printed = capsys.readouterr().out
    assert engine in printed
    assert "browser crashed" in printed
    assert not list(record_dir.glob("record_*.png"))


def test_main_continues_after_a_failing_engine(record_dir, monkeypatch, capsys):
    (record_dir / "record_time_ok-engine").write_text("100")
    (record_dir / "record_time_bad-engine").write_text("100")

    def ok_engine(rh, sheet):
        assert rh.check_for_new_record(FakePage(), "in 90 milliseconds") == RunOutcome.NEW_RECORD
        return RunOutcome.NEW_RECORD

    def bad_engine(rh, sheet):
        raise RuntimeError("page did not load")

    fake_engines = {"ok-engine": "ok_engine", "bad-engine": "bad_engine"}
    monkeypatch.setattr(task, "engines", fake_engines)
    monkeypatch.setattr(task, "ok_engine", ok_engine, raising=False)
    monkeypatch.setattr(task, "bad_engine", bad_engine, raising=False)

    outcomes = main(None, base_dir=record_dir, sheet=[])

    assert outcomes["ok-engine"] == RunOutcome.NEW_RECORD
    assert outcomes["bad-engine"] == RunOutcome.PAGE_ERROR
    assert (record_dir / "record_time_ok-engine").read_text() == "90"
    assert (record_dir / "record_time_bad-engine").read_text() == "100"


def test_failure_while_tearing_down_keeps_decided_outcome(record_dir, monkeypatch, capsys):
    """A browser close error after a record was saved must not turn it into PAGE_ERROR."""
    engine = "demo-engine"
    record_file = record_dir / f"record_time_{engine}"
    record_file.write_text("200")

    def engine_then_close_error(rh, sheet):
        rh.check_for_new_record(FakePage(), "in 150 milliseconds")
        raise RuntimeError("close() failed")

    fake_engines = {engine: "engine_then_close_error"}
    monkeypatch.setattr(task, "engines", fake_engines)
    monkeypatch.setattr(task, "engine_then_close_error", engine_then_close_error, raising=False)

    outcome = run_engine(engine, [], record_dir)

    assert outcome == RunOutcome.NEW_RECORD
    assert record_file.read_text() == "150"
    assert "close() failed" in capsys.readouterr().out


def test_parse_result_time_patterns():
    assert RecordHandler.parse_result_time("in 123 milliseconds") == 123
    assert RecordHandler.parse_result_time("in 1,234 milliseconds") == 1234
    assert RecordHandler.parse_result_time("completed in 47 ms") == 47
    assert RecordHandler.parse_result_time("nothing to see") is None
