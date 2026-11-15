"""
collect.py 的 mp 断点续跑 / 逐号结局 / 退出码测试。

不依赖真实登录态和网络：用假的 mp_client 模块（FakeMPClient）注入，
文章列表按 (fakeid, begin) 确定性生成，异常按脚本编排触发。
直接运行：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import shutil
import sys
import types
import unittest
from pathlib import Path

# Windows 控制台默认 GBK，打印脚本里的 emoji 会 UnicodeEncodeError；
# 测试与系统代码页解耦，统一强制 UTF-8 输出。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import collect
import export_links
from mp_client import MPFreqError, MPSessionError

REPO_ROOT = Path(__file__).resolve().parent.parent


# ------------------------------- 假 MPClient -------------------------------
class FakeMPClient:
    """按脚本（FakeScript）模拟搜号 / 拉列表；记录每次调用的参数。"""

    def __init__(self, token, cookie, timeout=20):
        self.token, self.cookie = token, cookie
        self.script = FakeScript.current

    def resolve_fakeid(self, keyword):
        self.script.calls["resolve"].append(keyword)
        behavior = self.script.resolve_behavior.get(keyword, "ok")
        if isinstance(behavior, Exception):
            raise behavior
        if behavior is None:
            return None
        return {"fakeid": f"fk_{keyword}", "nickname": keyword}

    def list_articles(self, fakeid, begin=0, count=20):
        self.script.calls["list"].append((fakeid, begin, count))
        key = (fakeid, begin)
        if key in self.script.list_raises:
            raise self.script.list_raises[key]
        if begin in self.script.list_raises_by_begin:
            raise self.script.list_raises_by_begin[begin]
        if begin >= self.script.empty_before:
            return []
        return [
            {
                "link": f"https://mp.weixin.qq.com/s/{fakeid}-{begin}-{i}",
                "title": f"标题{begin}-{i}",
                "digest": "摘要",
                "author_name": "作者",
                "update_time": 1_700_000_000 + begin + i,
            }
            for i in range(count)
        ]


def _fake_fetch(url, timeout=20):
    return "正文"


class FakeScript:
    """一次 collect_mp 运行期间 FakeMPClient 读取的剧本。"""

    current: "FakeScript | None" = None

    def __init__(self, *, empty_before=10_000):
        self.resolve_behavior: dict = {}        # 号名 -> "ok" / None / 异常
        self.list_raises: dict = {}             # (fakeid, begin) -> 异常
        self.list_raises_by_begin: dict = {}    # begin -> 异常（不区分号）
        self.empty_before = empty_before
        self.calls = {"resolve": [], "list": []}


class MemoryStorage:
    """记录所有 save 调用的最小存储。"""

    def __init__(self):
        self.saved: dict[str, object] = {}

    def save(self, art):
        self.saved[art.guid] = art

    def close(self):
        pass


def _install_fake_mp_module():
    mod = types.ModuleType("mp_client")
    mod.MPClient = FakeMPClient
    mod.MPSessionError = MPSessionError
    mod.MPFreqError = MPFreqError
    mod.fetch_article_text = _fake_fetch
    sys.modules["mp_client"] = mod


class MpResumeTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = REPO_ROOT / "state" / f"_test_{self._testMethodName}"
        self.tmp.mkdir(parents=True, exist_ok=True)

        # 把 collect 的所有状态文件指到隔离目录，绝不碰真实 seen.json 等
        self._orig = {
            "STATE_FILE": collect.STATE_FILE,
            "MP_ACCOUNTS_FILE": collect.MP_ACCOUNTS_FILE,
            "MP_CHECKPOINT_FILE": collect.MP_CHECKPOINT_FILE,
            "MP_SESSION_FILE": collect.MP_SESSION_FILE,
        }
        collect.STATE_FILE = self.tmp / "seen.json"
        collect.MP_ACCOUNTS_FILE = self.tmp / "mp_accounts.json"
        collect.MP_CHECKPOINT_FILE = self.tmp / "mp_checkpoint.json"
        collect.MP_SESSION_FILE = self.tmp / "mp_session.json"

        # export_links 的推送追踪文件也指到隔离目录（main() 会触发 export）
        self._orig_export = (export_links.PUSHED_FILE, export_links.PENDING_FILE)
        export_links.PUSHED_FILE = self.tmp / "ima_pushed.json"
        export_links.PENDING_FILE = self.tmp / "ima_pending.json"

        self._orig_sleep = collect.time.sleep
        collect.time.sleep = lambda *_a, **_k: None
        _install_fake_mp_module()

        # 默认有一份有效登录态
        collect.save_json(collect.MP_SESSION_FILE, {"token": "t", "cookie": "c"})

    def tearDown(self):
        FakeScript.current = None
        collect.STATE_FILE = self._orig["STATE_FILE"]
        collect.MP_ACCOUNTS_FILE = self._orig["MP_ACCOUNTS_FILE"]
        collect.MP_CHECKPOINT_FILE = self._orig["MP_CHECKPOINT_FILE"]
        collect.MP_SESSION_FILE = self._orig["MP_SESSION_FILE"]
        export_links.PUSHED_FILE, export_links.PENDING_FILE = self._orig_export
        collect.time.sleep = self._orig_sleep
        sys.modules.pop("mp_client", None)   # 移除假模块，后续 import 恢复真实模块
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cfg(self, accounts, pages=1, save_full=False):
        return {
            "source": "mp",
            "save_full_content": save_full,
            "mp": {"pages": pages, "accounts": list(accounts)},
        }

    def run_collect(self, script, cfg, seen=None):
        FakeScript.current = script
        storage = MemoryStorage()
        seen = set() if seen is None else seen
        result = collect.collect_mp(cfg, storage, seen)
        # 模拟 main：落盘 seen，下一次运行重新加载
        collect.save_json(collect.STATE_FILE, sorted(seen))
        return result, storage, seen

    def load_seen(self):
        return set(collect.load_json(collect.STATE_FILE, []))

    def load_checkpoint(self):
        return collect.load_mp_checkpoint()["accounts"]


class TestMpOutcomes(MpResumeTestBase):
    def test_full_run_all_done(self):
        cfg = self.cfg(["号A", "号B"], pages=2)
        script = FakeScript()
        (new_total, errs, incomplete), storage, seen = self.run_collect(script, cfg)

        self.assertEqual(incomplete, 0)
        self.assertEqual(errs, 0)
        self.assertEqual(new_total, 80)               # 2 号 × 2 页 × 20 篇
        self.assertEqual(len(storage.saved), 80)
        cp = self.load_checkpoint()
        for name in ("号A", "号B"):
            self.assertEqual(cp[name]["outcome"], collect.MP_DONE)
            self.assertEqual(cp[name]["pages_done"], 2)
            self.assertEqual(cp[name]["new_count"], 40)
            self.assertIsNone(cp[name]["resume"])    # 完成后不留断点
        # 列表请求都从第 0 页开始
        self.assertIn(("fk_号A", 0, 20), script.calls["list"])

    def test_session_expired_mid_paging_records_breakpoint_and_continues(self):
        cfg = self.cfg(["号A", "号B"], pages=3)
        script = FakeScript()
        # 号A 翻第 2 页（begin=20）时登录态失效；号B 应照常跑完
        script.list_raises[("fk_号A", 20)] = MPSessionError("登录态失效：200003")
        (new_total, errs, incomplete), storage, _ = self.run_collect(script, cfg)

        self.assertEqual(incomplete, 1)              # 只有 A 未完成，B 不受影响
        cp = self.load_checkpoint()
        a = cp["号A"]
        self.assertEqual(a["outcome"], collect.MP_SESSION_EXPIRED)
        self.assertEqual(a["pages_done"], 1)         # 第 1 页成功处理
        self.assertEqual(a["new_count"], 20)
        self.assertEqual(a["resume"], {"fakeid": "fk_号A", "begin": 20})
        self.assertEqual(cp["号B"]["outcome"], collect.MP_DONE)
        # B 的请求确实发出了（没有被整批 break 掉）
        self.assertTrue(any(f == "fk_号B" for f, _b, _c in script.calls["list"]))
        self.assertEqual(new_total, 20 + 60)         # A 20 + B 60

    def test_resume_from_breakpoint_no_duplicate_writes(self):
        cfg = self.cfg(["号A"], pages=3)

        # 第一次：begin=20 失效
        script1 = FakeScript()
        script1.list_raises[("fk_号A", 20)] = MPSessionError("失效")
        r1, storage1, seen1 = self.run_collect(script1, cfg)
        self.assertEqual(r1[2], 1)
        self.assertEqual(len(storage1.saved), 20)

        # 第二次：登录态恢复，应从 begin=20 续翻，seen 重新从文件加载
        script2 = FakeScript()
        seen2 = self.load_seen()
        r2, storage2, _ = self.run_collect(script2, cfg, seen=seen2)
        self.assertEqual(r2[2], 0)                    # 本次全部完成
        begins = [b for _f, b, _c in script2.calls["list"]]
        self.assertEqual(begins, [20, 40, 60])        # 没有从 0 重来
        cp = self.load_checkpoint()
        self.assertEqual(cp["号A"]["outcome"], collect.MP_DONE)
        self.assertIsNone(cp["号A"]["resume"])
        # 两次合计 4 页 × 20 = 80 篇，且无任何 guid 被重复写入
        all_guids = set(storage1.saved) | set(storage2.saved)
        self.assertEqual(len(all_guids), 80)
        self.assertEqual(len(storage2.saved), 60)

    def test_rate_limited_mid_paging_also_resumes(self):
        cfg = self.cfg(["号A"], pages=2)
        script = FakeScript()
        script.list_raises[("fk_号A", 0)] = MPFreqError("被限频：200013")
        (_, _, incomplete), _, _ = self.run_collect(script, cfg)
        self.assertEqual(incomplete, 1)
        cp = self.load_checkpoint()
        self.assertEqual(cp["号A"]["outcome"], collect.MP_RATE_LIMITED)
        self.assertEqual(cp["号A"]["resume"], {"fakeid": "fk_号A", "begin": 0})

        # 续跑：begin=0 恢复正常
        script2 = FakeScript()
        seen = self.load_seen()
        (_, _, incomplete2), storage2, _ = self.run_collect(script2, cfg, seen=seen)
        self.assertEqual(incomplete2, 0)
        self.assertEqual(len(storage2.saved), 40)

    def test_rate_limited_at_search_marks_and_continues(self):
        cfg = self.cfg(["号A", "号B"], pages=1)
        script = FakeScript()
        script.resolve_behavior["号A"] = MPFreqError("搜号限频")
        (_, errs, incomplete), _, _ = self.run_collect(script, cfg)

        cp = self.load_checkpoint()
        self.assertEqual(cp["号A"]["outcome"], collect.MP_RATE_LIMITED)
        self.assertIsNone(cp["号A"]["resume"])       # 连 fakeid 都没有，不带断点
        self.assertEqual(cp["号B"]["outcome"], collect.MP_DONE)
        self.assertEqual(incomplete, 1)
        self.assertEqual(errs, 0)                     # 限频不算 err_total

    def test_search_failures(self):
        cfg = self.cfg(["号A", "号B", "号C"], pages=1)
        script = FakeScript()
        script.resolve_behavior["号A"] = None                  # 没搜到
        script.resolve_behavior["号B"] = RuntimeError("boom")  # 搜号请求异常
        (_, errs, incomplete), _, _ = self.run_collect(script, cfg)

        cp = self.load_checkpoint()
        self.assertEqual(cp["号A"]["outcome"], collect.MP_SEARCH_FAILED)
        self.assertEqual(cp["号B"]["outcome"], collect.MP_SEARCH_FAILED)
        self.assertEqual(cp["号C"]["outcome"], collect.MP_DONE)
        self.assertEqual(errs, 2)
        self.assertEqual(incomplete, 2)

    def test_fetch_error_no_resume_next_run_starts_at_zero(self):
        cfg = self.cfg(["号A"], pages=2)
        script = FakeScript()
        script.list_raises[("fk_号A", 0)] = RuntimeError("接口返回错误 500")
        (_, errs, incomplete), _, _ = self.run_collect(script, cfg)
        self.assertEqual((errs, incomplete), (1, 1))
        cp = self.load_checkpoint()
        self.assertEqual(cp["号A"]["outcome"], collect.MP_FETCH_ERROR)
        self.assertIsNone(cp["号A"]["resume"])       # 拉取异常不留断点

        script2 = FakeScript()
        seen = self.load_seen()
        self.run_collect(script2, cfg, seen=seen)
        begins = [b for _f, b, _c in script2.calls["list"]]
        self.assertEqual(begins, [0, 20])            # 下次从第 0 页重新增量

    def test_corrupt_checkpoint_falls_back_to_full_scan(self):
        # 损坏文件 + 结构不对的合法 JSON，都不应崩溃
        collect.MP_CHECKPOINT_FILE.write_text("{这不是json", encoding="utf-8")
        cfg = self.cfg(["号A"], pages=1)
        script = FakeScript()
        (_, _, incomplete), storage, _ = self.run_collect(script, cfg)
        self.assertEqual(incomplete, 0)
        self.assertEqual(len(storage.saved), 20)

        collect.MP_CHECKPOINT_FILE.write_text(json.dumps(["奇怪结构"]), encoding="utf-8")
        script2 = FakeScript()
        seen = self.load_seen()
        self.run_collect(script2, cfg, seen=seen)
        self.assertEqual([b for _f, b, _c in script2.calls["list"]], [0])
        self.assertEqual(self.load_checkpoint()["号A"]["outcome"], collect.MP_DONE)

    def test_missing_checkpoint_starts_fresh(self):
        self.assertFalse(collect.MP_CHECKPOINT_FILE.exists())
        cfg = self.cfg(["号A"], pages=1)
        (_, _, incomplete), _, _ = self.run_collect(FakeScript(), cfg)
        self.assertEqual(incomplete, 0)
        self.assertTrue(collect.MP_CHECKPOINT_FILE.exists())

    def test_missing_session_marks_all_accounts_without_crash(self):
        collect.MP_SESSION_FILE.unlink()
        cfg = self.cfg(["号A", "号B"], pages=1)
        storage = MemoryStorage()
        new_total, errs, incomplete = collect.collect_mp(cfg, storage, set())
        self.assertEqual((new_total, errs, incomplete), (0, 1, 2))
        cp = self.load_checkpoint()
        self.assertEqual(cp["号A"]["outcome"], collect.MP_SESSION_EXPIRED)
        self.assertEqual(cp["号B"]["outcome"], collect.MP_SESSION_EXPIRED)
        self.assertIsNone(cp["号A"]["resume"])

    def test_resume_ignored_when_fakeid_changed(self):
        cfg = self.cfg(["号A"], pages=1)
        # 预置一条断点，但 fakeid 与缓存里的对不上（号被重建/换主体）
        collect.save_json(collect.MP_ACCOUNTS_FILE,
                          {"号A": {"fakeid": "fk_new", "nickname": "号A"}})
        collect.save_json(collect.MP_CHECKPOINT_FILE, {"accounts": {
            "号A": {"outcome": collect.MP_SESSION_EXPIRED, "pages_done": 1,
                    "new_count": 0,
                    "resume": {"fakeid": "fk_old", "begin": 40},
                    "detail": "", "updated_at": ""}}})
        script = FakeScript()
        self.run_collect(script, cfg)
        begins = [b for _f, b, _c in script.calls["list"]]
        self.assertEqual(begins, [0])                # fakeid 不匹配 → 从 0 开始

    def test_empty_page_marks_done(self):
        cfg = self.cfg(["号A"], pages=3)
        script = FakeScript(empty_before=20)         # 第 1 页有，第 2 页空
        (_, _, incomplete), storage, _ = self.run_collect(script, cfg)
        self.assertEqual(incomplete, 0)
        self.assertEqual(len(storage.saved), 20)
        self.assertEqual(self.load_checkpoint()["号A"]["pages_done"], 1)

    def test_rerun_dedups_via_seen(self):
        cfg = self.cfg(["号A"], pages=1)
        r1, s1, seen = self.run_collect(FakeScript(), cfg)
        self.assertEqual(len(s1.saved), 20)
        seen2 = self.load_seen()
        r2, s2, _ = self.run_collect(FakeScript(), cfg, seen=seen2)
        self.assertEqual(r2[0], 0)                   # 没有新增
        self.assertEqual(len(s2.saved), 0)           # 不重复写素材库
        self.assertEqual(self.load_checkpoint()["号A"]["new_count"], 0)


class TestRssPathUnchanged(unittest.TestCase):
    def test_collect_rss_still_returns_two_tuple(self):
        cfg = {"save_full_content": False,
               "feeds": [{"name": "__自测示例__", "url": "tests/sample_feed.xml"}]}

        class _S:
            def __init__(self):
                self.n = 0

            def save(self, art):
                self.n += 1

            def close(self):
                pass

        storage, seen = _S(), set()
        result = collect.collect_rss(cfg, storage, seen)
        self.assertEqual(len(result), 2)             # 仍是 (new, err) 两元组
        self.assertIsInstance(result[0], int)
        self.assertIsInstance(result[1], int)
        self.assertGreater(storage.n, 0)


class TestMainExitCodes(MpResumeTestBase):
    def _write_config(self, accounts):
        cfg_path = self.tmp / "config.yaml"
        import yaml
        cfg_path.write_text(yaml.safe_dump({
            "source": "mp",
            "storage": "local",
            "save_full_content": False,
            "local": {"output_dir": str(self.tmp / "素材库")},
            "mp": {"pages": 1, "accounts": accounts},
        }), encoding="utf-8")
        return cfg_path

    def _run_main(self, script):
        FakeScript.current = script
        cfg_path = self._write_config(["号A"])
        old_argv = sys.argv
        sys.argv = ["collect.py", "-c", str(cfg_path)]
        try:
            return collect.main()
        finally:
            sys.argv = old_argv

    def test_exit_code_reflects_completion(self):
        # 全部完成 → 0
        rc = self._run_main(FakeScript())
        self.assertEqual(rc, 0)
        self.assertEqual(self.load_checkpoint()["号A"]["outcome"], collect.MP_DONE)

        # 再次运行中断 → 3
        bad = FakeScript()
        bad.list_raises[("fk_号A", 0)] = MPSessionError("又失效了")
        rc = self._run_main(bad)
        self.assertEqual(rc, collect.EXIT_MP_INCOMPLETE)
        self.assertEqual(self.load_checkpoint()["号A"]["outcome"],
                         collect.MP_SESSION_EXPIRED)

        # 恢复后再跑 → 回到 0，断点清除
        rc = self._run_main(FakeScript())
        self.assertEqual(rc, 0)
        self.assertIsNone(self.load_checkpoint()["号A"]["resume"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
