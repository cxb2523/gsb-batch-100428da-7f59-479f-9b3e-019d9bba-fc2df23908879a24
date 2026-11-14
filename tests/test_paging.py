"""多页抓取异常翻页验证：在本地演示页上核对日志与 stats 的一致性。

运行：python tests/test_paging.py
覆盖四种结局：正常翻页 / 翻页未生效（列表不变） / 空页 / 翻页失败（按钮点不动）。
"""
import functools
import http.server
import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

from config import find_edge
from engine.rpa import BossRpa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = "127.0.0.1"
PORT = 8124
BASE = f"http://{HOST}:{PORT}/mock/mock-paging.html"

failures = 0


def check(name, cond, extra=""):
    global failures
    print(f"{'PASS' if cond else 'FAIL'} | {name}" + (f" | {extra}" if extra else ""))
    if not cond:
        failures += 1


def make_config(auto_page=True, max_pages=5, max_greet=30):
    return {
        "job": {"position_name": "Java开发", "new_talent_flow": False},
        "filters": {
            "gender": "all", "min_age": 18, "max_age": 60,
            "education": [], "experience": [], "activity": [], "schools": [],
            "job_intention": [], "job_hopping": [], "keywords": ["Java"],
        },
        "matching": {
            "mode": "rules", "min_score": 50,
            "rules": {
                "education_weight": 20, "work_years_weight": 20, "age_weight": 10,
                "gender_weight": 10, "keyword_weight": 40, "keywords": ["Java"],
            },
            "ai": {},
        },
        "action": {
            "greet_mode": "auto", "greet_message": "您好，方便沟通吗？",
            "max_greet_count": max_greet, "interval_min_ms": 30, "interval_max_ms": 60,
            "auto_page": auto_page, "max_pages": max_pages,
        },
        "recording": {"mode": "excel", "feishu": {}},
    }


def records():
    path = os.path.join(ROOT, "data", "records.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(l) for l in f.read().strip().splitlines() if l.strip()]


def greeted_records():
    return [r for r in records() if r.get("result") == "已打招呼"]


def block_text_logs(logs, keyword):
    return [m for m in logs if keyword in m]


def summary(logs):
    for m in logs:
        if "多页抓取汇总" in m:
            return m
    return ""


def new_rpa(cfg, logs):
    rpa = BossRpa(cfg, on_log=lambda m: logs.append(m))
    rpa.PAGE_CHANGE_TIMEOUT = 1.2  # 测试里缩短等待列表刷新的时间
    return rpa


def main():
    edge = find_edge()
    check("找到 Edge", edge is not None, str(edge))
    if not edge:
        sys.exit(1)

    # 本地静态服务器（测试进程内启动，退出时关闭）
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=ROOT)
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer((HOST, PORT), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    data_dir = os.path.join(ROOT, "data")
    os.makedirs(data_dir, exist_ok=True)
    rec_path = os.path.join(data_dir, "records.jsonl")
    if os.path.exists(rec_path):
        os.remove(rec_path)

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                tempfile.mkdtemp(prefix="hr-paging-"), executable_path=edge, headless=False,
            )

            # ---- 场景 1：正常翻页（逐页处理、不重复、最后按钮禁用 = 没有下一页）----
            logs = []
            rpa = new_rpa(make_config(max_pages=5, max_greet=30), logs)
            page = ctx.new_page()
            page.goto(BASE + "?mode=normal", wait_until="domcontentloaded")
            page.wait_for_timeout(500)
            rpa.run_pages(page)
            page.close()

            check("正常翻页：stats.page == 3", rpa.stats["page"] == 3, f"page={rpa.stats['page']}")
            check("正常翻页：逐页抓取日志 3 条", len(block_text_logs(logs, "页：抓取到")) == 3,
                  str(block_text_logs(logs, "页：抓取到")))
            check("正常翻页：scanned == 8", rpa.stats["scanned"] == 8, f"scanned={rpa.stats['scanned']}")
            check("正常翻页：greeted == 7", rpa.stats["greeted"] == 7, f"greeted={rpa.stats['greeted']}")
            check("正常翻页：无重复打招呼记录",
                  len(greeted_records()) == len([[r["name"], r["position"]] for r in greeted_records()]),
                  f"记录 {len(greeted_records())} 条")
            s = summary(logs)
            check("正常翻页：汇总日志与 stats 一致",
                  "实际抓取 3 页（stats.page=3）" in s and "因翻页未生效跳过 0 页" in s,
                  s)
            check("正常翻页：结局为没有下一页",
                  "没有下一页" in s and "翻页失败" not in s, s)

            # ---- 场景 2：翻页后内容不变（跳过该页、不重复打招呼）----
            if os.path.exists(rec_path):
                os.remove(rec_path)
            logs = []
            rpa = new_rpa(make_config(max_pages=3, max_greet=30), logs)
            page = ctx.new_page()
            page.goto(BASE + "?mode=stuck", wait_until="domcontentloaded")
            page.wait_for_timeout(500)
            rpa.run_pages(page)
            page.close()

            check("翻页未生效：stats.page == 1", rpa.stats["page"] == 1, f"page={rpa.stats['page']}")
            stuck_logs = block_text_logs(logs, "跳过该页")
            check("翻页未生效：跳过页日志 2 条", len(stuck_logs) == 2, str(stuck_logs))
            check("翻页未生效：日志写明跳过原因",
                  all("翻页未生效" in m and "不重复计入" in m for m in stuck_logs), str(stuck_logs))
            check("翻页未生效：scanned 未重复计入（3）",
                  rpa.stats["scanned"] == 3, f"scanned={rpa.stats['scanned']}")
            check("翻页未生效：greeted 未重复计入（2）",
                  rpa.stats["greeted"] == 2, f"greeted={rpa.stats['greeted']}")
            check("翻页未生效：未对同一人二次打招呼",
                  len(block_text_logs(logs, "已发送招呼语")) == 2,
                  f"发送 {len(block_text_logs(logs, '已发送招呼语'))} 次")
            check("翻页未生效：记录无重复",
                  len(greeted_records()) == len({(r["name"], r["position"]) for r in greeted_records()}),
                  f"记录 {len(greeted_records())} 条")
            s = summary(logs)
            check("翻页未生效：汇总日志含跳过页数与实际页数",
                  "实际抓取 1 页（stats.page=1）" in s and "因翻页未生效跳过 2 页" in s, s)

            # ---- 场景 3：翻到空页 -> 停止并交代结束位置 ----
            if os.path.exists(rec_path):
                os.remove(rec_path)
            logs = []
            rpa = new_rpa(make_config(max_pages=4, max_greet=30), logs)
            page = ctx.new_page()
            page.goto(BASE + "?mode=empty", wait_until="domcontentloaded")
            page.wait_for_timeout(500)
            rpa.run_pages(page)
            page.close()

            check("空页：stats.page == 1", rpa.stats["page"] == 1, f"page={rpa.stats['page']}")
            check("空页：日志指出空页并结束",
                  any("第 2 页为空页" in m and "不再继续翻页" in m for m in logs),
                  str(block_text_logs(logs, "空页")))
            check("空页：空页后未再抓取（抓取日志仅 1 条）",
                  len(block_text_logs(logs, "页：抓取到")) == 1)
            s = summary(logs)
            check("空页：汇总日志交代空页结束位置",
                  "空页结束位置：第 2 页" in s and "实际抓取 1 页（stats.page=1）" in s, s)

            # ---- 场景 4：翻页按钮失效（与「没有下一页」区分）----
            if os.path.exists(rec_path):
                os.remove(rec_path)
            logs = []
            rpa = new_rpa(make_config(max_pages=4, max_greet=30), logs)
            page = ctx.new_page()
            page.goto(BASE + "?mode=broken", wait_until="domcontentloaded")
            page.wait_for_timeout(500)
            rpa.run_pages(page)
            page.close()

            check("翻页失败：stats.page == 1", rpa.stats["page"] == 1, f"page={rpa.stats['page']}")
            fail_logs = block_text_logs(logs, "页后翻页失败")
            no_next_logs = block_text_logs(logs, "没有下一页")
            check("翻页失败：日志明确为翻页失败", len(fail_logs) == 1, str(fail_logs))
            check("翻页失败：不误报为没有下一页", len(no_next_logs) == 0, str(no_next_logs))
            s = summary(logs)
            check("翻页失败：汇总日志结局为翻页失败",
                  "翻页失败" in s and "实际抓取 1 页（stats.page=1）" in s, s)

            # ---- 场景 5：达到单次打招呼上限提前停止 ----
            if os.path.exists(rec_path):
                os.remove(rec_path)
            logs = []
            rpa = new_rpa(make_config(max_pages=5, max_greet=2), logs)
            page = ctx.new_page()
            page.goto(BASE + "?mode=normal", wait_until="domcontentloaded")
            page.wait_for_timeout(500)
            rpa.run_pages(page)
            page.close()

            check("上限：stats.greeted == 2", rpa.stats["greeted"] == 2, f"greeted={rpa.stats['greeted']}")
            s = summary(logs)
            check("上限：汇总日志标记提前停止",
                  "因达到单次打招呼上限提前停止：是" in s and "实际抓取 1 页（stats.page=1）" in s, s)
            check("上限：结束原因写明上限", "结束原因：达到单次打招呼上限（2）" in s, s)

            # ---- 场景 6：单页模式（auto_page=False）行为不变 ----
            if os.path.exists(rec_path):
                os.remove(rec_path)
            logs = []
            rpa = new_rpa(make_config(auto_page=False, max_pages=1, max_greet=30), logs)
            page = ctx.new_page()
            page.goto(BASE + "?mode=normal", wait_until="domcontentloaded")
            page.wait_for_timeout(500)
            rpa.run_pages(page)
            page.close()

            check("单页：stats.page == 1", rpa.stats["page"] == 1, f"page={rpa.stats['page']}")
            check("单页：只抓 1 页（不翻页）", len(block_text_logs(logs, "页：抓取到")) == 1)
            check("单页：scanned == 3", rpa.stats["scanned"] == 3, f"scanned={rpa.stats['scanned']}")
            check("单页：greeted == 2", rpa.stats["greeted"] == 2, f"greeted={rpa.stats['greeted']}")
            s = summary(logs)
            check("单页：汇总日志 page 一致", "实际抓取 1 页（stats.page=1）" in s, s)

            ctx.close()
    finally:
        srv.shutdown()

    print(failures == 0 and "\n多页翻页验证全部通过 ✓" or f"\n有 {failures} 项失败 ✗")
    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
