"""Worker 任务处理器测试。"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from conftest import (
    make_comment,
    make_comments_response,
    make_post,
    make_posts_response,
    make_thread,
    make_threads_response,
)
from tiebameow.client.tieba_client import UnretriableApiError
from tiebameow.models.orm import Post as PostModel
from tiebameow.models.orm import Thread as ThreadModel

from src.core.lock import MemoryLockManager
from src.scraper.router import QueueRouter
from src.scraper.tasks import (
    FullScanCommentsTask,
    FullScanPostsTask,
    IncrementalScanCommentsTask,
    IncrementalScanPostsTask,
    ScanThreadsTask,
    Task,
)
from src.scraper.worker import (
    DeepScanTaskHandler,
    FullScanCommentsTaskHandler,
    FullScanPostsTaskHandler,
    IncrementalScanCommentsTaskHandler,
    IncrementalScanPostsTaskHandler,
    ThreadsTaskHandler,
    Worker,
)


def _drain_router(router: QueueRouter) -> list[Task]:
    """从路由器的所有通道队列中取出所有任务"""
    items: list[Task] = []
    for q in router.lanes.values():
        while not q.empty():
            items.append(q.get_nowait())
    return items


def create_mock_container(tb_client=None, redis_client=None, config=None):
    lock_manager = MemoryLockManager()
    return cast(
        "Any",
        SimpleNamespace(
            tb_client=tb_client,
            redis_client=redis_client,
            config=config,
            tb_clients=None,
            threads_limiter=AsyncMock(),
            posts_limiter=AsyncMock(),
            comments_limiter=AsyncMock(),
            limiter=AsyncMock(),
            lock_manager=lock_manager,
        ),
    )


# ==================== ThreadsTaskHandler 测试 ====================


@pytest.mark.asyncio
async def test_threads_handler_new_threads_schedule_and_push():
    """测试 ThreadsTaskHandler 处理新主题帖"""
    thread = make_thread(tid=1, reply_num=5)
    threads_response = make_threads_response([thread])

    tb_client = SimpleNamespace(get_threads_dto=AsyncMock(return_value=threads_response))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(return_value={thread.tid}),
        save_items=AsyncMock(),
        save_threads_and_pending_scans=AsyncMock(),
        get_threads_by_tids=AsyncMock(return_value=[]),
        push_object_event=AsyncMock(),
    )
    router = QueueRouter()
    handler = ThreadsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=router,
    )

    await handler.handle(ScanThreadsTask(fid=thread.fid, fname=thread.fname, pn=1))

    # 新主题帖应该推送到对象流
    datastore.push_object_event.assert_awaited_once_with("thread", thread)

    # 应该原子保存 thread 元数据与 pending_scan
    datastore.save_threads_and_pending_scans.assert_awaited_once()
    atomic_call = datastore.save_threads_and_pending_scans.await_args
    thread_dtos = atomic_call.args[0]
    assert thread_dtos is not None
    assert thread_dtos[0].tid == thread.tid
    assert atomic_call.kwargs.get("backfill") is False
    assert atomic_call.kwargs.get("upsert_threads") is False

    # 应该生成 FullScanPostsTask
    queued_tasks = _drain_router(router)
    assert any(isinstance(item.content, FullScanPostsTask) for item in queued_tasks)


@pytest.mark.asyncio
async def test_threads_handler_new_thread_zero_replies():
    """测试 ThreadsTaskHandler 处理没有回复的新主题帖"""
    thread = make_thread(tid=1, reply_num=0)
    threads_response = make_threads_response([thread])

    tb_client = SimpleNamespace(get_threads_dto=AsyncMock(return_value=threads_response))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(return_value={thread.tid}),
        save_items=AsyncMock(),
        save_threads_and_pending_scans=AsyncMock(),
        get_threads_by_tids=AsyncMock(return_value=[]),
        push_object_event=AsyncMock(),
    )
    router = QueueRouter()
    handler = ThreadsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=router,
    )

    await handler.handle(ScanThreadsTask(fid=thread.fid, fname=thread.fname, pn=1))

    # 没有回复的主题帖也应通过原子方法保存元数据
    datastore.save_threads_and_pending_scans.assert_awaited_once()
    atomic_call = datastore.save_threads_and_pending_scans.await_args
    thread_dtos = atomic_call.args[0]
    assert thread_dtos is not None
    assert thread_dtos[0].tid == thread.tid
    assert atomic_call.kwargs.get("backfill") is False
    assert atomic_call.kwargs.get("upsert_threads") is False

    # 不应该生成 FullScanPostsTask
    queued_tasks = _drain_router(router)
    assert not any(isinstance(item.content, FullScanPostsTask) for item in queued_tasks)


@pytest.mark.asyncio
async def test_threads_handler_old_thread_with_updates():
    """测试 ThreadsTaskHandler 处理有更新的旧主题帖"""
    # 旧主题帖：数据库中的 last_time 是 1，新抓取的是 100
    old_last_time = datetime.fromtimestamp(1)
    new_last_time = datetime.fromtimestamp(100)

    thread = make_thread(tid=1, reply_num=10, last_time=new_last_time)
    threads_response = make_threads_response([thread])

    # 模拟数据库中的旧数据（ThreadModel 没有 fname 字段）
    stored_thread = ThreadModel(tid=1, fid=10, reply_num=5, last_time=old_last_time)

    tb_client = SimpleNamespace(get_threads_dto=AsyncMock(return_value=threads_response))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(return_value=set()),  # 不是新帖
        save_items=AsyncMock(),
        save_threads_and_pending_scans=AsyncMock(),
        get_threads_by_tids=AsyncMock(return_value=[stored_thread]),
        get_pending_thread_scan_tids=AsyncMock(return_value=set()),
        push_object_event=AsyncMock(),
    )
    router = QueueRouter()
    handler = ThreadsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=router,
    )

    await handler.handle(ScanThreadsTask(fid=thread.fid, fname=thread.fname, pn=1))

    # 应该生成 IncrementalScanPostsTask
    queued_tasks = _drain_router(router)
    incremental_tasks = [t for t in queued_tasks if isinstance(t.content, IncrementalScanPostsTask)]
    assert len(incremental_tasks) == 1
    assert isinstance(incremental_tasks[0].content, IncrementalScanPostsTask)
    assert incremental_tasks[0].content.tid == thread.tid


@pytest.mark.asyncio
async def test_threads_handler_old_thread_with_updates_skips_incremental_when_full_scan_pending():
    """测试旧主题帖在存在 pending full scan 标记时不会调度增量任务。"""
    old_last_time = datetime.fromtimestamp(1)
    new_last_time = datetime.fromtimestamp(100)

    thread = make_thread(tid=1, reply_num=10, last_time=new_last_time)
    threads_response = make_threads_response([thread])
    stored_thread = ThreadModel(tid=1, fid=10, reply_num=5, last_time=old_last_time)

    tb_client = SimpleNamespace(get_threads_dto=AsyncMock(return_value=threads_response))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(return_value=set()),
        save_items=AsyncMock(),
        save_threads_and_pending_scans=AsyncMock(),
        get_threads_by_tids=AsyncMock(return_value=[stored_thread]),
        get_pending_thread_scan_tids=AsyncMock(return_value={thread.tid}),
        push_object_event=AsyncMock(),
    )
    router = QueueRouter()
    handler = ThreadsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=router,
    )

    await handler.handle(ScanThreadsTask(fid=thread.fid, fname=thread.fname, pn=1))

    queued_tasks = _drain_router(router)
    incremental_tasks = [t for t in queued_tasks if isinstance(t.content, IncrementalScanPostsTask)]
    assert len(incremental_tasks) == 0


@pytest.mark.asyncio
async def test_threads_handler_backfill_enqueues_next_page():
    """测试 ThreadsTaskHandler 在回溯模式下生成下一页任务"""
    thread = make_thread(tid=1, reply_num=5)
    threads_response = make_threads_response([thread], has_more=True, current_page=1, total_page=3)

    tb_client = SimpleNamespace(get_threads_dto=AsyncMock(return_value=threads_response))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(return_value={thread.tid}),
        save_items=AsyncMock(),
        save_threads_and_pending_scans=AsyncMock(),
        get_threads_by_tids=AsyncMock(return_value=[]),
        push_object_event=AsyncMock(),
    )
    router = QueueRouter()
    handler = ThreadsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=router,
    )

    await handler.handle(ScanThreadsTask(fid=thread.fid, fname=thread.fname, pn=1, backfill=True, max_pages=5))

    # 回溯模式不应该推送对象事件
    datastore.push_object_event.assert_not_called()

    # 应该生成下一页的 ScanThreadsTask
    queued_tasks = _drain_router(router)
    next_page_tasks = [t for t in queued_tasks if isinstance(t.content, ScanThreadsTask) and t.content.pn == 2]
    assert len(next_page_tasks) == 1
    assert isinstance(next_page_tasks[0].content, ScanThreadsTask)
    assert next_page_tasks[0].content.backfill is True


@pytest.mark.asyncio
async def test_threads_handler_filters_livepost():
    """测试 ThreadsTaskHandler 过滤直播帖"""
    normal_thread = make_thread(tid=1, reply_num=5, is_livepost=False)
    live_thread = make_thread(tid=2, reply_num=5, is_livepost=True)
    threads_response = make_threads_response([normal_thread, live_thread])

    tb_client = SimpleNamespace(get_threads_dto=AsyncMock(return_value=threads_response))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(return_value={1}),  # 只有 tid=1 是新的
        save_items=AsyncMock(),
        save_threads_and_pending_scans=AsyncMock(),
        get_threads_by_tids=AsyncMock(return_value=[]),
        push_object_event=AsyncMock(),
    )
    router = QueueRouter()
    handler = ThreadsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=router,
    )

    await handler.handle(ScanThreadsTask(fid=10, fname="bar", pn=1))

    # filter_new_ids 应该只接收过滤后的 tid
    call_args = datastore.filter_new_ids.call_args
    assert 2 not in call_args[0][1]  # 直播帖 tid=2 应该被过滤


@pytest.mark.asyncio
async def test_threads_handler_force_mode_enables_thread_upsert():
    """测试 ThreadsTaskHandler 在 force 模式下会启用 thread 元数据 upsert。"""
    thread = make_thread(tid=1, reply_num=5)
    threads_response = make_threads_response([thread])

    tb_client = SimpleNamespace(get_threads_dto=AsyncMock(return_value=threads_response))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(return_value=set()),
        save_items=AsyncMock(),
        save_threads_and_pending_scans=AsyncMock(),
        get_threads_by_tids=AsyncMock(return_value=[]),
        push_object_event=AsyncMock(),
    )
    router = QueueRouter()
    handler = ThreadsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=router,
    )

    await handler.handle(ScanThreadsTask(fid=thread.fid, fname=thread.fname, pn=1, force=True))

    datastore.save_threads_and_pending_scans.assert_awaited_once()
    atomic_call = datastore.save_threads_and_pending_scans.await_args
    assert atomic_call.kwargs.get("upsert_threads") is True

    # force 模式不应走 thread 的 filter_new_ids 分流（但 ensure_users 仍会查询 user）
    assert not any(call.args and call.args[0] == "thread" for call in datastore.filter_new_ids.await_args_list)


# ==================== FullScanPostsTaskHandler 测试 ====================


@pytest.mark.asyncio
async def test_full_scan_posts_handler_process_page():
    """测试 FullScanPostsTaskHandler 处理回复页面"""
    post = make_post(pid=100, tid=1, floor=2, reply_num=0)
    posts_response = make_posts_response([post])

    tb_client = SimpleNamespace(get_posts_dto=AsyncMock(return_value=posts_response))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(return_value={post.pid}),
        save_items=AsyncMock(),
        push_object_event=AsyncMock(),
        remove_pending_scan=AsyncMock(),
        add_pending_comment_scan=AsyncMock(),
    )
    router = QueueRouter()
    handler = FullScanPostsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=router,
    )

    await handler.handle(FullScanPostsTask(tid=post.tid))

    # 检查是否保存了回复
    post_save_calls = [
        call.args[0]
        for call in datastore.save_items.await_args_list
        if call.args and isinstance(call.args[0], list) and call.args[0] and isinstance(call.args[0][0], PostModel)
    ]
    assert post_save_calls, "Expected post models to be persisted"
    assert post_save_calls[0][0].pid == post.pid

    # 检查是否推送了事件
    datastore.push_object_event.assert_awaited_once_with("post", post)


@pytest.mark.asyncio
async def test_full_scan_posts_handler_generates_comment_task():
    """测试 FullScanPostsTaskHandler 在楼中楼超过10条时生成扫描任务"""
    # 回复有超过10条楼中楼
    post = make_post(pid=100, tid=1, floor=2, reply_num=15)
    posts_response = make_posts_response([post])

    tb_client = SimpleNamespace(get_posts_dto=AsyncMock(return_value=posts_response))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(return_value={post.pid}),
        save_items=AsyncMock(),
        push_object_event=AsyncMock(),
        remove_pending_scan=AsyncMock(),
        add_pending_comment_scan=AsyncMock(),
    )
    router = QueueRouter()
    handler = FullScanPostsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=router,
    )

    await handler.handle(FullScanPostsTask(tid=post.tid))

    # 应该生成 FullScanCommentsTask
    queued_tasks = _drain_router(router)
    comment_tasks = [t for t in queued_tasks if isinstance(t.content, FullScanCommentsTask)]
    assert len(comment_tasks) == 1
    assert isinstance(comment_tasks[0].content, FullScanCommentsTask)
    assert comment_tasks[0].content.pid == post.pid


@pytest.mark.asyncio
async def test_full_scan_posts_handler_removes_pending_marker():
    """测试 FullScanPostsTaskHandler 在完成后移除 pending_scan 标记"""
    post = make_post(pid=100, tid=1, floor=2)
    posts_response = make_posts_response([post])

    tb_client = SimpleNamespace(get_posts_dto=AsyncMock(return_value=posts_response))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(return_value=set()),
        save_items=AsyncMock(),
        push_object_event=AsyncMock(),
        remove_pending_thread_scan=AsyncMock(),
        add_pending_comment_scan=AsyncMock(),
    )
    router = QueueRouter()
    handler = FullScanPostsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=router,
    )

    await handler.handle(FullScanPostsTask(tid=1))

    # _finalize_thread 应该移除 pending_scan 标记
    datastore.remove_pending_thread_scan.assert_awaited_with(1)


# ==================== IncrementalScanPostsTaskHandler 测试 ====================


@pytest.mark.asyncio
async def test_incremental_scan_posts_handler():
    """测试 IncrementalScanPostsTaskHandler 增量扫描"""
    stored_last_time = datetime.fromtimestamp(1)
    new_post_time = datetime.fromtimestamp(100)

    # 新回复（时间戳大于 stored_last_time）
    new_post = make_post(pid=200, tid=1, floor=3, create_time=new_post_time)
    posts_response = make_posts_response([new_post], current_page=1, total_page=1)

    async def mock_get_posts_dto(*args, **kwargs):
        return posts_response

    tb_client = SimpleNamespace(get_posts_dto=AsyncMock(side_effect=mock_get_posts_dto))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(return_value={new_post.pid}),
        save_items=AsyncMock(),
        get_posts_by_pids=AsyncMock(return_value=[]),
        push_object_event=AsyncMock(),
        update_thread_metadata=AsyncMock(),
        add_pending_comment_scan=AsyncMock(),
    )

    class MockConfig:
        deep_scan_enabled = False
        deep_scan_depth = 3

    router = QueueRouter()
    handler = IncrementalScanPostsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client, config=MockConfig()),
        datastore=cast("Any", datastore),
        router=router,
    )

    target_last_time = datetime.fromtimestamp(200)
    await handler.handle(
        IncrementalScanPostsTask(
            tid=1,
            stored_last_time=stored_last_time,
            stored_reply_num=5,
            target_last_time=target_last_time,
            target_reply_num=10,
        )
    )

    # 应该更新主题帖元数据
    datastore.update_thread_metadata.assert_awaited()


# ==================== FullScanCommentsTaskHandler 测试 ====================


@pytest.mark.asyncio
async def test_full_scan_comments_handler():
    """测试 FullScanCommentsTaskHandler 全量扫描楼中楼"""
    comment = make_comment(cid=1000, pid=100, tid=1)
    comments_response = make_comments_response([comment])

    tb_client = SimpleNamespace(get_comments_dto=AsyncMock(return_value=comments_response))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(return_value={comment.cid}),
        save_items=AsyncMock(),
        push_object_event=AsyncMock(),
        remove_pending_comment_scan=AsyncMock(),
    )
    router = QueueRouter()
    handler = FullScanCommentsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=router,
    )

    await handler.handle(FullScanCommentsTask(tid=1, pid=100))

    # 应该推送楼中楼事件
    datastore.push_object_event.assert_awaited_with("comment", comment)
    datastore.remove_pending_comment_scan.assert_awaited_once_with(1, 100)


@pytest.mark.asyncio
async def test_full_scan_comments_handler_backfill_no_push():
    """测试 FullScanCommentsTaskHandler 回溯模式不推送事件"""
    comment = make_comment(cid=1000, pid=100, tid=1)
    comments_response = make_comments_response([comment])

    tb_client = SimpleNamespace(get_comments_dto=AsyncMock(return_value=comments_response))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(return_value={comment.cid}),
        save_items=AsyncMock(),
        push_object_event=AsyncMock(),
        remove_pending_comment_scan=AsyncMock(),
    )
    router = QueueRouter()
    handler = FullScanCommentsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=router,
    )

    await handler.handle(FullScanCommentsTask(tid=1, pid=100, backfill=True))

    # 回溯模式不应该推送事件
    datastore.push_object_event.assert_not_called()
    datastore.remove_pending_comment_scan.assert_awaited_once_with(1, 100)


# ==================== IncrementalScanCommentsTaskHandler 测试 ====================


@pytest.mark.asyncio
async def test_incremental_scan_comments_handler():
    """测试 IncrementalScanCommentsTaskHandler 增量扫描楼中楼"""
    new_comment = make_comment(cid=2000, pid=100, tid=1)
    old_comment = make_comment(cid=1000, pid=100, tid=1)
    comments_response = make_comments_response([new_comment, old_comment], current_page=1, total_page=1)

    tb_client = SimpleNamespace(get_comments_dto=AsyncMock(return_value=comments_response))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(return_value={new_comment.cid}),  # 只有 cid=2000 是新的
        save_items=AsyncMock(),
        push_object_event=AsyncMock(),
        remove_pending_comment_scan=AsyncMock(),
    )
    router = QueueRouter()
    handler = IncrementalScanCommentsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=router,
    )

    await handler.handle(IncrementalScanCommentsTask(tid=1, pid=100))

    # 应该只推送新楼中楼
    datastore.push_object_event.assert_awaited_with("comment", new_comment)
    # 增量扫描只应清理 incremental 标记，避免误删 full 标记
    datastore.remove_pending_comment_scan.assert_awaited_once_with(1, 100, task_kind="incremental")


def _make_incremental_comments_handler(get_comments_dto, filter_new_ids) -> IncrementalScanCommentsTaskHandler:
    """构造带 mock 客户端与 datastore 的增量楼中楼处理器。"""
    tb_client = SimpleNamespace(get_comments_dto=AsyncMock(side_effect=get_comments_dto))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(side_effect=filter_new_ids),
        save_items=AsyncMock(),
        push_object_event=AsyncMock(),
        remove_pending_comment_scan=AsyncMock(),
    )
    return IncrementalScanCommentsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=QueueRouter(),
    )


def _requested_comment_pages(tb_client) -> list[int]:
    """提取 get_comments_dto 调用中的页码参数。"""
    return [call.args[2] for call in tb_client.get_comments_dto.await_args_list]


@pytest.mark.asyncio
async def test_incremental_scan_comments_mixed_page_continues_to_earlier_page():
    """一页同时含新旧楼中楼时，应继续向更早页面翻页而不是提前停止。"""
    page3_new = make_comment(cid=3001, pid=100, tid=1)
    page3_old = make_comment(cid=1001, pid=100, tid=1)
    page2_new = make_comment(cid=2002, pid=100, tid=1)
    page2_old = make_comment(cid=1002, pid=100, tid=1)
    page1_old = make_comment(cid=1003, pid=100, tid=1)

    page3 = make_comments_response([page3_new, page3_old], current_page=3, total_page=3)
    page2 = make_comments_response([page2_new, page2_old], current_page=2, total_page=3)
    page1 = make_comments_response([page1_old], current_page=1, total_page=3)

    def get_comments_dto(tid, pid, pn):
        return {3: page3, 2: page2, 1: page1}[pn]

    def filter_new_ids(kind, ids):
        if kind != "comment":
            return set(ids)
        return {cid for cid in ids if cid in {3001, 2002}}

    handler = _make_incremental_comments_handler(get_comments_dto, filter_new_ids)
    tb_client = handler.container.tb_client

    await handler.handle(IncrementalScanCommentsTask(tid=1, pid=100))

    # 初始 pn=1 取总页数；倒序依次请求 pn=3、pn=2；pn=1 复用初始页数据不再发请求。
    # 第 3、2 页都混有新楼中楼，必须继续推进，直到第 1 页全为旧记录才停止。
    assert _requested_comment_pages(tb_client) == [1, 3, 2]
    # 第 1 页（全旧）同样经过去重检查，证明扫描确实推进到了更早页面
    comment_filter_calls = [
        call.args[1] for call in handler.datastore.filter_new_ids.await_args_list if call.args[0] == "comment"
    ]
    assert comment_filter_calls == [[3001, 1001], [2002, 1002], [1003]]
    # 混合页中的新楼中楼都应推送，不能因为同页存在旧记录而漏抓
    pushed_cids = {
        call.args[1].cid for call in handler.datastore.push_object_event.await_args_list if call.args[0] == "comment"
    }
    assert pushed_cids == {3001, 2002}
    handler.datastore.remove_pending_comment_scan.assert_awaited_once_with(1, 100, task_kind="incremental")


@pytest.mark.asyncio
async def test_incremental_scan_comments_stops_after_consecutive_all_old_pages():
    """连续页面全部为已存在楼中楼时应停止，不再请求更早的页面。"""
    page3_old_a = make_comment(cid=1001, pid=100, tid=1)
    page3_old_b = make_comment(cid=1002, pid=100, tid=1)
    page2_old_a = make_comment(cid=1003, pid=100, tid=1)
    page2_old_b = make_comment(cid=1004, pid=100, tid=1)
    page1_untouched = make_comments_response([make_comment(cid=9999, pid=100, tid=1)], current_page=1, total_page=3)

    page3 = make_comments_response([page3_old_a, page3_old_b], current_page=3, total_page=3)
    page2 = make_comments_response([page2_old_a, page2_old_b], current_page=2, total_page=3)

    def get_comments_dto(tid, pid, pn):
        return {3: page3, 2: page2, 1: page1_untouched}[pn]

    handler = _make_incremental_comments_handler(get_comments_dto, lambda kind, ids: set())
    tb_client = handler.container.tb_client

    await handler.handle(IncrementalScanCommentsTask(tid=1, pid=100))

    # 初始 pn=1 取总页数，倒序从 pn=3 开始；pn=3 全旧即停止，pn=2、pn=1 均不应再被处理
    assert _requested_comment_pages(tb_client) == [1, 3]
    handler.datastore.push_object_event.assert_not_called()
    handler.datastore.remove_pending_comment_scan.assert_awaited_once_with(1, 100, task_kind="incremental")


@pytest.mark.asyncio
async def test_incremental_completion_keeps_pending_full_marker():
    """增量任务完成后只清理 incremental 标记，同一 (tid, pid) 的 full 标记必须保留。"""
    new_comment = make_comment(cid=2000, pid=100, tid=1)
    old_comment = make_comment(cid=1000, pid=100, tid=1)
    comments_response = make_comments_response([new_comment, old_comment], current_page=1, total_page=1)

    tb_client = SimpleNamespace(get_comments_dto=AsyncMock(return_value=comments_response))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(return_value={new_comment.cid}),
        save_items=AsyncMock(),
        push_object_event=AsyncMock(),
        remove_pending_comment_scan=AsyncMock(),
    )
    handler = IncrementalScanCommentsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=QueueRouter(),
    )

    await handler.handle(IncrementalScanCommentsTask(tid=1, pid=100))

    # 必须带 task_kind="incremental" 过滤，不能无条件删除整行（full 标记仍在）
    datastore.remove_pending_comment_scan.assert_awaited_once_with(1, 100, task_kind="incremental")
    remove_kwargs = datastore.remove_pending_comment_scan.await_args.kwargs
    assert remove_kwargs.get("task_kind") == "incremental"


@pytest.mark.asyncio
async def test_full_scan_comments_completion_clears_marker_unconditionally():
    """全量任务完成后按原逻辑无条件清除该 (tid, pid) 的 pending 标记。"""
    comment = make_comment(cid=1000, pid=100, tid=1)
    comments_response = make_comments_response([comment])

    tb_client = SimpleNamespace(get_comments_dto=AsyncMock(return_value=comments_response))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(return_value={comment.cid}),
        save_items=AsyncMock(),
        push_object_event=AsyncMock(),
        remove_pending_comment_scan=AsyncMock(),
    )
    handler = FullScanCommentsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=QueueRouter(),
    )

    await handler.handle(FullScanCommentsTask(tid=1, pid=100))

    # 全量清理不带 task_kind 过滤，整行 pending 标记被移除
    datastore.remove_pending_comment_scan.assert_awaited_once_with(1, 100)
    assert not datastore.remove_pending_comment_scan.await_args.kwargs


@pytest.mark.asyncio
async def test_incremental_scan_comments_deleted_clears_only_incremental_marker():
    """回复或主题贴被删除（err_code=4）时，增量任务仍走 incremental 清理路径。"""
    tb_client = SimpleNamespace(
        get_comments_dto=AsyncMock(side_effect=UnretriableApiError(4, "thread or post deleted"))
    )
    datastore = SimpleNamespace(remove_pending_comment_scan=AsyncMock())
    handler = IncrementalScanCommentsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=QueueRouter(),
    )

    # err_code=4 在处理器内部被捕获，不应向上抛出，且不能留下永久 pending
    await handler.handle(IncrementalScanCommentsTask(tid=1, pid=100))

    datastore.remove_pending_comment_scan.assert_awaited_once_with(1, 100, task_kind="incremental")


@pytest.mark.asyncio
async def test_incremental_scan_comments_unexpected_error_still_clears_marker():
    """增量任务抛出非删除类异常时，也要清理 incremental 标记，避免永久 pending。"""
    tb_client = SimpleNamespace(get_comments_dto=AsyncMock(side_effect=RuntimeError("boom")))
    datastore = SimpleNamespace(remove_pending_comment_scan=AsyncMock())
    handler = IncrementalScanCommentsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=QueueRouter(),
    )

    # 异常在处理器内部被捕获并记录，不应向上抛出
    await handler.handle(IncrementalScanCommentsTask(tid=1, pid=100))

    datastore.remove_pending_comment_scan.assert_awaited_once_with(1, 100, task_kind="incremental")


@pytest.mark.asyncio
async def test_full_scan_comments_unexpected_error_still_clears_marker():
    """全量任务抛出异常时也要按原逻辑清理标记，避免永久 pending。"""
    tb_client = SimpleNamespace(get_comments_dto=AsyncMock(side_effect=RuntimeError("boom")))
    datastore = SimpleNamespace(remove_pending_comment_scan=AsyncMock())
    handler = FullScanCommentsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=QueueRouter(),
    )

    await handler.handle(FullScanCommentsTask(tid=1, pid=100))

    datastore.remove_pending_comment_scan.assert_awaited_once_with(1, 100)


@pytest.mark.asyncio
async def test_deep_scan_schedules_incremental_comment_task_with_pending_marker():
    """测试 DeepScan 在生成 IncrementalScanCommentsTask 前会写入 pending_comment_scan。"""
    post = make_post(pid=100, tid=1, floor=2, reply_num=12, comments=[])
    stored_post = SimpleNamespace(pid=100, reply_num=1)

    datastore = SimpleNamespace(
        get_posts_by_pids=AsyncMock(return_value=[stored_post]),
        add_pending_comment_scan=AsyncMock(),
        save_items=AsyncMock(),
    )
    router = QueueRouter()
    handler = DeepScanTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=SimpleNamespace()),
        datastore=cast("Any", datastore),
        router=router,
    )

    updates_found = await handler._check_posts_for_comment_updates([post])

    assert updates_found == 0
    datastore.add_pending_comment_scan.assert_awaited_once_with(
        tid=post.tid,
        pid=post.pid,
        fid=post.fid,
        fname=post.fname,
        backfill=False,
        task_kind="incremental",
    )

    queued_tasks = _drain_router(router)
    incremental_tasks = [t for t in queued_tasks if isinstance(t.content, IncrementalScanCommentsTask)]
    assert len(incremental_tasks) == 1
    assert isinstance(incremental_tasks[0].content, IncrementalScanCommentsTask)
    assert incremental_tasks[0].content.tid == post.tid
    assert incremental_tasks[0].content.pid == post.pid


# ==================== Worker 测试 ====================


@pytest.mark.asyncio
async def test_worker_processes_task():
    """测试 Worker 处理任务"""
    thread = make_thread(tid=1, reply_num=0)
    threads_response = make_threads_response([thread])

    tb_client = SimpleNamespace(get_threads_dto=AsyncMock(return_value=threads_response))
    datastore = SimpleNamespace(
        filter_new_ids=AsyncMock(return_value={thread.tid}),
        save_items=AsyncMock(),
        save_threads_and_pending_scans=AsyncMock(),
        get_threads_by_tids=AsyncMock(return_value=[]),
        push_object_event=AsyncMock(),
    )
    router = QueueRouter()
    queue = router.threads_queue

    worker = Worker(
        worker_id=0,
        queue=queue,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=router,
        lane="threads",
    )

    # 添加任务到队列
    from src.scraper.tasks import Priority

    task = Task(priority=Priority.HIGH, content=ScanThreadsTask(fid=thread.fid, fname=thread.fname, pn=1))
    await queue.put(task)

    # 启动 worker 并在处理完一个任务后取消
    async def run_worker_briefly():
        worker_task = asyncio.create_task(worker.run())
        # 等待任务被处理
        await asyncio.sleep(0.1)
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    await run_worker_briefly()

    # 验证任务被处理
    tb_client.get_threads_dto.assert_awaited()


@pytest.mark.asyncio
async def test_worker_memory_lock():
    """测试 LockManager 锁功能"""
    tb_client = SimpleNamespace()
    datastore = SimpleNamespace()
    router = QueueRouter()

    # 创建一个 handler 来测试锁
    handler = ThreadsTaskHandler(
        worker_id=0,
        container=create_mock_container(tb_client=tb_client),
        datastore=cast("Any", datastore),
        router=router,
    )

    # 第一次获取锁应该成功
    lock_key = "test:lock:1"
    assert await handler.try_acquire_lock(lock_key, ttl=10)

    # 第二次获取同一个锁应该失败
    assert not await handler.try_acquire_lock(lock_key, ttl=10)

    # 释放锁后应该可以重新获取
    await handler.release_lock(lock_key)
    assert await handler.try_acquire_lock(lock_key, ttl=10)

    # 清理
    await handler.release_lock(lock_key)
