"""
ingest.py  —  Bronze Layer
==========================
Downloads raw GH Archive hourly .json.gz files for a given date range and
uploads them into the S3 Bronze zone (s3a://github-archive-bucket/bronze/).

GH Archive URL pattern:
  https://data.gharchive.org/YYYY-MM-DD-H.json.gz   (H = 0..23)

This module does NOT use Spark for the download step; it uses requests + boto3
so it can run lightweight without spinning up the Spark cluster.  Spark is only
used for reading/writing the actual Iceberg tables downstream.

Per-hour outcome records
------------------------
Every (date, hour) task produces one :class:`HourOutcome` describing its final
status, so a run can be audited hour by hour.  The possible statuses are:

* ``uploaded``         — file downloaded and written to S3 in this run
* ``already_exists``   — object was already present in S3; skipped (idempotent)
* ``not_found``        — source returned HTTP 404; skipped, never a failure
* ``download_failed``  — download raised an exception (reason recorded)
* ``upload_failed``    — S3 upload raised an exception (reason recorded)
* ``simulated``        — dry-run; no network or S3 access attempted

Failures never abort the run: the failing hour is recorded and the next hour
is processed.  Re-runs only skip hours whose S3 object genuinely exists, so a
failed hour is retried on the next run instead of being mistaken for complete.
"""

import gzip
import io
import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Callable, Iterator, List, Optional

import boto3
import requests
import yaml
from botocore.config import Config
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)
from tqdm import tqdm

logger = logging.getLogger(__name__)


# =============================================================================
# Outcome model
# =============================================================================

class IngestStatus(str, Enum):
    """Final status of a single (date, hour) ingest task."""

    UPLOADED = "uploaded"
    ALREADY_EXISTS = "already_exists"
    NOT_FOUND = "not_found"
    DOWNLOAD_FAILED = "download_failed"
    UPLOAD_FAILED = "upload_failed"
    SIMULATED = "simulated"


@dataclass
class HourOutcome:
    """
    Auditable end-state of one (date, hour) task.

    Attributes
    ----------
    day, hour:
        Task coordinates.
    status:
        One of :class:`IngestStatus`.
    s3_key / url:
        Object key / source URL the task refers to.
    reason:
        Human-readable explanation, populated for failures and dry-run
        simulations; ``None`` for normal success/skip paths.
    event_count:
        Number of JSON-line events counted in the downloaded payload
        (-1 when the payload could not be parsed, 0 when no payload was
        processed, e.g. dry-run).
    """

    day: date
    hour: int
    status: IngestStatus
    s3_key: str
    url: str
    reason: Optional[str] = None
    event_count: int = 0

    @property
    def date_hour(self) -> str:
        return f"{self.day.isoformat()}-{self.hour}"

    def to_dict(self) -> dict:
        return {
            "date": self.day.isoformat(),
            "hour": self.hour,
            "date_hour": self.date_hour,
            "status": self.status.value,
            "s3_key": self.s3_key,
            "url": self.url,
            "reason": self.reason,
            "event_count": self.event_count,
        }


# =============================================================================
# Config helpers
# =============================================================================

def _load_config(config_path: Optional[str] = None) -> dict:
    if config_path is None:
        base = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        config_path = os.path.join(base, "config", "config.yaml")
    with open(config_path) as fh:
        return yaml.safe_load(fh)


def _s3_client(cfg: dict):
    """Return a boto3 S3 client pointed at LocalStack."""
    aws = cfg["aws"]
    return boto3.client(
        "s3",
        endpoint_url=aws["endpoint_url"],
        aws_access_key_id=aws["access_key_id"],
        aws_secret_access_key=aws["secret_access_key"],
        region_name=aws["region"],
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


# =============================================================================
# Date range generator
# =============================================================================

def _date_range(start: date, end: date) -> Iterator[date]:
    """Yields every date from start (inclusive) to end (inclusive)."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


# =============================================================================
# Download & upload helpers
# =============================================================================

@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(5),
    retry=retry_if_exception_type((requests.RequestException, IOError)),
    reraise=True,
)
def _download_hour(url: str, timeout: int) -> Optional[bytes]:
    """
    Download a single GH Archive hourly file.
    Returns raw gzip bytes, or None if the file does not exist (404).
    """
    logger.debug("Downloading %s", url)
    resp = requests.get(url, timeout=timeout, stream=True)
    if resp.status_code == 404:
        logger.warning("File not found (404): %s — skipping.", url)
        return None
    resp.raise_for_status()

    chunks = []
    for chunk in resp.iter_content(chunk_size=65536):
        if chunk:
            chunks.append(chunk)
    return b"".join(chunks)


def _s3_key_for(day: date, hour: int) -> str:
    """Return the S3 key for a given day/hour."""
    return f"bronze/{day.strftime('%Y/%m/%d')}/{day.strftime('%Y-%m-%d')}-{hour}.json.gz"


def _already_uploaded(s3, bucket: str, key: str) -> bool:
    """Check if a file already exists in S3 to enable idempotent re-runs."""
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except s3.exceptions.ClientError:
        return False
    except Exception:
        return False


def _count_events_in_gz(data: bytes) -> int:
    """Count JSON lines (events) in a gzip byte payload."""
    try:
        with gzip.open(io.BytesIO(data)) as gz:
            return sum(1 for _ in gz)
    except Exception:
        return -1


# =============================================================================
# Public API
# =============================================================================

class BronzeIngester:
    """
    Orchestrates downloading GH Archive files and uploading them to S3 Bronze.

    Parameters
    ----------
    config_path : str, optional
        Path to config.yaml.
    dry_run : bool
        If True, every hour is reported as a *simulated* outcome without any
        network, S3 or filesystem access — no S3 client is even constructed.
    s3_client : optional
        Inject an S3 client (e.g. a fake in tests).  Ignored in dry-run mode.
    downloader : callable, optional
        Inject ``downloader(url, timeout) -> bytes | None`` (e.g. a fake in
        tests); defaults to the tenacity-retrying real HTTP downloader.
        Never called in dry-run mode.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        dry_run: bool = False,
        s3_client=None,
        downloader: Optional[Callable[[str, int], Optional[bytes]]] = None,
    ):
        self.cfg = _load_config(config_path)
        self.dry_run = dry_run
        self.bucket = self.cfg["aws"]["bucket"]
        self.base_url = self.cfg["gharchive"]["base_url"]
        self.hours = self.cfg["gharchive"]["hours_per_day"]
        self.timeout = self.cfg["gharchive"]["download_timeout_seconds"]
        self.downloader = downloader or _download_hour

        # Dry-run must never touch S3: do not construct a client at all.
        if self.dry_run:
            self.s3 = None
        else:
            self.s3 = s3_client if s3_client is not None else _s3_client(self.cfg)

        logger.info(
            "BronzeIngester initialised. Bucket=%s, DryRun=%s", self.bucket, dry_run
        )

    # ------------------------------------------------------------------ #
    # Per-hour processing
    # ------------------------------------------------------------------ #

    def _process_hour(self, day: date, hour: int) -> HourOutcome:
        """
        Process a single (day, hour) task and return its auditable outcome.

        Any download/upload exception is caught and converted into a failure
        outcome; it never propagates, so subsequent hours keep processing.
        """
        key = _s3_key_for(day, hour)
        url = f"{self.base_url}/{day.strftime('%Y-%m-%d')}-{hour}.json.gz"

        # ---- Dry-run: no network, no S3, no writes of any kind ----
        if self.dry_run:
            logger.info("[DRY RUN] Would download and upload %s", key)
            return HourOutcome(
                day=day,
                hour=hour,
                status=IngestStatus.SIMULATED,
                s3_key=key,
                url=url,
                reason="dry-run: no download or S3 operation attempted",
            )

        # ---- Idempotency: only an object that really exists skips ----
        if _already_uploaded(self.s3, self.bucket, key):
            logger.debug("Already in S3, skipping: %s", key)
            return HourOutcome(
                day=day,
                hour=hour,
                status=IngestStatus.ALREADY_EXISTS,
                s3_key=key,
                url=url,
            )

        # ---- Download (404 is a skip, never a failure) ----
        try:
            data = self.downloader(url, self.timeout)
        except Exception as exc:
            reason = f"download error: {type(exc).__name__}: {exc}"
            logger.error("Failed to download %s: %s", url, reason)
            return HourOutcome(
                day=day,
                hour=hour,
                status=IngestStatus.DOWNLOAD_FAILED,
                s3_key=key,
                url=url,
                reason=reason,
            )

        if data is None:
            reason = "source returned HTTP 404 (file not available)"
            logger.warning("404 from source for %s — skipping.", url)
            return HourOutcome(
                day=day,
                hour=hour,
                status=IngestStatus.NOT_FOUND,
                s3_key=key,
                url=url,
                reason=reason,
            )

        event_count = _count_events_in_gz(data)

        # ---- Upload ----
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType="application/gzip",
            )
        except Exception as exc:
            reason = f"upload error: {type(exc).__name__}: {exc}"
            logger.error("Upload failed for %s: %s", key, reason)
            return HourOutcome(
                day=day,
                hour=hour,
                status=IngestStatus.UPLOAD_FAILED,
                s3_key=key,
                url=url,
                reason=reason,
                event_count=event_count,
            )

        logger.debug(
            "Uploaded s3://%s/%s (%d events)", self.bucket, key, event_count
        )
        return HourOutcome(
            day=day,
            hour=hour,
            status=IngestStatus.UPLOADED,
            s3_key=key,
            url=url,
            event_count=event_count,
        )

    # ------------------------------------------------------------------ #
    # Full-range run
    # ------------------------------------------------------------------ #

    def run(self, start_date: date, end_date: date) -> dict:
        """
        Download GH Archive files for every hour in [start_date, end_date] and
        upload to S3 Bronze.

        Returns
        -------
        dict
            Aggregate statistics plus the per-hour auditable outcomes:

            ``downloaded``  — uploaded this run + dry-run simulations
            ``skipped``     — already-existing S3 objects + source 404s
            ``failed``      — download failures + upload failures
            ``total_events``— events counted inside uploaded payloads
            ``total_tasks`` — number of (date, hour) tasks processed
            ``outcomes``    — counts keyed by status value
            ``results``     — list of :class:`HourOutcome` records (dicts)
        """
        logger.info(
            "Starting Bronze ingest: %s → %s (%d hours/day)",
            start_date,
            end_date,
            len(self.hours),
        )

        dates = list(_date_range(start_date, end_date))
        total_tasks = len(dates) * len(self.hours)

        outcomes: List[HourOutcome] = []
        with tqdm(total=total_tasks, desc="Bronze Ingest", unit="file") as pbar:
            for day in dates:
                for hour in self.hours:
                    outcome = self._process_hour(day, hour)
                    outcomes.append(outcome)
                    pbar.set_postfix(
                        status=outcome.status.value,
                        failed=sum(
                            1
                            for o in outcomes
                            if o.status
                            in (
                                IngestStatus.DOWNLOAD_FAILED,
                                IngestStatus.UPLOAD_FAILED,
                            )
                        ),
                    )
                    pbar.update(1)

        stats = self._summarize(outcomes)

        logger.info(
            "Bronze ingest complete. Downloaded=%d, Skipped=%d, Failed=%d, "
            "TotalEvents=%d",
            stats["downloaded"],
            stats["skipped"],
            stats["failed"],
            stats["total_events"],
        )
        logger.info("Per-hour outcomes: %s", stats["outcomes"])
        return stats

    @staticmethod
    def _summarize(outcomes: List[HourOutcome]) -> dict:
        """
        Aggregate per-hour outcomes into the run-level statistics.

        Legacy counters are kept (main.py and other callers depend on them)
        and are derived from the same status records, so the two views can
        never disagree:

        * downloaded = uploaded + simulated
        * skipped    = already_exists + not_found   (404 is not a failure)
        * failed     = download_failed + upload_failed
        """
        outcome_counts = {status.value: 0 for status in IngestStatus}
        total_events = 0
        for outcome in outcomes:
            outcome_counts[outcome.status.value] += 1
            if outcome.status == IngestStatus.UPLOADED:
                total_events += max(outcome.event_count, 0)

        downloaded = (
            outcome_counts[IngestStatus.UPLOADED.value]
            + outcome_counts[IngestStatus.SIMULATED.value]
        )
        skipped = (
            outcome_counts[IngestStatus.ALREADY_EXISTS.value]
            + outcome_counts[IngestStatus.NOT_FOUND.value]
        )
        failed = (
            outcome_counts[IngestStatus.DOWNLOAD_FAILED.value]
            + outcome_counts[IngestStatus.UPLOAD_FAILED.value]
        )

        return {
            "downloaded": downloaded,
            "skipped": skipped,
            "failed": failed,
            "total_events": total_events,
            "total_tasks": len(outcomes),
            "outcomes": outcome_counts,
            "results": [outcome.to_dict() for outcome in outcomes],
        }

    def list_bronze_files(self, prefix: str = "bronze/") -> list:
        """List all files in the Bronze S3 zone."""
        paginator = self.s3.get_paginator("list_objects_v2")
        keys = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys
