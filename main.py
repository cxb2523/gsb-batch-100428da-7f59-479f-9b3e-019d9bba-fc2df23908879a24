#!/usr/bin/env python3
"""企业微信智能客服 — 协调器

职责：启动 → mainloop 驱动 → 后台线程扫描 → 调度 → 退出
所有参数由 config.json 驱动。
"""

import asyncio
import logging
import os
import queue
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

# ═══ 日志配置必须在所有 import 之前，否则 basicConfig 会被忽略 ═══
os.makedirs("logs", exist_ok=True)
LOG_PATH = os.path.abspath("logs/monitor.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
    force=True,  # 强制覆盖任何已有配置
)
log = logging.getLogger(__name__)

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # skip HF network verification (~10s delay)
from dotenv import load_dotenv
load_dotenv()

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

# 注意: torch 必须在 paddle 之前导入，否则 DLL 冲突
from rag.responder import Responder
from rag.human_fallback import PendingQueue
from config.manager import ConfigManager
from wxbot.scanner import Scanner
from wxbot.detector import MessageDetector
from debug.visualizer import DebugVisualizer
from gui.main_window import MainWindow

# PaddleOCR 导入会修改 root logger 级别为 WARNING，需要恢复
logging.getLogger().setLevel(logging.INFO)


def main():
    """Entry point — tkinter mainloop drives the event loop."""
    try:
        _main_impl()
    except KeyboardInterrupt:
        log.info("停止 (Ctrl+C)")
    except Exception as e:
        log.critical(f"致命异常，进程退出: {e}\n{traceback.format_exc()}")
        raise


def _main_impl():
    # 初始化模块
    config = ConfigManager()
    cfg = config.config

    scanner = Scanner(cfg)
    detector = MessageDetector(cfg)
    responder = Responder(scanner)

    # 调试可视化
    debug_cfg = cfg.get("debug", {})
    visualizer = DebugVisualizer(
        output_dir=debug_cfg.get("output_dir", "debug"),
        max_history=debug_cfg.get("max_history", 10),
    )
    save_debug = debug_cfg.get("save_screenshots", True)

    # 主控窗口
    window = MainWindow()

    # 人工确认队列
    pending_queue = PendingQueue()

    # 线程安全发送队列：auto_send（后台线程）和 manual send（UI）统一入队
    send_queue: queue.Queue[tuple[str, str, str]] = queue.Queue()

    def _refresh_pending_tab():
        """Refresh the pending queue tab in the GUI."""
        items = pending_queue.get_all()
        counts = {name: pending_queue.get_cleanup_count(name)
                  for name in set(i.customer_name for i in items)}
        window.refresh_pending_queue(items, counts)

    def _do_send(customer_name: str, reply_text: str) -> bool:
        """Execute the actual send: switch to WeChat, paste, enter, switch back."""
        try:
            scanner._switch_to_wecom_and_back(
                lambda: scanner.send_message_via_keyboard(reply_text))
            log.info(f"[发送] {customer_name}: {reply_text[:80]}")
            return True
        except Exception as e:
            log.error(f"[发送失败] {customer_name}: {e}")
            return False

    def _process_send_queue_tick():
        """Process ONE send queue item per tick to avoid blocking GUI.

        ctypes clipboard + keyboard input takes ~300ms.
        Processing multiple items in a loop would freeze the UI.
        """
        try:
            mode, customer_name, reply_text = send_queue.get_nowait()
            log.debug(f"发送队列处理: mode={mode} customer={customer_name}")
            send_ok = _do_send(customer_name, reply_text)
            # Log send result AFTER actual send (not optimistically)
            responder.log_send_result(customer_name, reply_text, send_ok)
            if send_ok:
                root.after(0, window.increment_auto_sent)
                root.after(0,
                    lambda: window.add_record(
                        customer_name, "", "replied", reply_text))
        except queue.Empty:
            pass
        root.after(500, _process_send_queue_tick)

    # Register pending queue action callbacks
    def _handle_pending_send(customer_name):
        item = pending_queue.remove(customer_name)
        if item and item.ai_reply:
            if window.is_paused():
                # Paused: copy to clipboard, don't steal mouse
                root.clipboard_clear()
                root.clipboard_append(item.ai_reply)
                log.info(f"已复制到剪贴板: {customer_name}: {item.ai_reply[:50]}")
                root.after(0,
                    lambda: window.add_record(customer_name, "", "replied",
                                              f"[已复制] {item.ai_reply[:40]}"))
            else:
                send_queue.put(("send", customer_name, item.ai_reply))
                log.info(f"人工确认发送: {customer_name}: {item.ai_reply[:50]}")
                root.after(0,
                    lambda: window.add_record(customer_name, "", "replied", item.ai_reply))
                root.after(0, window.increment_auto_sent)
        _refresh_pending_tab()

    def _handle_pending_edit(customer_name, edited_text=None):
        """Edit & send — receives edited text from dialog."""
        if edited_text is None:
            return
        item = pending_queue.remove(customer_name)
        if item and edited_text:
            if window.is_paused():
                root.clipboard_clear()
                root.clipboard_append(edited_text)
                log.info(f"已复制到剪贴板(编辑): {customer_name}: {edited_text[:50]}")
                root.after(0,
                    lambda: window.add_record(customer_name, "", "replied",
                                              f"[已复制] {edited_text[:40]}"))
            else:
                send_queue.put(("send", customer_name, edited_text))
                log.info(f"人工编辑发送: {customer_name}: {edited_text[:50]}")
                root.after(0,
                    lambda: window.add_record(customer_name, "", "replied", edited_text))
                root.after(0, window.increment_auto_sent)
        _refresh_pending_tab()

    def _get_pending_reply(customer_name):
        """Get full reply text for a pending item (for edit dialog)."""
        item = pending_queue.get(customer_name)
        return item.ai_reply if item else ""

    def _handle_pending_ignore(customer_name):
        pending_queue.remove(customer_name)
        _refresh_pending_tab()

    window.set_pending_callbacks(
        on_send=_handle_pending_send,
        on_edit=_handle_pending_edit,
        on_ignore=_handle_pending_ignore,
        on_get_reply=_get_pending_reply,
    )

    # --- 读取配置 ---
    CHECK = cfg.get("check_interval_seconds", 3)

    # 裁切参数
    crop = cfg.get("crop", {})
    name_row = crop.get("name_row", {})
    row_top_offset = name_row.get("top_offset", -30)
    row_bot_offset = name_row.get("bottom_offset", 110)

    # OCR 置信度
    occ = cfg.get("ocr_confidence", {})
    conf_name = occ.get("name_detection", 0.05)
    conf_bubble = occ.get("bubble_text", 0.15)

    # 时序
    tmg = cfg.get("timing", {})
    t_after_click_row = tmg.get("after_click_row_delay", 1.2)
    t_after_scroll = tmg.get("after_scroll_delay", 0.5)

    # 兜底
    fallback = cfg.get("fallback", {})

    log.info(f"WeCom Auto Reply 启动 (日志: {LOG_PATH})")

    root = window.root

    def _cleanup():
        """Close Qdrant on exit."""
        try:
            responder.close()
            log.info("Qdrant 客户端已关闭")
        except Exception:
            pass
        log.info("清理完成")

    # ── background scan thread ─────────────────────────────────────

    _scan_lock = threading.Lock()

    def scan_tick():
        """Schedule scan in background thread, repeat every CHECK seconds."""
        threading.Thread(target=_run_scan, daemon=True).start()
        root.after(CHECK * 1000, scan_tick)

    def _run_scan():
        """Background thread: scan, OCR, RAG, dispatch."""
        if not _scan_lock.acquire(blocking=False):
            log.debug("上一轮扫描未完成，跳过")
            return
        try:
            if window.is_paused():
                return

            config.reload()

            if save_debug:
                visualizer.start_session()

            # ══ 1. 窗口 ══
            if not scanner.focus():
                return

            # ══ 2. 聊天列表截图 + 去重 ══
            c2_img = scanner.capture_chat_list()
            if c2_img is None:
                return
            if detector.col2_seen(c2_img):
                return

            # ══ 3. 红点检测 ══
            reds = detector.detect_red_dots(c2_img)
            if reds:
                log.info(f"红点: {len(reds)}个, Y={reds}")

            if save_debug:
                visualizer.save_chat_list(c2_img, reds)

            # ══ 4. 预过滤: 找到有@微信的客户红点 ══
            replied = False
            customer_red_y = None
            customer_name = "客户"

            if reds:
                cw2 = c2_img.size[0]
                for ry in reversed(reds):
                    # Skip recently processed y-positions to avoid
                    # expensive OCR re-scan every 3s after send failure
                    if not detector.is_clickable(ry, timeout=15):
                        log.info(f"红点y={ry} 冷却中，跳过")
                        continue
                    row_img = c2_img.crop(
                        (0, max(0, ry + row_top_offset), cw2,
                         ry + row_bot_offset))
                    name_crop = detector.detect_name_region(row_img)
                    if name_crop is None:
                        log.info(f"红点y={ry} 未检测到名字区域")
                        continue
                    name_text = detector.extract_text(name_crop,
                                                     min_conf=conf_name)
                    is_cust = detector.is_customer_name(name_crop, name_text)
                    log.info(f"红点y={ry} 对话名: [{name_text[:50]}] "
                             f"客户={is_cust}")

                    if save_debug:
                        visualizer.save_name_crop(name_crop, name_text)

                    if is_cust:
                        customer_red_y = ry
                        customer_name = detector.clean_name_text(name_text) if name_text else "客户"
                        break
                    detector.mark_clicked(ry)

                if customer_red_y is None:
                    log.info("红点均非客户对话")
                else:
                    # ══ 5. 点击客户红点 ══
                    if not scanner.click_col2_row(customer_red_y):
                        log.warning("col2 点击失败，跳过")
                        return
                    detector.mark_clicked(customer_red_y)
                    time.sleep(t_after_click_row)

                    # ══ 6. 聊天区截图 ══
                    scanner.scroll_to_bottom()
                    time.sleep(t_after_scroll)

                    chat_img = scanner.capture_chat_area()
                    if chat_img is None:
                        return

                    if save_debug:
                        visualizer.save_chat_area(chat_img)

                    # ══ 7. 气泡扫描 ══
                    unreplied, has_blue = detector.extract_unreplied_bubbles(
                        chat_img)
                    if not unreplied:
                        log.info("无气泡/已回复")
                    else:
                        # ══ 8. OCR 合并所有未回复灰泡 ══
                        text = " ".join(
                            detector.extract_text(b, min_conf=conf_bubble)
                            for b, _ in unreplied
                        ).strip()
                        if not text or len(text) < 2:
                            log.info("OCR空")
                        else:
                            log.info(f"OCR: {text[:120]}")

                            if save_debug:
                                visualizer.save_ocr_result(chat_img, text)
                                visualizer.update_meta(
                                    unreplied=[text], is_customer=True)

                            # ══ 9. 客户判定 ══
                            # If red dot path already confirmed customer,
                            # skip bubble is_customer check (which can fail
                            # when bubble nick OCR doesn't match pattern)
                            bn = unreplied[0][1]
                            if customer_name not in ("", "客户") or detector.is_customer(bn, has_blue, text):
                                log.info(">>> 客户消息")
                                if detector.has_stop_keyword(text):
                                    log.info("客户要求停止回复，跳过")
                                    detector.mark_message_seen(customer_name, text)
                                    if save_debug:
                                        visualizer.set_action("stop_reply")
                                elif detector.contains_own_reply(text):
                                    log.info("OCR包含自己回复，跳过(蓝泡检测失败)")
                                    detector.mark_message_seen(customer_name, text)
                                    if save_debug:
                                        visualizer.set_action("stop_reply")
                                elif detector.is_message_seen(customer_name, text):
                                    log.info("消息已处理过(去重)")
                                    if save_debug:
                                        visualizer.set_action("dedup")
                                elif detector.is_in_cooldown():
                                    log.info("冷却中")
                                    if save_debug:
                                        visualizer.set_action("cooldown")
                                else:
                                    # ══ 10. RAG 回复 ══
                                    result = asyncio.run(
                                        responder.handle_customer_message(
                                            customer_name, [text]))
                                    if result.success and result.dispatch_level == "auto_send":
                                        # Unified send path: all sends go through
                                        # send_queue → _do_send → _switch_to_wecom_and_back
                                        # UI update happens in _process_send_queue_tick
                                        # after _do_send confirms success
                                        send_queue.put(("send", customer_name, result.reply_text))
                                        detector.mark_replied()
                                        detector.mark_message_seen(customer_name, text)
                                        detector.mark_text_sent(result.reply_text)
                                        detector.clear_seen()
                                        replied = True
                                        if save_debug:
                                            visualizer.set_action(
                                                "replied", result.reply_text)
                                    elif result.success and result.dispatch_level == "human_confirm":
                                        detector.mark_message_seen(customer_name, text)
                                        pending_queue.push(
                                            customer_name, text,
                                            result.reply_text,
                                            result.retrieval_score,
                                            result.guard_decision,
                                        )
                                        root.after(0, _refresh_pending_tab)
                                        log.info(f"人工确认: {customer_name} "
                                                 f"score={result.retrieval_score:.2f}")
                                        if save_debug:
                                            visualizer.set_action("human_confirm", result.reply_text)
                                        root.after(0,
                                            lambda: window.add_record(
                                                customer_name, text, "human_confirm",
                                                f"[待确认] {result.reply_text[:40]}"))
                                    else:
                                        # 真正升级才标记已见；技术失败(如发送失败)允许重试
                                        if result.escalated:
                                            detector.mark_message_seen(customer_name, text)
                                        if save_debug:
                                            visualizer.set_action(
                                                "escalated", result.reason)
                                        root.after(0,
                                            lambda: window.add_record(
                                                customer_name, text, "escalated",
                                                result.reason))
                            else:
                                log.info("非客户")
                                if save_debug:
                                    visualizer.update_meta(is_customer=False)

            # ══ 11. 兜底：无回复时扫前N条 ══
            if not replied and not detector.is_in_cooldown():
                found, fb_text, fb_name = asyncio.run(
                    _scan_fallback(scanner, detector, cfg))
                fb_cust = (fb_name or customer_name)
                # Use customer_name for dedup (main path's cleaned name),
                # not fb_name which may differ due to OCR at different Y.
                if found and not detector.is_message_seen(customer_name, fb_text):
                    result = asyncio.run(
                        responder.handle_customer_message(
                            fb_cust, [fb_text]))
                    if result.success and result.dispatch_level == "auto_send":
                        # Unified send path
                        send_queue.put(("send", fb_cust, result.reply_text))
                        detector.mark_replied()
                        detector.mark_message_seen(fb_cust, fb_text)
                        detector.mark_text_sent(result.reply_text)
                        detector.clear_seen()
                    elif result.success and result.dispatch_level == "human_confirm":
                        detector.mark_message_seen(fb_cust, fb_text)
                        pending_queue.push(
                            fb_cust, fb_text, result.reply_text,
                            result.retrieval_score, result.guard_decision)
                        root.after(0, _refresh_pending_tab)
                        log.info(f"人工确认(兜底): {fb_cust} "
                                 f"score={result.retrieval_score:.2f}")
                    elif result.escalated:
                        detector.mark_message_seen(fb_cust, fb_text)

            if save_debug:
                visualizer.finish()

        except Exception as e:
            log.error(f"扫描异常: {e}\n{traceback.format_exc()}")
        finally:
            _scan_lock.release()

    # ── exit detection ─────────────────────────────────────────────

    def _check_exit():
        """Check for exit flag every 100ms on main thread."""
        if window._should_exit:
            _cleanup()
            root.destroy()
            return
        root.after(100, _check_exit)

    # ── start everything ───────────────────────────────────────────

    root.after(CHECK * 1000, scan_tick)
    root.after(500, _process_send_queue_tick)
    root.after(100, _check_exit)

    root.mainloop()
    log.info("已退出")


async def _scan_fallback(scanner: Scanner, detector: MessageDetector,
                         cfg: dict):
    """兜底扫描：扫 col2 前 N 个条目找客户对话"""
    c2 = scanner.get_col2_region()
    if not c2:
        return False, "", None

    c2_img = scanner.capture_chat_list()
    if c2_img is None:
        return False, "", None

    # --- 从 config 读取所有参数 ---
    fallback_cfg = cfg.get("fallback", {})
    start_y = int((c2[3] if len(c2) > 3 else 650) * fallback_cfg.get("start_y_ratio", 0.09))
    row_h = fallback_cfg.get("row_height_px", 55)
    max_rows = fallback_cfg.get("max_scan_rows", 3)

    crop_cfg = cfg.get("crop", {}).get("name_row", {})
    row_top = crop_cfg.get("top_offset", -30)
    row_bot = crop_cfg.get("bottom_offset", 110)

    occ = cfg.get("ocr_confidence", {})
    conf_name = occ.get("name_detection", 0.05)
    conf_bubble = occ.get("bubble_text", 0.15)

    tmg = cfg.get("timing", {})
    t_click = tmg.get("fallback_after_click_delay", 1.0)
    t_scroll = tmg.get("fallback_after_scroll_delay", 0.3)

    dedup = cfg.get("dedup", {})
    cooldown = dedup.get("fallback_click_cooldown_seconds", 120)

    for i in range(max_rows):
        y = start_y + i * row_h
        if not detector.is_clickable(y, timeout=cooldown):
            continue

        cw2 = c2_img.size[0]
        row_img = c2_img.crop((0, max(0, y + row_top), cw2, y + row_bot))
        name_crop = detector.detect_name_region(row_img)
        if name_crop is None:
            log.info(f"兜底行{i} y={y} 未检测到名字区域")
            continue
        name_text = detector.extract_text(name_crop, min_conf=conf_name)
        is_cust = detector.is_customer_name(name_crop, name_text)
        log.info(f"兜底行{i} y={y} 对话名: [{name_text[:50]}] 客户={is_cust}")
        if not is_cust:
            detector.mark_clicked(y)
            continue

        if not scanner.click_col2_row(y):
            log.warning(f"兜底 col2 点击失败 y={y}")
            continue
        await asyncio.sleep(t_click)

        scanner.scroll_to_bottom()
        await asyncio.sleep(t_scroll)

        chat_img = scanner.capture_chat_area()
        if chat_img is None:
            continue

        unreplied, has_blue = detector.extract_unreplied_bubbles(chat_img)
        if not unreplied:
            continue

        combined = " ".join(
            detector.extract_text(b, min_conf=conf_bubble)
            for b, _ in unreplied
        ).strip()
        if not combined or len(combined) < 2:
            continue

        bn = unreplied[0][1]
        if not detector.is_customer(bn, has_blue, combined):
            continue

        log.info(f"兜底y={y} OCR: {combined[:100]}")
        log.info(f">>> 兜底客户! y={y}")
        detector.mark_clicked(y)
        return True, combined, detector.clean_name_text(name_text)

    return False, "", None


def _detach_process():
    """Re-launch this script as a detached process.

    When the user closes the terminal, the detached process survives.
    Uses DETACHED_PROCESS (not CREATE_NO_WINDOW) so tkinter GUI
    window can still be created.

    Strips --detach from argv to prevent infinite respawn loop.
    """
    import subprocess
    log.info("以脱离终端模式重新启动...")
    # Strip --detach from argv to prevent child from entering detach again
    child_argv = [a for a in sys.argv if a != "--detach"]
    try:
        subprocess.Popen(
            [sys.executable] + child_argv,
            creationflags=(
                subprocess.DETACHED_PROCESS
                if os.name == "nt" else 0
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log.info("新进程已启动，当前进程退出")
    except Exception as e:
        log.error(f"脱离模式启动失败: {e}")
    sys.exit(0)


if __name__ == "__main__":
    if "--detach" in sys.argv:
        _detach_process()
    else:
        main()
