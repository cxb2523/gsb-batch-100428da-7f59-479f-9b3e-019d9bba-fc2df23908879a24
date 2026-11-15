"""Helpers for the Amazon Textract invoice batch.

The keywords in ``resources/invoices.resource`` keep one mutable job record
per S3 file. This library owns the record bookkeeping, the construction of the
Excel rows and the per-file processing report.

Keeping the bookkeeping in Python keeps the Robot code readable and avoids
having to inspect Robot Framework objects (dictionaries, document models,
invoice records) inside ``IF``/``Evaluate`` expressions.
"""

import csv
import os

# Statuses a job can still move on from.
PROCESSING_STATUSES = ("SUBMITTED", "IN_PROGRESS", "PENDING")
# Statuses which are final for a job.
FINAL_STATUSES = ("SUCCEEDED", "FAILED", "TIMEOUT")

INVOICE_COLUMNS = (
    "To",
    "Invoice Number",
    "Order Number",
    "Invoice Date",
    "Due Date",
    "Balance Due",
)

# Excel column name -> invoice model key.
INVOICE_FIELDS = (
    ("To", "to"),
    ("Invoice Number", "invoice_number"),
    ("Order Number", "order_number"),
    ("Invoice Date", "invoice_date"),
    ("Due Date", "due_date"),
    ("Balance Due", "balance_due"),
)

OUTCOME_COLUMNS = (
    "Filename",
    "Status",
    "Reason",
    "Cleanup Status",
    "Cleanup Reason",
)

DEFAULT_TIMEOUT_REASON = "Processing did not finish before the cleanup stage"


class InvoiceBatch:
    """Keyword library with the invoice batch bookkeeping helpers."""

    ROBOT_LIBRARY_SCOPE = "GLOBAL"
    ROBOT_LIBRARY_VERSION = "1.0"

    # ------------------------------------------------------------------
    # Job records
    # ------------------------------------------------------------------
    def create_job_record(self, filename, job_id="", status="SUBMITTED", reason=""):
        """Create a job record for a single S3 file."""
        return {
            "filename": self._text(filename),
            "job_id": self._text(job_id),
            "status": self._text(status),
            "reason": self._text(reason),
            "last_status": "",
            "last_error": "",
            "invoices": None,
            "results_saved": True,
            "cleanup_status": "",
            "cleanup_reason": "",
        }

    def create_cleanup_job_records(self, keys):
        """Create records for a plain bucket cleanup, one per object key."""
        return [
            self.create_job_record(
                key,
                status="NOT_ANALYSED",
                reason="Bucket cleanup only: the file was not analysed in this run",
            )
            for key in list(keys or [])
        ]

    def normalize_processing_jobs(self, jobs):
        """Return ``jobs`` as a list of job records.

        Accepts a list of records, a single record or a single job identifier.
        Existing dictionaries are returned as-is (they are only completed with
        missing fields), so that status updates made by the robot remain
        visible to the caller.
        """
        if jobs is None:
            return []
        if isinstance(jobs, str) and not jobs.strip():
            return []
        if isinstance(jobs, (dict, str)):
            items = [jobs]
        elif isinstance(jobs, (list, tuple)):
            items = list(jobs)
        else:
            items = [jobs]

        records = []
        for item in items:
            if isinstance(item, dict):
                record = item
                defaults = self.create_job_record(
                    item.get("filename", item.get("job_id", "")),
                    item.get("job_id", ""),
                    item.get("status", "SUBMITTED"),
                    item.get("reason", ""),
                )
                for key, value in defaults.items():
                    if key not in record or record[key] is None:
                        record[key] = value
                records.append(record)
            else:
                identifier = self._text(item)
                records.append(self.create_job_record(identifier, identifier))
        return records

    def read_job_field(self, job, key, default=""):
        """Return a field of a job record, or ``default`` when it is absent."""
        if isinstance(job, dict):
            value = job.get(key, default)
            return default if value is None else value
        return default

    def is_processing_status(self, status):
        """Return True while a job can still become SUCCEEDED."""
        return self._text(status) in PROCESSING_STATUSES

    def get_pending_processing_jobs(self, jobs):
        """Return the job records which still have to be polled."""
        return [
            record
            for record in self.normalize_processing_jobs(jobs)
            if self.is_processing_status(record.get("status"))
        ]

    def finalize_unfinished_jobs(self, jobs):
        """Give a TIMEOUT status to jobs which never reached a final status."""
        for record in self.normalize_processing_jobs(jobs):
            if not self.is_processing_status(record.get("status")):
                continue
            record["status"] = "TIMEOUT"
            record["reason"] = DEFAULT_TIMEOUT_REASON

    # ------------------------------------------------------------------
    # Value inspection helpers (used from Robot Framework conditions)
    # ------------------------------------------------------------------
    def has_entries(self, value):
        """Return True when a value is not empty (list, dict, string, ...)."""
        return bool(value)

    def is_missing(self, value):
        """Return True when a value is ``None`` (used for failed conversions)."""
        return value is None

    def get_response_field(self, response, key, default=""):
        """Return a field of a Textract response, tolerating odd responses."""
        if isinstance(response, dict):
            value = response.get(key, default)
            return default if value is None else value
        return default

    def get_job_status(self, response):
        """Return the ``JobStatus`` of a Textract response.

        A response which carries ``Blocks`` but no explicit status is treated as
        successful, so a bare analysis result is not mistaken for a stalled job.
        The status is upper-cased so the robot can compare it directly.
        """
        status = self._text(self.get_response_field(response, "JobStatus")).strip()
        if status:
            return status.upper()
        if isinstance(response, dict) and response.get("Blocks"):
            return "SUCCEEDED"
        return "UNKNOWN"

    def build_failed_job_reason(self, status_message):
        """Build the failure reason of a job reported as FAILED by Textract."""
        message = self._text(status_message)
        if message:
            return "Amazon Textract reported a failed analysis job: {}".format(message)
        return "Amazon Textract reported a failed analysis job (JobStatus FAILED)"

    def to_round_count(self, value, default=90):
        """Turn a wait round setting (int or numeric string) into an int.

        An unusable setting falls back to ``default`` instead of aborting the
        batch, so a typo in the configuration can never lose the results.
        """
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return max(value, 0)
        text = self._text(value).strip().lower()
        if not text:
            return default
        try:
            return max(int(float(text)), 0)
        except (TypeError, ValueError):
            return default

    def build_timeout_reason(self, max_rounds, last_status, last_error):
        """Build the TIMEOUT reason including the last observed job state."""
        rounds = self._text(max_rounds)
        error = self._text(last_error)
        if error:
            detail = "last polling error: {}".format(error)
        else:
            detail = "last Amazon Textract status: {}".format(
                self._text(last_status) or "UNKNOWN"
            )
        return (
            "Timed out after {} wait rounds without a SUCCEEDED result ({})".format(
                rounds, detail
            )
        )

    # ------------------------------------------------------------------
    # Invoices and Excel rows
    # ------------------------------------------------------------------
    def collect_successful_invoices(self, jobs):
        """Collect invoices from successful jobs, keeping the S3 key order."""
        invoices = []
        for record in self.normalize_processing_jobs(jobs):
            if self._text(record.get("status")) != "SUCCEEDED":
                continue
            job_invoices = record.get("invoices")
            if isinstance(job_invoices, dict):
                invoices.append(job_invoices)
            elif isinstance(job_invoices, (list, tuple)):
                invoices.extend(job_invoices)
        return invoices

    def build_invoice_worksheet_rows(self, invoices):
        """Build the rows to append to the invoice workbook, header included.

        The first row is always the header row, so the workbook keeps its
        columns even when no invoice could be extracted from the batch.
        """
        return [list(INVOICE_COLUMNS)] + self.build_invoice_rows(invoices)

    def build_invoice_rows(self, invoices):
        """Build the Excel rows from invoice models.

        Every row is a list of values in the fixed column order of
        ``INVOICE_COLUMNS``, so the workbook does not depend on dictionary
        ordering or on the ``header`` mapping of the Excel library.
        """
        if invoices is None:
            return []
        if isinstance(invoices, dict):
            invoices = [invoices]
        elif isinstance(invoices, (list, tuple)):
            invoices = list(invoices)
        else:
            return []

        rows = []
        for invoice in invoices:
            source = invoice if isinstance(invoice, dict) else {}
            row = []
            for _, key in INVOICE_FIELDS:
                value = source.get(key, "")
                row.append("" if value is None else value)
            rows.append(row)
        return rows

    # ------------------------------------------------------------------
    # Per-file outcome report
    # ------------------------------------------------------------------
    def write_processing_outcomes(self, path, jobs):
        """Write one row per file with its final status and failure reason.

        The report is always written, even when the batch is empty or when no
        invoice succeeded, so the output directory always contains a readable
        record of what happened to each file.
        """
        rows = []
        for record in self.normalize_processing_jobs(jobs):
            rows.append(
                {
                    "Filename": self._clean(record.get("filename")),
                    "Status": self._clean(record.get("status")),
                    "Reason": self._clean(record.get("reason")),
                    "Cleanup Status": self._clean(record.get("cleanup_status")),
                    "Cleanup Reason": self._clean(record.get("cleanup_reason")),
                }
            )

        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=OUTCOME_COLUMNS, extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(rows)
        return path

    def build_outcome_summary(self, jobs):
        """Return a one line summary of the batch outcome for the log."""
        records = self.normalize_processing_jobs(jobs)
        if not records:
            return "No invoice file was found in the Amazon S3 bucket"
        counts = {}
        for record in records:
            status = self._text(record.get("status")) or "UNKNOWN"
            counts[status] = counts.get(status, 0) + 1
        ordered = [status for status in FINAL_STATUSES if status in counts]
        ordered += sorted(status for status in counts if status not in ordered)
        parts = ["{}: {}".format(status, counts[status]) for status in ordered]
        return "{} file(s) processed ({})".format(len(records), ", ".join(parts))

    # ------------------------------------------------------------------
    # Cleanup helpers
    # ------------------------------------------------------------------
    def should_delete_job_file(self, delete_all, job):
        """Decide whether the file of a job may be removed from the bucket.

        A plain bucket cleanup (``delete_all``) removes every listed object.
        During the invoice task only files which were analysed successfully are
        removed, so failed or timed out files stay available for a retry and are
        never silently treated as processed. A successful file is also kept when
        its results could not be exported, so the data is not lost.
        """
        if not isinstance(job, dict):
            return bool(delete_all)
        if job.get("results_saved") is False:
            return False
        if bool(delete_all):
            return True
        return self._text(job.get("status")) == "SUCCEEDED"

    def mark_invoices_not_saved(self, jobs):
        """Mark successful jobs whose results could not be exported.

        A successfully analysed file must not be removed from the bucket when
        the invoice workbook could not be written, otherwise the only copy of
        the extracted data would be lost.
        """
        for record in self.normalize_processing_jobs(jobs):
            if self._text(record.get("status")) == "SUCCEEDED":
                record["results_saved"] = False
        return jobs

    def record_skipped_cleanup(self, job):
        """Record why the file of a job is kept in the bucket."""
        if not isinstance(job, dict):
            return ""
        if job.get("results_saved") is False:
            reason = (
                "The invoice Excel file could not be written, "
                "so the file is kept in the bucket to avoid losing its data"
            )
        else:
            reason = (
                "File was not processed successfully and is kept in the bucket "
                "for a retry"
            )
        job["cleanup_status"] = "SKIPPED"
        job["cleanup_reason"] = reason
        return reason

    def delete_failed(self, deleted):
        """Interpret the result of the Amazon S3 deletion keyword.

        The RPA library returns the number of deleted objects on success and
        ``False`` (or ``0``) when the deletion was not confirmed; transport
        errors are raised instead of being returned. Anything else (for example
        an object count or a list of deleted keys) is treated as a success.
        """
        if deleted is False:
            return True
        if deleted is None:
            return False
        try:
            return int(deleted) == 0
        except (TypeError, ValueError):
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _text(value):
        if value is None:
            return ""
        return str(value)

    @classmethod
    def _clean(cls, value):
        if value is None:
            return ""
        return str(value).replace("\r", " ").replace("\n", " ").strip()
