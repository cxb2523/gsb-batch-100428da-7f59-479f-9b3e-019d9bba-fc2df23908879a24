# -*- coding: utf-8 -*-
"""Tests de service.search_offers: eventos, filtros y paridad con el pipeline."""
import unittest
from unittest.mock import MagicMock, patch

from jobhunter.service import search_offers

CFG = {
    "gemini_api_key": "k",
    "smtp_email": "yo@gmail.com",
    "smtp_password": "apppassword123456",
    "profile": {"name": "Jose"},
    "search_queries": ["query uno", "query dos"],
}


def _kb(**over):
    kb = {"runs": [], "applications": [], "rejected_companies": []}
    kb.update(over)
    return kb


def _post(text, emails, index=0, url="https://lnkd.in/x"):
    return {
        "text": text,
        "emails_found": emails,
        "index": index,
        "author_url": None,
        "author_name": None,
        "post_url": url,
    }


def _offer_answer(**over):
    a = {
        "is_job": True,
        "job_title": "Backend Dev",
        "company": "TechCo",
        "description": "d",
        "requirements": "r",
        "contact_email": "hr@techco.com",
        "contact_name": "HR",
        "location": "Remote",
        "work_mode": "remote",
        "salary": None,
        "language": "es",
        "is_relevant": True,
        "relevance_reason": "match",
    }
    a.update(over)
    return a


def _fake_playwright(page_url="https://www.linkedin.com/feed/"):
    """Construye el mock del context manager sync_playwright()."""
    page = MagicMock()
    page.url = page_url
    page.query_selector_all.return_value = []
    browser = MagicMock()
    browser.pages = [page]
    p = MagicMock()
    p.chromium.launch_persistent_context.return_value = browser
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=p)
    cm.__exit__ = MagicMock(return_value=False)
    return cm, page


class SearchBase(unittest.TestCase):
    def setUp(self):
        self.events = []
        patches = [
            patch("jobhunter.service.kill_playwright_zombies"),
            patch("jobhunter.service.find_chrome", return_value="C:/chrome.exe"),
            patch("jobhunter.service.os.path.exists", return_value=True),
        ]
        for pt in patches:
            pt.start()
            self.addCleanup(pt.stop)

    def on_event(self, name, payload):
        self.events.append((name, payload))

    def names(self):
        return [n for n, _ in self.events]

    def run_search(self, cfg=None, kb=None, scrape_side_effect=None,
                   filter_side_effect=None, page_url="https://www.linkedin.com/feed/"):
        cm, page = _fake_playwright(page_url)
        self.page = page
        with patch("jobhunter.service.sync_playwright", return_value=cm), \
             patch("jobhunter.service.scrape_posts",
                   side_effect=scrape_side_effect or [[], []]) as m_scrape, \
             patch("jobhunter.service.agent_filter", side_effect=filter_side_effect or []):
            self.scrape_mock = m_scrape
            return search_offers(cfg or CFG, kb or _kb(), on_event=self.on_event, pace=False)


class TestGuards(SearchBase):
    def test_not_configured(self):
        res = search_offers({"profile": {}}, _kb(), on_event=self.on_event, pace=False)
        self.assertEqual(res["error"]["kind"], "not_configured")
        self.assertNotIn("phase", self.names())

    def test_no_session(self):
        with patch("jobhunter.service.os.path.exists", return_value=False):
            res = search_offers(CFG, _kb(), on_event=self.on_event, pace=False)
        self.assertEqual(res["error"]["kind"], "no_session")

    def test_session_expired(self):
        res = self.run_search(page_url="https://www.linkedin.com/login")
        self.assertEqual(res["error"]["kind"], "session_expired")

    def test_no_posts_with_email(self):
        res = self.run_search(scrape_side_effect=[[_post("x" * 60, [])], []])
        self.assertEqual(res["error"]["kind"], "no_posts")
        self.assertEqual(res["stats"]["posts_scraped"], 1)


class TestHappyPath(SearchBase):
    def test_full_flow(self):
        p1 = _post("oferta real con email " + "a" * 100, ["hr@techco.com"], index=0)
        p2 = _post("texto sin email " + "b" * 60, [], index=1)
        p3 = _post("oferta irrelevante " + "c" * 100, ["x@y.com"], index=2)
        dup = dict(p1)
        res = self.run_search(
            scrape_side_effect=[[p1, p2], [dup, p3]],
            filter_side_effect=[
                _offer_answer(),
                _offer_answer(is_relevant=False, relevance_reason="otro rubro",
                              company="OtraCo", contact_email="x@y.com"),
            ],
        )
        self.assertIsNone(res["error"])
        self.assertEqual(len(res["offers"]), 1)
        offer = res["offers"][0]
        self.assertEqual(offer["company"], "TechCo")
        self.assertEqual(offer["post_url"], p1["post_url"])
        self.assertEqual(offer["query"], "query uno")
        self.assertEqual(res["stats"]["posts_scraped"], 3)
        self.assertEqual(res["stats"]["posts_with_emails"], 2)
        self.assertEqual(res["stats"]["filter_accepted"], 1)
        self.assertEqual(len(res["decisions"]), 2)
        names = self.names()
        phases = [(p["phase"], p["status"]) for n, p in self.events if n == "phase"]
        self.assertEqual(phases, [
            ("scrape", "start"), ("scrape", "done"),
            ("analyze", "start"), ("analyze", "done"),
            ("dedupe", "start"), ("dedupe", "done"),
        ])
        self.assertIn("progress", names)
        self.assertIn("decision", names)

    def test_short_text_skipped(self):
        p1 = _post("corto", ["a@b.com"])
        res = self.run_search(scrape_side_effect=[[p1], []], filter_side_effect=[])
        self.assertEqual(len(res["offers"]), 0)
        self.assertEqual(len(res["decisions"]), 1)
        self.assertIn("skipped", res["decisions"][0]["relevance_reason"])

    def test_invalid_email_cleaned(self):
        p1 = _post("oferta " + "a" * 100, ["hr@techco.com"])
        res = self.run_search(
            scrape_side_effect=[[p1], []],
            filter_side_effect=[_offer_answer(contact_email="null")],
        )
        self.assertEqual(len(res["offers"]), 0)
        self.assertEqual(res["stats"]["offers_no_email"], 1)


class TestFilters(SearchBase):
    def test_blacklist_excludes(self):
        p1 = _post("oferta " + "a" * 100, ["hr@techco.com"])
        res = self.run_search(
            kb=_kb(rejected_companies=["techco"]),
            scrape_side_effect=[[p1], []],
            filter_side_effect=[_offer_answer()],
        )
        self.assertEqual(len(res["offers"]), 0)
        self.assertEqual(res["stats"]["blacklisted"], 1)

    def test_cooldown_excludes(self):
        from datetime import datetime
        p1 = _post("oferta " + "a" * 100, ["hr@techco.com"])
        apps = [{"date": datetime.now().isoformat(), "company": "TechCo",
                 "job_title": "Backend Dev"}]
        res = self.run_search(
            kb=_kb(applications=apps),
            scrape_side_effect=[[p1], []],
            filter_side_effect=[_offer_answer()],
        )
        self.assertEqual(len(res["offers"]), 0)
        self.assertEqual(res["stats"]["already_applied"], 1)

    def test_batch_dedupe(self):
        p1 = _post("oferta uno " + "a" * 100, ["hr@techco.com"])
        p2 = _post("oferta dos " + "b" * 100, ["hr@techco.com"])
        res = self.run_search(
            scrape_side_effect=[[p1, p2], []],
            filter_side_effect=[_offer_answer(), _offer_answer()],
        )
        self.assertEqual(len(res["offers"]), 1)
        self.assertEqual(res["stats"]["batch_dupes"], 1)


class TestQueryFailures(SearchBase):
    """Una query que falla no aborta el run; solo si TODAS fallan hay error."""
    CFG3 = dict(CFG, search_queries=["q1", "q2", "q3"])

    def phases(self):
        return [(p["phase"], p["status"]) for n, p in self.events if n == "phase"]

    def test_partial_failure_keeps_other_posts(self):
        p1 = _post("oferta uno " + "a" * 100, ["hr@techco.com"], index=0)
        p3 = _post("oferta tres " + "c" * 100, ["hr@tres.com"], index=0)
        res = self.run_search(
            cfg=self.CFG3,
            scrape_side_effect=[[p1], RuntimeError("scroll timeout\nstacktrace"), [p3]],
            filter_side_effect=[
                _offer_answer(),
                _offer_answer(company="TresCo", contact_email="hr@tres.com"),
            ],
        )
        self.assertIsNone(res["error"])
        # los posts de las queries exitosas siguen su curso normal
        self.assertEqual(len(res["offers"]), 2)
        self.assertEqual({o["query"] for o in res["offers"]}, {"q1", "q3"})
        self.assertEqual(res["stats"]["posts_scraped"], 2)
        self.assertEqual(res["stats"]["queries_total"], 3)
        self.assertEqual(res["stats"]["queries_failed"], 1)
        # desenlace por query expuesto en el resultado
        queries = res["queries"]
        self.assertEqual([q["status"] for q in queries],
                         ["success", "failed", "success"])
        self.assertEqual(queries[1]["query"], "q2")
        self.assertIn("scroll timeout", queries[1]["error"])
        self.assertNotIn("stacktrace", queries[1]["error"])
        self.assertEqual(queries[1]["posts"], 0)
        self.assertIsNone(queries[0]["error"])
        # la query fallida no produjo posts
        self.assertNotIn("q2", {o["query"] for o in res["offers"]})
        # las fases conservan su orden habitual
        self.assertEqual(self.phases(), [
            ("scrape", "start"), ("scrape", "done"),
            ("analyze", "start"), ("analyze", "done"),
            ("dedupe", "start"), ("dedupe", "done"),
        ])

    def test_partial_failure_dedupes_across_queries(self):
        p1 = _post("mismo texto " + "a" * 100, ["hr@techco.com"], index=0)
        dup = dict(p1)
        res = self.run_search(
            cfg=self.CFG3,
            scrape_side_effect=[[p1], RuntimeError("boom"), [dup]],
            filter_side_effect=[_offer_answer()],
        )
        self.assertIsNone(res["error"])
        self.assertEqual(res["stats"]["posts_scraped"], 1)
        self.assertEqual(len(res["offers"]), 1)
        self.assertEqual(res["offers"][0]["query"], "q1")

    def test_all_queries_failed_returns_error(self):
        res = self.run_search(cfg=self.CFG3, scrape_side_effect=RuntimeError("boom"))
        self.assertEqual(res["error"]["kind"], "scrape_failed")
        self.assertIn("boom", res["error"]["message"])
        self.assertEqual(res["offers"], [])
        self.assertEqual(len(res["queries"]), 3)
        self.assertTrue(all(q["status"] == "failed" for q in res["queries"]))
        self.assertEqual(res["stats"]["queries_failed"], 3)
        # no se analiza nada: solo corrio la fase de scrape
        self.assertEqual(self.phases(), [("scrape", "start"), ("scrape", "done")])

    def test_session_expired_mid_run_aborts(self):
        p1 = _post("oferta " + "a" * 100, ["hr@techco.com"])

        def scrape(page, query, time_filter="24h"):
            if query == "q2":
                self.page.url = "https://www.linkedin.com/login"
                raise RuntimeError("target closed")
            return [p1]

        res = self.run_search(cfg=self.CFG3, scrape_side_effect=scrape)
        # la sesion expirada aborta el run, no se traga como fallo de una query
        self.assertEqual(res["error"]["kind"], "session_expired")
        self.assertEqual(self.scrape_mock.call_count, 2)  # q3 nunca se intenta
        self.assertEqual([q["query"] for q in res["queries"]], ["q1"])

    def test_login_keyword_in_query_not_confused_with_expiry(self):
        cfg = dict(CFG, search_queries=["login page designer"])
        p1 = _post("oferta " + "a" * 100, ["hr@techco.com"])

        def scrape(page, query, time_filter="24h"):
            page.url = ("https://www.linkedin.com/search/results/content/"
                        "?keywords=login%20page%20designer")
            return [p1]

        res = self.run_search(cfg=cfg, scrape_side_effect=scrape,
                              filter_side_effect=[_offer_answer()])
        self.assertIsNone(res["error"])
        self.assertEqual(len(res["offers"]), 1)


if __name__ == "__main__":
    unittest.main()
