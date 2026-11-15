# -*- coding: utf-8 -*-
"""单任务下载管理器：在后台线程跑一个下载，服务端轮询其进度。

为什么单任务：浏览器是共享单例（串行单标签的反爬设计决定），同一时刻只能跑
一个下载。已在跑时拒绝新任务。Playwright 同步 API 不能在 asyncio 事件循环里
跑，所以放独立线程，主线程只读快照。
"""
import threading
from typing import Optional

import downloader


class TaskManager:
    """持有当前/最近一次下载任务的状态，线程安全地读写快照。"""

    def __init__(self, max_log: int = 400) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._max_log = max_log
        self._reset_state()

    def _reset_state(self) -> None:
        self._state = {
            'running': False,
            'phase': 'idle',      # idle|catalog|running|done|stopped|error
            'title': None,
            'novel_id': None,
            'total': 0,           # 全书章数
            'target': 0,          # 本次目标章数
            'fetched': 0,         # 本次已抓
            'todo': 0,            # 本次待抓
            'last': None,         # 最近一章描述
            'error': None,
            'single_txt': None,
            'split_dir': None,
            'log': [],
        }

    # ---- 读 ----
    def snapshot(self) -> dict:
        with self._lock:
            return {k: (list(v) if isinstance(v, list) else v)
                    for k, v in self._state.items()}

    def is_running(self) -> bool:
        with self._lock:
            return self._state['running']

    # ---- 写（内部，加锁）----
    def _append_log(self, msg: str) -> None:
        log = self._state['log']
        log.append(msg)
        if len(log) > self._max_log:
            del log[:len(log) - self._max_log]

    def _on_progress(self, event: dict) -> None:
        """downloader 进度回调（在工作线程里被调用）。"""
        phase = event.get('phase')
        with self._lock:
            if phase == 'catalog':
                self._state.update(
                    phase='running', title=event['title'],
                    novel_id=event['novel_id'], total=event['total'],
                    target=event['target'], todo=event['todo'],
                    fetched=0)
                self._append_log(
                    f"《{event['title']}》共 {event['total']} 章，"
                    f"目标 {event['target']}，待抓 {event['todo']}")
            elif phase == 'chapter':
                self._state['fetched'] += 1
                self._state['last'] = (
                    f"第{event['index']}章 {event['title']} "
                    f"({event['chars']}字)")
                self._append_log(
                    f"✓ [{event['index']}/{event['total']}] "
                    f"{event['title']} ({event['chars']}字) "
                    f"{event['i']}/{event['todo']}")
            elif phase == 'chapter_fail':
                self._append_log(
                    f"✗ 第{event['index']}章 {event['title']} "
                    f"失败：{event['error']}")
            elif phase == 'stopped':
                self._append_log('已停止（进度已保存，可续传）。')
            elif phase == 'done':
                self._state.update(
                    single_txt=event.get('single_txt'),
                    split_dir=event.get('split_dir'))
                self._append_log(
                    f"完成：{event.get('chapters')} 章已导出。")

    # ---- 启动/停止 ----
    def start(self, *, novel=None, attach=False, start=None,
              end=None, split=False) -> tuple:
        """启动下载。返回 (ok, message)。已在运行则拒绝。"""
        with self._lock:
            if self._state['running']:
                return False, '已有下载任务在运行，请先停止或等待完成。'
            self._reset_state()
            self._state['running'] = True
            self._state['phase'] = 'catalog'
        self._stop.clear()

        def _work():
            try:
                if attach:
                    downloader.download_attach(
                        split=split, progress=self._on_progress,
                        stop_event=self._stop)
                else:
                    downloader.download_novel(
                        novel, start=start, end=end, split=split,
                        progress=self._on_progress, stop_event=self._stop)
                with self._lock:
                    if self._state['phase'] != 'stopped':
                        self._state['phase'] = 'done'
            except SystemExit as e:
                with self._lock:
                    self._state['phase'] = 'error'
                    self._state['error'] = str(e)
                    self._append_log(f'错误：{e}')
            except Exception as e:  # noqa: BLE001
                with self._lock:
                    self._state['phase'] = 'error'
                    self._state['error'] = str(e)
                    self._append_log(f'异常：{e}')
            finally:
                with self._lock:
                    self._state['running'] = False

        self._thread = threading.Thread(target=_work, daemon=True)
        self._thread.start()
        return True, '已启动。'

    def stop(self) -> tuple:
        """请求停止当前任务（下一章前优雅退出）。"""
        with self._lock:
            if not self._state['running']:
                return False, '当前没有运行中的任务。'
        self._stop.set()
        return True, '已发送停止请求，将在当前章后停止。'
