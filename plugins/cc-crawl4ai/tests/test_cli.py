from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

CLI = Path(__file__).parents[1] / "bin" / "crawl4ai"
loader = importlib.machinery.SourceFileLoader("crawl4ai_cli", str(CLI))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class CliTests(unittest.TestCase):
    def parse(self, *args: str) -> argparse.Namespace:
        return module.build_parser().parse_args(list(args))

    def write_fake_crawler(
        self, runtime: Path, records: list[dict[str, object]]
    ) -> None:
        binary = runtime / ".venv" / "bin" / "crwl"
        binary.parent.mkdir(parents=True)
        binary.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "args = sys.argv[1:]\n"
            "assert args[args.index('-o') + 1] == 'all'\n"
            "assert '-O' not in args\n"
            f"print(json.dumps({records!r}))\n",
            encoding="utf-8",
        )
        binary.chmod(0o755)

    def write_portable_fake_crawler(
        self, root: Path, records: list[dict[str, object]]
    ) -> Path:
        """Create a real fake ``crwl`` launcher for POSIX and Windows.

        The launcher is an executable file that the CLI runs as a subprocess
        without a shell, so the fake exercises the same invocation path as an
        installed ``crwl``.
        """
        root.mkdir(parents=True, exist_ok=True)
        payload = root / "records.json"
        payload.write_text(json.dumps(records), encoding="utf-8")
        script = root / "fake_crwl.py"
        script.write_text(
            "import json, sys\n"
            "args = sys.argv[1:]\n"
            "assert args[0] == 'crawl', args\n"
            "assert args[args.index('-o') + 1] == 'all', args\n"
            "assert '-O' not in args, args\n"
            f"print(json.dumps(json.load(open({str(payload)!r}, encoding='utf-8'))))\n",
            encoding="utf-8",
        )
        if os.name == "nt":
            launcher = root / "crwl.cmd"
            launcher.write_text(
                "@echo off\r\n"
                f'"{sys.executable}" "{script}" %*\r\n',
                encoding="utf-8",
            )
        else:
            launcher = root / "crwl"
            launcher.write_text(
                "#!/usr/bin/env python3\n"
                "import runpy\n"
                f"runpy.run_path({str(script)!r}, run_name='__main__')\n",
                encoding="utf-8",
            )
            launcher.chmod(0o755)
        return launcher

    def run_cli_deep_crawl(
        self,
        base: Path,
        records: list[dict[str, object]],
        output_format: str,
        max_pages: int,
        output_file: str | None = None,
    ) -> tuple[int, str, Path]:
        """Run ``cmd_crawl`` against the fake launcher and return its artifact."""
        project = base / "project"
        project.mkdir(exist_ok=True)
        launcher = self.write_portable_fake_crawler(base / "fake-runtime", records)
        arguments = [
            "crawl",
            "https://example.com/",
            "--project-root",
            str(project),
            "--runtime-root",
            str(base / "runtime"),
            "--deep-crawl",
            "bfs",
            "--max-pages",
            str(max_pages),
            "--output-format",
            output_format,
        ]
        if output_file:
            arguments += ["--output-file", output_file]
        args = self.parse(*arguments)
        stream = StringIO()
        with (
            patch.object(module, "_resolve_tools", return_value=(launcher, None, None)),
            redirect_stdout(stream),
        ):
            code = module.cmd_crawl(args)
        stdout_text = stream.getvalue()
        printed = re.search(r"Full output saved to: (.+)", stdout_text)
        self.assertIsNotNone(printed, "CLI did not report its artifact path")
        artifact = Path(printed.group(1).strip())
        self.assertTrue(artifact.is_file(), f"missing artifact {artifact}")
        if output_file:
            self.assertEqual(artifact, project / output_file)
        else:
            self.assertTrue(
                artifact.is_relative_to(project / ".crawl4ai" / "outputs")
            )
        return code, stdout_text, artifact

    def test_bm25_selects_relevant_structural_chunk(self) -> None:
        content = "# Intro\n\nGeneral words only.\n\n# Pricing\n\nEnterprise price is 99 dollars."
        filtered, matched, total = module.filter_bm25(content, "enterprise price", 0.1)
        self.assertEqual(total, 2)
        self.assertEqual(matched, 1)
        self.assertIn("Enterprise price", filtered)
        self.assertNotIn("General words", filtered)

    def test_image_references_resolve_relative_urls_and_deduplicate(self) -> None:
        content = module.append_image_references(
            "Body",
            [{"src": "/img/a.png", "alt": "Diagram"}, {"src": "/img/a.png"}],
            "https://example.com/docs/page",
            "markdown",
        )
        self.assertEqual(content.count("https://example.com/img/a.png"), 1)
        self.assertIn("![Diagram]", content)

    def test_deep_crawl_url_normalization_matches_upstream_semantics(self) -> None:
        base = "https://DOCS.example.com/guide"
        self.assertEqual(
            module.normalize_deep_crawl_url(
                "https://DOCS.example.com/guide/#part", base
            ),
            "https://docs.example.com/guide/",
        )
        self.assertEqual(
            module.normalize_deep_crawl_url(
                "https://docs.example.com/guide?b=2&utm_source=x&a=1", base
            ),
            "https://docs.example.com/guide?b=2&a=1",
        )
        self.assertNotEqual(
            module.normalize_deep_crawl_url("https://docs.example.com/guide", base),
            module.normalize_deep_crawl_url("https://docs.example.com/guide/", base),
        )

    def test_deep_crawl_fitted_markdown_serializes_structured_records(self) -> None:
        pages = [
            {
                "url": "https://example.com/a",
                "markdown": {"raw_markdown": "raw", "fit_markdown": "fitted"},
            }
        ]
        self.assertIn("fitted", module.serialize_deep_crawl_pages(pages, "md-fit"))
        self.assertNotIn("raw", module.serialize_deep_crawl_pages(pages, "md-fit"))

    def test_deep_crawl_outcome_distinguishes_success_and_failure(self) -> None:
        pages = [
            {
                "url": "https://example.com/ok",
                "markdown": {"raw_markdown": "body", "fit_markdown": "fit"},
                "success": True,
            },
            {
                "url": "https://example.com/flagged",
                "markdown": {"raw_markdown": "body"},
                "success": False,
            },
            {
                "url": "https://example.com/empty",
                "markdown": {"raw_markdown": "   "},
                "success": True,
            },
            {
                "url": "https://example.com/missing",
                "markdown": {},
                "success": True,
            },
            {"url": "https://example.com/no-markdown", "success": True},
        ]
        outcomes = module.classify_deep_crawl_pages(pages, "markdown")
        self.assertEqual(
            [outcome["status"] for outcome in outcomes],
            ["success", "failure", "failure", "failure", "failure"],
        )
        self.assertNotIn("reason", outcomes[0])
        self.assertEqual(outcomes[1]["reason"], "upstream reported success=false")
        self.assertTrue(outcomes[2]["reason"])
        self.assertIn("raw_markdown", outcomes[3]["reason"])
        self.assertEqual(outcomes[4]["reason"], "no markdown body")
        for outcome in outcomes:
            self.assertTrue(outcome["url"].startswith("https://example.com/"))

    def test_deep_crawl_failure_keeps_upstream_error_message(self) -> None:
        pages = [
            {
                "url": "https://example.com/upstream-error",
                "success": False,
                "error_message": "  HTTP 503\n\nservice unavailable  ",
            },
            {
                "url": "https://example.com/empty-with-error",
                "markdown": {"raw_markdown": ""},
                "error_message": "timeout",
            },
        ]
        outcomes = module.classify_deep_crawl_pages(pages, "markdown")
        self.assertEqual(outcomes[0]["reason"], "HTTP 503 service unavailable")
        self.assertEqual(outcomes[1]["reason"], "timeout")

    def test_deep_crawl_all_counts_either_markdown_view_as_success(self) -> None:
        pages = [
            {"url": "https://example.com/raw", "markdown": {"raw_markdown": "raw"}},
            {"url": "https://example.com/fit", "markdown": {"fit_markdown": "fit"}},
        ]
        outcomes = module.classify_deep_crawl_pages(pages, "all")
        self.assertEqual(
            [outcome["status"] for outcome in outcomes], ["success", "success"]
        )
        fitted_only = module.classify_deep_crawl_pages(
            [{"url": "https://example.com/fit", "markdown": {"fit_markdown": "fit"}}],
            "markdown",
        )
        self.assertEqual(fitted_only[0]["status"], "failure")

    def test_deep_crawl_all_format_marks_outcome_per_record(self) -> None:
        pages = [
            {"url": "https://example.com/a", "markdown": {"raw_markdown": "A"}},
            {"url": "https://example.com/b", "success": False},
        ]
        serialized = module.serialize_deep_crawl_pages(pages, "all")
        records = json.loads(serialized)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0][module.DEEP_CRAWL_OUTCOME_FIELD], "success")
        self.assertNotIn(module.DEEP_CRAWL_REASON_FIELD, records[0])
        self.assertEqual(records[1][module.DEEP_CRAWL_OUTCOME_FIELD], "failure")
        self.assertIn("success=false", records[1][module.DEEP_CRAWL_REASON_FIELD])
        self.assertEqual(records[0]["url"], "https://example.com/a")

    def test_deep_crawl_markdown_outcomes_append_at_end_without_failed_bodies(
        self,
    ) -> None:
        pages = [
            {
                "url": "https://example.com/ok",
                "markdown": {"raw_markdown": "KEEP-ME"},
                "success": True,
            },
            {
                "url": "https://example.com/broken",
                "markdown": {"raw_markdown": "DROP-ME"},
                "success": False,
            },
        ]
        serialized = module.serialize_deep_crawl_pages(pages, "markdown")
        self.assertIn("KEEP-ME", serialized)
        self.assertNotIn("DROP-ME", serialized)
        outcome_section = serialized.index("# Page outcomes")
        self.assertGreater(outcome_section, serialized.index("KEEP-ME"))
        self.assertIn("FAILED https://example.com/broken", serialized)
        self.assertNotIn("FAILED https://example.com/ok", serialized)
        self.assertNotIn("truncat", serialized.lower())

    def test_deep_crawl_truncation_only_at_limit(self) -> None:
        pages = [
            {"url": f"https://example.com/{index}", "markdown": {"raw_markdown": "x"}}
            for index in range(2)
        ]
        truncation = {"returned_records": 2, "saved_pages": 2, "max_pages": 2}
        limited = module.serialize_deep_crawl_pages(pages, "markdown", truncation)
        self.assertIn("--max-pages limit of 2", limited)
        self.assertIn("saved 2 unique pages", limited)
        self.assertIn("may be truncated", limited)
        unlimited = module.serialize_deep_crawl_pages(pages, "markdown", None)
        self.assertNotIn("truncat", unlimited.lower())
        records = json.loads(
            module.serialize_deep_crawl_pages(pages, "all", truncation)
        )
        marker = records[0][module.DEEP_CRAWL_TRUNCATION_FIELD]
        self.assertTrue(marker["may_be_truncated"])
        plain = json.loads(module.serialize_deep_crawl_pages(pages, "all", None))
        self.assertNotIn(module.DEEP_CRAWL_TRUNCATION_FIELD, plain[0])

    def test_missing_markdown_table_is_restored(self) -> None:
        crawl_markdown = "| Name | Price |\n| --- | --- |\n| A | 10 |"
        restored = module.append_markdown_tables(
            "Product list", crawl_markdown, "markdown"
        )
        self.assertIn("| Name | Price |", restored)
        self.assertIn("| A | 10 |", restored)

    def test_artifact_path_is_project_absolute_and_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve()
            path = module._artifact_path(
                project, "https://www.Example.com/a/b?q=x", "markdown", None
            )
            self.assertTrue(path.is_absolute())
            self.assertTrue(
                str(path).startswith(
                    str(project / ".crawl4ai" / "outputs" / "example.com")
                )
            )
            self.assertEqual(path.suffix, ".md")

    def test_all_artifacts_use_json_for_regular_and_deep_crawl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve()
            regular = module._artifact_path(project, "https://example.com", "all", None)
            args = self.parse(
                "crawl",
                "https://example.com",
                "--output-format",
                "all",
                "--deep-crawl",
                "bfs",
                "--max-pages",
                "2",
            )
            module._validate_crawl(args)
            deep = module._artifact_path(project, args.url, args.output_format, None)
            self.assertEqual(regular.suffix, ".json")
            self.assertEqual(deep.suffix, ".json")

    def test_build_args_keeps_values_as_individual_argv_items(self) -> None:
        args = self.parse(
            "crawl",
            "https://example.com",
            "--question",
            "x; echo unsafe",
            "--timeout",
            "5",
        )
        values = module._build_crwl_args(args, None)
        self.assertIn("x; echo unsafe", values)
        self.assertNotIn("echo", values)
        self.assertEqual(values[-1], "https://example.com")

    def test_validation_rejects_unbounded_deep_crawl(self) -> None:
        args = self.parse("crawl", "https://example.com", "--deep-crawl", "bfs")
        with self.assertRaises(module.CliError):
            module._validate_crawl(args)

    def test_validation_rejects_json_without_extractor(self) -> None:
        args = self.parse("crawl", "https://example.com", "--output-format", "json")
        with self.assertRaises(module.CliError):
            module._validate_crawl(args)

    def test_validation_rejects_text_without_trafilatura(self) -> None:
        args = self.parse("crawl", "https://example.com", "--output-format", "text")
        with self.assertRaises(module.CliError):
            module._validate_crawl(args)

    def test_validation_rejects_two_json_strategies(self) -> None:
        args = self.parse(
            "crawl",
            "https://example.com",
            "--output-format",
            "json",
            "--json-extract",
            "products",
            "--schema-path",
            "/tmp/schema.json",
            "--extraction-config",
            "/tmp/config.yaml",
        )
        with self.assertRaises(module.CliError):
            module._validate_crawl(args)

    def test_validation_rejects_trafilatura_deep_crawl(self) -> None:
        args = self.parse(
            "crawl",
            "https://example.com",
            "--extractor",
            "trafilatura",
            "--deep-crawl",
            "bfs",
            "--max-pages",
            "2",
        )
        with self.assertRaises(module.CliError):
            module._validate_crawl(args)

    def test_validation_keeps_text_and_json_invalid_for_deep_crawl(self) -> None:
        for output_format in ("text", "json"):
            with self.subTest(output_format=output_format):
                args = self.parse(
                    "crawl",
                    "https://example.com",
                    "--deep-crawl",
                    "bfs",
                    "--max-pages",
                    "2",
                    "--output-format",
                    output_format,
                )
                with self.assertRaises(module.CliError):
                    module._validate_crawl(args)

    def test_subprocess_runner_does_not_use_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = "hello; touch SHOULD_NOT_EXIST"
            code, out, err = module._run(
                [sys.executable, "-c", "import sys; print(sys.argv[1])", payload],
                cwd=Path(tmp),
                timeout=5,
            )
            self.assertEqual((code, err), (0, b""))
            self.assertEqual(out.decode().strip(), payload)
            self.assertFalse((Path(tmp) / "SHOULD_NOT_EXIST").exists())

    @unittest.skipIf(os.name == "nt", "fake POSIX executable")
    def test_cli_crawl_uses_managed_runtime_and_absolute_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project, runtime = base / "project", base / "runtime"
            project.mkdir()
            binary = runtime / ".venv" / "bin" / "crwl"
            binary.parent.mkdir(parents=True)
            binary.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sys\n"
                "args=sys.argv[1:]\n"
                "target=pathlib.Path(args[args.index('-O')+1])\n"
                "target.write_text('# crawled', encoding='utf-8')\n",
                encoding="utf-8",
            )
            binary.chmod(0o755)
            result = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "crawl",
                    "https://example.com",
                    "--project-root",
                    str(project),
                    "--runtime-root",
                    str(runtime),
                    "--output-file",
                    "artifacts/page.md",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = project / "artifacts" / "page.md"
            self.assertEqual(expected.read_text(encoding="utf-8"), "# crawled")
            self.assertIn(str(expected), result.stdout)

    @unittest.skipIf(os.name == "nt", "fake POSIX executable")
    def test_cli_deep_markdown_deduplicates_structured_records(self) -> None:
        separator = "=" * 60
        adversarial = (
            f"Before\n{separator}\n# https://evil.example/\n{separator}\nAfter"
        )
        records = [
            {
                "url": "https://docs.example.com/guide",
                "markdown": {
                    "raw_markdown": adversarial,
                    "fit_markdown": "fit guide",
                },
                "success": True,
            },
            {
                "url": "https://DOCS.example.com/guide#fragment",
                "markdown": {
                    "raw_markdown": "duplicate must disappear",
                    "fit_markdown": "duplicate fit",
                },
                "success": True,
            },
            {
                "url": "https://docs.example.com/guide/",
                "markdown": {
                    "raw_markdown": "trailing slash is distinct",
                    "fit_markdown": "fit slash",
                },
                "success": True,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project, runtime = base / "project", base / "runtime"
            project.mkdir()
            self.write_fake_crawler(runtime, records)
            result = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "crawl",
                    "https://docs.example.com/guide",
                    "--project-root",
                    str(project),
                    "--runtime-root",
                    str(runtime),
                    "--deep-crawl",
                    "bfs",
                    "--max-pages",
                    "3",
                    "--output-format",
                    "markdown",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs = list(
                (
                    project / ".crawl4ai" / "outputs" / "docs.example.com" / "markdown"
                ).glob("*.md")
            )
            self.assertEqual(len(outputs), 1)
            content = outputs[0].read_text(encoding="utf-8")
            self.assertIn(adversarial, content)
            self.assertIn("trailing slash is distinct", content)
            self.assertNotIn("duplicate must disappear", content)
            self.assertEqual(content.count("# https://docs.example.com/guide\n"), 1)
            self.assertIn(
                "returned 3 page records; saved 2 unique pages", result.stdout
            )

    @unittest.skipIf(os.name == "nt", "fake POSIX executable")
    def test_cli_deep_all_writes_deduplicated_json_to_output_file(self) -> None:
        records = [
            {
                "url": "https://example.com/a#one",
                "markdown": {"raw_markdown": "first", "fit_markdown": "fit first"},
                "metadata": {"title": "kept intact"},
            },
            {
                "url": "https://example.com/a#two",
                "markdown": {"raw_markdown": "second", "fit_markdown": "fit second"},
                "metadata": {"title": "duplicate"},
            },
            {
                "url": "https://example.com/a/",
                "markdown": {"raw_markdown": "slash", "fit_markdown": "fit slash"},
                "metadata": {"title": "distinct"},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project, runtime = base / "project", base / "runtime"
            project.mkdir()
            self.write_fake_crawler(runtime, records)
            result = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "crawl",
                    "https://example.com/a",
                    "--project-root",
                    str(project),
                    "--runtime-root",
                    str(runtime),
                    "--deep-crawl",
                    "bfs",
                    "--max-pages",
                    "3",
                    "--output-format",
                    "all",
                    "--output-file",
                    "artifacts/deep.json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            output = project / "artifacts" / "deep.json"
            pages = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(pages), 2)
            self.assertEqual(pages[0]["metadata"]["title"], "kept intact")
            self.assertEqual(pages[1]["url"], "https://example.com/a/")
            self.assertIn(str(output), result.stdout)

    def test_cli_deep_markdown_reports_page_outcomes_and_omits_failed_bodies(
        self,
    ) -> None:
        records = [
            {
                "url": "https://example.com/good",
                "markdown": {"raw_markdown": "GOOD-BODY"},
                "success": True,
            },
            {
                "url": "https://example.com/failed",
                "markdown": {"raw_markdown": "FAILED-BODY"},
                "success": False,
            },
            {
                "url": "https://example.com/empty",
                "markdown": {"raw_markdown": "  "},
                "success": True,
            },
            {
                "url": "https://example.com/good#duplicate",
                "markdown": {"raw_markdown": "DUPLICATE-BODY"},
                "success": True,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            code, stdout, artifact = self.run_cli_deep_crawl(
                base, records, "markdown", max_pages=4
            )
            self.assertEqual(code, 0)
            content = artifact.read_text(encoding="utf-8")
            self.assertIn("GOOD-BODY", content)
            self.assertNotIn("FAILED-BODY", content)
            self.assertNotIn("DUPLICATE-BODY", content)
            self.assertIn("- SUCCESS https://example.com/good", content)
            self.assertIn("FAILED https://example.com/failed", content)
            self.assertIn("success=false", content)
            self.assertIn("FAILED https://example.com/empty", content)
            self.assertEqual(content.count("- FAILED https://example.com/good"), 0)
            self.assertEqual(content.count("- SUCCESS https://example.com/good"), 1)
            # The outcome list is appended after the body, not interleaved with it.
            self.assertGreater(
                content.index("# Page outcomes"),
                content.index("GOOD-BODY"),
            )
            self.assertIn("saved 3 unique pages", stdout)
            self.assertIn("1 successful, 2 failed", stdout)
            self.assertIn("WARNING", stdout)
            self.assertIn("limit of 4", stdout)
            self.assertIn("truncated", content)
            self.assertTrue(artifact.is_absolute())

    def test_cli_deep_all_records_carry_outcome_and_truncation(self) -> None:
        records = [
            {"url": "https://example.com/a", "markdown": {"raw_markdown": "A"}},
            {"url": "https://example.com/b", "success": False},
            {"url": "https://example.com/c", "markdown": {"raw_markdown": "C"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            code, stdout, artifact = self.run_cli_deep_crawl(
                base, records, "all", max_pages=3, output_file="deep/all.json"
            )
            self.assertEqual(code, 0)
            page_records = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(len(page_records), 3)
            outcomes = [
                record[module.DEEP_CRAWL_OUTCOME_FIELD] for record in page_records
            ]
            self.assertEqual(outcomes, ["success", "failure", "success"])
            self.assertIn("reason", page_records[1])
            for record in page_records:
                self.assertTrue(
                    record[module.DEEP_CRAWL_TRUNCATION_FIELD]["may_be_truncated"]
                )
            self.assertIn("may be truncated", stdout)

    def test_cli_deep_crawl_does_not_flag_truncation_below_limit(self) -> None:
        records = [
            {"url": "https://example.com/a", "markdown": {"raw_markdown": "A"}},
            {"url": "https://example.com/b", "markdown": {"raw_markdown": "B"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            code, stdout, artifact = self.run_cli_deep_crawl(
                base, records, "markdown", max_pages=10
            )
            self.assertEqual(code, 0)
            content = artifact.read_text(encoding="utf-8")
            self.assertNotIn("truncat", content.lower())
            self.assertNotIn("WARNING", stdout)
            self.assertNotIn("truncat", stdout.lower())
            self.assertIn("saved 2 unique pages", stdout)

    def test_cli_deep_all_is_not_truncated_below_limit(self) -> None:
        records = [
            {"url": "https://example.com/a", "markdown": {"raw_markdown": "A"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            code, stdout, artifact = self.run_cli_deep_crawl(
                base, records, "all", max_pages=3, output_file="deep/below.json"
            )
            self.assertEqual(code, 0)
            page_records = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertNotIn(module.DEEP_CRAWL_TRUNCATION_FIELD, page_records[0])
            self.assertNotIn("truncat", stdout.lower())

    def test_cli_deep_crawl_with_only_failed_pages_still_succeeds(self) -> None:
        records = [
            {"url": "https://example.com/a", "success": False},
            {"url": "https://example.com/b", "markdown": {}, "success": True},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            code, stdout, artifact = self.run_cli_deep_crawl(
                base, records, "markdown", max_pages=2
            )
            self.assertEqual(code, 0)
            content = artifact.read_text(encoding="utf-8")
            self.assertIn("Unique pages: 2 (successful: 0, failed: 2)", content)
            self.assertNotIn("# https://example.com/a\n", content)

    def test_cli_deep_crawl_keeps_generated_paths_and_never_overwrites(self) -> None:
        records = [
            {"url": "https://example.com/a", "markdown": {"raw_markdown": "A"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = self.run_cli_deep_crawl(base, records, "markdown", max_pages=1)
            second = self.run_cli_deep_crawl(base, records, "markdown", max_pages=1)
            folder = base / "project" / ".crawl4ai" / "outputs" / "example.com"
            artifacts = sorted((folder / "markdown").glob("*.md"))
            self.assertEqual(len(artifacts), 2)
            self.assertNotEqual(first[2], second[2])
            self.assertIn(first[2], artifacts)
            self.assertIn(second[2], artifacts)
            for artifact in artifacts:
                self.assertTrue(artifact.read_text(encoding="utf-8").startswith("\n\n"))
            self.assertTrue(second[2].is_relative_to(folder / "markdown"))

    @unittest.skipIf(os.name == "nt", "fake POSIX executables")
    def test_skip_browser_is_partial_and_invalidates_old_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project, runtime = base / "project", base / "runtime"
            project.mkdir()
            (runtime / module.BROWSER_MARKER).parent.mkdir(parents=True)
            (runtime / module.BROWSER_MARKER).write_text("stale", encoding="utf-8")
            args = self.parse(
                "install",
                "--scope",
                "custom",
                "--directory",
                str(runtime),
                "--project-root",
                str(project),
                "--skip-browser",
            )
            crawl = runtime / ".venv" / "bin" / "crwl"
            traf = runtime / ".trafilatura-venv" / "bin" / "trafilatura"
            for executable in (crawl, traf):
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executable.chmod(0o755)
            output = StringIO()
            with (
                patch.object(module, "_run_visible"),
                patch.object(module, "_probe_executable", return_value=(True, "ok")),
                patch.object(
                    module,
                    "_runtime_identity",
                    return_value={
                        "crwl": str(crawl),
                        "trafilatura": str(traf),
                        "crawl4ai": "0.9.4",
                        "trafilatura_version": "2.2.0",
                        "fingerprint": "new",
                    },
                ),
                redirect_stdout(output),
            ):
                result = module.cmd_install(args)
            self.assertEqual(result, 2)
            self.assertIn("Expected duration: about 5–15 minutes", output.getvalue())
            self.assertIn("[1/7]", output.getvalue())
            self.assertIn("[6/7]", output.getvalue())
            self.assertIn("elapsed:", output.getvalue())
            self.assertIn("NOT verified", output.getvalue())
            self.assertNotIn("Browser setup verified", output.getvalue())
            self.assertFalse((runtime / module.BROWSER_MARKER).exists())
            state = json.loads((runtime / module.RUNTIME_STATE).read_text())
            self.assertEqual(state["fingerprint"], "new")

    @unittest.skipIf(os.name == "nt", "fake POSIX executables")
    def test_browser_setup_uses_runtime_not_project_as_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project, runtime = base / "project", base / "runtime"
            project.mkdir()
            crawl = runtime / ".venv" / "bin" / "crwl"
            traf = runtime / ".trafilatura-venv" / "bin" / "trafilatura"
            setup = runtime / ".venv" / "bin" / "crawl4ai-setup"
            for executable in (crawl, traf, setup):
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executable.chmod(0o755)
            args = self.parse(
                "install",
                "--scope",
                "custom",
                "--directory",
                str(runtime),
                "--project-root",
                str(project),
            )
            with (
                patch.object(module, "_run_visible"),
                patch.object(module, "_probe_executable", return_value=(True, "ok")),
                patch.object(
                    module, "_runtime_identity", return_value={"fingerprint": "current"}
                ),
                patch.object(module, "_run", return_value=(0, b"", b"")) as run,
                redirect_stdout(StringIO()),
            ):
                self.assertEqual(module.cmd_install(args), 0)
            setup_call = run.call_args
            self.assertEqual(setup_call.kwargs["cwd"].resolve(), runtime.resolve())
            self.assertEqual(
                Path(setup_call.kwargs["env"]["CRAWL4_AI_BASE_DIRECTORY"]).resolve(),
                runtime.resolve(),
            )
            self.assertFalse((project / "global.yml").exists())

    @unittest.skipIf(os.name == "nt", "fake POSIX executables")
    def test_status_probes_executables_and_rejects_stale_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project, runtime = base / "project", base / "runtime"
            project.mkdir()
            crawl = runtime / ".venv" / "bin" / "crwl"
            traf = runtime / ".trafilatura-venv" / "bin" / "trafilatura"
            for executable in (crawl, traf):
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
                executable.chmod(0o755)
            module._write_json_atomic(
                runtime / module.BROWSER_MARKER,
                {"verified_at": "yesterday", "runtime_fingerprint": "stale"},
            )
            args = self.parse(
                "status",
                "--project-root",
                str(project),
                "--runtime-root",
                str(runtime),
            )
            output = StringIO()
            with redirect_stdout(output):
                result = module.cmd_status(args)
            self.assertEqual(result, 1)
            self.assertIn("Crawl4AI: broken", output.getvalue())
            self.assertIn("Trafilatura: broken", output.getvalue())
            self.assertIn("Browser: unverified", output.getvalue())

            for executable in (crawl, traf):
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            with (
                patch.object(
                    module,
                    "_runtime_identity",
                    return_value={"fingerprint": "current"},
                ),
                redirect_stdout(output := StringIO()),
            ):
                result = module.cmd_status(args)
            self.assertEqual(result, 2)
            self.assertIn("Browser: unverified", output.getvalue())

    def test_browser_marker_requires_current_runtime_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module._write_json_atomic(
                root / module.BROWSER_MARKER,
                {"verified_at": "now", "runtime_fingerprint": "one"},
            )
            self.assertEqual(
                module._browser_verification(root, {"fingerprint": "one"}), "now"
            )
            self.assertIsNone(
                module._browser_verification(root, {"fingerprint": "two"})
            )

    def test_clear_cache_preserves_outputs_and_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            for directory in ("cache", "robots", "outputs", "runtime"):
                target = project / ".crawl4ai" / directory
                target.mkdir(parents=True)
                (target / "data").write_text("x", encoding="utf-8")
            args = self.parse("clear-cache", "--project-root", str(project))
            with redirect_stdout(StringIO()):
                self.assertEqual(module.cmd_clear_cache(args), 0)
            self.assertFalse((project / ".crawl4ai" / "cache").exists())
            self.assertFalse((project / ".crawl4ai" / "robots").exists())
            self.assertTrue((project / ".crawl4ai" / "outputs" / "data").exists())
            self.assertTrue((project / ".crawl4ai" / "runtime" / "data").exists())

    @unittest.skipIf(os.name == "nt", "fake POSIX executables")
    def test_smoke_test_records_current_browser_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project, runtime = base / "project", base / "runtime"
            project.mkdir()
            crawl = runtime / ".venv" / "bin" / "crwl"
            traf = runtime / ".trafilatura-venv" / "bin" / "trafilatura"
            crawl.parent.mkdir(parents=True)
            traf.parent.mkdir(parents=True)
            crawl.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "print(json.dumps({'html': '<html><body>Hello</body></html>'}))\n",
                encoding="utf-8",
            )
            traf.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "data=sys.stdin.read()\n"
                "assert 'Hello' in data\n"
                "print('# Hello')\n",
                encoding="utf-8",
            )
            crawl.chmod(0o755)
            traf.chmod(0o755)
            args = self.parse(
                "test",
                "--project-root",
                str(project),
                "--runtime-root",
                str(runtime),
                "--timeout",
                "5",
            )
            with redirect_stdout(StringIO()):
                self.assertEqual(module.cmd_test(args), 0)
            identity = module._runtime_identity(crawl, traf, project)
            self.assertIsNotNone(module._browser_verification(runtime, identity))
            self.assertTrue((runtime / module.RUNTIME_STATE).is_file())

    @unittest.skipIf(os.name == "nt", "fake POSIX executable")
    def test_trafilatura_pipeline_writes_extract_and_raw_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            crawl_json = project / "crawl.json"
            crawl_json.write_text(
                json.dumps(
                    {
                        "html": "<html><body><h1>Hello</h1></body></html>",
                        "markdown": {"raw_markdown": "# Hello"},
                        "media": {"images": []},
                    }
                ),
                encoding="utf-8",
            )
            executable = project / "trafilatura"
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "html = sys.stdin.read()\n"
                "assert '<h1>Hello</h1>' in html\n"
                "print('# Extracted\\n\\nHello world')\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            args = self.parse(
                "crawl",
                "https://example.com",
                "--extractor",
                "trafilatura",
                "--output-format",
                "markdown",
            )
            output = project / "result.md"
            extracted, raw, stats = module._extract_trafilatura(
                crawl_json, executable, args, project, output
            )
            self.assertEqual(stats, None)
            self.assertIn("# Extracted", extracted.read_text(encoding="utf-8"))
            self.assertIn("<h1>Hello</h1>", raw.read_text(encoding="utf-8"))

    @unittest.skipIf(os.name == "nt", "POSIX process-group behavior")
    def test_timeout_terminates_child_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid_file = root / "child.pid"
            script = (
                "import pathlib, subprocess, sys, time\n"
                "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
                "time.sleep(60)\n"
            )
            with self.assertRaises(module.CliError):
                module._run(
                    [sys.executable, "-c", script, str(pid_file)],
                    cwd=root,
                    timeout=1,
                )
            child_pid = int(pid_file.read_text())
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                result = subprocess.run(
                    ["ps", "-p", str(child_pid), "-o", "stat="],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if not result.stdout.strip() or result.stdout.strip().startswith("Z"):
                    break
                time.sleep(0.05)
            else:
                self.fail(f"child process {child_pid} survived timeout cleanup")

    def test_requirements_are_exactly_pinned(self) -> None:
        expected = {
            "crawl4ai.txt": "crawl4ai==0.9.4",
            "trafilatura.txt": "trafilatura==2.2.0",
        }
        for filename, requirement in expected.items():
            lines = [
                line.strip()
                for line in (module.REQUIREMENTS_DIR / filename)
                .read_text()
                .splitlines()
                if line.strip() and not line.startswith("#")
            ]
            self.assertEqual(lines, [requirement])

    def test_cli_help(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CLI), "crawl", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--bm25-query", result.stdout)
        self.assertIn("--deep-crawl", result.stdout)


if __name__ == "__main__":
    unittest.main()
