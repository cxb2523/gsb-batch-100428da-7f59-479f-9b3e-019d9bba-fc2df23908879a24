"""Playwright RPA 引擎：Edge 独立配置登录态 -> 列表模式 -> 筛选 -> 翻页抓取 -> 匹配 -> 打招呼。"""
import random
import queue
import re
import threading
import time
import datetime
import json
import os

from playwright.sync_api import sync_playwright

from config import EDGE_PROFILE_DIR, find_edge
from engine.matcher import extract_work_years, match, parse_education, to_number
from engine.recorder import Recorder
from engine.selectors import SELECTORS
from engine.stealth import STEALTH_JS

ZHIPIN_HOME = "https://www.zhipin.com/"
LOGIN_URL_MARK = "login.zhipin.com"


def _first_selector(group):
    """选择器组取第一条可用的（实际用 locator 链逐个尝试）。"""
    return group[0] if isinstance(group, list) and group else group


class BossRpa:
    def __init__(self, config, on_log=None, on_state=None, stop_event=None, cmd_queue=None):
        self.config = config
        self.on_log = on_log or (lambda msg: None)
        self.on_state = on_state or (lambda stats: None)
        self.stop_event = stop_event or threading.Event()
        self.cmd_queue = cmd_queue or queue.Queue()
        self.stats = {"page": 0, "scanned": 0, "matched": 0, "greeted": 0, "skipped": 0}
        self.recorder = Recorder(config, on_log)
        self.greeted_keys = set()
        self._frame = None
        self._manual_idx = 0
        self._load_greeted_keys()

    # ---------- 工具 ----------
    def log(self, msg):
        self.on_log(msg)

    def push_state(self):
        self.on_state(dict(self.stats))

    def _load_greeted_keys(self):
        try:
            with open("data/records.jsonl", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    import json
                    r = json.loads(line)
                    if r.get("result") in ("已打招呼", "已发常用语+求简历"):
                        self.greeted_keys.add(self._record_key(r.get("name"), r.get("position"), r.get("company")))
        except Exception:
            pass

    @staticmethod
    def _record_key(name, position, company):
        return f"{name}|{position or ''}|{company or ''}"

    def _op_frame(self):
        """候选人列表所在 frame（新版页面内容可能在 iframe 中）。"""
        if self._frame is not None:
            return self._frame
        page = getattr(self, "page", None)
        if page is None:
            raise RuntimeError("page 尚未初始化")
        for f in page.frames:
            if "zhipin.com" not in f.url:
                continue
            try:
                if f.locator(".candidate-card-wrap, .recommend-list, .list-row, [class*='job-item']").count() > 0:
                    self._frame = f
                    return f
            except Exception:
                continue
        self._frame = page.main_frame
        return self._frame

    def _loc_first(self, selectors, timeout=3000):
        """按选择器列表顺序定位，返回第一个可见的 locator 或 None。
        同一条选择器可能命中隐藏元素（如折叠面板标题），会自动跳过找下一个可见的。"""
        for sel in selectors:
            locs = self._op_frame().locator(sel)
            n = min(locs.count(), 20)
            for i in range(n):
                try:
                    loc = locs.nth(i)
                    if loc.is_visible(timeout=timeout):
                        return loc
                except Exception:
                    continue
        return None

    def _loc_first_row(self, root, selectors, timeout=3000):
        """在某个元素（候选行 / 弹窗）内按选择器顺序定位。"""
        for sel in selectors:
            loc = root.locator(sel).first
            try:
                if loc.count() > 0 and loc.is_visible(timeout=timeout):
                    return loc
            except Exception:
                continue
        return None

    def _click_option(self, container_selectors, value, exact=True):
        """在筛选容器内点击文本等于 value 的选项。"""
        frame = self._op_frame()
        for sel in container_selectors:
            container = frame.locator(sel).first
            if container.count() == 0:
                continue
            options = container.locator(SELECTORS["filter"]["option"])
            n = options.count()
            for i in range(n):
                opt = options.nth(i)
                try:
                    t = opt.inner_text().strip()
                except Exception:
                    continue
                if (exact and t == value) or (not exact and value in t):
                    try:
                        opt.click(timeout=2000)
                        return True
                    except Exception:
                        continue
        return False

    # ---------- 1. 前置登录 ----------
    def ensure_login(self, page):
        self.log("正在打开 BOSS 直聘（https://www.zhipin.com/）…")
        if not self._goto_retry(page, ZHIPIN_HOME):
            self.log("⚠️ 自动打开 BOSS 直聘失败，请在弹出的窗口中手动输入 https://www.zhipin.com/ 并回车。")
        if self._on_verify_page(page):
            self.log("🔐 检测到安全验证页。请在弹出的窗口中手动完成滑块 / 人机验证（约几秒），完成后自动继续。")
            self._wait_condition(page, lambda p: not self._on_verify_page(p) and not p.url.startswith("about:"),
                                 timeout=180, hint="等待安全验证完成…")
        if self._is_logged_in(page):
            self.log("已检测到登录态（独立 Edge 配置）。")
            return

        self.log("⚠️ 未检测到登录态。请在弹出的浏览器窗口中完成扫码 / 密码登录（首次需手动登录一次）。")
        self.log("当前窗口地址：" + self._safe_url(page))
        deadline = time.time() + 300  # 最多等 5 分钟
        while time.time() < deadline:
            if self.stop_event.is_set():
                raise RuntimeError("用户停止：登录等待中止")
            if self._is_logged_in(page):
                self.log("✅ 登录成功，继续执行。")
                return
            time.sleep(2)
        raise RuntimeError("等待登录超时（5 分钟）")

    def _goto_retry(self, page, url, tries=2):
        """导航并自动重试（应对偶发超时 / Out of Memory 崩溃）。"""
        for i in range(tries):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                time.sleep(2)
                return True
            except Exception as e:
                self.log(f"页面加载失败（第 {i + 1}/{tries} 次）：{e}")
                time.sleep(2)
        return False

    def _safe_url(self, page):
        try:
            return page.url
        except Exception:
            return "（无法读取）"

    def _on_verify_page(self, page):
        try:
            url = page.url
            return ("verify" in url and "zhipin.com" in url) or ("safe/verify" in url)
        except Exception:
            return False

    def _is_logged_in(self, page):
        try:
            url = page.url
            if LOGIN_URL_MARK in url:
                return False
            cookies = page.context.cookies()
            if any(c["name"] in ("zp_token", "wt2", "__zp_stoken__") and c["value"] for c in cookies):
                return True
        except Exception:
            pass
        return False

    # ---------- 2. 打开岗位页 ----------
    def open_job_page(self, page):
        position = self.config.get("job", {}).get("position_name", "").strip()
        new_talent = self.config.get("job", {}).get("new_talent_flow", False)

        # 新版页面：/web/chat/recommend；旧版兜底 /web/boss/recommend
        candidates = [
            "https://www.zhipin.com/web/chat/recommend",
            "https://www.zhipin.com/web/boss/recommend",
        ]
        opened = False
        for url in candidates:
            if self._goto_retry(page, url):
                if not self._on_verify_page(page) and "停止维护" not in self._body_text(page):
                    opened = True
                    self.log(f"已打开推荐牛人页：{page.url}")
                    break
        time.sleep(2)

        if "geek" in page.url:
            self.log("⚠️ 当前是求职端页面（/web/geek/）。请确认登录的是【企业招聘账号】（需要有「我要招聘」权限）；")
            self.log("正在尝试重新打开招聘端推荐页…")
            self._goto_retry(page, "https://www.zhipin.com/web/chat/recommend")
            time.sleep(2)

        if new_talent:
            self.log("新牛人子流程：请确认页面已切换到「新牛人」分类（如需）。")

        if position:
            # 尝试在岗位切换器中选择岗位
            switched = self._switch_position(page, position)
            if switched:
                self.log(f"已切换推荐岗位：{position}")
            else:
                self.log(f"未找到岗位切换器（{position}），请手动在页面顶部切换推荐岗位。")
        if new_talent:
            self.log("请手动点击页面上的「新牛人」入口后，本程序将自动继续（最长等待 60 秒）。")
            self._wait_condition(page, lambda p: self._new_talent_ready(p), timeout=60,
                                 hint="等待切换新牛人…")

    def _switch_position(self, page, position):
        # 新版：左侧岗位 filter .job-item
        try:
            items = page.locator(", ".join(SELECTORS["page"]["job_item"]))
            n = items.count()
            for i in range(n):
                t = items.nth(i).inner_text(timeout=1500)
                if position in t:
                    items.nth(i).click(timeout=3000)
                    time.sleep(1.5)
                    return True
        except Exception:
            pass
        # 旧版：岗位输入框
        candidates = [
            page.locator("[class*='position'] input"),
            page.locator("[class*='job'] input"),
            page.locator("input[placeholder*='岗位']"),
        ]
        for inp in candidates:
            if inp.count():
                try:
                    inp.click(timeout=2000)
                    inp.fill(position, timeout=2000)
                    time.sleep(1)
                    page.keyboard.press("Enter")
                    time.sleep(1)
                    return True
                except Exception:
                    continue
        return False

    def _body_text(self, page):
        try:
            return page.locator("body").inner_text(timeout=2000)[:300]
        except Exception:
            return ""

    def _new_talent_ready(self, page):
        try:
            return "新牛人" in page.locator("body").inner_text()
        except Exception:
            return False

    # ---------- 3. 列表模式 ----------
    def ensure_list_mode(self, page):
        self.log("检查页面模式…")
        rows = self._count_rows()
        cards = self._count_cards()
        if rows > 0:
            self.log(f"✅ 列表模式（行数 {rows}）。")
            return
        if cards > 0:
            self.log(f"⚠️ 当前为卡片模式（{cards} 张卡片）。新版页面以卡片模式为主，将直接继续；")
            self.log("如需列表模式，可在页面右键 → 「切换为列表模式」后重试。")
        else:
            self.log("⚠️ 未识别到候选人列表（可能页面结构已变化），请手动确认当前为推荐牛人列表页。")

    def _count_rows(self):
        n = 0
        for sel in SELECTORS["page"]["candidate_row"]:
            loc = self._op_frame().locator(sel)
            c = loc.count()
            for i in range(c):
                try:
                    if loc.nth(i).is_visible():
                        n += 1
                except Exception:
                    continue
        return n

    def _count_cards(self):
        n = 0
        for sel in SELECTORS["page"]["candidate_card"]:
            loc = self._op_frame().locator(sel)
            c = loc.count()
            for i in range(c):
                try:
                    if loc.nth(i).is_visible():
                        n += 1
                except Exception:
                    continue
        return n

    def _wait_condition(self, page, cond, timeout, hint=""):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.stop_event.is_set():
                raise RuntimeError("用户停止")
            if cond(page):
                return True
            time.sleep(2)
        if hint:
            self.log(f"⏰ 等待超时：{hint}")
        return False

    # ---------- 4. 设置筛选 ----------
    def apply_filters(self, page):
        self.log("设置筛选条件…")
        f = self.config.get("filters", {})
        panel = self._loc_first(SELECTORS["filter"]["panel"], timeout=1500)
        if panel is None:
            self.log("⚠️ 未找到筛选面板，跳过筛选设置（选择器需校准）。")
            return

        try:
            # 多选组：活跃度 / 院校 / 经验 / 学历 / 求职意向 / 跳槽频率
            self._set_multi(SELECTORS["filter"]["activity"], f.get("activity") or [])
            self._set_multi(SELECTORS["filter"]["school"], f.get("schools") or [])
            self._set_multi(SELECTORS["filter"]["experience"], f.get("experience") or [])
            self._set_multi(SELECTORS["filter"]["education"], f.get("education") or [])
            self._set_multi(SELECTORS["filter"]["intention"], f.get("job_intention") or [])
            self._set_multi(SELECTORS["filter"]["hopping"], f.get("job_hopping") or [])

            # 性别单选
            gender = f.get("gender")
            if gender and gender != "all":
                want = "男" if gender == "male" else "女"
                if not self._click_option(SELECTORS["filter"]["gender"], want):
                    self.log(f"⚠️ 未找到性别选项：{want}")

            # 年龄区间
            if not self._set_age_range(f.get("min_age"), f.get("max_age")):
                self.log(f"⚠️ 未找到年龄区间选项（{f.get('min_age')}-{f.get('max_age')}岁），请手动在面板中设置。")

            confirm = self._loc_first(SELECTORS["filter"]["confirm"], timeout=1500)
            if confirm:
                confirm.click(timeout=3000)
                self.log("已点击筛选确认按钮。")
                time.sleep(1)
        except Exception as e:
            self.log(f"筛选设置异常（不阻塞）：{e}")

    def _set_multi(self, container_selectors, values):
        for value in values:
            ok = self._click_option(container_selectors, value)
            if not ok:
                self.log(f"⚠️ 未找到筛选选项：{value}")
            time.sleep(0.3)

    def _set_age_range(self, min_age, max_age):
        """尝试匹配年龄区间选项（如 20-25岁）。"""
        frame = self._op_frame()
        for sel in SELECTORS["filter"]["age"]:
            container = frame.locator(sel).first
            if container.count() == 0:
                continue
            options = container.locator(SELECTORS["filter"]["option"])
            n = options.count()
            best = None
            best_overlap = -1
            for i in range(n):
                try:
                    t = options.nth(i).inner_text().strip()
                except Exception:
                    continue
                m = re.search(r"(\d+)\s*[-~到]\s*(\d+)", t)
                if not m:
                    continue
                a, b = int(m.group(1)), int(m.group(2))
                lo = max(a, min_age or 0)
                hi = min(b, max_age or 99)
                overlap = max(0, hi - lo)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best = (t, i)
            if best:
                options.nth(best[1]).click(timeout=2000)
                self.log(f"已选择年龄区间：{best[0]}")
                return True
        return False

    # ---------- 5. 主循环 ----------
    def run_pages(self, page):
        self.page = page
        action = self.config.get("action", {})
        # 沟通页模式：发常用语 + 求简历（不经过推荐牛人页）
        if action.get("flow_mode") == "chat_greet":
            self.flow_chat_greet(page)
            return
        max_pages = action.get("max_pages", 5) if action.get("auto_page", True) else 1
        max_greet = action.get("max_greet_count", 30)
        greeted_count = 0

        counted_pages = 0          # 实际抓取并处理过的页数（与 stats["page"] 同步）
        skipped_pages = 0          # 因「翻页未生效」整页跳过的次数
        empty_page_pos = None      # 空页所在位置（第 N 次翻页尝试）
        stopped_by_max_greet = False
        last_page_keys = None      # 上一个有效页的候选人去重键集合
        end_reason = "达到最大页数"

        for page_idx in range(1, max_pages + 1):
            if self.stop_event.is_set():
                end_reason = "收到停止指令"
                self.log("收到停止指令，任务结束。")
                break

            parsed = self._collect_page(prev_keys=last_page_keys)
            if self.stop_event.is_set():  # 等待翻页加载期间收到停止指令
                end_reason = "收到停止指令"
                self.log("收到停止指令，任务结束。")
                break

            if not parsed:
                empty_page_pos = page_idx
                end_reason = f"空页（第 {page_idx} 页）"
                self.log(f"⚠️ 第 {page_idx} 页为空页（抓取到 0 个候选人），抓取到此结束，不再继续翻页。")
                break

            page_keys = self._page_keys(parsed)

            # 翻页后列表仍是同一批人：整页跳过，不重复计入 scanned/matched/greeted
            if last_page_keys and page_keys and page_keys == last_page_keys:
                skipped_pages += 1
                self.log(
                    f"⚠️ 第 {page_idx} 页：翻页未生效（本页 {len(parsed)} 个候选人与上一页完全相同），"
                    f"跳过该页，不重复计入 scanned/matched/greeted。"
                )
                if page_idx >= max_pages:
                    end_reason = f"达到最大页数（第 {page_idx} 页翻页未生效）"
                    break
                outcome, detail = self._go_next_page(page)
                if outcome != "advanced":
                    end_reason = self._log_page_stop(outcome, detail, page_idx, counted_pages)
                    break
                continue

            counted_pages += 1
            self.stats["page"] = counted_pages
            self.push_state()
            self.log(f"第 {page_idx} 页：抓取到 {len(parsed)} 个候选人。")
            last_page_keys = page_keys

            for row, profile in parsed:
                if self.stop_event.is_set():
                    break
                if not profile.get("name"):
                    self.stats["skipped"] += 1
                    continue

                key = self._record_key(profile["name"], profile.get("position"), profile.get("company"))
                if key in self.greeted_keys:
                    self.log(f"跳过（已打招呼）：{profile['name']}")
                    continue
                if greeted_count >= max_greet:
                    stopped_by_max_greet = True
                    end_reason = f"达到单次打招呼上限（{max_greet}）"
                    self.log(f"已达单次运行打招呼上限（{max_greet}），任务结束。")
                    self.stop_event.set()
                    break

                self.stats["scanned"] += 1
                try:
                    score, passed, reasons = match(profile, self.config)
                except Exception as e:
                    self.log(f"匹配异常（{profile['name']}）：{e}")
                    self.stats["skipped"] += 1
                    continue

                reason = "；".join(reasons or [])
                if passed:
                    self.stats["matched"] += 1
                    self.log(f"✅ 匹配通过 {score} 分：{profile['name']}（{profile.get('position','')}）{reason}")
                    ok = self.greet(row, profile)
                    if ok:
                        greeted_count += 1
                        self.stats["greeted"] += 1
                        self.greeted_keys.add(key)
                        self.recorder.write(profile, score, reason, "已打招呼", self._record_mode())
                        if greeted_count >= max_greet:
                            # 刚好达到上限：不再抓取后续页，汇总日志标记提前停止
                            stopped_by_max_greet = True
                            end_reason = f"达到单次打招呼上限（{max_greet}）"
                            self.log(f"已达单次运行打招呼上限（{max_greet}），任务结束。")
                            self.stop_event.set()
                            self.push_state()
                            break
                    else:
                        self.recorder.write(profile, score, reason, "已匹配未打招呼", self._record_mode())
                else:
                    self.stats["skipped"] += 1
                    self.log(f"跳过 {score} 分：{profile['name']} {reason}")
                self.push_state()

                # 随机延时模拟真人
                time.sleep(random.randint(action.get("interval_min_ms", 4000), action.get("interval_max_ms", 8000)) / 1000)

            if stopped_by_max_greet:
                break
            if self.stop_event.is_set():
                end_reason = "收到停止指令"
                break
            if page_idx >= max_pages:
                end_reason = f"达到最大页数（{max_pages} 页）"
                break

            outcome, detail = self._go_next_page(page)
            if outcome != "advanced":
                end_reason = self._log_page_stop(outcome, detail, page_idx, counted_pages)
                break

        self.log(
            "📊 多页抓取汇总："
            f"实际抓取 {counted_pages} 页（stats.page={self.stats['page']}）；"
            f"因翻页未生效跳过 {skipped_pages} 页；"
            f"空页结束位置：{('第 %d 页' % empty_page_pos) if empty_page_pos else '无'}；"
            f"因达到单次打招呼上限提前停止：{'是' if stopped_by_max_greet else '否'}；"
            f"结束原因：{end_reason}。"
            f"本次共扫描 {self.stats['scanned']} 人（stats.scanned={self.stats['scanned']}）、"
            f"匹配 {self.stats['matched']} 人（stats.matched={self.stats['matched']}）、"
            f"打招呼 {self.stats['greeted']} 人（stats.greeted={self.stats['greeted']}）。"
        )
        self.log("任务完成。")

    # ---------- 沟通页流程：发送常用语 + 求简历 ----------
    def flow_chat_greet(self, page):
        self.page = page
        self._frame = None  # 重新探测沟通页所在 frame
        action = self.config.get("action", {})
        max_greet = action.get("max_greet_count", 30)
        message = action.get("greet_message", "")
        frame = self._op_frame()

        self.log("打开沟通页（/web/chat/index）…")
        if not self._goto_retry(page, "https://www.zhipin.com/web/chat/index"):
            self.log("⚠️ 沟通页打开失败，请检查登录态后重试。")
            return
        time.sleep(2)

        items = frame.locator(", ".join(SELECTORS["chat"]["candidate_item"]))
        count = items.count()
        if count == 0:
            self.log("⚠️ 未找到沟通列表候选人（.geek-item 选择器需校准）。")
            return
        self.log(f"沟通列表共 {count} 位候选人，开始逐位发送常用语 + 求简历…")

        greeted_count = 0
        for i in range(count):
            if self.stop_event.is_set():
                self.log("收到停止指令，任务结束。")
                break
            if greeted_count >= max_greet:
                self.log(f"已达单次运行上限（{max_greet}），任务结束。")
                break

            item = items.nth(i)
            try:
                name = item.locator("[class*='name'], [class*='geek']").first.inner_text(timeout=1500).strip() or f"候选人{i+1}"
            except Exception:
                name = f"候选人{i+1}"

            key = self._record_key(name, "沟通列表", "")
            # 已处理过则跳过（幂等）
            if key in self.greeted_keys:
                self.log(f"跳过（已处理）：{name}")
                continue

            try:
                item.click(timeout=3000)
                time.sleep(1.2)
                ok = self._chat_send_phrase(message)
                if ok:
                    self._chat_request_resume()
                    greeted_count += 1
                    self.stats["greeted"] += 1
                    self.stats["scanned"] += 1
                    self.greeted_keys.add(key)
                    self.recorder.write(
                        {"name": name, "position": "沟通列表", "company": ""},
                        100, "", "已发常用语+求简历", self._record_mode()
                    )
                    self.log(f"✅ 已对 {name} 发送常用语并请求简历。")
                else:
                    self.stats["skipped"] += 1
                    self.log(f"⚠️ 发送失败：{name}")
            except Exception as e:
                self.stats["skipped"] += 1
                self.log(f"⚠️ 处理 {name} 异常：{e}")
            self.push_state()
            time.sleep(random.randint(
                action.get("interval_min_ms", 4000), action.get("interval_max_ms", 8000)) / 1000)

        self.log("沟通页流程完成。")

    def _chat_send_phrase(self, message):
        """发送常用语：优先点聊天框「常用语」快捷按钮，找不到则直接填输入框发送。"""
        frame = self._op_frame()
        # 1) 常用语快捷按钮
        quick = self._loc_first(SELECTORS["chat"]["quick_phrase"], timeout=1500)
        if quick is not None:
            try:
                quick.click(timeout=2000)
                time.sleep(1)
                phrase_panel = frame.locator(
                    "[class*='quick'] [class*='item'], [class*='phrase'] [class*='item'], "
                    "[class*='quick'] li, [class*='quick'] span"
                )
                if phrase_panel.count() > 0:
                    phrase_panel.first.click(timeout=2000)
                    time.sleep(0.5)
                    if self._do_send():
                        return True
            except Exception:
                pass
        # 2) 直接填输入框发送
        try:
            inp = self._loc_first(SELECTORS["chat"]["message_input"], timeout=2000)
            if inp is None:
                self.log("⚠️ 未找到聊天输入框。")
                return False
            inp.fill(message, timeout=2000)
            time.sleep(0.3)
            return self._do_send()
        except Exception:
            return False

    def _do_send(self):
        """尝试发送：优先点发送按钮，找不到按钮则用回车键。返回是否发送成功。"""
        frame = self._op_frame()
        # 方式 1：发送按钮
        send = self._loc_first(SELECTORS["chat"]["send_button"], timeout=1200)
        if send is not None:
            try:
                send.click(timeout=2000)
                time.sleep(0.6)
                if self._msg_sent():
                    return True
            except Exception:
                pass
        # 方式 2：回车键发送（Boss 聊天框支持 Enter）
        try:
            inp = self._loc_first(SELECTORS["chat"]["message_input"], timeout=1500)
            if inp is not None:
                inp.click(timeout=1000)
                frame.page.keyboard.press("Enter")
                time.sleep(0.8)
                if self._msg_sent():
                    return True
        except Exception:
            pass
        self.log("⚠️ 发送按钮未找到或点击无效（输入框内容仍在），请检查页面发送按钮结构。")
        return False

    def _msg_sent(self):
        """验证是否发送成功：输入框内容被清空即视为已发送。"""
        try:
            inp = self._loc_first(SELECTORS["chat"]["message_input"], timeout=1000)
            if inp is None:
                return False
            try:
                val = inp.input_value(timeout=1000)
            except Exception:
                val = inp.inner_text(timeout=1000)  # contenteditable 输入框
            return not (val or "").strip()
        except Exception:
            return False

    def _chat_request_resume(self):
        """点击「求简历」按钮。"""
        btn = self._loc_first(SELECTORS["chat"]["request_resume"], timeout=2500)
        if btn is not None:
            try:
                self.log("[诊断] 找到求简历按钮：" + str(btn.evaluate("el => el.outerHTML.slice(0, 220)")))
            except Exception:
                pass
            return self._click_resume_flow(btn)

        # 文字按钮找不到：尝试点击候选人行的悬停操作图标，再在菜单里选「求简历」
        self.log("未找到「求简历」文字按钮，尝试点击候选人行操作图标…")
        try:
            # 悬停候选人行，让操作图标显示出来
            item = self._loc_first(SELECTORS["chat"]["candidate_item"], timeout=1500)
            if item is not None:
                item.hover(timeout=2000)
                time.sleep(0.5)
        except Exception:
            pass
        icon = self._loc_first(SELECTORS["chat"]["resume_operate_icon"], timeout=2000)
        if icon is not None:
            try:
                icon.click(timeout=3000)
                time.sleep(1.2)
                menu_btn = self._loc_first(SELECTORS["chat"]["resume_menu_item"], timeout=2500)
                if menu_btn is not None:
                    return self._click_resume_flow(menu_btn)
                self.log("⚠️ 操作菜单已打开，但未找到「求简历」菜单项。")
            except Exception as e:
                self.log(f"⚠️ 操作图标流程异常：{e}")

        self.log("⚠️ 仍未找到「求简历」入口，正在扫描页面按钮文本以便校准…")
        found = "、".join(self._dump_button_texts())
        self.log("页面上找到的按钮/工具文本：" + (found or "（未识别到）"))
        return False

    def _click_resume_flow(self, btn):
        """点击求简历入口 -> 处理确认框。"""
        try:
            btn.click(timeout=3000)
            time.sleep(1)
            # 新版页面：点求简历后会弹出确认框，需要再点一次「确认」
            dialog = self._loc_first(SELECTORS["chat"]["resume_confirm_dialog"], timeout=1500)
            if dialog is not None:
                confirm = self._loc_first_row(
                    dialog,
                    ["button:has-text('确认')", "button:has-text('确定')", "button"],
                    timeout=1500,
                )
            else:
                confirm = self._loc_first(SELECTORS["chat"]["resume_confirm_button"], timeout=1500)
            if confirm is not None:
                confirm.click(timeout=3000)
                time.sleep(0.8)
                self.log("📄 已点击求简历并确认。")
            else:
                self.log("📄 已点击求简历（未出现确认框）。")
            return True
        except Exception as e:
            self.log(f"⚠️ 求简历点击失败：{e}")
            return False

    def _dump_button_texts(self):
        """扫描页面上的按钮与工具条文本（用于校准选择器）。"""
        frame = self._op_frame()
        texts = []
        for sel in ("button", "a", "[class*='tool']", "[class*='btn']", "[class*='menu']"):
            try:
                locs = frame.locator(sel)
                n = min(locs.count(), 40)
                for i in range(n):
                    try:
                        t = locs.nth(i).inner_text(timeout=800).strip().replace("\n", " ")
                        if t and len(t) <= 16 and t not in texts:
                            texts.append(t)
                    except Exception:
                        continue
            except Exception:
                continue
        return texts[:50]

    def _record_mode(self):
        return "飞书" if self.config.get("recording", {}).get("mode") == "feishu" else "本地Excel"

    # ---------- 翻页 ----------
    # 翻页后等待列表刷新的最长时间（秒）：超过仍与上一页相同即判定「翻页未生效」
    PAGE_CHANGE_TIMEOUT = 5.0

    @staticmethod
    def _page_keys(parsed):
        """本页候选人的去重键集合（姓名|岗位|公司）。"""
        return {
            BossRpa._record_key(p.get("name"), p.get("position"), p.get("company"))
            for _, p in parsed if p.get("name")
        }

    def _collect_page(self, prev_keys=None):
        """抓取当前页候选人，返回 [(row, profile), ...]。

        首页只抓一次（保持单页行为不变）；翻页后若列表与上一页完全相同（含本页为空），
        会在 PAGE_CHANGE_TIMEOUT 内轮询等待异步刷新，超时仍相同则交给调用方判定结局。
        """
        parsed = [(row, self._parse_row(row)) for row in self._collect_rows()]
        if prev_keys is None:
            return parsed
        deadline = time.time() + self.PAGE_CHANGE_TIMEOUT
        while not self.stop_event.is_set() and time.time() < deadline:
            keys = self._page_keys(parsed)
            if keys and keys != prev_keys:
                return parsed
            time.sleep(0.8)
            parsed = [(row, self._parse_row(row)) for row in self._collect_rows()]
        return parsed

    @staticmethod
    def _btn_disabled(btn):
        """按钮是否已禁用（disabled 属性 / disabled class / aria-disabled）。"""
        try:
            if btn.is_disabled(timeout=500):
                return True
        except Exception:
            pass
        try:
            cls = btn.get_attribute("class") or ""
            if re.search(r"(^|[-\s])(disabled|is-disable)([-\s]|$)", cls):
                return True
            if btn.get_attribute("aria-disabled") == "true":
                return True
        except Exception:
            pass
        return False

    def _go_next_page(self, page):
        """尝试翻到下一页。返回 (outcome, detail)：

        - advanced：已点击并完成翻页动作；
        - no_next ：没有下一页（找不到按钮，或按钮已禁用 = 最后一页）；
        - failed  ：找到按钮但点击抛异常（翻页失败）。
        """
        try:
            self._op_frame().page.mouse.wheel(0, 5000)
            time.sleep(1)
        except Exception:
            pass

        buttons = []
        next_btn = self._loc_first(SELECTORS["page"]["next_page"], timeout=1500)
        if next_btn is not None:
            buttons.append(("下一页按钮", next_btn))
        more = self._loc_first(SELECTORS["page"]["load_more"], timeout=1000)
        if more is not None:
            buttons.append(("加载更多按钮", more))
        if not buttons:
            return "no_next", "未找到下一页 / 加载更多按钮"

        enabled = [(label, btn) for label, btn in buttons if not self._btn_disabled(btn)]
        if not enabled:
            return "no_next", "下一页 / 加载更多按钮均已禁用（最后一页）"

        last_err = ""
        for label, btn in enabled:
            try:
                btn.click(timeout=3000)
                time.sleep(1.5)
                return "advanced", label
            except Exception as e:
                last_err = f"{label}点击异常：{str(e).strip().splitlines()[0]}"
                continue
        return "failed", last_err or "翻页按钮点击失败"

    def _log_page_stop(self, outcome, detail, page_pos, counted_pages):
        """记录翻页动作的两种失败结局，返回写进汇总日志的结束原因。"""
        if outcome == "no_next":
            self.log(f"第 {page_pos} 页后翻页结束：没有下一页（{detail}）。")
            return f"没有下一页（停在第 {page_pos} 页，已抓取 {counted_pages} 页）"
        self.log(f"⚠️ 第 {page_pos} 页后翻页失败：{detail}。抓取中止。")
        return f"翻页失败（停在第 {page_pos} 页，已抓取 {counted_pages} 页）"

    def _collect_rows(self):
        frame = self._op_frame()
        for sel in SELECTORS["page"]["candidate_row"]:
            loc = frame.locator(sel)
            n = loc.count()
            rows = []
            for i in range(n):
                try:
                    if loc.nth(i).is_visible():
                        rows.append(loc.nth(i))
                except Exception:
                    continue
            if rows:
                return rows
        # 列表模式未命中时回退卡片
        for sel in SELECTORS["page"]["candidate_card"]:
            loc = frame.locator(sel)
            n = loc.count()
            rows = []
            for i in range(n):
                try:
                    if loc.nth(i).is_visible():
                        rows.append(loc.nth(i))
                except Exception:
                    continue
            if rows:
                self.log("⚠️ 未命中列表行选择器，回退到卡片模式解析。")
                return rows
        return []

    def _parse_row(self, row):
        def text_of(selectors, default=""):
            for sel in selectors:
                loc = row.locator(sel).first
                try:
                    if loc.count() > 0:
                        t = loc.inner_text(timeout=1000).strip()
                        if t:
                            return t
                except Exception:
                    continue
            return default

        name = text_of(SELECTORS["candidate"]["name"])
        age_text = text_of(SELECTORS["candidate"]["age"])
        gender_text = text_of(SELECTORS["candidate"]["gender"]) or (re.search(r"男|女", age_text).group(0) if re.search(r"男|女", age_text) else "")
        edu_text = text_of(SELECTORS["candidate"]["education"])
        years_text = text_of(SELECTORS["candidate"]["work_years"])
        tags = []
        for sel in SELECTORS["candidate"]["tags"]:
            locs = row.locator(sel)
            n = locs.count()
            tags.extend(locs.nth(i).inner_text().strip() for i in range(n) if locs.nth(i).inner_text().strip())
            if tags:
                break
        viewed_text = text_of(SELECTORS["candidate"]["viewed"])

        profile = {
            "name": name,
            "age": to_number(age_text),
            "gender": gender_text,
            "education": parse_education(edu_text),
            "work_years": extract_work_years(years_text or " ".join(tags)),
            "school": text_of(SELECTORS["candidate"]["school"]),
            "position": text_of(SELECTORS["candidate"]["position"]),
            "company": text_of(SELECTORS["candidate"]["company"]),
            "salary": text_of(SELECTORS["candidate"]["salary"]),
            "tags": tags,
            "summary": text_of(SELECTORS["candidate"]["summary"]),
            "viewed": bool(re.search(r"已查看|已看过|已读", viewed_text)),
        }
        return profile

    # ---------- 6. 打招呼 ----------
    def greet(self, row, profile):
        mode = self.config.get("action", {}).get("greet_mode", "auto")
        btn = self._loc_first_row(row, SELECTORS["candidate"]["greet_button"], timeout=2000)
        if btn is None:
            self.log(f"⚠️ 未找到打招呼按钮：{profile['name']}")
            return False

        if mode == "manual":
            btn.scroll_into_view_if_needed()
            self.log(f"✋ 半自动模式：请手动点击「{profile['name']}」的打招呼按钮（等待 5 秒）。")
            time.sleep(5)
            return False

        try:
            btn.click(timeout=5000)  # Playwright 真实鼠标事件，isTrusted=True
        except Exception as e:
            self.log(f"打招呼按钮点击失败：{e}")
            return False
        time.sleep(0.8)

        # 弹出对话框时填写招呼语并发送
        dialog = self._loc_first(SELECTORS["greet_dialog"]["panel"], timeout=1500)
        if dialog is not None:
            msg_input = self._loc_first_row(dialog, SELECTORS["greet_dialog"]["message_input"], timeout=1000)
            if msg_input is not None:
                msg_input.fill(self.config.get("action", {}).get("greet_message", ""), timeout=2000)
                time.sleep(0.2)
            send_btn = self._loc_first_row(dialog, SELECTORS["greet_dialog"]["send_button"], timeout=1000) or \
                self._loc_first(SELECTORS["greet_dialog"]["send_button"], timeout=1000)
            if send_btn is not None:
                send_btn.click(timeout=3000)
                self.log(f"✅ 已发送招呼语：{profile['name']}")
                time.sleep(0.5)
                return True
        self.log(f"✅ 已点击打招呼：{profile['name']}")
        return True

    def goto_next_page(self, page):
        """兼容旧接口：返回是否成功翻页（细节结局见 _go_next_page）。"""
        outcome, _ = self._go_next_page(page)
        return outcome == "advanced"

    # ---------- 7. 总入口 ----------
    def run(self):
        edge = find_edge()
        if not edge:
            raise RuntimeError("未找到 Edge 浏览器，请先安装 Microsoft Edge。")

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                EDGE_PROFILE_DIR,
                executable_path=edge,
                headless=False,
                viewport=None,
                slow_mo=50,  # 每步操作加微小延迟，模拟真人节奏
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                ],
                ignore_default_args=["--enable-automation"],
            )
            try:
                context.add_init_script(STEALTH_JS)
                # 只保留一个干净标签页，避免恢复上次残留的 about:blank
                for extra in context.pages[1:]:
                    try:
                        extra.close()
                    except Exception:
                        pass
                page = context.pages[0] if context.pages else context.new_page()
                self.page = page
                self.log(f"Edge 已启动（独立配置目录：{EDGE_PROFILE_DIR}）")
                self.ensure_login(page)
                flow_mode = self.config.get("action", {}).get("flow_mode", "manual")
                if flow_mode == "manual":
                    self.run_manual()
                elif flow_mode == "chat_greet":
                    self.log("运行模式：沟通页发送常用语 + 求简历（跳过推荐页）。")
                    self.run_pages(page)
                else:
                    self.open_job_page(page)
                    self.ensure_list_mode(page)
                    self.apply_filters(page)
                    self.run_pages(page)
            finally:
                context.close()

    # ---------- 手动辅助模式：浏览器常驻，等用户点按钮执行 ----------
    def run_manual(self):
        self.page = self.page
        self.log("手动辅助模式：正在打开沟通页…")
        if not self._goto_retry(self.page, "https://www.zhipin.com/web/chat/index"):
            self.log("⚠️ 沟通页打开失败。")
            return
        time.sleep(2)
        self.log("✅ 已就绪。请在浏览器中点击候选人，然后回到应用点按钮执行操作。")
        self.log("可执行操作：点候选人 / 发送常用语 / 求简历。")

        while not self.stop_event.is_set():
            try:
                cmd = self.cmd_queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self._manual_execute(cmd)
            except Exception as e:
                self.log(f"⚠️ 操作失败：{e}")
            finally:
                self.on_state(dict(self.stats))

        self.log("手动辅助模式已退出。")

    def _manual_execute(self, cmd):
        ctype = cmd.get("type") if isinstance(cmd, dict) else cmd
        message = cmd.get("message") if isinstance(cmd, dict) else self.config.get("action", {}).get("greet_message", "")
        if ctype == "send_phrase":
            ok = self._chat_send_phrase(message)
            self.log("✅ 常用语已发送。" if ok else "⚠️ 常用语发送失败。")
        elif ctype == "request_resume":
            ok = self._chat_request_resume()
            self.log("✅ 已请求简历。" if ok else "⚠️ 求简历失败。")
        elif ctype == "grab_resume":
            ok = self.manual_grab_resume()
            self.log("✅ 在线简历抓取完成。" if ok else "⚠️ 在线简历抓取失败。")
        elif ctype == "next_candidate":
            name = self.manual_next_candidate()
            self.log(f"已点选候选人：{name}" if name else "⚠️ 沟通列表没有更多候选人了。")
        elif ctype == "hide_text_button":
            # 测试辅助：隐藏「求简历」文字按钮，强制走图标菜单路径
            btn = self._loc_first(SELECTORS["chat"]["request_resume"], timeout=1000)
            if btn is not None:
                btn.evaluate("el => el.style.display = 'none'")
                self.log("已隐藏文字按钮（测试指令）。")
            else:
                self.log("文字按钮不在页面上。")
        else:
            self.log(f"未知指令：{ctype}")

    def manual_next_candidate(self):
        """点选候选人：优先点「当前选中联系人」的下一个，列表重排也不怕。"""
        frame = self._op_frame()
        items = self._candidate_items(frame)
        count = len(items)
        if count == 0:
            self.log("⚠️ 未找到候选人列表项（选择器需校准）。")
            return ""
        if count < 2:
            self.log(f"⚠️ 只识别到 {count} 个候选人项（真实列表可能更多，选择器需校准）。")

        # 当前聊天中的联系人名字，用于定位「下一个」
        current_name = self._current_chat_name(frame, items)
        start = 0
        if current_name:
            for i, el in enumerate(items):
                if current_name == self._item_name(el):
                    start = i + 1
                    break

        # 从 start 开始找下一个可点的候选人（跳过已选中项，循环防越界）
        for off in range(count):
            idx = (start + off) % count
            el = items[idx]
            if self._is_selected(el):
                continue
            name = self._item_name(el) or f"候选人{idx + 1}"
            try:
                el.click(timeout=3000)
                time.sleep(1)
                return name
            except Exception as e:
                self.log(f"点候选人失败（{name}）：{e}")
                return ""
        self.log("⚠️ 没有找到可切换的候选人。")
        return ""

    # ---------- 抓取在线简历 ----------
    def manual_grab_resume(self):
        """点击聊天窗口的「在线简历」，抓取简历内容。"""
        frame = self._op_frame()

        # 点击前记录页面文本（用于识别悬浮层新增内容）与新页面集合
        before_text = ""
        try:
            before_text = frame.locator("body").inner_text(timeout=2000)
        except Exception:
            pass
        pages_before = set()
        try:
            pages_before = set(self.page.context.pages)
        except Exception:
            pass

        # 遍历所有「在线简历」入口点击，直到检测到简历内容出现
        opened = self._open_online_resume(frame, before_text)
        if not opened:
            self.log("⚠️ 点击「在线简历」后未检测到简历内容（可能入口结构不同），继续尝试抓取…")
            self.log("页面上找到的「在线简历」元素：" + self._count_text_els(frame, "在线简历"))
            time.sleep(1)

        # 等待简历容器内容加载（异步接口，轮询最多 8 秒）
        loaded_text = self._wait_resume_loaded(frame)
        if loaded_text:
            text = loaded_text
            self.log(f"简历容器已加载内容（约 {len(text)} 字）。")
        else:
            self.log("⚠️ 简历容器内容未加载出来（可能加载慢或需要交互），继续尝试其他通道…")

        # 长简历滚动：仅当简历是文本内容时滚动（图片分页简历滚动会翻页）
        if loaded_text:
            try:
                self._scroll_collect()
            except Exception:
                pass

        # 1) 简历 iframe（合并已加载的容器内容）
        text = loaded_text or self._grab_iframe_resume()

        # 2) 悬浮层 / 主页面
        if not text:
            try:
                text = self._grab_floating_text(frame, before_text)
                if text:
                    self.log(f"已识别悬浮层简历内容（约 {len(text)} 字）。")
            except Exception:
                pass

        # 3) shadow DOM（新版组件可能用 shadow DOM 承载简历）
        if not text:
            try:
                text = self._grab_shadow_text(frame)
                if text:
                    self.log(f"已从 shadow DOM 抓取到简历（约 {len(text)} 字）。")
            except Exception:
                pass

        # 4) 滚动分段截图 + OCR（长简历滚动展示时，逐屏截图再识别）
        pages = []
        try:
            pages = self._screenshot_scroll_resume(frame)
            if len(pages) <= 1:
                # 滚动截图只出一屏时，可能是翻页式简历，再试翻页截图
                pages = self._screenshot_resume_pages(frame)
        except Exception:
            pass
        if pages:
            try:
                from engine.ocr import ocr_images
                ocr_text = ocr_images(pages)
                if ocr_text and re.search(r"求职意向|工作经历|教育经历|姓名|学历|期望", ocr_text):
                    text = ocr_text
                    self.log(f"已通过 OCR 识别简历文字（约 {len(text)} 字）。")
                elif not text:
                    self.log("⚠️ OCR 识别为空（图片可能不清晰）。")
            except Exception as e:
                self.log(f"⚠️ OCR 识别失败：{e}")

        if not text:
            self.log("⚠️ 未能抓取到简历内容。")
            try:
                self.log("页面结构诊断：frames=" + str(len(self.page.frames)) +
                         " 可见悬浮容器=" + str(self._count_fixed_containers(frame)))
            except Exception:
                pass

        # 悬浮层内容类型诊断 + 分页截图（长简历常以分页图片展示）
        try:
            diag = self._floating_media_diag(frame)
            if diag:
                self.log("悬浮层结构：" + str(diag))
        except Exception:
            pass

        if not text:
            return False

        info = self._parse_resume_text(text)
        if info:
            self.log("📄 在线简历：" + self._fmt_resume(info))
        else:
            self.log("📄 在线简历：未能结构化提取，原文片段如下（供校准）：")
            self.log("   " + (text or "").strip()[:400].replace("\n", " ⏎ "))

        # 简历原文落盘
        saved = self._save_resume_text(text)
        if saved:
            self.log(f"📁 简历原文已保存：{saved}")

        self.recorder.write(
            {"name": info.get("姓名") or "未识别", "position": "简历抓取",
             "company": ""},
            "", "", "已抓取在线简历", self._record_mode()
        )
        self._close_floating_panel()
        return bool(info.get("姓名") or text.strip())

    def _open_online_resume(self, frame, before_text):
        """逐个点击「在线简历」入口，直到检测到简历章节内容出现。"""
        locs = frame.locator(":text-is('在线简历')")
        n = min(locs.count(), 12)
        for i in range(n):
            el = locs.nth(i)
            try:
                if not el.is_visible(timeout=800):
                    continue
                el.click(timeout=2000)
                time.sleep(2.5)  # 悬浮层异步加载，多等一会
            except Exception:
                continue
            # 检测主页面新增内容是否含简历章节
            try:
                after = frame.locator("body").inner_text(timeout=2000)
                added = self._diff_added(before_text, after)
                if re.search(r"求职意向|工作经历|教育经历|项目经历|专业技能|自我评价", added or ""):
                    self.log(f"第 {i + 1} 个「在线简历」入口打开成功。")
                    return True
            except Exception:
                pass
            # 检测 iframe 是否有简历
            try:
                if self._grab_iframe_resume():
                    self.log(f"第 {i + 1} 个「在线简历」入口在 iframe 中打开成功。")
                    return True
            except Exception:
                pass
        return False

    def _wait_resume_loaded(self, frame, timeout=8):
        """轮询等待简历容器（boss-dialog/resume-recommend）内容加载。"""
        deadline = time.time() + timeout
        last_text = ""
        empty_count = 0
        while time.time() < deadline:
            try:
                t = frame.evaluate("""
                    () => {
                        const els = [...document.querySelectorAll(
                            '[class*="resume-recommend"], [class*="resume-common"], [class*="boss-dialog__body"], [class*="resume-detail"], [class*="resume-panel"]'
                        )];
                        if (!els.length) return '';
                        return els.map(el => el.innerText || '')
                                  .sort((a, b) => b.length - a.length)[0] || '';
                    }
                """)
                if len(t) > 100 or re.search(r"求职意向|工作经历|教育经历|项目经历|专业技能|自我评价", t):
                    return t
                if t:
                    last_text = t
                    empty_count = 0
                else:
                    empty_count += 1
                    if empty_count >= 3:
                        break  # 连续 3 秒没有简历容器，提前退出
            except Exception:
                pass
            time.sleep(1)
        self.log("简历容器当前内容：" + (last_text[:150].replace("\n", " ⏎ ") or "（空）"))
        return last_text if len(last_text) > 50 else ""

    def _count_text_els(self, frame, text):
        try:
            return str(frame.locator(f":text-is('{text}')").count())
        except Exception:
            return "0"

    def _grab_iframe_resume(self):
        """抓取页面内 iframe 中的简历内容（轮询等待异步加载）。"""
        report = "未找到简历 iframe"
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                for f in self.page.frames:
                    if "resume" not in f.url.lower():
                        continue
                    try:
                        t = f.locator("body").inner_text(timeout=1500)
                    except Exception:
                        continue
                    if t and (len(t) > 100 or re.search(
                            r"求职意向|工作经历|教育经历|项目经历|专业技能|自我评价|姓名|学历", t)):
                        # iframe 内滚动到底，收全懒加载内容
                        try:
                            f.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                            time.sleep(0.8)
                            t = f.locator("body").inner_text(timeout=2000)
                        except Exception:
                            pass
                        self.log(f"从简历 iframe 抓取到文字：{f.url[:90]}")
                        return t
                    if t:
                        report = f"iframe {f.url[:70]} 文本{len(t)}字（未含简历章节）"
            except Exception:
                pass
            time.sleep(0.8)
        self.log("简历 iframe 诊断：" + report)
        return ""

    def _grab_shadow_text(self, frame):
        """收集 shadow DOM 中的简历文本。"""
        try:
            return frame.evaluate("""
                () => {
                    const out = [];
                    const walk = (root) => {
                        try {
                            const all = root.querySelectorAll('*');
                            for (const el of all) {
                                if (el.shadowRoot) walk(el.shadowRoot);
                            }
                        } catch (e) {}
                        const t = root.innerText || '';
                        if (t && /求职意向|工作经历|教育经历|项目经历|专业技能|自我评价/.test(t)) {
                            out.push(t);
                        }
                    };
                    walk(document);
                    return out.sort((a, b) => b.length - a.length)[0] || '';
                }
            """)
        except Exception:
            return ""

    def _count_fixed_containers(self, frame):
        try:
            return frame.evaluate("""
                () => [...document.querySelectorAll(
                    '[style*="fixed"], [class*="dialog"], [class*="modal"], [class*="popup"], [class*="drawer"], [class*="resume"]'
                )].filter(el => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 200 && r.height > 100 &&
                           s.display !== 'none' && s.visibility !== 'hidden';
                }).length
            """)
        except Exception:
            return -1

    def _floating_media_diag(self, frame):
        """诊断悬浮层内容类型（文字/图片/Canvas）。"""
        try:
            return frame.evaluate("""
                () => [...document.querySelectorAll(
                    '[style*="fixed"], [class*="dialog"], [class*="modal"], [class*="popup"], [class*="drawer"], [class*="resume"]'
                )].filter(el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 200 && r.height > 100;
                }).map(el => ({
                    cls: (el.className || '').toString().slice(0, 60),
                    imgs: el.querySelectorAll('img').length,
                    canvases: el.querySelectorAll('canvas').length,
                    textLen: (el.innerText || '').length
                })).slice(0, 6)
            """)
        except Exception:
            return []

    def _floating_box(self, frame):
        """返回悬浮层简历容器的位置尺寸（优先已知简历容器，避免截到整页）。"""
        try:
            return frame.evaluate("""
                () => {
                    // 1) 已知简历容器：优先 iframe 简历区，排除右侧信息栏
                    const knownEls = [...document.querySelectorAll(
                        '[class*="iframe-resume-detail"], [class*="resume-content-wrap"]'
                    )];
                    if (!knownEls.length) {
                        document.querySelectorAll('[class*="resume-detail"]').forEach(el => {
                            const cls = el.className || '';
                            if (!/right-side/.test(cls) && !/wrap/.test(cls) && !/right/.test(cls)) {
                                knownEls.push(el);
                            }
                        });
                    }
                    const known = knownEls.sort((a, b) => {
                        const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
                        return (ra.width || 0) - (rb.width || 0);
                    })[0];
                    if (known) {
                        const r = known.getBoundingClientRect();
                        return { x: r.x, y: r.y, width: r.width, height: r.height };
                    }
                    // 2) 备选：可见 fixed 容器中面积最小但 ≥200x100（悬浮层通常小于整页）
                    const els = [...document.querySelectorAll(
                        '[style*="fixed"], [class*="dialog"], [class*="modal"], [class*="popup"], [class*="drawer"]'
                    )].filter(el => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width >= 200 && r.height >= 100 &&
                               r.width < window.innerWidth * 0.98 &&
                               s.display !== 'none' && s.visibility !== 'hidden';
                    }).sort((a, b) => {
                        const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
                        return (ra.width * ra.height) - (rb.width * rb.height);
                    });
                    if (!els.length) return null;
                    const r = els[0].getBoundingClientRect();
                    return { x: r.x, y: r.y, width: r.width, height: r.height };
                }
            """)
        except Exception:
            return None

    def _screenshot_resume_pages(self, frame):
        """分页截取悬浮层简历：每页截图，点「下一页」直到没有（长简历分页图片）。"""
        saved = []
        resume_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "data", "resumes")
        try:
            os.makedirs(resume_dir, exist_ok=True)
        except Exception:
            return saved
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        next_selectors = [
            "[class*='next']",
            "[class*='page-next']",
            "button:has-text('下一页')",
            "[class*='arrow-right']",
            "[class*='chevron-right']",
            "[class*='page'] button"
        ]
        for page_no in range(1, 30):
            box = self._floating_box(frame)
            if not box:
                break
            path = os.path.join(resume_dir, f"简历_第{page_no}页_{ts}.png")
            try:
                self.page.screenshot(path=path, clip=box)
            except Exception:
                break
            saved.append(path)
            next_btn = self._loc_first(next_selectors, timeout=1500)
            if next_btn is None:
                break
            try:
                next_btn.click(timeout=2000)
                time.sleep(1.5)  # 等翻页动画与下一页加载
            except Exception:
                break
        return saved

    def _screenshot_scroll_resume(self, frame):
        """滚动分段截取悬浮层简历：找到滚动容器，逐屏向下滚动截图，直到滚到底。"""
        saved = []
        resume_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "data", "resumes")
        try:
            os.makedirs(resume_dir, exist_ok=True)
        except Exception:
            return saved
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        # 找到悬浮层内可滚动的容器（滚动高度最大的）
        info = None
        try:
            info = frame.evaluate("""
                () => {
                    const els = [...document.querySelectorAll(
                        '[class*="iframe-resume-detail"], [class*="resume-content-wrap"], [class*="resume-detail"], ' +
                        '[class*="resume-recommend"], [class*="boss-dialog__body"], [class*="resume-common"], [class*="resume-panel"], ' +
                        '[class*="resume-recommend"] *, [class*="boss-dialog__body"] *, [class*="resume-common"] *, [class*="resume-panel"] *'
                    )].filter(el => el.scrollHeight > el.clientHeight + 20);
                    if (!els.length) return null;
                    const el = els.sort((a, b) => b.scrollHeight - a.scrollHeight)[0];
                    return { total: el.scrollHeight, step: el.clientHeight, top: el.scrollTop };
                }
            """)
        except Exception:
            info = None

        if not info:
            return saved

        total = info["total"]
        step = max(info["step"], 400)
        pos = info["top"]
        seen = 0
        while pos < total and seen < 40:
            try:
                frame.evaluate("""
                    (p) => {
                        const els = [...document.querySelectorAll(
                            '[class*="iframe-resume-detail"], [class*="resume-content-wrap"], [class*="resume-detail"], ' +
                            '[class*="resume-recommend"], [class*="boss-dialog__body"], [class*="resume-common"], [class*="resume-panel"], ' +
                            '[class*="resume-recommend"] *, [class*="boss-dialog__body"] *, [class*="resume-common"] *, [class*="resume-panel"] *'
                        )].filter(el => el.scrollHeight > el.clientHeight + 20);
                        const el = els.sort((a, b) => b.scrollHeight - a.scrollHeight)[0];
                        if (el) el.scrollTop = p;
                    }
                """, pos)
                time.sleep(0.6)
                box = self._floating_box(frame)
                if not box:
                    break
                path = os.path.join(resume_dir, f"简历_段{seen + 1}_{ts}.png")
                self.page.screenshot(path=path, clip=box)
                saved.append(path)
                seen += 1
            except Exception:
                break
            pos += step
        if saved:
            self.log(f"简历滚动截图完成（共 {seen} 段）。")
        return saved

    def _save_resume_text(self, text):
        """把抓取的简历原文保存到 data/resumes/ 目录。
        仅当内容看起来像简历（含姓名/学历/经历等特征）时才保存，避免把对话存成简历。"""
        if not re.search(r"求职意向|工作经历|教育经历|项目经历|专业技能|自我评价|姓名|学历|期望", text or ""):
            return ""
        try:
            resume_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                      "data", "resumes")
            os.makedirs(resume_dir, exist_ok=True)
            fname = "简历_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt"
            path = os.path.join(resume_dir, fname)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text or "")
            return path
        except Exception:
            return ""

    def _close_floating_panel(self):
        """关闭在线简历悬浮层（点关闭按钮或按 Esc）。"""
        try:
            close_btn = self._loc_first(
                ["[class*='close']", "[class*='dialog'] [class*='close']",
                 "[class*='modal'] [class*='close']", "button:has-text('关闭')"],
                timeout=1200,
            )
            if close_btn is not None:
                close_btn.click(timeout=1500)
                time.sleep(0.5)
                return
            self._op_frame().page.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception:
            try:
                self._op_frame().page.keyboard.press("Escape")
            except Exception:
                pass

    def _diff_added(self, before, after):
        """返回 after 相比 before 新增的文本块（用于提取悬浮层内容）。"""
        import difflib
        b = (before or "").splitlines()
        a = (after or "").splitlines()
        sm = difflib.SequenceMatcher(None, b, a)
        chunks = []
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op in ("insert", "replace"):
                chunks.extend(a[j1:j2])
        return "\n".join(chunks).strip()

    def _scroll_collect(self, max_scrolls=25):
        """用 JS 把所有可滚动容器滚到底（长简历懒加载），直到内容不再变长。"""
        frame = self._op_frame()
        prev_len = -1
        for i in range(max_scrolls):
            try:
                frame.evaluate("""
                    () => {
                        const els = document.querySelectorAll('*');
                        els.forEach(el => {
                            try {
                                if (el.scrollHeight > el.clientHeight + 5) {
                                    el.scrollTop = el.scrollHeight;
                                }
                            } catch (e) {}
                        });
                        window.scrollTo(0, document.body.scrollHeight);
                    }
                """)
                time.sleep(0.8)
                cur_len = len(frame.locator("body").inner_text(timeout=2000))
                if cur_len == prev_len:
                    break
                prev_len = cur_len
            except Exception:
                break
        self.log(f"简历滚动收集完成（滚动 {i + 1} 次）。")

    def _grab_floating_text(self, frame, before_text):
        """滚动后抓取悬浮层简历文本：优先「点击后新增内容」，不足则取可见悬浮容器。"""
        after_text = ""
        try:
            after_text = frame.locator("body").inner_text(timeout=2000)
        except Exception:
            pass
        added = self._diff_added(before_text, after_text)
        if len(added) > 50:
            return added

        # 可见的固定定位容器（悬浮层常见）
        try:
            containers = frame.evaluate("""
                () => [...document.querySelectorAll(
                    '[style*="fixed"], [class*="dialog"], [class*="modal"], [class*="popup"], [class*="drawer"], [class*="resume"]'
                )].filter(el => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 200 && r.height > 100 &&
                           s.display !== 'none' && s.visibility !== 'hidden' &&
                           /求职意向|工作经历|教育经历|技能|期望/.test(el.innerText || '');
                }).map(el => el.innerText || '')
            """)
            best = max(containers or [], key=len, default="")
            if len(best) > 50:
                return best
        except Exception:
            pass
        # 关键词没匹配到时：取可见固定容器中文本最长的（悬浮层通常文本最长）
        try:
            containers = frame.evaluate("""
                () => [...document.querySelectorAll(
                    '[style*="fixed"], [class*="dialog"], [class*="modal"], [class*="popup"], [class*="drawer"], [class*="resume"]'
                )].filter(el => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 200 && r.height > 100 &&
                           s.display !== 'none' && s.visibility !== 'hidden';
                }).map(el => el.innerText || '')
            """)
            best = max(containers or [], key=len, default="")
            if len(best) > 100:
                return best
        except Exception:
            pass
        return after_text

    def _parse_resume_text(self, text):
        """从简历文本中结构化提取字段。"""
        import re
        info = {}
        t = text or ""

        # 先定位简历主体：从简历头部特征（求职意向/在线简历标题）开始截取，
        # 不能用「工作经历/教育经历」当起点——它们在简历中部，会砍掉头部基本信息
        body_start = len(t)
        for kw in ("求职意向", "在线简历", "期望职位", "期望城市"):
            idx = t.find(kw)
            if 0 <= idx < body_start:
                body_start = idx
        if body_start < len(t):
            t = t[body_start:]
        # 再排除聊天消息特征行（"简历已投递"等求职者消息）
        cut = t.find("简历已投递")
        if 0 <= cut <= 200:
            t = t[cut + 6:]

        def val(pattern):
            m = re.search(pattern, t)
            return m.group(1).strip() if m else ""

        info["姓名"] = val(r"姓名[^\u4e00-\u9fa5]{0,6}([\u4e00-\u9fa5]{2,4})") or \
            val(r"^([\u4e00-\u9fa5]{2,4})\s*$")
        info["性别"] = val(r"性别[^\u4e00-\u9fa5]{0,6}(男|女)")
        info["年龄"] = val(r"年龄[^\d]{0,6}(\d{1,2})")
        info["学历"] = val(r"学历[^\u4e00-\u9fa5]{0,6}(博士|硕士|本科|大专)")
        m = re.search(r"经验[^\d]{0,6}(\d{1,2})\s*年|(\d{1,2})\s*年经验", t)
        info["年限"] = (m.group(1) or m.group(2)) if m else ""
        info["技能"] = val(r"技能[：:]\s*([^\n]+)")
        # 技能兜底：常见技能词
        if not info["技能"]:
            common = ["Java", "Python", "Spring", "MySQL", "Redis", "Kafka", "Vue", "React",
                      "Golang", "分布式", "微服务", "高并发", "Kubernetes", "Docker"]
            found = [s for s in common if s in t]
            if found:
                info["技能"] = "、".join(found)

        # 兜底：真实简历常是无标签排版（如「张三 男 28岁 · 本科」）
        if not any(info.values()):
            m = re.search(r"^([\u4e00-\u9fa5]{2,4})\s+(男|女)", t, re.M)
            if m:
                info["姓名"] = m.group(1)
                info["性别"] = m.group(2)
            m = re.search(r"(\d{1,2})\s*岁", t)
            if m:
                info["年龄"] = m.group(1)
            for k in ("博士", "硕士", "本科", "大专"):
                if k in t:
                    info["学历"] = k
                    break
            m = re.search(r"(\d{1,2})\s*年(?:以上)?(?:经验|工作|从业)", t)
            if m:
                info["年限"] = m.group(1)

        return {k: v for k, v in info.items() if v}

    def _fmt_resume(self, info):
        return " ".join(f"{k}:{v}" for k, v in info.items()) or "（未能结构化提取，已保存原文）"

    def _candidate_items(self, frame):
        """候选人列表项（排除分类标签与不可见元素）。"""
        for sel in SELECTORS["chat"]["candidate_item"]:
            loc = frame.locator(sel)
            n = loc.count()
            rows = []
            for i in range(n):
                try:
                    el = loc.nth(i)
                    if not el.is_visible(timeout=800):
                        continue
                    cls = el.get_attribute("class") or ""
                    # 排除「全部/新招呼/沟通中」等分类标签
                    if "chat-label-item" in cls:
                        continue
                    rows.append(el)
                except Exception:
                    continue
            if rows:
                return rows
        return []

    def _item_name(self, el):
        # 元素本身就是名字（.geek-name / .name）时直接取文本
        try:
            if el.evaluate("el => el.matches('.geek-name, [class*=\"geek-name\"], .name, [class*=\"geek\"]')"):
                t = el.inner_text(timeout=800).strip()
                if t:
                    return t
        except Exception:
            pass
        for sel in SELECTORS["chat"]["candidate_name"]:
            try:
                loc = el.locator(sel).first
                if loc.count() > 0:
                    t = loc.inner_text(timeout=800).strip()
                    if t:
                        return t
            except Exception:
                continue
        return ""

    def _is_selected(self, el):
        try:
            # 当前选中项的 class 可能在容器上，向上找
            return el.evaluate("""
                el => {
                    let n = el;
                    while (n && n.tagName !== 'BODY') {
                        const c = n.className || '';
                        if (/(^|\\s)(selected|sel|active|current)(\\s|$)/.test(String(c))) return true;
                        n = n.parentElement;
                    }
                    return false;
                }
            """)
        except Exception:
            return False

    def _current_chat_name(self, frame, items):
        """当前聊天中的联系人名：优先看列表中被选中的项，其次聊天窗口标题。"""
        for el in items:
            if self._is_selected(el):
                n = self._item_name(el)
                if n:
                    return n
        try:
            for sel in ("[class*='chat-header']", "[class*='chat-title']", "[class*='chat-info']"):
                loc = frame.locator(sel).first
                if loc.count() > 0:
                    t = loc.inner_text(timeout=1000).strip()
                    if t:
                        import re
                        m = re.search(r"与\s*([^\s，。]+)", t)
                        if m:
                            return m.group(1)
        except Exception:
            pass
        return ""

    # ---------- 8. 检查页面（调试） ----------
    def check_selectors(self, page):
        report = {}
        for group in ("page", "candidate"):
            report[group] = {}
            for key, sels in SELECTORS[group].items():
                if isinstance(sels, list):
                    report[group][key] = self._op_frame().locator(", ".join(sels)).count()
        report["rows"] = self._count_rows()
        report["cards"] = self._count_cards()
        return report
