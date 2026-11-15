import os
import shutil
import tempfile
import unittest

import json
import sqlite3
import xml.etree.ElementTree as ET

from modules.crawler import Crawler
from modules.checker import url_canon, extract_domain, folder
from modules.export import export_json, export_xml, export_database


class TestExportFunctions(unittest.TestCase):
    def setUp(self):
        _website = url_canon('torcrawl.com', False)
        self.out_path = folder(extract_domain(_website), False)
        self.crawler = Crawler(_website, 0, 1, self.out_path, False, False)

    def tearDown(self):
        shutil.rmtree(self.out_path)

    def test_export_findings_creates_json(self):
        prefix = f"{self.crawler.timestamp}_results_test"
        self.crawler.findings["links"].update({"https://torcrawl.com", "https://torcrawl.com/about"})
        self.crawler.findings["external_links"].add("https://external.com/path")
        self.crawler.findings["images"].add("https://torcrawl.com/img/logo.png")
        self.crawler.findings["emails"].add("mailto:test@torcrawl.com")

        payload = self.crawler.export_payload()
        export_json(self.out_path, prefix, payload["data"], verbose=False)

        json_path = os.path.join(self.out_path, f"{prefix}.json")
        self.assertTrue(os.path.exists(json_path))

        with open(json_path, "r", encoding="UTF-8") as json_file:
            data = json.load(json_file)

        self.assertIn("links", data)
        self.assertIn("external_links", data)
        self.assertIn("images", data)
        self.assertIn("emails", data)
        self.assertIn("https://torcrawl.com/about", data["links"])
        self.assertIn("https://external.com/path", data["external_links"])

    def test_export_findings_creates_xml(self):
        prefix = f"{self.crawler.timestamp}_results_test_xml"
        self.crawler.findings["links"].update({"https://torcrawl.com"})
        self.crawler.findings["scripts"].add("https://torcrawl.com/static/app.js")
        self.crawler.findings["telephones"].add("tel:012-013-104-5")

        payload = self.crawler.export_payload()
        export_xml(self.out_path, prefix, payload["data"], verbose=False)

        xml_path = os.path.join(self.out_path, f"{prefix}.xml")
        self.assertTrue(os.path.exists(xml_path))

        tree = ET.parse(xml_path)
        root = tree.getroot()

        links_section = root.find("links")
        scripts_section = root.find("scripts")
        telephones_section = root.find("telephones")

        self.assertIsNotNone(links_section)
        self.assertIsNotNone(scripts_section)
        self.assertIsNotNone(telephones_section)
        self.assertEqual(links_section[0].text, "https://torcrawl.com")
        self.assertEqual(scripts_section[0].text, "https://torcrawl.com/static/app.js")
        self.assertEqual(telephones_section[0].text, "tel:012-013-104-5")

    def test_export_database_stores_nodes_edges_and_titles(self):
        prefix = f"{self.crawler.timestamp}_results_test_db"
        self.crawler.findings["links"].update({"https://torcrawl.com", "https://torcrawl.com/about"})
        self.crawler.edges.add(("https://torcrawl.com", "https://torcrawl.com/about"))
        self.crawler.titles["https://torcrawl.com"] = "Home"
        self.crawler.titles["https://torcrawl.com/about"] = "About"

        payload = self.crawler.export_payload()
        export_database(self.out_path, prefix, payload["data"], payload["edges"], payload["titles"], payload["resources"], verbose=False)

        db_path = os.path.join(self.out_path, f"{prefix}.db")
        self.assertTrue(os.path.exists(db_path))

        conn = sqlite3.connect(db_path)
        try:
            with conn:
                cur = conn.cursor()
                cur.execute("SELECT url, title FROM nodes ORDER BY url;")
                rows = cur.fetchall()
                self.assertIn(("https://torcrawl.com", "Home"), rows)
                self.assertIn(("https://torcrawl.com/about", "About"), rows)

                cur.execute("SELECT from_url, to_url FROM edges;")
                edges = cur.fetchall()
                self.assertIn(("https://torcrawl.com", "https://torcrawl.com/about"), edges)
        finally:
            conn.close()

    def test_export_database_stores_outcomes_idempotently(self):
        """Outcomes are stored once per URL and re-exporting does not duplicate rows."""
        prefix = f"{self.crawler.timestamp}_results_test_outcomes"
        self.crawler.outcomes = {
            "https://torcrawl.com": {"outcome": "success", "error": None},
            "https://torcrawl.com/missing": {"outcome": "request_failed", "error": "URLError: unreachable"},
            "https://torcrawl.com/broken": {"outcome": "read_failed", "error": "OSError: reset"},
        }
        self.crawler.findings["links"].update(self.crawler.outcomes.keys())
        payload = self.crawler.export_payload()

        db_path = os.path.join(self.out_path, f"{prefix}.db")
        export_database(self.out_path, prefix, payload["data"], payload["edges"],
                        payload["titles"], payload["resources"], payload["outcomes"], verbose=False)
        export_database(self.out_path, prefix, payload["data"], payload["edges"],
                        payload["titles"], payload["resources"], payload["outcomes"], verbose=False)

        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT url, outcome, error FROM outcomes ORDER BY url;")
            rows = cur.fetchall()
        finally:
            conn.close()

        self.assertEqual(len(rows), 3)
        self.assertIn(("https://torcrawl.com/missing", "request_failed", "URLError: unreachable"), rows)
        self.assertIn(("https://torcrawl.com/broken", "read_failed", "OSError: reset"), rows)
        self.assertIn(("https://torcrawl.com", "success", None), rows)

        # A later export with an updated outcome overwrites the row in place.
        updated = dict(payload["outcomes"])
        updated["https://torcrawl.com/broken"] = {"outcome": "success", "error": None}
        export_database(self.out_path, prefix, payload["data"], payload["edges"],
                        payload["titles"], payload["resources"], updated, verbose=False)

        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM outcomes;")
            self.assertEqual(cur.fetchone()[0], 3)
            cur.execute("SELECT outcome, error FROM outcomes WHERE url = ?;",
                        ("https://torcrawl.com/broken",))
            self.assertEqual(cur.fetchone(), ("success", None))
        finally:
            conn.close()

    def test_export_database_outcomes_fall_back_to_data(self):
        """Callers passing outcomes inside data still populate the outcomes table."""
        prefix = f"{self.crawler.timestamp}_results_test_outcomes_data"
        data = dict(self.crawler.export_payload()["data"])
        data["outcomes"] = {"https://torcrawl.com": {"outcome": "success", "error": None}}

        db_path = os.path.join(self.out_path, f"{prefix}.db")
        export_database(self.out_path, prefix, data, set(), {}, verbose=False)

        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT url, outcome FROM outcomes;")
            rows = cur.fetchall()
        finally:
            conn.close()

        self.assertEqual(rows, [("https://torcrawl.com", "success")])

    def test_json_and_xml_do_not_include_outcomes(self):
        """JSON/XML exports keep their original field set."""
        prefix = f"{self.crawler.timestamp}_results_test_no_outcomes"
        self.crawler.outcomes = {"https://torcrawl.com": {"outcome": "success", "error": None}}
        self.crawler.findings["links"].add("https://torcrawl.com")
        payload = self.crawler.export_payload()

        export_json(self.out_path, prefix, payload["data"], verbose=False)
        export_xml(self.out_path, prefix, payload["data"], verbose=False)

        with open(os.path.join(self.out_path, f"{prefix}.json"), "r", encoding="UTF-8") as json_file:
            data = json.load(json_file)
        self.assertNotIn("outcomes", data)

        root = ET.parse(os.path.join(self.out_path, f"{prefix}.xml")).getroot()
        self.assertIsNone(root.find("outcomes"))


if __name__ == '__main__':
    unittest.main()

