# -*- coding: utf-8 -*-
"""详情补数改造验收（离线，不出网）。

覆盖：
  1) 正常补数全部成功 + 同日重跑幂等
  2) 部分详情失败：跳过不影响同词入库，尝试/成功/失败计数正确
  3) 补数中途命中风控：当前词记录（含已补想要数）已入库，熔断照常触发，
     汇总可见该词补数进度，后续词不再采集
  4) 搜索阶段命中风控：无记录可存，熔断 + 退出语义照常
  5) main() 端到端（mock 路径）：退出码 2、crawl_runs 状态、控制台/告警含补数进度

跑法: python -I test_crawler_detail.py   （-I 下脚本目录不入 sys.path，首行自举）
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
import contextlib
import io
import shutil
import tempfile
from pathlib import Path

import config
import crawler
import db
import main as main_mod
from fetcher import BaseFetcher, CaptchaError
from parser import ItemRecord

RUN_DATE = "2026-11-14"
WORDS = ["考研资料", "剪辑教程", "Procreate笔刷"]


def make_records(prefix, n, want=None):
    """造 n 条搜索结果（want_count 初始为 None，模拟 2026 列表不带想要数）。"""
    return [ItemRecord(item_id=f"{prefix}-{i:03d}", title=f"{prefix}商品{i}",
                       price=10.0 + i, want_count=want, area="上海",
                       seller_nick=f"卖家{i}", publish_time="2026-11-01",
                       pic_url=f"http://x/{prefix}{i}.jpg") for i in range(n)]


class ScriptedFetcher(BaseFetcher):
    """可编程离线抓取器。

    n_per_word:    {词: 搜索结果条数}，page>=2 一律 EMPTY（不改变翻页行为）
    fail_on:       get_want 返回 None 的 item_id 集合（单条详情失败）
    captcha_on:    get_want 第几次调用（全局序号，1 起）抛 CaptchaError
    captcha_search: 搜索阶段对哪个词直接抛 CaptchaError
    """
    def __init__(self, n_per_word, fail_on=None, captcha_on=None, captcha_search=None):
        self.n_per_word = n_per_word
        self.fail_on = set(fail_on or [])
        self.captcha_on = captcha_on
        self.captcha_search = captcha_search
        self.want_calls = 0
        self.last_word = None

    def search(self, keyword, page):
        if keyword == self.captcha_search:
            raise CaptchaError(f"{keyword} 第1页 命中风控: rgV587")
        if page > 1:
            from fetcher import FetchResult
            return FetchResult(True, items=[], error="EMPTY")
        self.last_word = keyword
        from fetcher import FetchResult
        return FetchResult(True, items=make_records(keyword, self.n_per_word.get(keyword, 0)))

    def get_want(self, item_id):
        self.want_calls += 1
        if self.captcha_on is not None and self.want_calls == self.captcha_on:
            raise CaptchaError(f"详情 {item_id} 命中风控: FAIL_SYS_USER_VALIDATE")
        if item_id in self.fail_on:
            return None
        return 100

    def suggest(self, keyword):
        return []


@contextlib.contextmanager
def patched_env(sample_size=15):
    """关睡眠 + 固定抽样数，跑完还原。"""
    saved_sleep, saved_size = crawler.time.sleep, config.DETAIL_SAMPLE_SIZE
    crawler.time.sleep = lambda *_: None
    config.DETAIL_SAMPLE_SIZE = sample_size
    try:
        yield
    finally:
        crawler.time.sleep = saved_sleep
        config.DETAIL_SAMPLE_SIZE = saved_size


def fresh_db(tmp, words):
    conn = db.get_conn(tmp / "t.db")
    db.init_db(conn)
    kw = db.sync_keywords(conn, words)
    return conn, kw


def want_values(conn, kw_id):
    rows = db.get_snapshot_rows(conn, RUN_DATE, kw_id)
    return {r["item_id"]: r["want_count"] for r in rows}


# ---------- 1) 全部成功 + 幂等 ----------

def test_all_success():
    tmp = Path(tempfile.mkdtemp(prefix="cd_1_"))
    try:
        conn, kw = fresh_db(tmp, WORDS)
        with patched_env():
            s = crawler.crawl_all(conn, ScriptedFetcher({w: 6 for w in WORDS}), kw, RUN_DATE)
        assert not s.captcha and s.keywords_failed == 0
        assert s.keywords_ok == 3 and s.items_saved == 18
        for w in WORDS:
            st = s.detail_stats[w]
            assert (st.attempted, st.success, st.failed, st.saved) == (6, 6, 0, 6), st
        assert all(v == 100 for r in [want_values(conn, i) for i in kw.values()] for v in r.values())

        # 同日重跑：删旧插新幂等，行数不翻倍，补数计数照常
        with patched_env():
            s2 = crawler.crawl_all(conn, ScriptedFetcher({w: 6 for w in WORDS}), kw, RUN_DATE)
        n = conn.execute("SELECT COUNT(*) FROM item_snapshots").fetchone()[0]
        assert n == 18 and s2.items_saved == 18 and s2.keywords_ok == 3
        print("test_all_success ✅（全成功 18 条；重跑快照仍 18 条）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 2) 部分详情失败仍入库、计数正确 ----------

def test_partial_detail_failure():
    tmp = Path(tempfile.mkdtemp(prefix="cd_2_"))
    try:
        conn, kw = fresh_db(tmp, ["考研资料"])
        fetcher = ScriptedFetcher({"考研资料": 5},
                                  fail_on={"考研资料-001", "考研资料-003"})
        with patched_env():
            s = crawler.crawl_all(conn, fetcher, kw, RUN_DATE)
        st = s.detail_stats["考研资料"]
        assert (st.attempted, st.success, st.failed) == (5, 3, 2), st
        assert s.keywords_ok == 1 and s.items_saved == 5 and not s.captcha
        wants = want_values(conn, kw["考研资料"])
        assert wants["考研资料-001"] is None and wants["考研资料-003"] is None
        assert wants["考研资料-000"] == 100 and len(wants) == 5   # 失败的也入库，want 留 NULL

        # 抽样上限：8 条结果、SAMPLE_SIZE=5 -> 只尝试 5 次，其余 want 保持 NULL
        conn2, kw2 = fresh_db(tmp, ["剪辑教程"])
        with patched_env(sample_size=5):
            s2 = crawler.crawl_all(
                conn2, ScriptedFetcher({"剪辑教程": 8}, fail_on={"剪辑教程-004"}),
                kw2, RUN_DATE)
        st2 = s2.detail_stats["剪辑教程"]
        assert (st2.attempted, st2.success, st2.failed) == (5, 4, 1), st2
        wants2 = want_values(conn2, kw2["剪辑教程"])
        assert len(wants2) == 8 and wants2["剪辑教程-007"] is None
        print("test_partial_detail_failure ✅（失败 2 条仍入库 want=NULL；抽样上限外不补）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 3) 补数中途命中风控：当前词已入库、熔断、进度可见、后续词不采 ----------

def test_captcha_mid_detail():
    tmp = Path(tempfile.mkdtemp(prefix="cd_3_"))
    try:
        conn, kw = fresh_db(tmp, WORDS)
        # 词1 5 条全成功（第 1~5 次调用）；词2 第 3 次详情调用（全局第 8 次）熔断
        fetcher = ScriptedFetcher({w: 5 for w in WORDS}, captcha_on=8)
        with patched_env():
            s = crawler.crawl_all(conn, fetcher, kw, RUN_DATE)

        assert s.captcha and s.captcha_keyword == "剪辑教程"
        assert s.captcha_detail_step == 3, s.captcha_detail_step

        st1 = s.detail_stats["考研资料"]
        assert (st1.attempted, st1.success, st1.failed) == (5, 5, 0)
        st2 = s.detail_stats["剪辑教程"]
        assert (st2.attempted, st2.success, st2.failed) == (3, 2, 0), st2  # 熔断那一次计入尝试
        assert st2.saved == 5                                            # 当前词 5 条全入库
        assert "Procreate笔刷" not in s.detail_stats                    # 后续词完全没采
        assert s.keywords_ok == 2 and s.items_saved == 10 and s.keywords_failed == 0
        assert any("剪辑教程" in e for e in s.errors)

        # DB 实证：熔断词的记录与已补想要数都在；未补到的两条 want=NULL
        wants = want_values(conn, kw["剪辑教程"])
        assert len(wants) == 5
        assert wants["剪辑教程-000"] == 100 and wants["剪辑教程-001"] == 100
        assert wants["剪辑教程-002"] is None and wants["剪辑教程-004"] is None
        # 后续词在库里没有当日快照
        assert db.get_snapshot_rows(conn, RUN_DATE, kw["Procreate笔刷"]) == []

        # 同日重跑（这次不再熔断）：删旧插新幂等，熔断词数据可被正常补全
        with patched_env():
            s2 = crawler.crawl_all(conn, ScriptedFetcher({w: 5 for w in WORDS}), kw, RUN_DATE)
        assert not s2.captcha and s2.items_saved == 15
        n = conn.execute("SELECT COUNT(*) FROM item_snapshots").fetchone()[0]
        assert n == 15
        print("test_captcha_mid_detail ✅（熔断词 5 条已入库、前 2 条想要数保留，后续词未采）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 4) 搜索阶段命中风控 ----------

def test_captcha_at_search():
    tmp = Path(tempfile.mkdtemp(prefix="cd_4_"))
    try:
        conn, kw = fresh_db(tmp, WORDS)
        fetcher = ScriptedFetcher({w: 4 for w in WORDS}, captcha_search="剪辑教程")
        with patched_env():
            s = crawler.crawl_all(conn, fetcher, kw, RUN_DATE)
        assert s.captcha and s.captcha_keyword == "剪辑教程"
        assert s.captcha_detail_step is None                      # 搜索阶段熔断没有补数进度
        assert "剪辑教程" not in s.detail_stats
        assert s.keywords_ok == 1 and s.keywords_failed == 1      # 熔断词无记录=失败
        assert s.items_saved == 4
        assert db.get_snapshot_rows(conn, RUN_DATE, kw["剪辑教程"]) == []
        assert db.get_snapshot_rows(conn, RUN_DATE, kw["Procreate笔刷"]) == []
        print("test_captcha_at_search ✅（搜索熔断无记录，计 failed，后续词未采）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 5) main() 端到端：退出码 2 + 汇总打印 + 告警文件 ----------

def test_main_end_to_end_captcha():
    tmp = Path(tempfile.mkdtemp(prefix="cd_5_"))
    try:
        runtime = tmp / "runtime"
        runtime.mkdir()
        (tmp / "keywords.txt").write_text("\n".join(WORDS) + "\n", encoding="utf-8")

        saved = {a: getattr(config, a) for a in
                 ("RUNTIME_DIR", "DB_PATH", "KEYWORDS_FILE", "SAMPLE_PATH",
                  "COOKIE_PATH", "ALERT_DIR", "HOT_KEYWORDS_PATH", "NGRAM_TOP_PATH")}
        saved_sleep, saved_fetcher, saved_argv = (
            crawler.time.sleep, main_mod.MockFetcher, main_mod.sys.argv)
        try:
            config.RUNTIME_DIR = runtime
            config.DB_PATH = runtime / "xianyu.db"
            config.KEYWORDS_FILE = tmp / "keywords.txt"
            config.SAMPLE_PATH = runtime / "sample_search.json"
            config.COOKIE_PATH = runtime / "cookies.txt"
            config.ALERT_DIR = runtime / "alerts"
            config.HOT_KEYWORDS_PATH = runtime / "hot_keywords.txt"
            config.NGRAM_TOP_PATH = runtime / "ngram_top.txt"
            crawler.time.sleep = lambda *_: None
            config.DETAIL_SAMPLE_SIZE = 15

            class E2EFetcher(ScriptedFetcher):
                def __init__(self, _sample_path):
                    super().__init__({w: 5 for w in WORDS}, captcha_on=8)

            main_mod.MockFetcher = E2EFetcher
            main_mod.sys.argv = ["main.py", "--mock"]

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main_mod.main()
            out = buf.getvalue()
        finally:
            for a, v in saved.items():
                setattr(config, a, v)
            crawler.time.sleep = saved_sleep
            main_mod.MockFetcher = saved_fetcher
            main_mod.sys.argv = saved_argv

        assert code == 2, code
        assert "[熔断] 关键词「剪辑教程」详情补数进行到第 3 条" in out, out
        assert "考研资料: 尝试 5 / 成功 5 / 失败 0" in out, out
        assert "剪辑教程: 尝试 3 / 成功 2 / 失败 0" in out, out
        assert "[采集] captcha: 2/3 词, 10 条快照" in out, out

        # crawl_runs 落库为 captcha（mock 模式库在 RUNTIME_DIR/mock/mock.db）
        conn = db.get_conn(runtime / "mock" / "mock.db")
        row = conn.execute("SELECT status, keywords_ok, keywords_failed, items_saved "
                           "FROM crawl_runs ORDER BY run_id DESC LIMIT 1").fetchone()
        # 熔断词有 5 条记录入库 -> 计入 ok（数据保住）；失败数为 0
        assert tuple(row) == ("captcha", 2, 0, 10), tuple(row)

        # 熔断词快照确实入库；告警文件含词与补数进度
        n = conn.execute("SELECT COUNT(*) FROM item_snapshots").fetchone()[0]
        assert n == 10
        alert = (runtime / "alerts" / f"alert_{RUN_DATE}.md").read_text(encoding="utf-8")
        assert "剪辑教程" in alert and "尝试 3 / 成功 2 / 失败 0" in alert
        assert (runtime / "mock" / f"report_{RUN_DATE}.md").exists()  # 熔断也照常出日报
        print("test_main_end_to_end_captcha ✅（退出码 2、打印/告警/入库/日报齐全）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_main_end_to_end_success():
    """正常路径端到端：退出码 0，补数计数也出现在汇总输出里。"""
    tmp = Path(tempfile.mkdtemp(prefix="cd_6_"))
    try:
        runtime = tmp / "runtime"
        runtime.mkdir()
        (tmp / "keywords.txt").write_text("\n".join(WORDS[:2]) + "\n", encoding="utf-8")
        saved = {a: getattr(config, a) for a in
                 ("RUNTIME_DIR", "DB_PATH", "KEYWORDS_FILE", "SAMPLE_PATH",
                  "COOKIE_PATH", "ALERT_DIR", "HOT_KEYWORDS_PATH", "NGRAM_TOP_PATH")}
        saved_sleep, saved_fetcher, saved_argv = (
            crawler.time.sleep, main_mod.MockFetcher, main_mod.sys.argv)
        try:
            config.RUNTIME_DIR = runtime
            config.DB_PATH = runtime / "xianyu.db"
            config.KEYWORDS_FILE = tmp / "keywords.txt"
            config.SAMPLE_PATH = runtime / "sample_search.json"
            config.COOKIE_PATH = runtime / "cookies.txt"
            config.ALERT_DIR = runtime / "alerts"
            config.HOT_KEYWORDS_PATH = runtime / "hot_keywords.txt"
            config.NGRAM_TOP_PATH = runtime / "ngram_top.txt"
            crawler.time.sleep = lambda *_: None

            class E2EFetcher(ScriptedFetcher):
                def __init__(self, _sample_path):
                    super().__init__({w: 4 for w in WORDS[:2]},
                                     fail_on={f"{WORDS[0]}-002"})

            main_mod.MockFetcher = E2EFetcher
            main_mod.sys.argv = ["main.py", "--mock"]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main_mod.main()
            out = buf.getvalue()
        finally:
            for a, v in saved.items():
                setattr(config, a, v)
            crawler.time.sleep = saved_sleep
            main_mod.MockFetcher = saved_fetcher
            main_mod.sys.argv = saved_argv

        assert code == 0, code
        assert f"{WORDS[0]}: 尝试 4 / 成功 3 / 失败 1" in out, out
        assert f"{WORDS[1]}: 尝试 4 / 成功 4 / 失败 0" in out, out
        assert "[采集] success: 2/2 词, 8 条快照" in out, out
        print("test_main_end_to_end_success ✅（退出码 0，补数计数已打印）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_all_success()
    test_partial_detail_failure()
    test_captcha_mid_detail()
    test_captcha_at_search()
    test_main_end_to_end_captcha()
    test_main_end_to_end_success()
    print("\n全部验收断言通过 ✅")


if __name__ == "__main__":
    main()
