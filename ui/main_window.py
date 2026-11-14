"""tkinter 启动参数界面：配置岗位画像 -> 开始/停止 RPA -> 日志与统计。"""
import datetime
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from config import load_config, save_config
from engine.matcher import match
from engine.rpa import BossRpa

# ---------------- 中性 + 语义色主题（Material 风格） ----------------
BG         = "#FFFFFF"   # 页面/卡片底（白）
CANVAS_BG  = "#F5F5F5"   # 滚动区底（浅灰）
BORDER     = "#E0E0E0"   # 描边/分隔线
TEXT       = "#212121"   # 主文字（近黑）
MUTED      = "#757575"   # 次要文字（中灰）
LOG_BG     = "#FAFAFA"   # 日志区底
LOG_FG     = "#37474F"   # 日志文字
DIS_BG     = "#F5F5F5"
DIS_FG     = "#9E9E9E"

SUCCESS      = "#43A047"   # 绿：成功/确认（开始运行）
SUCCESS_DEEP = "#2E7D32"
SUCCESS_TINT = "#E8F5E9"
DANGER       = "#E53935"   # 红：危险/删除/错误（停止）
DANGER_DEEP  = "#C62828"
DANGER_TINT  = "#FFEBEE"
INFO         = "#1E88E5"   # 蓝：信息提示（保存/测试）
INFO_DEEP    = "#1565C0"
INFO_TINT    = "#E3F2FD"
WARN         = "#FB8C00"   # 橙：警告（跳过计数）
WARN_DEEP    = "#EF6C00"

FONT_BANNER = ("Microsoft YaHei", 14, "bold")
FONT_TITLE  = ("Microsoft YaHei", 10, "bold")
FONT_BODY   = ("Microsoft YaHei", 9)
FONT_STAT   = ("Microsoft YaHei", 11, "bold")


def _setup_ui_theme():
    """中性 + 语义色主题：白底浅灰描边，仅用红/绿/蓝/橙表达功能语义，低饱和度、高对比。"""
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", font=FONT_BODY, background=BG, foreground=TEXT)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Body.TFrame", background=CANVAS_BG)

    # 默认按钮：中性描边（次要操作）
    style.configure("TButton", font=FONT_BODY, padding=(16, 9),
                    background=BG, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
    style.map("TButton",
              background=[("pressed", "#EEEEEE"), ("active", "#F5F5F5"), ("disabled", DIS_BG)],
              foreground=[("disabled", DIS_FG)])
    # 开始运行：绿（SUCCESS）
    style.configure("Start.TButton", background=SUCCESS, foreground="#FFFFFF",
                    bordercolor=SUCCESS, lightcolor=SUCCESS, darkcolor=SUCCESS)
    style.map("Start.TButton",
              background=[("pressed", SUCCESS_DEEP), ("active", SUCCESS), ("disabled", DIS_BG)],
              foreground=[("disabled", DIS_FG)])
    # 停止：红（DANGER）
    style.configure("Stop.TButton", background=DANGER, foreground="#FFFFFF",
                    bordercolor=DANGER, lightcolor=DANGER, darkcolor=DANGER)
    style.map("Stop.TButton",
              background=[("pressed", DANGER_DEEP), ("active", DANGER), ("disabled", DIS_BG)],
              foreground=[("disabled", DIS_FG)])
    # 保存配置：蓝（INFO）
    style.configure("Save.TButton", background=INFO, foreground="#FFFFFF",
                    bordercolor=INFO, lightcolor=INFO, darkcolor=INFO)
    style.map("Save.TButton",
              background=[("pressed", INFO_DEEP), ("active", INFO), ("disabled", DIS_BG)],
              foreground=[("disabled", DIS_FG)])
    # 测试匹配引擎：蓝色描边（信息/诊断）
    style.configure("Test.TButton", background=BG, foreground=INFO_DEEP,
                    bordercolor=INFO, lightcolor=INFO, darkcolor=INFO)
    style.map("Test.TButton",
              background=[("pressed", INFO_TINT), ("active", INFO_TINT), ("disabled", DIS_BG)],
              foreground=[("disabled", DIS_FG)])

    # 分区卡片：白底 + 浅灰描边，标题深灰加粗
    style.configure("TLabelframe", background=BG, bordercolor=BORDER, borderwidth=1,
                    relief="solid", lightcolor=BORDER, darkcolor=BORDER)
    style.configure("TLabelframe.Label", background=BG, foreground=TEXT, font=FONT_TITLE)

    # 统计小卡：统一中性样式，数值按语义着色（已打招呼=绿 / 已跳过=橙）
    style.configure("Stat.TLabelframe", background=BG, bordercolor=BORDER, borderwidth=1,
                    relief="solid", lightcolor=BORDER, darkcolor=BORDER)
    style.configure("Stat.TLabelframe.Label", background=BG, foreground=MUTED,
                    font=("Microsoft YaHei", 8, "bold"))
    style.configure("StatValue.TLabel", background=BG, foreground=TEXT, font=FONT_STAT)
    style.configure("StatOk.TLabel", background=BG, foreground=SUCCESS_DEEP, font=FONT_STAT)
    style.configure("StatWarn.TLabel", background=BG, foreground=WARN_DEEP, font=FONT_STAT)

    style.configure("TRadiobutton", background=BG, foreground=TEXT)
    style.map("TRadiobutton", background=[("active", BG)])
    style.configure("TCheckbutton", background=BG, foreground=TEXT)
    style.map("TCheckbutton", background=[("active", BG)])

    style.configure("TEntry", fieldbackground="#FFFFFF", foreground=TEXT,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=3)
    style.configure("TSpinbox", fieldbackground="#FFFFFF", foreground=TEXT,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    arrowcolor=MUTED, padding=2)

    style.configure("TScrollbar", background="#BDBDBD", troughcolor="#F5F5F5",
                    bordercolor="#F5F5F5", arrowcolor=MUTED, relief="flat")
    style.configure("Vertical.TScrollbar", background="#BDBDBD", troughcolor="#F5F5F5",
                    bordercolor="#F5F5F5", arrowcolor=MUTED, relief="flat")
    return style



class ScrollableFrame(ttk.Frame):
    def __init__(self, container, *args, **kwargs):
        super().__init__(container, *args, **kwargs)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0, bg=CANVAS_BG)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, style="Body.TFrame")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self.window_id, width=e.width))
        self.inner.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_mousewheel))
        self.inner.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


class MainWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("Boss 直聘 HR 智能招聘助手")
        self.root.geometry("640x780")
        self.root.configure(bg=BG)
        self.config = load_config()

        self.log_queue = queue.Queue()
        self.state_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.cmd_queue = queue.Queue()

        # 多选组：{UI key: [(显示文本, BooleanVar), ...]}
        self.multi_vars = {
            "activity": [],
            "schools": [],
            "experience": [],
            "education": [],
            "intention": [],
            "hopping": [],
        }
        self._build_ui()
        self._load_form(self.config)
        self._toggle_manual()
        self._poll_queues()

    # ---------- UI 构建 ----------
    def _build_ui(self):
        _setup_ui_theme()

        # 顶部横幅
        banner = ttk.Frame(self.root, padding=(10, 10, 10, 0))
        banner.pack(fill="x")
        ttk.Label(banner, text="Boss 直聘 HR 智能招聘助手", font=FONT_BANNER,
                  foreground=TEXT).pack(anchor="w")
        ttk.Label(banner, text="中性 · 语义色 · 招聘 RPA 小助手", font=("Microsoft YaHei", 9),
                  foreground=MUTED).pack(anchor="w")

        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        self.status_var = tk.StringVar(value="● 未运行")
        self.status_label = ttk.Label(top, textvariable=self.status_var,
                                      font=("Microsoft YaHei", 12, "bold"), foreground=MUTED)
        self.status_label.pack(side="left")

        self.stat_vars = {
            "page": tk.StringVar(value="0"),
            "scanned": tk.StringVar(value="0"),
            "matched": tk.StringVar(value="0"),
            "greeted": tk.StringVar(value="0"),
            "skipped": tk.StringVar(value="0"),
        }
        stats = ttk.Frame(top)
        stats.pack(side="right")
        stat_meta = [("page", "页数", "StatValue"), ("scanned", "已扫描", "StatValue"),
                     ("matched", "已匹配", "StatValue"), ("greeted", "已打招呼", "StatOk"),
                     ("skipped", "已跳过", "StatWarn")]
        for key, label, st in stat_meta:
            f = ttk.LabelFrame(stats, text=label, padding=(6, 2), style="Stat.TLabelframe")
            f.pack(side="left", padx=2)
            ttk.Label(f, textvariable=self.stat_vars[key],
                      style=st + ".TLabel").pack()

        btns = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        btns.pack(fill="x")
        self.btn_start = ttk.Button(btns, text="▶ 开始运行", command=self.start, style="Start.TButton")
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(btns, text="■ 停止", command=self.stop, state="disabled", style="Stop.TButton")
        self.btn_stop.pack(side="left", padx=6)
        ttk.Button(btns, text="保存配置", command=self.save, style="Save.TButton").pack(side="left", padx=6)
        ttk.Button(btns, text="测试匹配引擎", command=self.test_matcher, style="Test.TButton").pack(side="right")

        body = ScrollableFrame(self.root)
        body.pack(fill="both", expand=True, padx=8)
        form = body.inner
        self._build_form(form)

        log_frame = ttk.LabelFrame(self.root, text="运行日志", padding=6)
        log_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log_text = tk.Text(log_frame, height=9, state="disabled", wrap="word",
                                font=("Microsoft YaHei", 9), bg=LOG_BG, fg=LOG_FG,
                                insertbackground=LOG_FG, selectbackground=INFO_TINT,
                                relief="flat", borderwidth=0)
        sb = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        tip = ttk.Label(self.root, text="前置要求：Edge 独立配置登录 BOSS 直聘企业账号；页面处于「列表模式」；网络稳定。",
                        foreground=MUTED, padding=6)
        tip.pack(fill="x")

    def _build_form(self, parent):
        pad = {"padx": 6, "pady": 3}

        f0 = ttk.LabelFrame(parent, text="① 启动参数（岗位画像与筛选）", padding=8)
        f0.pack(fill="x", pady=4)

        row = ttk.Frame(f0)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="岗位名称（必填，用于切换推荐岗位）：").pack(side="left")
        self.position_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.position_var, width=28).pack(side="left", padx=6)

        row = ttk.Frame(f0)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="最小年龄：").pack(side="left")
        self.min_age_var = tk.IntVar(value=20)
        ttk.Spinbox(row, from_=18, to=70, textvariable=self.min_age_var, width=5).pack(side="left")
        ttk.Label(row, text="  最大年龄：").pack(side="left")
        self.max_age_var = tk.IntVar(value=45)
        ttk.Spinbox(row, from_=18, to=70, textvariable=self.max_age_var, width=5).pack(side="left")
        ttk.Label(row, text="  性别：").pack(side="left")
        self.gender_var = tk.StringVar(value="all")
        for val, label in [("all", "不限"), ("male", "男"), ("female", "女")]:
            ttk.Radiobutton(row, text=label, value=val, variable=self.gender_var).pack(side="left", padx=3)
        ttk.Label(row, text="  新牛人筛选：").pack(side="left", padx=(10, 0))
        self.new_talent_var = tk.StringVar(value="no")
        ttk.Radiobutton(row, text="否(常规)", value="no", variable=self.new_talent_var).pack(side="left")
        ttk.Radiobutton(row, text="是(新牛人子流程)", value="yes", variable=self.new_talent_var).pack(side="left")

        self._add_multi_group(f0, "活跃度（多选）", "activity",
                              ["刚刚活跃", "今日活跃", "本周活跃"])
        self._add_multi_group(f0, "院校要求（多选）", "schools",
                              ["985", "211", "双一流"])
        self._add_multi_group(f0, "经验要求（多选）", "experience",
                              ["1-3年", "3-5年", "5-10年", "10年以上"])
        self._add_multi_group(f0, "学历要求（多选）", "education",
                              ["大专", "本科", "硕士", "博士"])
        self._add_multi_group(f0, "求职意向（多选）", "intention",
                              ["只看在线", "只看今日查看"])
        self._add_multi_group(f0, "跳槽频率（多选）", "hopping",
                              ["稳定", "一般", "频繁"])

        row = ttk.Frame(f0)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="技能关键词（逗号分隔，用于匹配打分）：").pack(side="left")
        self.keywords_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.keywords_var, width=40).pack(side="left", padx=6)

        f1 = ttk.LabelFrame(parent, text="② 打招呼与节奏", padding=8)
        f1.pack(fill="x", pady=4)
        row = ttk.Frame(f1)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="运行模式：").pack(side="left")
        self.flow_mode_var = tk.StringVar(value="manual")
        ttk.Radiobutton(row, text="手动辅助（自己点按钮操作）", value="manual", variable=self.flow_mode_var,
                        command=self._toggle_manual).pack(side="left")
        ttk.Radiobutton(row, text="沟通页自动发常用语+求简历", value="chat_greet", variable=self.flow_mode_var,
                        command=self._toggle_manual).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(row, text="推荐牛人自动（需已发布岗位）", value="recommend", variable=self.flow_mode_var,
                        command=self._toggle_manual).pack(side="left", padx=(8, 0))
        ttk.Label(row, text="（手动模式：浏览器里自己点候选人，再用下面按钮操作）",
                  foreground="#6b7280").pack(side="left", padx=8)

        self.manual_frame = ttk.LabelFrame(f1, text="手动操作（需先点「开始运行」并登录）", padding=8)
        man_row = ttk.Frame(self.manual_frame)
        man_row.pack(fill="x")
        self.btn_pick_candidate = ttk.Button(man_row, text="① 点候选人", command=self.cmd_pick_candidate)
        self.btn_pick_candidate.pack(side="left")
        self.btn_send_phrase = ttk.Button(man_row, text="② 发送常用语", command=self.cmd_send_phrase)
        self.btn_send_phrase.pack(side="left", padx=6)
        self.btn_request_resume = ttk.Button(man_row, text="③ 求简历", command=self.cmd_request_resume)
        self.btn_request_resume.pack(side="left", padx=6)
        self.btn_grab_resume = ttk.Button(man_row, text="④ 抓取简历", command=self.cmd_grab_resume)
        self.btn_grab_resume.pack(side="left", padx=6)
        ttk.Label(man_row, text="（顺序：先①点选候选人，再②/③/④操作）", foreground="#6b7280").pack(side="left", padx=8)

        row = ttk.Frame(f1)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="打招呼数量（单次运行上限）：").pack(side="left")
        self.max_greet_var = tk.IntVar(value=30)
        ttk.Spinbox(row, from_=1, to=500, textvariable=self.max_greet_var, width=6).pack(side="left")
        ttk.Label(row, text="  打招呼方式：").pack(side="left")
        self.greet_mode_var = tk.StringVar(value="auto")
        ttk.Radiobutton(row, text="自动(真实鼠标事件)", value="auto", variable=self.greet_mode_var).pack(side="left")
        ttk.Radiobutton(row, text="半自动(人工确认)", value="manual", variable=self.greet_mode_var).pack(side="left")
        row = ttk.Frame(f1)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="随机延时(ms) 下限：").pack(side="left")
        self.interval_min_var = tk.IntVar(value=60000)
        ttk.Spinbox(row, from_=1000, to=120000, increment=1000, textvariable=self.interval_min_var, width=7).pack(side="left")
        ttk.Label(row, text="  上限：").pack(side="left")
        self.interval_max_var = tk.IntVar(value=60000)
        ttk.Spinbox(row, from_=1000, to=120000, increment=1000, textvariable=self.interval_max_var, width=7).pack(side="left")
        ttk.Label(row, text="  最大翻页：").pack(side="left")
        self.max_pages_var = tk.IntVar(value=5)
        ttk.Spinbox(row, from_=1, to=50, textvariable=self.max_pages_var, width=5).pack(side="left")

        row = ttk.Frame(f1)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="招呼语模板：").pack(side="left", anchor="n")
        self.greet_message_text = tk.Text(row, height=2, width=60, font=("Microsoft YaHei", 9))
        self.greet_message_text.pack(side="left", padx=6)

        f2 = ttk.LabelFrame(parent, text="③ 记录方式", padding=8)
        f2.pack(fill="x", pady=4)
        row = ttk.Frame(f2)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="记录方式：").pack(side="left")
        self.record_mode_var = tk.StringVar(value="excel")
        ttk.Radiobutton(row, text="本地 Excel", value="excel", variable=self.record_mode_var,
                        command=self._toggle_feishu).pack(side="left")
        ttk.Radiobutton(row, text="飞书多维表", value="feishu", variable=self.record_mode_var,
                        command=self._toggle_feishu).pack(side="left")
        ttk.Label(row, text="  本地 Excel 记录保存在 data/招聘记录.xlsx", foreground="#6b7280").pack(side="left", padx=8)

        self.feishu_frame = ttk.Frame(f2)
        self.feishu_frame.pack(fill="x", **pad)
        self._build_feishu(self.feishu_frame)
        self._toggle_feishu()

        f3 = ttk.LabelFrame(parent, text="高级：匹配引擎（默认本地规则，可换 AI）", padding=8)
        f3.pack(fill="x", pady=4)
        row = ttk.Frame(f3)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="匹配模式：").pack(side="left")
        self.mode_var = tk.StringVar(value="rules")
        ttk.Radiobutton(row, text="本地规则", value="rules", variable=self.mode_var).pack(side="left")
        ttk.Radiobutton(row, text="AI 引擎", value="ai", variable=self.mode_var).pack(side="left")
        ttk.Label(row, text="  最低匹配分：").pack(side="left")
        self.min_score_var = tk.IntVar(value=60)
        ttk.Spinbox(row, from_=0, to=100, textvariable=self.min_score_var, width=5).pack(side="left")
        ttk.Label(row, text="  AI 接口：").pack(side="left")
        self.ai_url_var = tk.StringVar(value="")
        ttk.Entry(row, textvariable=self.ai_url_var, width=30).pack(side="left", padx=6)
        ttk.Label(row, text="  Key：").pack(side="left")
        self.ai_key_var = tk.StringVar(value="")
        ttk.Entry(row, textvariable=self.ai_key_var, width=18, show="*").pack(side="left", padx=6)
        ttk.Label(row, text="  模型：").pack(side="left")
        self.ai_model_var = tk.StringVar(value="gpt-4o-mini")
        ttk.Entry(row, textvariable=self.ai_model_var, width=16).pack(side="left", padx=6)

    def _add_multi_group(self, parent, label, key, options):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=6, pady=3)
        ttk.Label(row, text=f"{label}：").pack(side="left")
        self.multi_vars[key] = []
        for opt in options:
            var = tk.BooleanVar(value=False)
            self.multi_vars[key].append((opt, var))
            cb = ttk.Checkbutton(row, text=opt, variable=var)
            cb.pack(side="left", padx=3)

    def _build_feishu(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill="x")
        self.feishu_app_id = tk.StringVar()
        self.feishu_secret = tk.StringVar()
        self.feishu_token = tk.StringVar()
        self.feishu_table = tk.StringVar()
        for i, (label, var, width) in enumerate([
            ("App ID", self.feishu_app_id, 16),
            ("App Secret", self.feishu_secret, 16),
            ("多维表 App Token", self.feishu_token, 20),
            ("Table ID", self.feishu_table, 14),
        ]):
            if i % 2 == 0:
                row = ttk.Frame(parent)
                row.pack(fill="x", padx=6, pady=2)
            ttk.Label(row, text=label).pack(side="left")
            ttk.Entry(row, textvariable=var, width=width, show="*" if label == "App Secret" else None).pack(side="left", padx=4)

    def _toggle_feishu(self):
        if self.record_mode_var.get() == "feishu":
            self.feishu_frame.pack(fill="x")
        else:
            self.feishu_frame.pack_forget()

    # ---------- 表单读写 ----------
    def _read_form(self):
        def multi(key):
            return [label for label, var in self.multi_vars[key] if var.get()]

        return {
            "job": {
                "position_name": self.position_var.get().strip(),
                "new_talent_flow": self.new_talent_var.get() == "yes",
            },
            "filters": {
                "gender": self.gender_var.get(),
                "min_age": self.min_age_var.get(),
                "max_age": self.max_age_var.get(),
                "education": multi("education"),
                "experience": multi("experience"),
                "activity": multi("activity"),
                "schools": multi("schools"),
                "job_intention": multi("intention"),
                "job_hopping": multi("hopping"),
                "keywords": [s.strip() for s in self.keywords_var.get().split(",") if s.strip()],
            },
            "matching": {
                "mode": self.mode_var.get(),
                "min_score": self.min_score_var.get(),
                "rules": {
                    "education_weight": 20,
                    "work_years_weight": 20,
                    "age_weight": 10,
                    "gender_weight": 10,
                    "keyword_weight": 40,
                    "keywords": [s.strip() for s in self.keywords_var.get().split(",") if s.strip()],
                },
                "ai": {
                    "api_url": self.ai_url_var.get().strip(),
                    "api_key": self.ai_key_var.get().strip(),
                    "model": self.ai_model_var.get().strip() or "gpt-4o-mini",
                    "fallback_to_rules": True,
                    "system_prompt": "你是资深招聘专家。根据岗位画像和候选人简历信息，输出 JSON：{\"score\": 0-100, \"reason\": \"简短理由\"}。",
                },
            },
            "action": {
                "flow_mode": self.flow_mode_var.get(),
                "greet_mode": self.greet_mode_var.get(),
                "greet_message": self.greet_message_text.get("1.0", "end").strip(),
                "max_greet_count": self.max_greet_var.get(),
                "interval_min_ms": self.interval_min_var.get(),
                "interval_max_ms": self.interval_max_var.get(),
                "auto_page": True,
                "max_pages": self.max_pages_var.get(),
            },
            "recording": {
                "mode": self.record_mode_var.get(),
                "feishu": {
                    "app_id": self.feishu_app_id.get().strip(),
                    "app_secret": self.feishu_secret.get().strip(),
                    "app_token": self.feishu_token.get().strip(),
                    "table_id": self.feishu_table.get().strip(),
                },
            },
        }

    def _load_form(self, cfg):
        f = cfg.get("filters", {})
        self.position_var.set(cfg.get("job", {}).get("position_name", ""))
        self.new_talent_var.set("yes" if cfg.get("job", {}).get("new_talent_flow") else "no")
        self.min_age_var.set(f.get("min_age", 20))
        self.max_age_var.set(f.get("max_age", 45))
        self.gender_var.set(f.get("gender", "all"))
        self.keywords_var.set(", ".join(f.get("keywords", [])))

        mapping = {
            "activity": f.get("activity", []),
            "schools": f.get("schools", []),
            "experience": f.get("experience", []),
            "education": f.get("education", []),
            "intention": f.get("job_intention", []),
            "hopping": f.get("job_hopping", []),
        }
        for key, values in mapping.items():
            for label, var in self.multi_vars[key]:
                var.set(label in values)

        a = cfg.get("action", {})
        self.flow_mode_var.set(a.get("flow_mode", "manual"))
        self.max_greet_var.set(a.get("max_greet_count", 30))
        self.greet_mode_var.set(a.get("greet_mode", "auto"))
        self.interval_min_var.set(a.get("interval_min_ms", 60000))
        self.interval_max_var.set(a.get("interval_max_ms", 60000))
        self.max_pages_var.set(a.get("max_pages", 5))
        self.greet_message_text.delete("1.0", "end")
        self.greet_message_text.insert("1.0", a.get("greet_message", ""))

        rec = cfg.get("recording", {})
        self.record_mode_var.set(rec.get("mode", "excel"))
        fs = rec.get("feishu", {})
        self.feishu_app_id.set(fs.get("app_id", ""))
        self.feishu_secret.set(fs.get("app_secret", ""))
        self.feishu_token.set(fs.get("app_token", ""))
        self.feishu_table.set(fs.get("table_id", ""))

        m = cfg.get("matching", {})
        self.mode_var.set(m.get("mode", "rules"))
        self.min_score_var.set(m.get("min_score", 60))
        ai = m.get("ai", {})
        self.ai_url_var.set(ai.get("api_url", ""))
        self.ai_key_var.set(ai.get("api_key", ""))
        self.ai_model_var.set(ai.get("model", "gpt-4o-mini"))

    # ---------- 动作 ----------
    def save(self):
        if not self.position_var.get().strip():
            messagebox.showwarning("缺少参数", "岗位名称是必填项。")
            return
        self.config = self._read_form()
        save_config(self.config)
        self.log("✅ 配置已保存到 data/config.json")

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.position_var.get().strip():
            messagebox.showwarning("缺少参数", "岗位名称是必填项。")
            return
        self.config = self._read_form()
        save_config(self.config)
        self.stop_event.clear()
        self.cmd_queue = queue.Queue()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.set_status("运行中")

        self.worker = threading.Thread(target=self._run_worker, daemon=True)
        self.worker.start()

    def _run_worker(self):
        try:
            rpa = BossRpa(self.config, on_log=self.log, on_state=self.on_state,
                          stop_event=self.stop_event, cmd_queue=self.cmd_queue)
            rpa.run()
        except Exception as e:
            self.log(f"❌ 任务异常：{e}")
        finally:
            self.log("任务已结束。")
            self.state_queue.put(("done", None))

    def stop(self):
        self.stop_event.set()
        self.log("已发送停止指令（当前步骤完成后停止）…")

    def test_matcher(self):
        profile = {
            "name": "测试候选人", "age": 28, "gender": "男", "education": "本科",
            "work_years": 5, "position": "Java 后端", "company": "某公司",
            "tags": ["Java", "Spring"], "summary": "5年后端经验", "school": "某大学",
        }
        cfg = self._read_form()
        score, passed, reasons = match(profile, cfg)
        self.log(f"🧪 测试匹配：{profile['name']} 得分 {score}，通过={passed}，原因：{'；'.join(reasons)}")

    # ---------- 日志与状态 ----------
    def log(self, msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_queue.put(f"[{ts}] {msg}")

    def on_state(self, stats):
        self.state_queue.put(("stats", stats))

    def set_status(self, text):
        self.state_queue.put(("status", text))

    def _poll_queues(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.config(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.config(state="disabled")
        except queue.Empty:
            pass
        try:
            while True:
                kind, payload = self.state_queue.get_nowait()
                if kind == "stats":
                    for k, v in payload.items():
                        if k in self.stat_vars:
                            self.stat_vars[k].set(str(v))
                elif kind == "status":
                    self.status_var.set(payload)
                    self.status_label.configure(foreground=SUCCESS_DEEP if "运行中" in payload else MUTED)
                elif kind == "done":
                    self.status_var.set("● 未运行")
                    self.btn_start.config(state="normal")
                    self.btn_stop.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queues)

    def _toggle_manual(self):
        if self.flow_mode_var.get() == "manual":
            self.manual_frame.pack(fill="x", pady=4)
        else:
            self.manual_frame.pack_forget()

    def _manual_running(self):
        if not (self.worker and self.worker.is_alive()):
            self.log("⚠️ 请先点「开始运行」并等待浏览器就绪。")
            return False
        if self.flow_mode_var.get() != "manual":
            self.log("⚠️ 当前不是手动辅助模式。")
            return False
        return True

    def cmd_send_phrase(self):
        if self._manual_running():
            self.cmd_queue.put({"type": "send_phrase",
                                "message": self.greet_message_text.get("1.0", "end").strip()})
            self.log("已发送指令：发送常用语")

    def cmd_request_resume(self):
        if self._manual_running():
            self.cmd_queue.put({"type": "request_resume"})
            self.log("已发送指令：求简历")

    def cmd_grab_resume(self):
        if self._manual_running():
            self.cmd_queue.put({"type": "grab_resume"})
            self.log("已发送指令：抓取在线简历")

    def cmd_pick_candidate(self):
        if self._manual_running():
            self.cmd_queue.put({"type": "next_candidate"})
            self.log("已发送指令：点候选人")
