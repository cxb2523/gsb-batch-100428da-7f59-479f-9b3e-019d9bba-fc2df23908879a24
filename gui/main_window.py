# gui/main_window.py
"""主控窗口模块 — tkinter GUI

职责：
- 实时显示每条消息的处理结果
- 状态栏显示统计信息
- 支持导出记录为 CSV
"""

import csv
import logging
import os
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class MessageRecord:
    """消息记录"""

    def __init__(self, timestamp: str, customer: str, message: str,
                 action: str, reply: str = ""):
        self.timestamp = timestamp
        self.customer = customer
        self.message = message
        self.action = action
        self.reply = reply

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "customer": self.customer,
            "message": self.message,
            "action": self.action,
            "reply": self.reply,
        }


class MainWindow:
    """主控窗口"""

    def __init__(self, on_pause: Callable = None, on_resume: Callable = None):
        self.on_pause = on_pause
        self.on_resume = on_resume
        self._records: List[MessageRecord] = []
        self._paused = False
        self._should_exit = False

        # Pending queue callbacks
        self._on_pending_send: Optional[Callable] = None
        self._on_pending_edit: Optional[Callable] = None
        self._on_pending_ignore: Optional[Callable] = None

        # Stats
        self._stats = {
            "replied": 0,
            "escalated": 0,
            "unreplied": 0,
            "unrecognized": 0,
        }
        self._auto_sent_count = 0
        self._error_marked_count = 0

        # Create window
        self._create_window()

    def _create_window(self):
        """创建窗口"""
        self.root = tk.Tk()
        self.root.title("企业微信智能客服")
        # 右上角定位
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"700x280+{sw - 720}+10")
        self.root.attributes('-topmost', True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 顶部标题栏
        self._create_header()

        # 状态栏
        self._create_status_bar()

        # 标签页
        self._create_tabs()

        # 底部按钮
        self._create_footer()

    def _create_header(self):
        """创建顶部标题栏"""
        header = ttk.Frame(self.root)
        header.pack(fill=tk.X, padx=10, pady=5)

        title = ttk.Label(header, text="企业微信智能客服", font=("微软雅黑", 14, "bold"))
        title.pack(side=tk.LEFT)

        # 按钮
        btn_frame = ttk.Frame(header)
        btn_frame.pack(side=tk.RIGHT)

        self._pause_btn = ttk.Button(btn_frame, text="暂停", command=self._toggle_pause)
        self._pause_btn.pack(side=tk.LEFT, padx=5)

    def _create_status_bar(self):
        """创建状态栏"""
        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill=tk.X, padx=10, pady=5)

        self._status_label = ttk.Label(status_frame, text="状态: 运行中")
        self._status_label.pack(side=tk.LEFT)

        ttk.Separator(status_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)

        self._stats_label = ttk.Label(
            status_frame,
            text="已回复: 0 | 待人工: 0 | 未回复: 0 | 未识别: 0",
        )
        self._stats_label.pack(side=tk.LEFT)

    def _create_tabs(self):
        """创建标签页"""
        tab_frame = ttk.Frame(self.root)
        tab_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # 标签栏
        tab_bar = ttk.Frame(tab_frame)
        tab_bar.pack(fill=tk.X)

        self._tab_buttons = {}
        tabs = ["全部", "待处理", "待回复", "已回复", "待人工", "未识别"]
        for tab in tabs:
            btn = ttk.Button(
                tab_bar, text=tab,
                command=lambda t=tab: self._switch_tab(t),
            )
            btn.pack(side=tk.LEFT, padx=2)
            self._tab_buttons[tab] = btn

        self._current_tab = "全部"

        # ── message list treeview ──────────────────────────────────

        self._list_frame = ttk.Frame(tab_frame)
        self._list_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        columns = ("time", "customer", "message", "action")
        self._tree = ttk.Treeview(self._list_frame, columns=columns, show="headings")
        self._tree.heading("time", text="时间")
        self._tree.heading("customer", text="客户")
        self._tree.heading("message", text="消息")
        self._tree.heading("action", text="处理结果")
        self._tree.column("time", width=80)
        self._tree.column("customer", width=120)
        self._tree.column("message", width=300)
        self._tree.column("action", width=100)

        scrollbar = ttk.Scrollbar(self._list_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._tree.tag_configure("replied", foreground="green")
        self._tree.tag_configure("escalated", foreground="red")
        self._tree.tag_configure("unreplied", foreground="orange")
        self._tree.tag_configure("unrecognized", foreground="gray")

        # ── pending queue treeview ─────────────────────────────────

        self._pending_frame = ttk.Frame(tab_frame)

        p_columns = ("ptime", "pcustomer", "pmsg", "preply", "pconf", "pstatus", "paction")
        self._pending_tree = ttk.Treeview(self._pending_frame, columns=p_columns, show="headings")
        self._pending_tree.heading("ptime", text="时间")
        self._pending_tree.heading("pcustomer", text="客户")
        self._pending_tree.heading("pmsg", text="最新消息")
        self._pending_tree.heading("preply", text="AI建议")
        self._pending_tree.heading("pconf", text="置信度")
        self._pending_tree.heading("pstatus", text="状态")
        self._pending_tree.heading("paction", text="")
        self._pending_tree.column("ptime", width=50)
        self._pending_tree.column("pcustomer", width=70)
        self._pending_tree.column("pmsg", width=120)
        self._pending_tree.column("preply", width=120)
        self._pending_tree.column("pconf", width=50)
        self._pending_tree.column("pstatus", width=50)
        self._pending_tree.column("paction", width=0)  # hidden, used for data binding

        p_scrollbar = ttk.Scrollbar(self._pending_frame, orient=tk.VERTICAL,
                                     command=self._pending_tree.yview)
        self._pending_tree.configure(yscrollcommand=p_scrollbar.set)

        # Pending stats bar
        self._pending_stats_label = ttk.Label(
            self._pending_frame,
            text="共 0 条待处理 | 已自动发送 0 | 标记错误 0",
        )

        # Pending action buttons
        pending_btn_frame = ttk.Frame(self._pending_frame)
        self._send_btn = ttk.Button(pending_btn_frame, text="发送",
                   command=self._handle_pending_send)
        self._send_btn.pack(side=tk.LEFT, padx=2)
        ttk.Button(pending_btn_frame, text="编辑",
                   command=self._handle_pending_edit).pack(side=tk.LEFT, padx=2)
        ttk.Button(pending_btn_frame, text="忽略",
                   command=self._handle_pending_ignore).pack(side=tk.LEFT, padx=2)

        self._pending_stats_label.pack(side=tk.BOTTOM, fill=tk.X, pady=3)
        pending_btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=3)
        self._pending_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        p_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._pending_frame.pack_forget()  # hidden by default

    def _create_footer(self):
        """创建底部按钮"""
        footer = ttk.Frame(self.root)
        footer.pack(fill=tk.X, padx=10, pady=5)

        ttk.Button(footer, text="导出记录", command=self._export_csv).pack(side=tk.LEFT, padx=5)
        ttk.Button(footer, text="清空", command=self._clear_records).pack(side=tk.LEFT, padx=5)


    def _toggle_pause(self):
        """切换暂停状态"""
        self._paused = not self._paused
        if self._paused:
            self._pause_btn.config(text="继续")
            self._status_label.config(text="状态: 已暂停")
            self._send_btn.config(text="复制")
            if self.on_pause:
                self.on_pause()
        else:
            self._pause_btn.config(text="暂停")
            self._status_label.config(text="状态: 运行中")
            self._send_btn.config(text="发送")
            if self.on_resume:
                self.on_resume()

    def toggle_pause(self):
        """公开的暂停切换方法（供全局热键调用）"""
        self._toggle_pause()

    def _switch_tab(self, tab: str):
        """切换标签页"""
        self._current_tab = tab
        if tab == "待处理":
            self._list_frame.pack_forget()
            self._pending_frame.pack(fill=tk.BOTH, expand=True)
        else:
            self._pending_frame.pack_forget()
            self._list_frame.pack(fill=tk.BOTH, expand=True, pady=5)
            self._refresh_list()

    def _refresh_list(self):
        """刷新消息列表"""
        # 清空
        for item in self._tree.get_children():
            self._tree.delete(item)

        # 过滤
        filtered = self._records
        if self._current_tab == "待回复":
            filtered = [r for r in filtered if r.action == "unreplied"]
        elif self._current_tab == "已回复":
            filtered = [r for r in filtered if r.action == "replied"]
        elif self._current_tab == "待人工":
            filtered = [r for r in filtered if r.action == "escalated"]
        elif self._current_tab == "未识别":
            filtered = [r for r in filtered if r.action == "unrecognized"]

        # 添加
        for record in filtered:
            tag = record.action
            self._tree.insert(
                "", tk.END,
                values=(
                    record.timestamp,
                    record.customer,
                    record.message[:50] + ("..." if len(record.message) > 50 else ""),
                    self._get_action_text(record.action),
                ),
                tags=(tag,),
            )

    def _get_action_text(self, action: str) -> str:
        """获取动作文本"""
        mapping = {
            "replied": "已自动回复",
            "escalated": "待人工处理",
            "unreplied": "待回复",
            "unrecognized": "未识别",
        }
        return mapping.get(action, action)

    def _export_csv(self):
        """导出记录为 CSV"""
        if not self._records:
            messagebox.showinfo("导出", "没有记录可导出")
            return

        filename = f"records_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        filepath = os.path.join("data", filename)

        os.makedirs("data", exist_ok=True)

        with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "customer", "message", "action", "reply"])
            writer.writeheader()
            for record in self._records:
                writer.writerow(record.to_dict())

        messagebox.showinfo("导出", f"记录已导出到: {filepath}")

    def _clear_records(self):
        """清空记录"""
        if messagebox.askyesno("清空", "确定要清空所有记录吗？"):
            self._records.clear()
            self._stats = {
                "replied": 0,
                "escalated": 0,
                "unreplied": 0,
                "unrecognized": 0,
            }
            self._refresh_list()
            self._update_stats()

    def _update_stats(self):
        """更新统计信息"""
        self._stats_label.config(
            text=f"已回复: {self._stats['replied']} | "
                 f"待人工: {self._stats['escalated']} | "
                 f"未回复: {self._stats['unreplied']} | "
                 f"未识别: {self._stats['unrecognized']}"
        )

    def _on_close(self):
        """关闭窗口"""
        import traceback
        import logging as _log
        if messagebox.askyesno("退出", "确定要退出吗？"):
            _stack = ''.join(traceback.format_stack())
            _log.getLogger(__name__).info(
                f"_on_close 被调用，调用栈:\n{_stack}")
            try:
                with open("logs/close_trace.log", "a", encoding="utf-8") as _f:
                    _f.write(f"\n=== _on_close called ===\n{_stack}\n")
                    _f.flush()
            except Exception:
                pass
            self._should_exit = True
            _log.getLogger(__name__).info("_on_close: 用户确认退出")
            self.root.destroy()
        else:
            _log.getLogger(__name__).info("_on_close: 用户取消退出")

    # ═══ 公开接口 ═══════════════════════════

    def add_record(self, customer: str, message: str, action: str,
                   reply: str = ""):
        """添加消息记录"""
        timestamp = datetime.now().strftime("%H:%M")
        record = MessageRecord(timestamp, customer, message, action, reply)
        self._records.append(record)

        # 更新统计
        if action in self._stats:
            self._stats[action] += 1

        # 刷新列表
        self._refresh_list()
        self._update_stats()

    def is_paused(self) -> bool:
        """是否暂停"""
        return self._paused

    def run(self):
        """运行主循环"""
        self.root.mainloop()

    def update(self):
        """更新界面（非阻塞）"""
        self.root.update_idletasks()
        self.root.update()

    # ── pending queue ──────────────────────────────────────────────

    def set_pending_callbacks(self,
                               on_send: Callable = None,
                               on_edit: Callable = None,
                               on_ignore: Callable = None,
                               on_get_reply: Callable = None):
        """Register callbacks for pending queue actions."""
        self._on_pending_send = on_send
        self._on_pending_edit = on_edit
        self._on_pending_ignore = on_ignore
        self._on_get_reply = on_get_reply  # returns full reply text for a customer

    def refresh_pending_queue(self, pending_items: list,
                               cleaned_counts: dict = None):
        """Refresh pending queue treeview from PendingItem list."""
        cleaned_counts = cleaned_counts or {}
        for item in self._pending_tree.get_children():
            self._pending_tree.delete(item)

        for item in pending_items:
            ts = item.timestamp
            from datetime import datetime
            time_str = datetime.fromtimestamp(ts).strftime("%H:%M") if ts > 100000 else ""
            row_id = self._pending_tree.insert(
                "", tk.END,
                values=(
                    time_str,
                    item.customer_name,
                    item.customer_message[:30] + ("..." if len(item.customer_message) > 30 else ""),
                    item.ai_reply[:30] + ("..." if len(item.ai_reply) > 30 else ""),
                    f"{item.confidence:.0%}",
                    item.status,
                    item.customer_name,  # hidden: key for actions
                ),
            )
            # Per-customer cleanup sub-row
            cleaned = cleaned_counts.get(item.customer_name, 0)
            if cleaned > 0:
                self._pending_tree.insert(
                    "", tk.END,
                    values=("", "", f"📦 已清理 {cleaned} 条更早消息", "", "", "", ""),
                )

        self._pending_stats_label.config(
            text=f"共 {len(pending_items)} 条待处理 | "
                 f"已自动发送 {self._auto_sent_count} | "
                 f"标记错误 {self._error_marked_count}"
        )

    def increment_auto_sent(self):
        self._auto_sent_count += 1

    def increment_error_marked(self):
        self._error_marked_count += 1

    def _handle_pending_send(self):
        sel = self._pending_tree.selection()
        if sel:
            customer_name = self._pending_tree.item(sel[0], "values")[6]
            if self._on_pending_send:
                self._on_pending_send(customer_name)

    def _handle_pending_edit(self):
        sel = self._pending_tree.selection()
        if not sel:
            return
        customer_name = self._pending_tree.item(sel[0], "values")[6]
        # Get full reply text via callback
        current_reply = ""
        if self._on_get_reply:
            current_reply = self._on_get_reply(customer_name) or ""

        # Edit dialog
        dialog = tk.Toplevel(self.root)
        dialog.title(f"编辑回复 — {customer_name}")
        dialog.geometry("450x250")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text=f"客户: {customer_name}").pack(
            padx=10, pady=(10, 5), anchor="w")
        ttk.Label(dialog, text="AI建议回复:").pack(padx=10, anchor="w")

        text_box = tk.Text(dialog, height=6, width=50, wrap=tk.WORD)
        text_box.pack(padx=10, pady=5, fill=tk.BOTH, expand=True)
        text_box.insert("1.0", current_reply)
        text_box.focus_set()

        result = {"text": None}

        def on_confirm():
            result["text"] = text_box.get("1.0", tk.END).strip()
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10)
        confirm_text = "复制" if self._paused else "发送"
        ttk.Button(btn_frame, text=confirm_text, command=on_confirm).pack(
            side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=on_cancel).pack(
            side=tk.LEFT, padx=5)

        self.root.wait_window(dialog)

        if result["text"] and self._on_pending_edit:
            self._on_pending_edit(customer_name, result["text"])

    def _handle_pending_ignore(self):
        sel = self._pending_tree.selection()
        if sel:
            customer_name = self._pending_tree.item(sel[0], "values")[6]
            if self._on_pending_ignore:
                self._on_pending_ignore(customer_name)
