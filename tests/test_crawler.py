from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp import web

from xzqh_crawler.crawler import XzqhCrawler
from xzqh_crawler.database import Database


def _query_tasks(db_path) -> list[dict[str, object]]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM crawl_tasks ORDER BY parent_code"
        ).fetchall()
    return [dict(row) for row in rows]


def _query_divisions_with_parent(db_path, parent_code: str) -> list[dict[str, object]]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM divisions WHERE parent_code = ? ORDER BY code",
            (parent_code,),
        ).fetchall()
    return [dict(row) for row in rows]


def _count_rejections(db_path) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT COUNT(*) FROM division_rejections").fetchone()
    return int(row[0])


def _set_task_running(db_path, parent_code: str, *, lease_delta_seconds: float) -> None:
    """直接改库模拟进程中断后遗留的 running 任务。"""
    lease_until = datetime.now(UTC) + timedelta(seconds=lease_delta_seconds)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE crawl_tasks
            SET state = 'running', lease_until = ?
            WHERE parent_code = ? AND target_level = 4
            """,
            (lease_until.isoformat(), parent_code),
        )
        conn.commit()


@asynccontextmanager
async def run_test_server(app: web.Application, port: int):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_crawler_crawls_level1_to_level4_and_updates_jobs(tmp_path, unused_tcp_port_factory):
    async def handler(request: web.Request) -> web.Response:
        code = request.query["code"]
        max_level = request.query["maxLevel"]

        if code == "0" and max_level == "3":
            return web.json_response(
                {
                    "status": 200,
                    "data": {
                        "code": "00",
                        "name": "中国",
                        "level": 0,
                        "children": [
                            {
                                "code": "11",
                                "name": "北京市",
                                "level": 1,
                                "children": [
                                    {
                                        "code": "110101",
                                        "name": "东城区",
                                        "level": 3,
                                        "children": [],
                                    },
                                ],
                            },
                            {
                                "code": "62",
                                "name": "甘肃省",
                                "level": 1,
                                "children": [
                                    {
                                        "code": "6202",
                                        "name": "嘉峪关市",
                                        "level": 2,
                                        "children": [],
                                    },
                                ],
                            },
                        ],
                    },
                },
            )

        if code == "110101" and max_level == "4":
            return web.json_response(
                {
                    "status": 200,
                    "data": {
                        "code": "110101",
                        "name": "东城区",
                        "level": 3,
                        "children": [
                            {
                                "code": "110101001",
                                "name": "景山街道",
                                "level": 4,
                                "children": [],
                            },
                        ],
                    },
                },
            )

        if code == "6202" and max_level == "4":
            return web.json_response(
                {
                    "status": 200,
                    "data": {
                        "code": "6202",
                        "name": "嘉峪关市",
                        "level": 2,
                        "children": [
                            {
                                "code": "620201001",
                                "name": "雄关街道",
                                "level": 4,
                                "children": [],
                            },
                        ],
                    },
                },
            )

        raise AssertionError(f"未预期的请求: code={code} maxLevel={max_level}")

    app = web.Application()
    app.router.add_get("/xzqh/getList", handler)

    db_path = tmp_path / "crawler.db"
    async with run_test_server(app, unused_tcp_port_factory()) as base_url:
        crawler = XzqhCrawler(
            db_path=str(db_path),
            base_url=base_url,
            request_timeout=3,
            max_concurrency=4,
            retry_attempts=2,
            retry_base_delay=0.01,
        )
        success = await crawler.crawl()

    assert success is True
    assert crawler.stats.provinces == 2
    assert crawler.stats.cities == 1
    assert crawler.stats.counties == 1
    assert crawler.stats.townships == 2

    async with Database(str(db_path)) as db:
        stats = await db.get_statistics()
        job_counts = await db.get_task_counts()

    assert stats["total"] == 6
    assert stats["level_4"] == 2
    assert job_counts == {"pending": 0, "running": 0, "succeeded": 2, "failed": 0}


# ---------------------------------------------------------------------------
# 空结果 / 真失败 / 中断续跑
# ---------------------------------------------------------------------------


def _tree_payload(code: str, name: str, level: int, children: list) -> dict:
    return {
        "status": 200,
        "data": {"code": code, "name": name, "level": level, "children": children},
    }


def _make_two_county_app(request_log: list[tuple[str, str]]):
    """一个父节点返回空 data，另一个父节点始终 503。"""

    async def handler(request: web.Request) -> web.Response:
        code = request.query["code"]
        max_level = request.query["maxLevel"]
        request_log.append((code, max_level))

        if code == "0" and max_level == "3":
            return web.json_response(
                _tree_payload(
                    "00",
                    "中国",
                    0,
                    [
                        {
                            "code": "11",
                            "name": "北京市",
                            "level": 1,
                            "children": [
                                {"code": "110101", "name": "东城区", "level": 3, "children": []},
                                {"code": "110102", "name": "西城区", "level": 3, "children": []},
                            ],
                        },
                    ],
                ),
            )

        if code == "110101" and max_level == "4":
            # 上游 200 但 data 为空：该父节点下确实没有乡级数据。
            return web.json_response({"status": 200, "data": None})

        if code == "110102" and max_level == "4":
            return web.json_response({"status": 503, "message": "busy"}, status=503)

        raise AssertionError(f"未预期的请求: code={code} maxLevel={max_level}")

    app = web.Application()
    app.router.add_get("/xzqh/getList", handler)
    return app


@pytest.mark.asyncio
async def test_empty_data_marks_task_succeeded_and_errors_mark_failed(
    tmp_path, unused_tcp_port_factory
):
    request_log: list[tuple[str, str]] = []
    app = _make_two_county_app(request_log)

    db_path = tmp_path / "crawler.db"
    async with run_test_server(app, unused_tcp_port_factory()) as base_url:
        crawler = XzqhCrawler(
            db_path=str(db_path),
            base_url=base_url,
            request_timeout=3,
            max_concurrency=2,
            retry_attempts=2,
            retry_base_delay=0.01,
            retry_max_delay=0.01,
        )
        success = await crawler.crawl()

    assert success is False
    assert crawler.stats.tasks_succeeded == 1
    assert crawler.stats.tasks_failed == 1
    assert crawler.stats.townships == 0

    async with Database(str(db_path)) as db:
        counts = await db.get_task_counts()
        assert counts == {"pending": 0, "running": 0, "succeeded": 1, "failed": 1}
        # 空结果父节点本身是县级记录，不应有乡级记录挂在它下面。
        assert [row["code"] for row in _query_divisions_with_parent(db_path, "110101")] == []

    rows = _query_tasks(db_path)
    failed_rows = [row for row in rows if row["state"] == "failed"]
    assert len(failed_rows) == 1
    assert failed_rows[0]["parent_code"] == "110102"
    assert failed_rows[0]["last_error"] and "503" in str(failed_rows[0]["last_error"])
    assert _count_rejections(db_path) == 0

    # 空结果父节点的请求只发生一次，失败父节点按重试次数请求。
    empty_calls = [entry for entry in request_log if entry == ("110101", "4")]
    failed_calls = [entry for entry in request_log if entry == ("110102", "4")]
    assert len(empty_calls) == 1
    assert len(failed_calls) == 2


@pytest.mark.asyncio
async def test_resume_only_requests_failed_parent(tmp_path, unused_tcp_port_factory):
    request_log: list[tuple[str, str]] = []
    app = _make_two_county_app(request_log)

    db_path = tmp_path / "crawler.db"
    async with run_test_server(app, unused_tcp_port_factory()) as base_url:
        first = XzqhCrawler(
            db_path=str(db_path),
            base_url=base_url,
            request_timeout=3,
            max_concurrency=2,
            retry_attempts=1,
            retry_base_delay=0.01,
            retry_max_delay=0.01,
        )
        assert await first.crawl() is False

        request_log.clear()
        second = XzqhCrawler(
            db_path=str(db_path),
            base_url=base_url,
            request_timeout=3,
            max_concurrency=2,
            retry_attempts=1,
            retry_base_delay=0.01,
            retry_max_delay=0.01,
        )
        assert await second.retry_failed_jobs() is False

    township_requests = [code for code, level in request_log if level == "4"]
    assert township_requests == ["110102"]

    async with Database(str(db_path)) as db:
        counts = await db.get_task_counts()
        assert counts["succeeded"] == 1
        assert counts["failed"] == 1
        assert _query_divisions_with_parent(db_path, "110101") == []
    assert _count_rejections(db_path) == 0


@pytest.mark.asyncio
async def test_resume_retries_expired_running_task(tmp_path, unused_tcp_port_factory):
    request_log: list[tuple[str, str]] = []
    app = _make_two_county_app(request_log)

    db_path = tmp_path / "crawler.db"
    async with run_test_server(app, unused_tcp_port_factory()) as base_url:
        first = XzqhCrawler(
            db_path=str(db_path),
            base_url=base_url,
            request_timeout=3,
            max_concurrency=2,
            retry_attempts=1,
            retry_base_delay=0.01,
            retry_max_delay=0.01,
        )
        assert await first.crawl() is False

        # 模拟进程中途退出：留下两个 running 任务，一个租约已过期，
        # 另一个租约仍然有效。
        _set_task_running(db_path, "110101", lease_delta_seconds=-10)
        _set_task_running(db_path, "110102", lease_delta_seconds=3600)

        request_log.clear()
        second = XzqhCrawler(
            db_path=str(db_path),
            base_url=base_url,
            request_timeout=3,
            max_concurrency=2,
            retry_attempts=1,
            retry_base_delay=0.01,
            retry_max_delay=0.01,
        )
        # 本次运行自己认领的任务里没有失败（110101 空结果成功），但
        # 110102 的有效租约仍被其他执行者持有，本次运行未收尾，记为失败。
        assert await second.retry_failed_jobs() is False

    township_requests = [code for code, level in request_log if level == "4"]
    # 只有租约过期的任务被重新请求并落到终态；租约仍有效的任务保留 running。
    assert township_requests == ["110101"]

    async with Database(str(db_path)) as db:
        counts = await db.get_task_counts()
        assert counts["succeeded"] == 1
        assert counts["running"] == 1
