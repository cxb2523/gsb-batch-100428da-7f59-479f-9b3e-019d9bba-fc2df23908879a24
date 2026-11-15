"""
抖音直播数据监控 - 轻量版

启动: python app.py
或者: uvicorn app:app --host 0.0.0.0 --port 8000

贴一个直播间链接 -> 自动开始采集数据 -> 网页上看结果
"""
import asyncio
import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger
from pydantic import BaseModel, Field

from collector import DouyinLiveCollector
from database import Database

load_dotenv()

# ======= 配置 =======
BROWSER_MODE = os.getenv("BROWSER_MODE", "cdp")
BROWSER_CDP_PORT = int(os.getenv("BROWSER_CDP_PORT", "9222"))
DOUYIN_COOKIE = os.getenv("DOUYIN_COOKIE", "")
PROXY_URL = os.getenv("PROXY_URL", "")
CHECK_INTERVAL_NORMAL = int(os.getenv("CHECK_INTERVAL_NORMAL", "10"))
CHECK_INTERVAL_LIVE = int(os.getenv("CHECK_INTERVAL_LIVE", "2"))
DATA_RETENTION_DAYS = int(os.getenv("DATA_RETENTION_DAYS", "7"))
PORT = int(os.getenv("PORT", "8080"))

# ======= 全局状态 =======
db = Database()
collector: Optional[DouyinLiveCollector] = None
_monitor_task: Optional[asyncio.Task] = None


def normalize_douyin_url(url: str) -> dict:
    """从各种抖音 URL 格式中提取信息"""
    url = url.strip().split("?")[0]  # 去掉参数

    # live.douyin.com/数字 格式
    m = re.search(r"live\.douyin\.com/(\d+)", url)
    if m:
        return {"type": "live", "room_id": m.group(1), "url": f"https://live.douyin.com/{m.group(1)}"}

    # douyin.com/user/sec_uid 格式
    m = re.search(r"douyin\.com/user/([^/\s?]+)", url)
    if m:
        return {"type": "user", "sec_uid": m.group(1), "url": f"https://www.douyin.com/user/{m.group(1)}"}

    # 短链接
    if "v.douyin.com" in url:
        return {"type": "short", "url": url}

    raise ValueError(f"无法识别的抖音链接: {url}")


# ======= 采集循环 =======
async def monitor_loop():
    """定时采集所有活跃房间的数据"""
    logger.info("[Monitor] 采集循环启动")
    while True:
        try:
            rooms = db.get_rooms(active_only=True)
            if not rooms:
                await asyncio.sleep(30)
                continue

            for room in rooms:
                if not collector or not collector.is_ready:
                    await asyncio.sleep(5)
                    continue

                logger.info(f"[Monitor] 采集: {room['nickname'] or room['url']}")
                data = await collector.fetch_live_data(room["url"])

                # 携带房间信息
                data["nickname"] = room.get("nickname", "")
                data["room_id"] = data.get("room_id") or str(room.get("id", ""))

                # 存库
                db.save_snapshot(data)

                live_status = "直播中" if data.get("is_live") else "未直播"
                viewers = data.get("viewers", "?")
                logger.info(f"  -> {live_status}, 在线={viewers}, 错误={data.get('error', '无')}")

                # 间隔
                interval = room.get("check_interval", CHECK_INTERVAL_NORMAL)
                if data.get("is_live"):
                    interval = min(interval, CHECK_INTERVAL_LIVE)
                await asyncio.sleep(max(interval * 60 * 0.5, 5))  # 至少等5秒

            # 每轮采集完成后清理过期数据
            if DATA_RETENTION_DAYS > 0:
                db.cleanup_old_data(DATA_RETENTION_DAYS)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[Monitor] 循环错误: {e}")
            await asyncio.sleep(30)


# ======= FastAPI =======
@asynccontextmanager
async def lifespan(app: FastAPI):
    global collector, _monitor_task

    # 启动
    collector = DouyinLiveCollector(
        browser_mode=BROWSER_MODE,
        cdp_port=BROWSER_CDP_PORT,
        cookie_str=DOUYIN_COOKIE,
        proxy_url=PROXY_URL,
    )
    await collector.start()

    _monitor_task = asyncio.create_task(monitor_loop())
    logger.info(f"[App] 启动完成, 浏览器模式={BROWSER_MODE}")

    yield

    # 关闭
    if _monitor_task:
        _monitor_task.cancel()
    if collector:
        await collector.close()


app = FastAPI(title="抖音直播数据监控", version="1.0.0", lifespan=lifespan)

# 模板
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


class RoomCreateRequest(BaseModel):
    url: str = Field(..., min_length=3, max_length=1000, description="抖音直播间或主页 URL")
    nickname: Optional[str] = Field(None, max_length=200, description="备注名")
    check_interval: int = Field(10, ge=1, le=240, description="检查间隔(分钟)")


# ======= API =======

@app.get("/")
async def index():
    """返回 Web 页面"""
    from fastapi.responses import HTMLResponse
    import aiofiles
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    async with aiofiles.open(template_path, "r", encoding="utf-8") as f:
        html = await f.read()
    return HTMLResponse(html)


@app.get("/api/health")
async def health():
    return {
        "status": "healthy",
        "mode": BROWSER_MODE,
        "cdp_connected": collector.is_ready if collector else False,
        "rooms": db.get_stats(),
    }


@app.post("/api/rooms")
async def add_room(req: RoomCreateRequest):
    """添加直播监控 - 贴链接就完事"""
    try:
        info = normalize_douyin_url(req.url)
    except ValueError as e:
        raise HTTPException(422, str(e))

    room_id = db.add_room(
        url=info["url"],
        nickname=req.nickname or "",
        check_interval=req.check_interval,
    )
    if room_id < 0:
        raise HTTPException(400, "该链接已在监控列表中")

    # 立即采集一次
    if collector and collector.is_ready:
        data = await collector.fetch_live_data(info["url"])
        data["nickname"] = req.nickname or ""
        data["room_id"] = info.get("room_id", "")
        db.save_snapshot(data)

    return {"success": True, "room_id": room_id, "url": info["url"]}


@app.get("/api/rooms")
async def list_rooms():
    """列出所有监控房间"""
    rooms = db.get_rooms(active_only=True)
    return {"count": len(rooms), "rooms": rooms}


@app.delete("/api/rooms/{room_id}")
async def delete_room(room_id: int):
    """删除监控房间"""
    db.remove_room(room_id)
    return {"success": True}


@app.get("/api/snapshots/{room_id}")
async def get_snapshots(room_id: str, limit: int = Query(50, le=200)):
    """获取某个房间的采集数据"""
    snapshots = db.get_recent_snapshots(room_id, limit=limit)
    return {"count": len(snapshots), "snapshots": snapshots}


@app.get("/api/snapshots")
async def all_snapshots(limit: int = Query(100, le=500)):
    """获取所有采集数据"""
    snapshots = db.get_all_live_snapshots(limit=limit)
    return {"count": len(snapshots), "snapshots": snapshots}


@app.get("/api/stats")
async def stats():
    return db.get_stats()


@app.get("/api/export/csv")
async def export_csv():
    """导出全部采集数据为 CSV

    已有列的顺序与含义保持不变; 来源、降级标记、降级原因追加在末尾,
    旧版消费方按列序/表头读取时不受影响。
    """
    from fastapi.responses import PlainTextResponse
    import csv, io

    snapshots = db.get_all_live_snapshots(limit=10000)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "时间", "房间ID", "是否直播", "标题", "在线人数", "点赞", "评论", "分享",
        "商品数", "错误", "来源", "是否降级", "降级原因",
    ])
    for s in snapshots:
        prods = s.get("products") or []
        prod_count = len(prods) if isinstance(prods, list) else 0
        writer.writerow([
            s.get("checked_at", ""),
            s.get("room_id", ""),
            "是" if s.get("is_live") else "否",
            s.get("title", ""),
            s.get("viewers", ""),
            s.get("likes", ""),
            s.get("comments", ""),
            s.get("shares", ""),
            prod_count,
            s.get("error", ""),
            s.get("source", "") or "",
            "是" if s.get("degraded") else "否",
            s.get("degrade_reason", "") or "",
        ])
    return PlainTextResponse(output.getvalue(), media_type="text/csv",
                              headers={"Content-Disposition": "attachment; filename=douyin_data.csv"})

@app.get("/api/cleanup")
async def cleanup(days: int = Query(7, ge=1, le=365)):
    """手动触发数据清理"""
    deleted = db.cleanup_old_data(days)
    return {"success": True, "deleted": deleted, "retention_days": days}


@app.get("/api/live-sessions")
async def live_sessions():
    """本场直播监控总结"""
    summaries = db.get_live_session_summaries()
    return {"count": len(summaries), "sessions": summaries}


@app.post("/api/fetch")
async def fetch_once(req: RoomCreateRequest):
    """临时采集一次（不加入监控）"""
    if not collector or not collector.is_ready:
        raise HTTPException(503, "采集器未就绪")
    try:
        info = normalize_douyin_url(req.url)
    except ValueError as e:
        raise HTTPException(422, str(e))

    data = await collector.fetch_live_data(info["url"])
    return data


# ======= 启动 =======
if __name__ == "__main__":
    import uvicorn
    logger.info(f"启动抖音直播数据监控, 端口={PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
