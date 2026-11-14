"""每日画布流水线测试。全部使用本地临时文件作为源，不依赖外网。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dailycanvas.pipeline import PublishError, run_pipeline  # noqa: E402

EDITION = "2026-11-14"


def write_source(directory: Path, name: str, items) -> Path:
    path = directory / name
    path.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    return path


def entry(eid, title, published, url=None):
    return {
        "id": eid,
        "title": title,
        "url": url or f"https://example.com/{eid}",
        "published": published,
    }


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.out = self.root / "output"
        self.config_path = self.root / "sources.json"

    def tearDown(self):
        self._tmp.cleanup()

    def write_config(self, sources, **extra):
        cfg = {"max_items": extra.get("max_items", 50), "retries": extra.get("retries", 1),
               "timeout_seconds": 1, "sources": sources}
        self.config_path.write_text(json.dumps(cfg), encoding="utf-8")

    def src(self, name, url, weight=1.0, enabled=True):
        return {"name": name, "url": url, "weight": weight, "enabled": enabled}

    def run_pipe(self, edition=EDITION):
        return run_pipeline(self.config_path, self.out, edition)

    def read_json_output(self):
        return json.loads((self.out / "daily.json").read_text(encoding="utf-8"))

    # ---------- 正常出刊 ----------

    def test_normal_publish(self):
        write_source(self.data, "a.json", [
            entry("a-1", "旧条目", "2026-11-13T10:00:00Z"),
            entry("a-2", "新条目", "2026-11-14T09:00:00Z"),
        ])
        write_source(self.data, "b.json", [
            entry("b-1", "中条目", "2026-11-14T00:00:00Z"),
        ])
        self.write_config([
            self.src("alpha", "data/a.json", weight=1.0),
            self.src("beta", "data/b.json", weight=2.0),
        ])

        result = self.run_pipe()

        self.assertEqual(result["item_count"], 3)
        doc = self.read_json_output()
        self.assertEqual(doc["meta"]["edition"], EDITION)
        self.assertEqual(doc["meta"]["item_count"], 3)
        # 新鲜度优先排序
        self.assertEqual([i["id"] for i in doc["items"]], ["a-2", "b-1", "a-1"])
        # 每条都带来源
        self.assertTrue(all(i["source"] for i in doc["items"]))
        # HTML 含期号、标题与来源
        html = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn(EDITION, html)
        self.assertIn("新条目", html)
        self.assertIn("alpha", html)

    def test_dirty_entries_dropped_but_source_ok(self):
        write_source(self.data, "dirty.json", [
            entry("ok-1", "正常条目", "2026-11-14T08:00:00Z"),
            {"title": "缺 id", "published": "2026-11-14T09:00:00Z"},
            {"id": "no-title", "published": "2026-11-14T09:00:00Z"},
            {"id": "bad-date", "title": "日期坏", "published": "not-a-date"},
        ])
        self.write_config([self.src("gamma", "data/dirty.json")])

        result = self.run_pipe()

        self.assertEqual(result["item_count"], 1)
        outcome = result["outcomes"][0]
        self.assertEqual(outcome.status, "ok")
        self.assertEqual(outcome.dropped, 3)
        self.assertEqual(self.read_json_output()["items"][0]["id"], "ok-1")

    # ---------- 单源失败降级 ----------

    def test_single_source_failure_degrades(self):
        write_source(self.data, "good.json", [
            entry("g-1", "存活源的条目", "2026-11-14T08:00:00Z"),
        ])
        self.write_config([
            self.src("good", "data/good.json"),
            self.src("broken", "data/missing.json"),  # 文件不存在
        ])

        result = self.run_pipe()

        # 仍然出刊，只含存活源的条目
        self.assertEqual(result["item_count"], 1)
        doc = self.read_json_output()
        self.assertEqual([i["id"] for i in doc["items"]], ["g-1"])
        # 失败源的结局可追溯：状态、原因、重试次数、耗时
        outcomes = {o["name"]: o for o in doc["meta"]["sources"]}
        self.assertEqual(outcomes["good"]["status"], "ok")
        broken = outcomes["broken"]
        self.assertEqual(broken["status"], "failed")
        self.assertTrue(broken["error"])
        self.assertEqual(broken["attempts"], 2)  # 1 次首发 + 1 次重试
        self.assertGreaterEqual(broken["elapsed_ms"], 0)

    def test_invalid_json_source_degrades(self):
        (self.data / "garbage.json").write_text("这不是 JSON {{{", encoding="utf-8")
        write_source(self.data, "good.json", [entry("g-1", "正常", "2026-11-14T08:00:00Z")])
        self.write_config([
            self.src("bad-json", "data/garbage.json"),
            self.src("good", "data/good.json"),
        ])

        result = self.run_pipe()

        self.assertEqual(result["item_count"], 1)
        outcomes = {o.name: o for o in result["outcomes"]}
        self.assertEqual(outcomes["bad-json"].status, "failed")
        self.assertIn("JSON", outcomes["bad-json"].error)

    # ---------- 全源失败报错 ----------

    def test_all_sources_failed_aborts(self):
        self.write_config([
            self.src("s1", "data/nope-1.json"),
            self.src("s2", "data/nope-2.json"),
        ])

        with self.assertRaises(PublishError) as ctx:
            self.run_pipe()

        self.assertIn("全部", str(ctx.exception))
        # 不发布空刊：输出目录里没有产物
        self.assertFalse((self.out / "index.html").exists())
        self.assertFalse((self.out / "daily.json").exists())

    def test_no_enabled_sources_aborts(self):
        self.write_config([self.src("off", "data/x.json", enabled=False)])
        with self.assertRaises(PublishError):
            self.run_pipe()

    # ---------- 去重 ----------

    def test_duplicates_keep_newest_only(self):
        write_source(self.data, "a.json", [
            entry("dup-1", "旧版本", "2026-11-12T10:00:00Z"),
            entry("a-only", "独有条目", "2026-11-13T10:00:00Z"),
        ])
        write_source(self.data, "b.json", [
            entry("dup-1", "新版本", "2026-11-14T10:00:00Z"),
        ])
        self.write_config([
            self.src("alpha", "data/a.json"),
            self.src("beta", "data/b.json"),
        ])

        result = self.run_pipe()

        doc = self.read_json_output()
        ids = [i["id"] for i in doc["items"]]
        self.assertEqual(ids.count("dup-1"), 1)
        kept = next(i for i in doc["items"] if i["id"] == "dup-1")
        self.assertEqual(kept["title"], "新版本")  # 保留较新一条
        self.assertEqual(result["item_count"], 2)

    # ---------- 确定性 ----------

    def test_rerun_same_day_is_deterministic(self):
        write_source(self.data, "a.json", [
            entry("a-1", "甲", "2026-11-14T08:00:00Z"),
            entry("a-2", "乙", "2026-11-14T08:00:00Z"),  # 同时刻，靠 id 定序
        ])
        write_source(self.data, "b.json", [
            entry("b-1", "丙", "2026-11-14T09:00:00Z"),
        ])
        self.write_config([
            self.src("alpha", "data/a.json", weight=1.0),
            self.src("beta", "data/b.json", weight=1.0),
        ])

        self.run_pipe()
        first_html = (self.out / "index.html").read_bytes()
        first_json = (self.out / "daily.json").read_bytes()
        self.run_pipe()
        self.assertEqual(first_html, (self.out / "index.html").read_bytes())
        self.assertEqual(first_json, (self.out / "daily.json").read_bytes())

    def test_max_items_cap(self):
        write_source(self.data, "many.json", [
            entry(f"e-{i:02d}", f"条目{i}", f"2026-11-14T{i % 24:02d}:00:00Z")
            for i in range(10)
        ])
        self.write_config([self.src("many", "data/many.json")], max_items=3)

        result = self.run_pipe()

        self.assertEqual(result["item_count"], 3)
        self.assertEqual(len(self.read_json_output()["items"]), 3)


if __name__ == "__main__":
    unittest.main()
