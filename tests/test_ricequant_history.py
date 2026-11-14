import logging
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from loguru import logger

from zer0share.storage import DailyPartitionStore
from zer0share.sync.ricequant_history import (
    OUTCOME_EMPTY,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    OUTCOME_SUCCESS,
    RiceQuantHistoryManifest,
    RiceQuantHistoryRunner,
    month_chunks,
    parse_bytes,
)


def test_parse_bytes_supports_gib_suffixes():
    assert parse_bytes("50G") == 50 * 1024**3
    assert parse_bytes("512M") == 512 * 1024**2


def test_month_chunks_split_range():
    assert month_chunks("20260506", "20260621") == [
        ("20260506", "20260531"),
        ("20260601", "20260621"),
    ]


def test_manifest_records_day_success(tmp_path):
    manifest = RiceQuantHistoryManifest(tmp_path / "meta.duckdb")
    manifest.record_day_success(
        trade_date="20260506",
        rows=100,
        symbols=2,
        parquet_size=4096,
        bytes_used_before=10,
        bytes_used_after=20,
        elapsed_seconds=1.5,
    )
    row = manifest.get_day("20260506")
    assert row["status"] == "success"
    assert row["rows"] == 100


# ---------------------------------------------------------------------------
# Helpers for Task-4 runner tests
# ---------------------------------------------------------------------------

@pytest.fixture
def propagate_loguru(caplog):
    """Bridge loguru output into pytest's caplog."""
    handler_id = logger.add(
        lambda msg: caplog.handler.emit(
            logging.LogRecord("loguru", logging.INFO, "", 0, msg, [], None)
        ),
        format="{message}",
    )
    yield caplog
    logger.remove(handler_id)


def _make_runner(tmp_path, pipeline, calendar_returns):
    """Build a RiceQuantHistoryRunner with fake dependencies."""
    manifest = RiceQuantHistoryManifest(tmp_path / "meta.duckdb")

    fake_calendar = MagicMock()
    fake_calendar.get_trading_days.return_value = calendar_returns

    fake_notifier = MagicMock()

    return RiceQuantHistoryRunner(
        pipeline=pipeline,
        manifest=manifest,
        calendar=fake_calendar,
        data_dir=tmp_path,
        notifier=fake_notifier,
    ), manifest


def _write_parquet_partition(data_dir: Path, trade_date: str, rows: int = 1) -> None:
    """Create a non-empty parquet partition for a given trade_date."""
    partition_dir = data_dir / "ricequant" / "stock_minute" / f"date={trade_date}"
    partition_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"symbol": ["000001.XSHG"] * rows, "trade_date": [trade_date] * rows})
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, partition_dir / "data.parquet")


# ---------------------------------------------------------------------------
# Task-4 runner tests
# ---------------------------------------------------------------------------

def test_runner_skips_existing_valid_partition(tmp_path):
    """Runner should skip days whose parquet partition already has rows."""
    _write_parquet_partition(tmp_path, "20260506", rows=1)

    fake_pipeline = MagicMock()
    runner, manifest = _make_runner(tmp_path, fake_pipeline, ["20260506"])

    runner.run("20260506", "20260506")

    # pipeline.run should NOT have been called
    fake_pipeline.run.assert_not_called()

    # manifest should record the day as skipped
    row = manifest.get_day("20260506")
    assert row is not None
    assert row["status"] == "skipped"


def test_runner_logs_each_day_start_and_finish(tmp_path, propagate_loguru):
    """Runner should log '开始同步 YYYYMMDD' and '完成 YYYYMMDD' per day."""
    fake_pipeline = MagicMock()
    fake_pipeline.run.return_value = None
    runner, manifest = _make_runner(tmp_path, fake_pipeline, ["20260506"])

    runner.run("20260506", "20260506")

    log_text = "\n".join(r.getMessage() for r in propagate_loguru.records)
    assert "开始同步 20260506" in log_text
    assert "完成 20260506" in log_text


def test_runner_retries_failed_day_then_records_success(tmp_path):
    """Runner retries on failure; on eventual success records status=success."""
    call_count = 0

    def pipeline_run(table_name, start_date, end_date):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient error")
        # Second call succeeds (parquet already written by first partial attempt
        # or we just don't write anything — the runner should still record success)

    fake_pipeline = MagicMock()
    fake_pipeline.run.side_effect = pipeline_run

    runner, manifest = _make_runner(tmp_path, fake_pipeline, ["20260506"])
    runner.run("20260506", "20260506", retries=3)

    assert fake_pipeline.run.call_count == 2
    row = manifest.get_day("20260506")
    assert row is not None
    assert row["status"] == "success"


def test_runner_suppresses_underlying_daily_pipeline_notifications(tmp_path):
    """History runner should not let per-day sync jobs spam notifications."""
    underlying_notifier = MagicMock()
    runtime = MagicMock()
    runtime.notifier = underlying_notifier

    class FakePipeline:
        def __init__(self):
            self._runtime = runtime
            self.called = False

        def run(self, table_name, start_date, end_date):
            self.called = True
            self._runtime.notifier.send(f"{table_name} 同步完成")

    fake_pipeline = FakePipeline()
    runner, _manifest = _make_runner(tmp_path, fake_pipeline, ["20260506"])

    runner.run("20260506", "20260506")

    assert fake_pipeline.called
    underlying_notifier.send.assert_not_called()
    assert fake_pipeline._runtime.notifier is underlying_notifier


def test_history_script_parser_defaults():
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    from sync_ricequant_history import build_parser
    parser = build_parser()
    args = parser.parse_args(["--start-date", "20160101", "--end-date", "20160131"])
    assert args.chunk == "month"
    assert args.retries == 3


def test_runner_stops_when_quota_exceeded(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    manifest = RiceQuantHistoryManifest(tmp_path / "meta.duckdb")

    call_count = {"n": 0}

    class FakeCalendar:
        def get_trading_days(self, exchange, start, end):
            return ["20260506", "20260507"]

    class FakePipeline:
        def run(self, table_name, start_date, end_date):
            call_count["n"] += 1

    class FakeNotifier:
        def notify_start(self, *a, **kw): pass
        def notify_progress(self, *a, **kw): pass
        def notify_stage_done(self, *a, **kw): pass
        def notify_error(self, *a, **kw): pass
        def send(self, *a, **kw): pass

    quota_calls = [
        (500 * 1024**2, 9 * 1024**3),   # before day 1: used 500M, remaining 9G
        (1 * 1024**3, 9 * 1024**3),     # after day 1: used 1G, remaining 9G (no stop)
        (1 * 1024**3, 9 * 1024**3),     # before day 2: used 1G, remaining 9G
        (2 * 1024**3, 1 * 1024**3),     # after day 2: used 2G, remaining 1G → stop
    ]
    quota_idx = {"i": 0}

    def fake_quota():
        q = quota_calls[quota_idx["i"]]
        quota_idx["i"] += 1
        return q

    runner = RiceQuantHistoryRunner(
        pipeline=FakePipeline(),
        manifest=manifest,
        calendar=FakeCalendar(),
        data_dir=data_dir,
        notifier=FakeNotifier(),
        get_quota=fake_quota,
    )
    runner.run("20260506", "20260507", stop_remaining_below=2 * 1024**3)
    # After day 2 remaining=1G < stop_remaining_below=2G → runner stops
    # Both days were processed (pipeline called twice before stop check triggered after day 2)
    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# Per-day outcome recording
# ---------------------------------------------------------------------------

class _SequencePipeline:
    """Fake pipeline whose per-day behavior is scripted by `behaviors`.

    behaviors maps trade_date -> ("write", df) | ("empty",) | ("raise", msg).
    """

    def __init__(self, data_dir: Path, behaviors: dict):
        self._store = DailyPartitionStore(
            Path(data_dir) / "ricequant" / "stock_minute"
        )
        self._behaviors = behaviors
        self.calls: list[str] = []

    def run(self, table_name, start_date, end_date):
        self.calls.append(start_date)
        behavior = self._behaviors[start_date]
        if behavior[0] == "write":
            self._store.write(start_date, behavior[1])
        elif behavior[0] == "raise":
            raise RuntimeError(behavior[1])
        # "empty": fetch succeeds but produces nothing to persist


def _two_symbol_df(trade_date: str) -> pd.DataFrame:
    return pd.DataFrame({
        "order_book_id": ["000001.XSHG", "600000.XSHG"],
        "trade_date": [trade_date, trade_date],
    })


def test_runner_records_per_day_outcomes_across_mixed_sequence(tmp_path):
    """skip / success / empty / failure / success-after-failure, all auditable."""
    days = ["20260504", "20260505", "20260506", "20260507", "20260508"]

    # Day 1: partition already exists on disk → skipped
    _write_parquet_partition(tmp_path, days[0], rows=1)

    pipeline = _SequencePipeline(tmp_path, {
        days[1]: ("write", _two_symbol_df(days[1])),   # success
        days[2]: ("empty",),                            # empty result
        days[3]: ("raise", "boom"),                     # keeps failing
        days[4]: ("write", _two_symbol_df(days[4])),    # success after failure
    })

    manifest = RiceQuantHistoryManifest(tmp_path / "meta.duckdb")
    calendar = MagicMock()
    calendar.get_trading_days.return_value = days
    notifier = MagicMock()

    outcomes = []
    runner = RiceQuantHistoryRunner(
        pipeline=pipeline,
        manifest=manifest,
        calendar=calendar,
        data_dir=tmp_path,
        notifier=notifier,
        outcome_recorder=outcomes.append,
    )
    runner.run(days[0], days[-1], retries=2)

    # Every trading day produced exactly one outcome, in calendar order —
    # including the days after the failed one.
    assert [o.trade_date for o in outcomes] == days
    assert [o.outcome for o in outcomes] == [
        OUTCOME_SKIPPED, OUTCOME_SUCCESS, OUTCOME_EMPTY,
        OUTCOME_FAILED, OUTCOME_SUCCESS,
    ]

    skipped, success, empty, failed, after = outcomes
    assert skipped.reason == "existing partition"

    assert success.rows == 2
    assert success.symbols == 2
    assert success.parquet_size > 0

    assert empty.rows == 0

    assert "boom" in failed.error

    assert after.rows == 2

    # The failing day was retried per the configured retry count.
    assert pipeline.calls.count(days[3]) == 2
    # The original failure notification is still sent.
    notifier.notify_error.assert_called_once()
    assert notifier.notify_error.call_args[0][0] == "ricequant_history"
    assert days[3] in notifier.notify_error.call_args[0][1]

    # Manifest records agree with the emitted outcomes.
    assert manifest.get_day(days[0])["status"] == "skipped"
    row = manifest.get_day(days[1])
    assert row["status"] == "success"
    assert row["rows"] == 2
    assert row["symbols"] == 2
    row = manifest.get_day(days[2])
    assert row["status"] == "success"  # empty days keep existing manifest rules
    assert row["rows"] == 0
    row = manifest.get_day(days[3])
    assert row["status"] == "failed"
    assert "boom" in row["error"]
    assert manifest.get_day(days[4])["status"] == "success"

    # Range query returns every recorded day in order...
    recorded = manifest.list_days(days[0], days[-1])
    assert [r["trade_date"] for r in recorded] == days
    # ...and can be narrowed to a sub-range.
    assert [r["trade_date"] for r in manifest.list_days(days[3], days[4])] == days[3:]

    # The failed day is locatable as the first day without a terminal record,
    # i.e. where a rerun should resume.
    resume_from = next(
        r["trade_date"] for r in recorded
        if r["status"] not in ("success", "skipped")
    )
    assert resume_from == days[3]


def test_runner_without_outcome_recorder_behaves_as_before(tmp_path):
    """Omitting outcome_recorder: same manifest writes, no extra side effects."""
    pipeline = _SequencePipeline(tmp_path, {
        "20260506": ("write", _two_symbol_df("20260506")),
        "20260507": ("raise", "boom"),
    })

    manifest = RiceQuantHistoryManifest(tmp_path / "meta.duckdb")
    calendar = MagicMock()
    calendar.get_trading_days.return_value = ["20260506", "20260507"]
    notifier = MagicMock()

    runner = RiceQuantHistoryRunner(
        pipeline=pipeline,
        manifest=manifest,
        calendar=calendar,
        data_dir=tmp_path,
        notifier=notifier,
    )
    runner.run("20260506", "20260507", retries=1)

    assert manifest.get_day("20260506")["status"] == "success"
    assert manifest.get_day("20260507")["status"] == "failed"
    notifier.notify_error.assert_called_once()


def test_runner_accepts_recorder_object_with_record_method(tmp_path):
    """The recorder may also be an object exposing record(outcome)."""

    class CollectingRecorder:
        def __init__(self):
            self.outcomes = []

        def record(self, outcome):
            self.outcomes.append(outcome)

    recorder = CollectingRecorder()
    pipeline = _SequencePipeline(tmp_path, {
        "20260506": ("write", _two_symbol_df("20260506")),
    })
    manifest = RiceQuantHistoryManifest(tmp_path / "meta.duckdb")
    calendar = MagicMock()
    calendar.get_trading_days.return_value = ["20260506"]

    runner = RiceQuantHistoryRunner(
        pipeline=pipeline,
        manifest=manifest,
        calendar=calendar,
        data_dir=tmp_path,
        notifier=MagicMock(),
        outcome_recorder=recorder,
    )
    runner.run("20260506", "20260506")

    assert [o.outcome for o in recorder.outcomes] == [OUTCOME_SUCCESS]
