import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import jsrip
from utils.reporter import ReportGenerator

REPO = Path(__file__).resolve().parent.parent

RESULTS = {
    "scan_info": {"urls": ["https://t.example"], "urls_count": 1,
                  "start_time": "2026-08-30T10:00:00",
                  "end_time": "2026-08-30T10:05:00", "duration": "0:05:00"},
    "statistics": {"js_files_found": 0, "secrets_found": 0, "endpoints_found": 0},
    "findings": {"secrets": [], "endpoints": [], "js_files": []},
}


def _parse(argv):
    with mock.patch.object(sys, "argv", ["jsrip.py"] + argv):
        return jsrip.parse_arguments()


class TestSelectableFormats(unittest.TestCase):
    def test_every_format_the_reporter_implements_is_selectable(self):
        """Dead branches in the reporter are unreachable features.

        `pdf` had a full implementation with no way to ask for it.
        """
        implemented = {"json", "md", "html", "csv", "pdf"}
        for fmt in sorted(implemented):
            with self.subTest(format=fmt):
                self.assertEqual(_parse(["-u", "https://e.com", "-f", fmt]).format,
                                 [fmt])

    def test_unknown_format_is_still_rejected(self):
        with self.assertRaises(SystemExit):
            _parse(["-u", "https://e.com", "-f", "docx"])

    def test_documented_default_matches_the_actual_default(self):
        """Help text promised `md json`; argparse handed back `json`."""
        args = _parse(["-u", "https://e.com"])
        action = next(a for a in jsrip.build_parser()._actions if a.dest == "format")
        for fmt in args.format:
            with self.subTest(format=fmt):
                self.assertIn(fmt, action.help)


class TestPdfBackendPrerequisites(unittest.TestCase):
    def test_pdf_run_leaves_the_inputs_every_backend_needs(self):
        """weasyprint renders report.html, pandoc renders report.md.

        Generating only markdown left the weasyprint path with no input, so
        it silently produced nothing on a fresh `-f pdf` run.
        """
        d = tempfile.mkdtemp()
        ReportGenerator(output_dir=d, logger=logging.getLogger("t")) \
            .generate_report(RESULTS, "pdf")
        reports = Path(d) / "reports"
        for name in ("report.md", "report.html"):
            with self.subTest(file=name):
                self.assertTrue((reports / name).exists())


class TestDependencyWeight(unittest.TestCase):
    def test_weasyprint_is_not_a_hard_requirement(self):
        """It pulls cairo/pango native libs and only ever served one of three
        interchangeable PDF backends — a hard install failure for a fallback."""
        for line in (REPO / "requirements.txt").read_text().splitlines():
            line = line.strip()
            if line.startswith("#"):
                continue
            self.assertNotIn("weasyprint", line.lower())


if __name__ == "__main__":
    unittest.main()
