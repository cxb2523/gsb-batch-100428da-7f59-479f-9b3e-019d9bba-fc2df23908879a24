"""Tests for the per-series outcome reporting of download_series_dicomweb."""
import unittest
from unittest.mock import Mock, patch

from receiver.dicomweb import download_series_dicomweb, refresh_download_outcomes

CONFIG = {
    "IMAGE_FOLDER": "/tmp/images",
    "DICOMWEB_WADO_BASE_URL": "https://pacs.example/wado",
    "DICOMWEB_TOKEN": "secret-token",
}


def _entry(n, **overrides):
    entry = {
        "study_uid": f"1.2.840.{n}",
        "series_uid": f"1.2.840.1.{n}",
        "accession_number": "ACC123",
        "patient_id": "PAT1",
        "series_number": str(n),
    }
    entry.update(overrides)
    return entry


def _fake_job(job_id):
    job = Mock()
    job.id = job_id
    return job


class DownloadSeriesDicomwebResultTest(unittest.TestCase):
    def test_all_series_queued_summary(self):
        series_list = [_entry(1), _entry(2), _entry(3)]
        jobs = [_fake_job(f"job-{n}") for n in range(3)]
        with patch(
            "receiver.dicomweb._queue_dicomweb_download", side_effect=jobs,
        ) as enqueue:
            result = download_series_dicomweb(
                CONFIG, series_list, "download", "nifti", "queue-medium",
            )

        self.assertEqual(enqueue.call_count, 3)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["queued"], 3)
        self.assertEqual(result["failed"], 0)
        self.assertIsNone(result["config_error"])
        self.assertEqual(len(result["series"]), 3)
        for i, series in enumerate(result["series"]):
            self.assertEqual(series["status"], "queued")
            self.assertIsNone(series["reason"])
            self.assertEqual(series["job_id"], f"job-{i}")
            self.assertEqual(series["series_uid"], series_list[i]["series_uid"])
            self.assertEqual(series["study_uid"], series_list[i]["study_uid"])
            self.assertEqual(
                series["accession_number"], series_list[i]["accession_number"],
            )

    def test_missing_fields_fail_one_series_others_still_processed(self):
        series_list = [
            _entry(1),
            _entry(2, series_uid=""),          # missing series_uid
            _entry(3, accession_number=None),  # missing accession_number
            _entry(4),
        ]
        with patch(
            "receiver.dicomweb._queue_dicomweb_download",
            side_effect=[_fake_job("job-1"), _fake_job("job-4")],
        ) as enqueue:
            result = download_series_dicomweb(
                CONFIG, series_list, "download", "dicom", "queue-high",
            )

        self.assertEqual(enqueue.call_count, 2)
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["queued"], 2)
        self.assertEqual(result["failed"], 2)

        by_uid = {s["series_uid"]: s for s in result["series"]}
        self.assertEqual(by_uid["1.2.840.1.1"]["status"], "queued")
        self.assertEqual(by_uid["1.2.840.1.1"]["job_id"], "job-1")
        self.assertEqual(by_uid["1.2.840.1.4"]["status"], "queued")

        # The entry without series_uid is still identifiable in the result.
        missing_series_uid = [
            s for s in result["series"] if not s["series_uid"]
        ]
        self.assertEqual(len(missing_series_uid), 1)
        self.assertEqual(missing_series_uid[0]["status"], "failed")
        self.assertIn("series_uid", missing_series_uid[0]["reason"])
        self.assertIsNone(missing_series_uid[0]["job_id"])

        missing_acc = by_uid["1.2.840.1.3"]
        self.assertEqual(missing_acc["status"], "failed")
        self.assertIn("accession_number", missing_acc["reason"])

    def test_invalid_config_fails_all_series_and_is_reported(self):
        bad_config = {"IMAGE_FOLDER": "/tmp/images"}  # no WADO URL, no auth
        series_list = [_entry(1), _entry(2)]
        with patch("receiver.dicomweb._queue_dicomweb_download") as enqueue:
            result = download_series_dicomweb(
                bad_config, series_list, "download", "dicom", "queue-medium",
            )

        enqueue.assert_not_called()
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["queued"], 0)
        self.assertEqual(result["failed"], 2)
        self.assertIn("DICOMWEB_WADO_BASE_URL", result["config_error"])
        self.assertEqual(len(result["series"]), 2)
        for series in result["series"]:
            self.assertEqual(series["status"], "failed")
            self.assertIn("configuration invalid", series["reason"])
            self.assertIsNone(series["job_id"])

    def test_enqueue_error_fails_only_that_series(self):
        series_list = [_entry(1), _entry(2)]
        with patch(
            "receiver.dicomweb._queue_dicomweb_download",
            side_effect=[_fake_job("job-1"), RuntimeError("redis down")],
        ):
            result = download_series_dicomweb(
                CONFIG, series_list, "download", "dicom", "queue-medium",
            )

        self.assertEqual(result["queued"], 1)
        self.assertEqual(result["failed"], 1)
        by_uid = {s["series_uid"]: s for s in result["series"]}
        self.assertEqual(by_uid["1.2.840.1.1"]["status"], "queued")
        self.assertEqual(by_uid["1.2.840.1.2"]["status"], "failed")
        self.assertIn("redis down", by_uid["1.2.840.1.2"]["reason"])


class RefreshDownloadOutcomesTest(unittest.TestCase):
    def _enqueued_result(self):
        series_list = [_entry(1), _entry(2), _entry(3)]
        jobs = [_fake_job(f"job-{n}") for n in (1, 2, 3)]
        with patch("receiver.dicomweb._queue_dicomweb_download", side_effect=jobs):
            return download_series_dicomweb(
                CONFIG, series_list, "download", "nifti", "queue-medium",
            )

    def test_failed_download_job_marks_series_failed(self):
        result = self._enqueued_result()

        def fetch(job_id, connection=None):
            job = Mock()
            if job_id == "job-2":
                job.get_status.return_value = "failed"
                job.exc_info = "Traceback...\nrequests.HTTPError: 500 Server Error"
            else:
                job.get_status.return_value = "finished"
                job.exc_info = None
            return job

        with patch("receiver.dicomweb.Job") as job_cls:
            job_cls.fetch.side_effect = fetch
            updated = refresh_download_outcomes(result, connection=Mock())

        self.assertIs(updated, result)
        by_uid = {s["series_uid"]: s for s in updated["series"]}
        self.assertEqual(by_uid["1.2.840.1.1"]["status"], "finished")
        self.assertEqual(by_uid["1.2.840.1.3"]["status"], "finished")

        failed = by_uid["1.2.840.1.2"]
        self.assertEqual(failed["status"], "failed")
        self.assertIn("500 Server Error", failed["reason"])

        self.assertEqual(updated["failed"], 1)
        self.assertEqual(updated["queued"], 2)
        self.assertEqual(updated["total"], 3)

    def test_refresh_keeps_pre_enqueue_failures_and_queued_state(self):
        series_list = [_entry(1), _entry(2, study_uid="")]
        with patch(
            "receiver.dicomweb._queue_dicomweb_download",
            return_value=_fake_job("job-1"),
        ):
            result = download_series_dicomweb(
                CONFIG, series_list, "download", "dicom", "queue-medium",
            )

        running_job = Mock()
        running_job.get_status.return_value = "started"
        with patch("receiver.dicomweb.Job") as job_cls:
            job_cls.fetch.return_value = running_job
            updated = refresh_download_outcomes(result, connection=Mock())

        by_uid = {s["series_uid"]: s for s in updated["series"]}
        self.assertEqual(by_uid["1.2.840.1.1"]["status"], "started")
        # The series that failed before enqueue keeps its original reason.
        failed = by_uid["1.2.840.1.2"]
        self.assertEqual(failed["status"], "failed")
        self.assertIn("study_uid", failed["reason"])
        self.assertIsNone(failed["job_id"])
        self.assertEqual(updated["failed"], 1)
        self.assertEqual(updated["queued"], 1)


if __name__ == "__main__":
    unittest.main()
