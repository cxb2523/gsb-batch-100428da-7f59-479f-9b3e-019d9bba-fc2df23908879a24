"""端到端验证：Playwright 驱动离线演示页，跑通 RPA 引擎核心流程。
运行：python tests/e2e_mock.py（需要本地 mock 服务器 http://127.0.0.1:8123）
"""
import json
import os
import queue
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

from config import find_edge
from engine.rpa import BossRpa

MOCK_URL = "http://127.0.0.1:8123/mock/mock-zhipin.html"
MOCK_CHAT_URL = "http://127.0.0.1:8123/mock/mock-chat.html"
failures = 0
logs = []


def check(name, cond, extra=""):
    global failures
    print(f"{'PASS' if cond else 'FAIL'} | {name}" + (f" | {extra}" if extra else ""))
    if not cond:
        failures += 1


def make_config():
    return {
        "job": {"position_name": "Java开发", "new_talent_flow": False},
        "filters": {
            "gender": "all", "min_age": 20, "max_age": 45,
            "education": ["本科", "硕士"], "experience": ["3-5年", "5-10年"],
            "activity": ["今日活跃"], "schools": [], "job_intention": [], "job_hopping": [],
            "keywords": ["Java"],
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
            "max_greet_count": 2, "interval_min_ms": 50, "interval_max_ms": 100,
            "auto_page": False, "max_pages": 1,
        },
        "recording": {"mode": "excel", "feishu": {}},
    }


def main():
    edge = find_edge()
    check("找到 Edge", edge is not None, str(edge))
    if not edge:
        sys.exit(1)

    # 清理旧记录，避免幂等跳过影响断言
    os.makedirs("data", exist_ok=True)
    if os.path.exists("data/records.jsonl"):
        os.remove("data/records.jsonl")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            tempfile.mkdtemp(prefix="hr-e2e-"),
            executable_path=edge,
            headless=True,
        )
        page = ctx.new_page()
        page.goto(MOCK_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(800)

        rpa = BossRpa(make_config(), on_log=lambda m: logs.append(m))
        rpa.page = page

        # 1. 列表模式检测
        rows = rpa._count_rows()
        cards = rpa._count_cards()
        check("演示页为列表模式", rows > 0, f"rows={rows} cards={cards}")

        # 2. 卡片模式：可见性检测应识别（列表行不可见、卡片可见）
        page.click("#modeToggle")
        page.wait_for_timeout(300)
        check("卡片模式下列表行不可见", rpa._count_rows() == 0,
              f"rows(visible)={rpa._count_rows()} cards(visible)={rpa._count_cards()}")
        page.click("#modeToggle")  # 切回列表模式
        page.wait_for_timeout(300)
        check("切回列表模式后行可见", rpa._count_rows() > 0)
        rpa.ensure_list_mode(page)  # 列表模式下应直接通过

        # 3. 筛选条件点击
        rpa.apply_filters(page)
        page.wait_for_timeout(500)
        activity_sel = page.eval_on_selector(".filter-group.activity",
            "el => el.querySelectorAll('span.sel').length")
        edu_sel = page.eval_on_selector(".filter-group.education",
            "el => el.querySelectorAll('span.sel').length")
        check("筛选条件已勾选（活跃度/学历）", activity_sel >= 1 and edu_sel >= 1,
              f"activity_sel={activity_sel} edu_sel={edu_sel}")

        # 4. 主循环（单页，最多 2 个打招呼）
        rpa.run_pages(page)

        # 5. 统计与记录
        check("已扫描候选人", rpa.stats["scanned"] > 0, f"scanned={rpa.stats['scanned']}")
        check("至少 1 人打招呼", rpa.stats["greeted"] >= 1, f"greeted={rpa.stats['greeted']}")
        check("日志有发送招呼语", any("已发送招呼语" in m for m in logs))

        with open("data/records.jsonl", "r", encoding="utf-8") as f:
            lines = [l for l in f.read().strip().splitlines() if l.strip()]
        check("记录文件已生成", len(lines) >= 1, f"记录 {len(lines)} 条")
        first = json.loads(lines[0])
        check("记录字段完整", first.get("name") and first.get("position_name") == "Java开发",
              str(first)[:120])

        # 6. 幂等：再次运行同一页，已打招呼的人不应重复
        logs2 = []
        rpa2 = BossRpa(make_config(), on_log=lambda m: logs2.append(m))
        rpa2.page = page
        rpa2.run_pages(page)
        with open("data/records.jsonl", "r", encoding="utf-8") as f:
            all_records = [json.loads(l) for l in f.read().strip().splitlines() if l.strip()]
        greeted_keys = [f"{r['name']}|{r['position']}|{r['company']}"
                        for r in all_records if r.get("result") == "已打招呼"]
        check("幂等：已打招呼记录无重复", len(greeted_keys) == len(set(greeted_keys)),
              f"记录 {len(greeted_keys)} 条，去重 {len(set(greeted_keys))} 条")
        check("幂等：日志出现跳过已打招呼", any("跳过（已打招呼）" in m for m in logs2),
              f"第二次运行日志条数 {len(logs2)}")

        # ---- 7. 沟通页流程：发常用语 + 求简历 ----
        chat_cfg = make_config()
        chat_cfg["action"]["flow_mode"] = "chat_greet"
        chat_cfg["action"]["max_greet_count"] = 3
        chat_cfg["action"]["interval_min_ms"] = 50
        chat_cfg["action"]["interval_max_ms"] = 100
        chat_logs = []
        chat_page = ctx.new_page()
        chat_page.goto(MOCK_CHAT_URL, wait_until="domcontentloaded")
        chat_page.wait_for_timeout(500)
        rpa_chat = BossRpa(chat_cfg, on_log=lambda m: chat_logs.append(m))
        rpa_chat.page = chat_page
        rpa_chat._goto_retry = lambda page, url, tries=2: True  # 阻止导航真实站点
        rpa_chat.run_pages(chat_page)
        check("沟通页流程：已处理候选人", rpa_chat.stats["greeted"] >= 1,
              f"greeted={rpa_chat.stats['greeted']}")
        check("沟通页流程：日志有求简历", any("求简历" in m for m in chat_logs))
        with open("data/records.jsonl", "r", encoding="utf-8") as f:
            chat_records = [json.loads(l) for l in f.read().strip().splitlines() if l.strip()]
        check("沟通页流程：记录包含发常用语+求简历",
              any(r.get("result") == "已发常用语+求简历" for r in chat_records))
        # 幂等：再次运行已处理的人应被跳过
        chat_logs2 = []
        rpa_chat2 = BossRpa(chat_cfg, on_log=lambda m: chat_logs2.append(m))
        rpa_chat2.page = chat_page
        rpa_chat2._goto_retry = lambda page, url, tries=2: True
        rpa_chat2.run_pages(chat_page)
        check("沟通页流程：已处理者被跳过", any("跳过（已处理）" in m for m in chat_logs2),
              f"二次运行日志 {len(chat_logs2)} 条")
        with open("data/records.jsonl", "r", encoding="utf-8") as f:
            chat_keys = [f"{r['name']}|{r['position']}|{r['company']}"
                         for r in [json.loads(l) for l in f.read().strip().splitlines() if l.strip()]
                         if r.get("result") == "已发常用语+求简历"]
        check("沟通页流程：记录无重复", len(chat_keys) == len(set(chat_keys)),
              f"记录 {len(chat_keys)} 条，去重 {len(set(chat_keys))} 条")
        chat_page.close()

        # ---- 8. 手动辅助模式：浏览器就绪，点按钮触发操作 ----
        manual_cfg = make_config()
        manual_cfg["action"]["flow_mode"] = "manual"
        manual_cfg["action"]["interval_min_ms"] = 50
        manual_cfg["action"]["interval_max_ms"] = 100
        manual_logs = []
        manual_queue = queue.Queue()
        stop_m = threading.Event()

        def manual_worker():
            # Playwright 页面必须在创建它的线程内操作，所以在 worker 线程内自建浏览器
            with sync_playwright() as pw:
                mctx = pw.chromium.launch_persistent_context(
                    tempfile.mkdtemp(prefix="hr-manual-"),
                    executable_path=edge,
                    headless=True,
                )
                m_page = mctx.new_page()
                m_page.goto(MOCK_CHAT_URL, wait_until="domcontentloaded")
                m_page.wait_for_timeout(500)
                rpa_manual = BossRpa(manual_cfg, on_log=lambda m: manual_logs.append(m),
                                     cmd_queue=manual_queue, stop_event=stop_m)
                rpa_manual.page = m_page
                rpa_manual._goto_retry = lambda page, url, tries=2: True
                rpa_manual.run_manual()
                mctx.close()

        t = threading.Thread(target=manual_worker, daemon=True)
        t.start()
        time.sleep(3)  # 等待浏览器就绪

        manual_queue.put({"type": "next_candidate"})
        time.sleep(1.5)
        manual_queue.put({"type": "next_candidate"})
        time.sleep(1.5)
        manual_queue.put({"type": "send_phrase", "message": "手动测试常用语"})
        time.sleep(2)
        manual_queue.put({"type": "grab_resume"})
        time.sleep(7)
        manual_queue.put({"type": "request_resume"})
        time.sleep(2)
        # 图标菜单路径：先隐藏文字按钮，让应用走「行内图标 → 菜单 → 求简历」
        manual_queue.put({"type": "hide_text_button"})
        time.sleep(1)
        manual_queue.put({"type": "request_resume"})
        time.sleep(3)
        stop_m.set()
        t.join(timeout=8)
        picked = [m for m in manual_logs if "已点选候选人" in m]
        check("手动模式：点候选人成功", len(picked) >= 1, f"点了 {len(picked)} 次")
        check("手动模式：第二次切换到下一个联系人", len(picked) >= 2 and picked[0] != picked[1],
              f"{picked[0] if picked else '无'} → {picked[1] if len(picked) > 1 else '无'}")
        check("手动模式：常用语发送成功", any("常用语已发送" in m for m in manual_logs))
        check("手动模式：在线简历抓取成功",
              any("📄 在线简历：" in m and "姓名" in m for m in manual_logs),
              next((m for m in manual_logs if "📄 在线简历：" in m), "无简历日志").split("：")[-1][:80])
        check("手动模式：长简历滚动抓取执行",
              any("简历滚动收集完成" in m for m in manual_logs))
        check("手动模式：求简历成功", any("已请求简历" in m for m in manual_logs))
        confirm_count = sum(1 for m in manual_logs if "已点击求简历并确认" in m)
        check("手动模式：图标菜单路径求简历成功",
              any("尝试点击候选人行操作图标" in m for m in manual_logs) and confirm_count >= 2,
              f"确认次数={confirm_count}")

        ctx.close()

    print(failures == 0 and "\n端到端验证全部通过 ✓" or f"\n有 {failures} 项失败 ✗")
    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
