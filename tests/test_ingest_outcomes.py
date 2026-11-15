"""
Offline tests for the Bronze ingester per-hour outcome model.

No real network or S3 is involved: a fake downloader stands in for
``requests`` and a fake in-memory S3 client stands in for boto3.
"""

import gzip
import io
from datetime import date, timedelta

import pytest
import yaml

from src.pipeline.ingest import BronzeIngester, IngestStatus, _s3_key_for


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

def gz_payload(lines):
    """Build an in-memory .json.gz payload from JSON-line strings."""
    buf = io.BytesIO()
    with gzip.open(buf, "wb") as gz:
        gz.write(("\n".join(lines) + "\n").encode("utf-8"))
    return buf.getvalue()


class FakeClientError(Exception):
    """Stands in for botocore.exceptions.ClientError."""


class FakeS3:
    """In-memory S3 client supporting head_object / put_object only."""

    class _Exceptions:
        ClientError = FakeClientError

    exceptions = _Exceptions()

    def __init__(self, existing=None, fail_put_keys=()):
        self.objects = dict(existing or {})
        self.fail_put_keys = set(fail_put_keys)
        self.head_calls = []
        self.put_calls = []

    def head_object(self, Bucket, Key):
        self.head_calls.append(Key)
        if Key in self.objects:
            return {"ContentLength": len(self.objects[Key])}
        raise FakeClientError({"Error": {"Code": "404", "Message": "Not Found"}},
                              "HeadObject")

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.put_calls.append((Key, Body, ContentType))
        if Key in self.fail_put_keys:
            raise FakeClientError({"Error": {"Code": "500", "Message": "boom"}},
                                  "PutObject")
        self.objects[Key] = Body
        return {"ETag": '"fake-etag"'}


class FakeDownloadError(Exception):
    pass


class FakeDownloader:
    """
    Configurable downloader(url, timeout) -> bytes | None.

    * ``not_found`` URLs return None (HTTP 404 semantics)
    * ``failing`` URLs raise FakeDownloadError
    * every other URL must be present in ``payloads``
    """

    def __init__(self, payloads=None, not_found=(), failing=()):
        self.payloads = dict(payloads or {})
        self.not_found = set(not_found)
        self.failing = set(failing)
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        if url in self.failing:
            raise FakeDownloadError(f"simulated download failure: {url}")
        if url in self.not_found:
            return None
        return self.payloads[url]


BASE_URL = "https://data.example.invalid"
BUCKET = "fake-bucket"


def write_config(tmp_path, hours):
    cfg = {
        "aws": {
            "endpoint_url": "http://s3.example.invalid",
            "region": "us-east-1",
            "access_key_id": "test",
            "secret_access_key": "test",
            "bucket": BUCKET,
        },
        "gharchive": {
            "base_url": BASE_URL,
            "hours_per_day": list(hours),
            "download_timeout_seconds": 5,
            "max_retries": 3,
            "retry_backoff_seconds": 5,
        },
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(cfg_path)


def make_ingester(tmp_path, hours, *, dry_run=False, s3=None, downloader=None):
    return BronzeIngester(
        config_path=write_config(tmp_path, hours),
        dry_run=dry_run,
        s3_client=s3,
        downloader=downloader,
    )


def url_for(day, hour):
    return f"{BASE_URL}/{day.strftime('%Y-%m-%d')}-{hour}.json.gz"


def index_by_hour(results):
    return {row["hour"]: row for row in results}


# ---------------------------------------------------------------------------
# 1. Status mapping — every non-dry-run outcome, stats stay consistent
# ---------------------------------------------------------------------------

def test_status_mapping_and_stats_consistency(tmp_path):
    day = date(2024, 1, 15)
    hours = [10, 11, 12, 13, 14, 15]

    existing_key = _s3_key_for(day, 10)
    fail_upload_key = _s3_key_for(day, 14)
    payload_11 = gz_payload(['{"id": 1}', '{"id": 2}', '{"id": 3}'])
    payload_14 = gz_payload(['{"id": 4}'])
    payload_15 = gz_payload(['{"id": 5}', '{"id": 6}'])

    s3 = FakeS3(
        existing={existing_key: b"pre-existing-bytes"},
        fail_put_keys={fail_upload_key},
    )
    downloader = FakeDownloader(
        payloads={
            url_for(day, 11): payload_11,
            url_for(day, 14): payload_14,
            url_for(day, 15): payload_15,
        },
        not_found={url_for(day, 12)},
        failing={url_for(day, 13)},
    )

    ingester = make_ingester(tmp_path, hours, s3=s3, downloader=downloader)
    stats = ingester.run(day, day)

    # Legacy counters must remain present and agree with the outcome records.
    assert stats["downloaded"] == 2          # hours 11, 15 uploaded
    assert stats["skipped"] == 2             # hour 10 already exists, 12 is 404
    assert stats["failed"] == 2              # hour 13 download, 14 upload
    assert stats["total_events"] == 5        # 3 events + 2 events
    assert stats["total_tasks"] == 6
    assert len(stats["results"]) == 6

    assert stats["outcomes"] == {
        "uploaded": 2,
        "already_exists": 1,
        "not_found": 1,
        "download_failed": 1,
        "upload_failed": 1,
        "simulated": 0,
    }

    by_hour = index_by_hour(stats["results"])
    assert by_hour[10]["status"] == IngestStatus.ALREADY_EXISTS.value
    assert by_hour[11]["status"] == IngestStatus.UPLOADED.value
    assert by_hour[12]["status"] == IngestStatus.NOT_FOUND.value
    assert by_hour[13]["status"] == IngestStatus.DOWNLOAD_FAILED.value
    assert by_hour[14]["status"] == IngestStatus.UPLOAD_FAILED.value
    assert by_hour[15]["status"] == IngestStatus.UPLOADED.value

    # Records carry full audit coordinates.
    for row in stats["results"]:
        assert row["date"] == day.isoformat()
        assert row["date_hour"] == f"{day.isoformat()}-{row['hour']}"
        assert row["s3_key"] == _s3_key_for(day, row["hour"])
        assert row["url"] == url_for(day, row["hour"])

    # Event counts travel with the record (including failed uploads).
    assert by_hour[11]["event_count"] == 3
    assert by_hour[15]["event_count"] == 2
    assert by_hour[14]["event_count"] == 1

    # Failures carry human-readable reasons mentioning the exception type.
    assert "FakeDownloadError" in by_hour[13]["reason"]
    assert "FakeClientError" in by_hour[14]["reason"]
    assert "404" in by_hour[12]["reason"]
    assert by_hour[11]["reason"] is None

    # Upload actually happened with gzip content type; failed upload left
    # nothing behind in S3.
    attempted_put_keys = {call[0] for call in s3.put_calls}
    assert attempted_put_keys == {
        _s3_key_for(day, 11), fail_upload_key, _s3_key_for(day, 15)
    }
    assert all(call[2] == "application/gzip" for call in s3.put_calls)
    assert set(s3.objects) == {
        existing_key, _s3_key_for(day, 11), _s3_key_for(day, 15)
    }
    assert s3.objects[_s3_key_for(day, 11)] == payload_11
    assert fail_upload_key not in s3.objects
    # The pre-existing object was only inspected, never overwritten.
    assert s3.objects[existing_key] == b"pre-existing-bytes"


# ---------------------------------------------------------------------------
# 2. Dry-run: no network, no S3 client, no writes — everything simulated
# ---------------------------------------------------------------------------

def test_dry_run_does_not_touch_network_or_s3(tmp_path):
    day = date(2024, 1, 15)
    hours = [10, 11]
    s3 = FakeS3(existing={_s3_key_for(day, 10): b"x"})
    downloader = FakeDownloader(
        payloads={url_for(day, h): gz_payload(["{}"]) for h in hours}
    )

    ingester = make_ingester(
        tmp_path, hours, dry_run=True, s3=s3, downloader=downloader
    )
    assert ingester.s3 is None  # not even constructed

    files_before = set(p.name for p in tmp_path.iterdir())
    stats = ingester.run(day, day)
    files_after = set(p.name for p in tmp_path.iterdir())
    assert files_before == files_after  # nothing written to disk either

    assert s3.head_calls == []
    assert s3.put_calls == []
    assert downloader.calls == []

    assert all(
        row["status"] == IngestStatus.SIMULATED.value for row in stats["results"]
    )
    assert stats["outcomes"]["simulated"] == 2
    assert stats["downloaded"] == 2       # simulations counted as downloaded
    assert stats["skipped"] == 0
    assert stats["failed"] == 0
    assert stats["total_events"] == 0
    assert all(row["reason"] for row in stats["results"])


# ---------------------------------------------------------------------------
# 3. A failing hour must not abort the remaining hours
# ---------------------------------------------------------------------------

def test_failures_are_isolated_per_hour(tmp_path):
    day = date(2024, 1, 15)
    hours = [10, 11, 12, 13]
    s3 = FakeS3(fail_put_keys={_s3_key_for(day, 12)})
    downloader = FakeDownloader(
        payloads={url_for(day, h): gz_payload(["{}"]) for h in hours},
        failing={url_for(day, 11)},
    )

    ingester = make_ingester(tmp_path, hours, s3=s3, downloader=downloader)
    stats = ingester.run(day, day)

    assert stats["total_tasks"] == 4          # every hour was still processed
    assert [row["hour"] for row in stats["results"]] == hours
    by_hour = index_by_hour(stats["results"])
    assert by_hour[10]["status"] == IngestStatus.UPLOADED.value
    assert by_hour[11]["status"] == IngestStatus.DOWNLOAD_FAILED.value
    assert by_hour[12]["status"] == IngestStatus.UPLOAD_FAILED.value
    assert by_hour[13]["status"] == IngestStatus.UPLOADED.value
    assert stats["failed"] == 2
    assert stats["downloaded"] == 2


# ---------------------------------------------------------------------------
# 4. Re-run semantics: failed hours retry; completed hours skip as "exists"
# ---------------------------------------------------------------------------

def test_failed_hours_are_retried_on_rerun(tmp_path):
    day = date(2024, 1, 15)
    hours = [10, 11, 12]
    key_10, key_11, key_12 = (_s3_key_for(day, h) for h in hours)

    s3 = FakeS3()  # empty bucket on first run
    broken_downloader = FakeDownloader(
        payloads={url_for(day, 10): gz_payload(['{"a": 1}'])},
        failing={url_for(day, 11), url_for(day, 12)},
    )
    ingester = make_ingester(tmp_path, hours, s3=s3, downloader=broken_downloader)
    first = ingester.run(day, day)

    by_hour = index_by_hour(first["results"])
    assert by_hour[10]["status"] == IngestStatus.UPLOADED.value
    assert by_hour[11]["status"] == IngestStatus.DOWNLOAD_FAILED.value
    assert by_hour[12]["status"] == IngestStatus.DOWNLOAD_FAILED.value
    # Failures must leave no residual S3 state that could fake completion.
    assert set(s3.objects) == {key_10}

    # Re-run: hour 10 now exists in S3, the other two download fine.
    fixed_downloader = FakeDownloader(
        payloads={
            url_for(day, 11): gz_payload(['{"b": 1}']),
            url_for(day, 12): gz_payload(['{"c": 1}']),
        }
    )
    rerun_ingester = make_ingester(
        tmp_path, hours, s3=s3, downloader=fixed_downloader
    )
    second = rerun_ingester.run(day, day)
    by_hour = index_by_hour(second["results"])

    assert by_hour[10]["status"] == IngestStatus.ALREADY_EXISTS.value
    assert by_hour[11]["status"] == IngestStatus.UPLOADED.value
    assert by_hour[12]["status"] == IngestStatus.UPLOADED.value
    assert second["failed"] == 0
    assert second["skipped"] == 1
    assert second["downloaded"] == 2

    # Hour 10 was never re-downloaded or re-uploaded; 11/12 were retried.
    assert fixed_downloader.calls == [url_for(day, 11), url_for(day, 12)]
    assert {call[0] for call in s3.put_calls} == {key_10, key_11, key_12}

    # A third run with everything present only records "already_exists".
    third = make_ingester(
        tmp_path, hours, s3=s3, downloader=FakeDownloader()
    ).run(day, day)
    assert all(
        row["status"] == IngestStatus.ALREADY_EXISTS.value for row in third["results"]
    )
    assert third["downloaded"] == 0
    assert third["skipped"] == 3


def test_upload_failure_is_retried_and_then_skips(tmp_path):
    day = date(2024, 1, 15)
    hours = [10]
    key = _s3_key_for(day, 10)
    payload = gz_payload(['{"id": 1}'])

    s3 = FakeS3(fail_put_keys={key})
    downloader = FakeDownloader(payloads={url_for(day, 10): payload})
    first = make_ingester(tmp_path, hours, s3=s3, downloader=downloader).run(day, day)
    assert first["results"][0]["status"] == IngestStatus.UPLOAD_FAILED.value
    assert key not in s3.objects  # no partial object left behind

    # Re-run with a healthy S3: the hour is retried and succeeds.
    s3.fail_put_keys.clear()
    second = make_ingester(tmp_path, hours, s3=s3, downloader=downloader).run(day, day)
    assert second["results"][0]["status"] == IngestStatus.UPLOADED.value
    assert s3.objects[key] == payload

    # Further runs treat the object as already-existing (never "uploaded").
    third = make_ingester(tmp_path, hours, s3=s3, downloader=downloader).run(day, day)
    assert third["results"][0]["status"] == IngestStatus.ALREADY_EXISTS.value
    assert third["downloaded"] == 0
    assert third["skipped"] == 1


# ---------------------------------------------------------------------------
# 5. 404 stays a skip (not a failure) and is re-evaluated on every run
# ---------------------------------------------------------------------------

def test_not_found_is_skipped_and_never_cached_as_complete(tmp_path):
    day = date(2024, 1, 15)
    hours = [10, 11]
    s3 = FakeS3()
    downloader = FakeDownloader(
        payloads={url_for(day, 11): gz_payload(["{}"])},
        not_found={url_for(day, 10)},
    )

    first = make_ingester(tmp_path, hours, s3=s3, downloader=downloader).run(day, day)
    by_hour = index_by_hour(first["results"])
    assert by_hour[10]["status"] == IngestStatus.NOT_FOUND.value
    assert by_hour[11]["status"] == IngestStatus.UPLOADED.value
    assert first["failed"] == 0
    assert first["skipped"] == 1

    # Re-run: the 404 hour is probed again (no local "done" marker), while the
    # uploaded hour is skipped via the S3 existence check.
    second = make_ingester(tmp_path, hours, s3=s3, downloader=downloader).run(day, day)
    by_hour = index_by_hour(second["results"])
    assert by_hour[10]["status"] == IngestStatus.NOT_FOUND.value
    assert by_hour[11]["status"] == IngestStatus.ALREADY_EXISTS.value
    assert second["failed"] == 0
    assert url_for(day, 10) in downloader.calls  # retried, not assumed done


# ---------------------------------------------------------------------------
# 6. Multi-day runs produce one record per (date, hour)
# ---------------------------------------------------------------------------

def test_multi_date_run_records_every_date_hour(tmp_path):
    start = date(2024, 1, 15)
    end = start + timedelta(days=2)
    hours = [10, 11]
    s3 = FakeS3()
    downloader = FakeDownloader(
        payloads={
            url_for(start + timedelta(days=d), h): gz_payload(["{}"])
            for d in range(3)
            for h in hours
        }
    )

    stats = make_ingester(tmp_path, hours, s3=s3, downloader=downloader).run(
        start, end
    )

    assert stats["total_tasks"] == 6
    assert len(stats["results"]) == 6
    expected = {
        (start + timedelta(days=d)).isoformat(): set(hours) for d in range(3)
    }
    seen = {}
    for row in stats["results"]:
        seen.setdefault(row["date"], set()).add(row["hour"])
        assert row["status"] == IngestStatus.UPLOADED.value
    assert seen == expected
    assert stats["downloaded"] == 6
