"""异步行政区划抓取流程。"""
from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from .client import XzqhClient
from .database import Database, TaskWrite
from .models import CrawlStats, Division, DivisionLevel, FetchResult, TaskState
from .utils import log_event

logger = logging.getLogger(__name__)
PROGRESS_LOG_INTERVAL = 25
PROGRESS_LOG_SECONDS = 5.0
DEFAULT_LEASE_SECONDS = 600.0

@dataclass(slots=True, frozen=True)
class WriterStats:
    saved_divisions: int
    succeeded: int
    failed: int

class XzqhCrawler:
    def __init__(self, *, db_path: str = "./data/xzqh.db", base_url: str = "https://dmfw.mca.gov.cn", request_timeout: float = 45.0, max_concurrency: int = 6, retry_attempts: int = 5, retry_base_delay: float = 2.0, retry_max_delay: float = 60.0, request_interval: float = 0.2, lease_seconds: float = DEFAULT_LEASE_SECONDS) -> None:
        self.db_path, self.base_url = db_path, base_url
        self.request_timeout, self.max_concurrency = request_timeout, max_concurrency
        self.retry_attempts, self.retry_base_delay = retry_attempts, retry_base_delay
        self.retry_max_delay, self.request_interval = retry_max_delay, request_interval
        self.lease_seconds = lease_seconds
        self.stats = CrawlStats()

    async def crawl(self) -> bool:
        return await self._run(mode="full", include_tree=True)

    async def retry_failed_jobs(self) -> bool:
        return await self._run(mode="resume", include_tree=False)

    async def _run(self, *, mode: str, include_tree: bool) -> bool:
        self.stats = CrawlStats(start_time=datetime.now())
        try:
            async with Database(self.db_path) as db, XzqhClient(base_url=self.base_url, request_timeout=self.request_timeout, max_concurrency=self.max_concurrency, retry_attempts=self.retry_attempts, retry_base_delay=self.retry_base_delay, retry_max_delay=self.retry_max_delay, request_interval=self.request_interval) as client:
                await db.start_run(mode=mode)
                if include_tree:
                    root = await client.fetch_tree(code="0", max_level=3)
                    divisions = [d for d in root.flatten() if int(d.level) <= 3]
                    await db.save_divisions(divisions)
                    self._record_l1_l3(divisions)
                    parent_codes = await db.get_task_parent_codes(DivisionLevel.TOWNSHIP)
                    await db.ensure_tasks(parent_codes, DivisionLevel.TOWNSHIP)
                # 只抓取待办任务，以及中断后租约已过期的 running 任务；
                # 已成功的任务（包括空结果）不会被重复请求。
                pending = await db.list_runnable_tasks()
                result = await self._crawl_tasks(db, client, pending)
                self.stats.townships = result.saved_divisions
                self.stats.tasks_succeeded = result.succeeded
                self.stats.tasks_failed = result.failed
                self.stats.total = (await db.get_statistics()).get("total", 0)
                # 运行结束时仍有 pending/running 任务（例如租约仍被其他执行者
                # 持有），说明本次运行没有收尾，不能报告成功。
                task_counts = await db.get_task_counts()
                unfinished = task_counts.get("pending", 0) + task_counts.get("running", 0)
                run_succeeded = result.failed == 0 and unfinished == 0
                await db.finish_run(status="succeeded" if run_succeeded else "failed", stats=self.stats)
                if unfinished:
                    log_event(logger, logging.WARNING, "unfinished_tasks", pending=task_counts.get("pending", 0), running=task_counts.get("running", 0))
                if client.remote_error_counts:
                    log_event(
                        logger,
                        logging.INFO,
                        "remote_error_summary",
                        statuses=",".join(
                            f"{status}:{count}"
                            for status, count in sorted(client.remote_error_counts.items())
                        ),
                    )
                return run_succeeded
        except Exception as exc:
            logger.error("抓取流程失败: %s", exc, exc_info=logger.isEnabledFor(logging.DEBUG))
            return False
        finally:
            self.stats.end_time = datetime.now()

    async def _crawl_tasks(self, db: Database, client: XzqhClient, parent_codes: list[str]) -> WriterStats:
        if not parent_codes:
            return WriterStats(0, 0, 0)
        worker_count = min(self.max_concurrency, len(parent_codes))
        fetch_queue: asyncio.Queue[str | None] = asyncio.Queue()
        write_queue: asyncio.Queue[FetchResult | None] = asyncio.Queue()
        # 已认领任务数：只有成功把任务置为 running 的父节点才会被请求，
        # writer 用它作为进度分母。
        claimed = [0]
        for code in parent_codes:
            await fetch_queue.put(code)
        for _ in range(worker_count):
            await fetch_queue.put(None)
        writer = asyncio.create_task(self._writer_loop(db, write_queue, claimed))
        workers = [asyncio.create_task(self._worker(db, fetch_queue, write_queue, client, claimed)) for _ in range(worker_count)]
        await asyncio.gather(*workers)
        await write_queue.put(None)
        return await writer

    async def _worker(self, db: Database, fetch_queue: asyncio.Queue[str | None], write_queue: asyncio.Queue[FetchResult | None], client: XzqhClient, claimed: list[int]) -> None:
        while (parent_code := await fetch_queue.get()) is not None:
            # 认领失败说明其他执行者仍持有有效租约，直接跳过，不重复请求。
            if not await db.claim_task(parent_code, DivisionLevel.TOWNSHIP, lease_seconds=self.lease_seconds):
                continue
            claimed[0] += 1
            await write_queue.put(await self._fetch_one(client, parent_code))

    async def _fetch_one(self, client: XzqhClient, parent_code: str) -> FetchResult:
        try:
            node = await client.fetch_township_tree(parent_code)
        except Exception as exc:
            return FetchResult(parent_code, DivisionLevel.TOWNSHIP, TaskState.FAILED, error=str(exc))
        if node is None:
            # 上游返回 200 但 data 为空：该父节点下确实没有乡级数据，
            # 这是成功的空结果，不写任何乡级记录，也不写 division_rejections。
            log_event(logger, logging.DEBUG, "empty_township_data", parent_code=parent_code)
            return FetchResult(parent_code, DivisionLevel.TOWNSHIP, TaskState.SUCCEEDED)
        divisions = tuple(d for d in node.flatten(parent_code=parent_code) if d.level == DivisionLevel.TOWNSHIP)
        return FetchResult(parent_code, DivisionLevel.TOWNSHIP, TaskState.SUCCEEDED, divisions=divisions)

    async def _writer_loop(self, db: Database, queue: asyncio.Queue[FetchResult | None], claimed: list[int]) -> WriterStats:
        saved = succeeded = failed = completed = 0
        last_log = time.monotonic()
        started_at = last_log
        while (result := await queue.get()) is not None:
            completed += 1
            if result.state is TaskState.SUCCEEDED: succeeded += 1
            else: failed += 1
            task_write = TaskWrite(result.parent_code, result.target_level, result.state, result.divisions, result.error)
            saved += await db.apply_task_write(task_write)
            now = time.monotonic()
            total = claimed[0]
            # 失败任务统一计入进度和最终摘要；不要为每个远端 5xx 单独打一行，
            # 否则批量故障时终端会被重复日志淹没。
            if completed == 1 or completed == total or completed % PROGRESS_LOG_INTERVAL == 0 or now - last_log >= PROGRESS_LOG_SECONDS:
                elapsed = max(now - started_at, 0.001)
                log_event(logger, logging.INFO, "progress", completed=f"{completed}/{total}", succeeded=succeeded, failed=failed, saved=saved, rate=f"{completed / elapsed:.1f}/s")
                last_log = now
        return WriterStats(saved, succeeded, failed)

    def _record_l1_l3(self, divisions: list[Division]) -> None:
        self.stats.provinces = sum(d.level == DivisionLevel.PROVINCE for d in divisions)
        self.stats.cities = sum(d.level == DivisionLevel.CITY for d in divisions)
        self.stats.counties = sum(d.level == DivisionLevel.COUNTY for d in divisions)
