import support  # noqa: F401

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from therightbibtex import cli


def version(vid, label, status, key):
    return {"id": vid, "label": label, "status": status,
            "bibtex": f"@inproceedings{{{key},\n  title = {{{label}}}\n}}"}


def work(wid, title, confidence, versions, rec=None):
    return {"id": wid, "title": title, "confidence": confidence, "versions": versions,
            "recommended": rec or versions[0]["id"]}


RESNET = work("w0", "Deep Residual Learning for Image Recognition", "high",
              [version("w0v0", "CVPR 2016", "published", "he2016deep"),
               version("w0v1", "arXiv 1512.03385", "preprint", "he2015deep"),
               dict(version("w0v2", "copy", "published", "copy"), suspicious=True)])
ADAM = work("w1", "Adam: A Method for Stochastic Optimization", "high",
            [version("w1v0", "ICLR 2015", "published", "kingma2015adam")])
CHRONOS = work("w2", "Chronos: Learning the Language of Time Series", "medium",
               [version("w2v0", "TMLR 2024", "published", "ansari2024chronos")])
CHRONOS_BOOK = work("w3", "Chronos", "low", [version("w3v0", "Springer 2020", "published", "other2020chronos")])

BIB = """@article{myresnet, title={Deep residual learning}, author={He, Kaiming}, year={2015}}
@article{myadam, title={Adam: a method for stochastic optimization}, author={Kingma, Diederik}}
@misc{mychronos, title={Chronos}, author={Ansari, Abdul Fatir}}
@misc{mylost, title={Nothing like this exists}, author={Nobody, Someone}}
"""
ANSWERS = {"Deep residual": [RESNET], "Adam": [ADAM], "Chronos": [CHRONOS, CHRONOS_BOOK], "Nothing": []}


class Tty(io.StringIO):
    def isatty(self):
        return True


class Choices(unittest.TestCase):
    def test_versions_then_other_papers(self):
        self.assertEqual([c["label"] for c in cli.choices([RESNET])],
                         ["CVPR 2016 (published, recommended)", "arXiv 1512.03385 (preprint)"])   # sans la copie
        self.assertEqual(len(cli.choices([ADAM, CHRONOS])), 1)          # sûr : pas d'autres papiers proposés
        labels = [c["label"] for c in cli.choices([CHRONOS, CHRONOS_BOOK])]
        self.assertEqual(labels[1], "other paper: Chronos (Springer 2020)")
        self.assertEqual(cli.choices([]), [])


class SkippedSources(unittest.TestCase):
    """Une source limitée ou coupée ne fait pas passer le papier pour inexistant."""

    REPORT = {"sources": [
        {"source": "crossref", "label": "Crossref", "state": "ratelimited", "skipped": True,
         "message": "rate limited, retry in 600s"},
        {"source": "s2", "label": "Semantic Scholar", "state": "breaker", "skipped": True,
         "message": "rate limited (429)"},
        {"source": "arxiv", "label": "arXiv", "state": "ok", "count": 3, "skipped": False},
        {"source": "zbmath", "label": "zbMATH", "state": "empty", "count": 0, "skipped": False},
        {"source": "scholar", "label": "Google Scholar", "state": "skipped", "count": 0, "skipped": False},
    ], "answered": 2, "skipped": ["crossref", "s2"], "elapsed": 1200}

    def test_note_names_sources_and_reasons(self):
        note = cli._skipped_note(self.REPORT)
        self.assertIn("2 sources unavailable", note)
        self.assertIn("Crossref: rate limited", note)
        self.assertIn("Semantic Scholar: temporarily disabled", note)
        self.assertNotIn("arXiv", note)
        self.assertNotIn("Google Scholar", note)       # jamais interrogée : pas un incident
        self.assertEqual(cli._skipped_note({"sources": []}), "")
        self.assertEqual(cli._skipped_note(None), "")

    def run_search(self, works, report=None, extra=None):
        opts = dict(query=["x"], refresh=False, setting=[], all=False, json=False, output=None,
                    quiet=False, verbose=False)
        opts.update(extra or {})
        args = SimpleNamespace(**opts)
        err = io.StringIO()
        stdout = io.StringIO()

        async def fake_search(http, q, settings, refresh, on_event=None):
            if on_event and report is not None:
                on_event({"type": "done", **report}, None)
            return works

        with mock.patch.object(cli, "_search", fake_search), mock.patch.object(sys, "stderr", err), \
                mock.patch.object(sys, "stdout", stdout), \
                mock.patch.object(cli, "Http", lambda: SimpleNamespace(close=mock.AsyncMock())):
            try:
                code = cli.cmd_search(args, cli.Out())
            except SystemExit as e:
                code = e.code
        return code, err.getvalue(), stdout.getvalue()

    def test_not_found_is_qualified_when_a_source_is_missing(self):
        code, err, _ = self.run_search([], self.REPORT)
        self.assertEqual(code, cli.EXIT_NOT_FOUND)
        self.assertIn("no paper found, but 2 sources unavailable", err)
        self.assertIn("may be incomplete", err)
        # Sans source muette, le message reste « not found ».
        code, err, _ = self.run_search([], {"sources": self.REPORT["sources"][2:]})
        self.assertIn("no paper found: try the exact title", err)

    def test_warns_when_fields_rest_on_fewer_sources(self):
        code, err, _ = self.run_search([RESNET], self.REPORT)
        self.assertEqual(code, 0)
        self.assertIn("2 sources unavailable", err)
        self.assertIn("fewer sources than usual", err)

    def test_json_carries_the_source_report(self):
        code, _, out = self.run_search([RESNET], self.REPORT, {"json": True})
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(payload["works"][0]["title"], RESNET["title"])
        self.assertEqual(sorted(payload["skipped"]), ["crossref", "s2"])
        self.assertEqual(payload["answered"], 2)
        self.assertEqual({x["source"]: x["state"] for x in payload["sources"]}["crossref"], "ratelimited")

    def test_answers(self):
        self.assertEqual(cli.parse_answer("\n", 3, "pick"), ("pick", 1))
        self.assertEqual(cli.parse_answer("", 3, "keep"), ("keep", 0))
        self.assertEqual(cli.parse_answer(" 2 ", 3, "pick"), ("pick", 2))
        self.assertEqual(cli.parse_answer("4", 3, "pick"), ("again", 0))
        self.assertEqual(cli.parse_answer("K", 3, "pick"), ("keep", 0))
        for text in ("s2", "s 2", "show 2"):
            self.assertEqual(cli.parse_answer(text, 3, "pick"), ("show", 2))
        self.assertEqual(cli.parse_answer("a", 3, "pick"), ("all", 0))
        self.assertEqual(cli.parse_answer("q", 3, "pick"), ("quit", 0))
        self.assertEqual(cli.parse_answer("oui", 3, "pick"), ("again", 0))


class Pick(unittest.TestCase):
    def run_pick(self, answers: str, text: str = BIB, **opts):
        async def fake_search(http, q, settings, refresh, on_event=None):
            return next(v for k, v in ANSWERS.items() if k.lower() in (q.title or q.raw).lower())

        d = Path(tempfile.mkdtemp())
        src, dst = d / "refs.bib", d / "out.bib"
        src.write_text(text, encoding="utf-8")
        args = SimpleNamespace(file=str(src), output=str(dst), new_keys=False, setting=[], refresh=False, **opts)
        err = io.StringIO()
        with mock.patch.object(cli, "_search", fake_search), mock.patch.object(sys, "stdin", Tty(answers)), \
                mock.patch.object(sys, "stderr", err):
            code = cli.cmd_pick(args, cli.Out())
        return code, dst.read_text(encoding="utf-8"), err.getvalue()

    def test_asks_only_when_there_is_a_choice(self):
        # ResNet : 2 versions -> on prend la prépublication (2) ; Adam : une seule version sûre, pas
        # de question ; Chronos : un autre papier possible -> on garde l'entrée (k) ; la dernière
        # n'est pas trouvée et reste telle quelle.
        code, out, err = self.run_pick("2\nk\n")
        self.assertEqual(code, 0)
        self.assertIn("@inproceedings{myresnet,\n  title = {arXiv 1512.03385}", out)   # clé d'origine gardée
        self.assertIn("@inproceedings{myadam,\n  title = {ICLR 2015}", out)
        self.assertIn("@misc{mychronos,", out)
        self.assertIn("@misc{mylost,", out)
        self.assertEqual(err.count("Choice ["), 2)
        self.assertIn("Wrote 2 entries", err)

    def test_show_default_and_quit(self):
        # s 2 montre le BibTeX sans choisir ; Entrée garde la recommandée ; q garde le reste tel quel.
        code, out, err = self.run_pick("s 2\n\nq\n")
        self.assertIn("@inproceedings{he2015deep,", err)          # le BibTeX montré
        self.assertIn("title = {CVPR 2016}", out)
        self.assertIn("@misc{mychronos,", out)
        self.assertNotIn("ansari2024chronos", out)

    def test_accept_all(self):
        code, out, err = self.run_pick("a\n")
        self.assertIn("title = {CVPR 2016}", out)
        self.assertIn("@inproceedings{mychronos,\n  title = {TMLR 2024}", out)   # correspondance moyenne : recommandée
        self.assertEqual(err.count("Choice ["), 1)

    def test_end_of_input_keeps_the_rest(self):
        code, out, err = self.run_pick("")
        self.assertIn("@article{myresnet,", out)
        self.assertIn("Wrote 0 entries", err)

    def test_needs_a_terminal(self):
        with mock.patch.object(sys, "stdin", io.StringIO("")), self.assertRaises(SystemExit):
            cli.cmd_pick(SimpleNamespace(file="refs.bib"), cli.Out())


if __name__ == "__main__":
    unittest.main()
