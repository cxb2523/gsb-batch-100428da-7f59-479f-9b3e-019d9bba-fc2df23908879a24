"""
API服务器模块
"""

import csv
import io
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Depends, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from loguru import logger
from pydantic import BaseModel, Field

from core.bililive_client import BililiveClient, BililiveResult
from core.config import Config
from core.database import Database
from core.url_parser import DouyinRoomTarget, normalize_douyin_url

app = FastAPI(
    title="抖音同行直播监控API",
    description="监控同行开播时间、直播时长、在线人数、进店人数与直播运营数据",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

config = None
db = None


class RoomCreateRequest(BaseModel):
    url: str = Field(..., min_length=3, max_length=1000, description="抖音直播间或主页 URL")
    nickname: Optional[str] = Field(None, max_length=200, description="备注名，可选")
    check_interval_minutes: int = Field(10, ge=2, le=240)
    enable_recording: bool = Field(True, description="是否同步加入 bililive-go 录制监听")


def _account_payload(account):
    return {
        "id": account.id,
        "douyin_id": account.douyin_id,
        "nickname": account.nickname,
        "sec_uid": account.sec_uid,
        "live_room_id": account.live_room_id,
        "live_url": account.live_url,
        "is_active": account.is_active,
        "check_interval_minutes": account.check_interval_minutes,
    }


def _bililive_disabled_result() -> BililiveResult:
    return BililiveResult(enabled=False, ok=False, message="bililive-go 接入未启用")


async def _add_to_bililive(target: DouyinRoomTarget, enabled_by_request: bool) -> BililiveResult:
    if not enabled_by_request:
        return BililiveResult(enabled=False, ok=False, message="本次请求未启用录制")
    if config is None or not config.bililive_enabled:
        return _bililive_disabled_result()
    client = BililiveClient(config.bililive_rpc_url, config.bililive_timeout_seconds)
    return await client.add_room(target.normalized_url)


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    return value


def _csv_response(filename: str, headers: list[str], rows: list[dict[str, Any]]):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_value(row.get(key)) for key in headers})
    return Response(
        content="\ufeff" + stream.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _csv_value(value):
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, (dict, list)):
        import json
        return json.dumps(value, ensure_ascii=False, default=str)
    return "" if value is None else value


async def get_db():
    if db is None:
        raise HTTPException(status_code=503, detail="数据库尚未初始化")
    return db


@app.get("/", response_class=HTMLResponse)
async def root():
    return """
    <html>
      <head><title>抖音同行直播监控</title></head>
      <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 40px; line-height: 1.6;">
        <h1>抖音同行直播监控</h1>
        <p>用于监控同行开播时间、直播时长、在线人数、进店人数、点赞评论等运营数据。</p>
        <ul>
          <li><a href="/docs">API 文档</a></li>
          <li><a href="/api/health">健康检查</a></li>
          <li><a href="/api/operations/overview">运营总览</a></li>
          <li><a href="/api/operations/accounts/ranking">同行排行榜</a></li>
          <li><a href="/api/operations/live-calendar">开播日历</a></li>
        </ul>
      </body>
    </html>
    """


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>直播数据监控</title>
  <style>
    :root {
      --bg: #f5f2ea;
      --ink: #171717;
      --muted: #67645d;
      --line: #d8d1c2;
      --panel: #fffdf8;
      --accent: #0f766e;
      --hot: #c2410c;
      --good: #166534;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }
    .wrap { max-width: 1240px; margin: 0 auto; padding: 28px 24px 48px; }
    header { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; margin-bottom: 22px; }
    h1 { margin: 0; font-size: 30px; line-height: 1.1; font-weight: 760; }
    .sub { margin-top: 8px; color: var(--muted); font-size: 14px; }
    .actions { display: flex; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
    a.btn, button {
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink);
      height: 36px;
      padding: 0 12px;
      border-radius: 6px;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 7px;
      font-size: 13px;
      cursor: pointer;
    }
    button.primary { background: var(--ink); color: white; border-color: var(--ink); }
    .metrics { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }
    .metric, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 1px 0 rgba(0,0,0,.03);
    }
    .metric { padding: 14px 14px 13px; min-height: 92px; }
    .label { color: var(--muted); font-size: 12px; }
    .value { margin-top: 10px; font-variant-numeric: tabular-nums; font-size: 28px; font-weight: 760; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    .panel { overflow: hidden; }
    .panel h2 {
      margin: 0;
      padding: 13px 14px;
      font-size: 15px;
      border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,.45);
    }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { text-align: left; padding: 11px 14px; border-bottom: 1px solid var(--line); vertical-align: top; }
    th { color: var(--muted); font-size: 12px; font-weight: 650; background: rgba(255,255,255,.38); }
    tr:last-child td { border-bottom: 0; }
    .empty { padding: 20px 14px; color: var(--muted); font-size: 13px; }
    .pill { display: inline-flex; height: 22px; align-items: center; padding: 0 8px; border-radius: 999px; background: #eee7d7; color: #463f32; font-size: 12px; }
    .pill.live { background: #fee2e2; color: #991b1b; }
    .pill.normal { background: #dcfce7; color: #166534; }
    .pill.warn { background: #fef3c7; color: #92400e; }
    .name { font-weight: 680; }
    .url { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
    .muted { color: var(--muted); }
    @media (max-width: 900px) {
      header { align-items: flex-start; flex-direction: column; }
      .actions { justify-content: flex-start; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 560px) {
      .wrap { padding: 18px 12px 32px; }
      .metrics { grid-template-columns: 1fr; }
      th:nth-child(3), td:nth-child(3) { display: none; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>直播数据监控</h1>
        <div class="sub" id="updated">正在读取数据...</div>
      </div>
      <div class="actions">
        <a class="btn" href="http://localhost:18080/">返回录制</a>
        <button class="primary" id="refresh">刷新</button>
      </div>
    </header>

    <section class="metrics">
      <div class="metric"><div class="label">监控账号</div><div class="value" id="mAccounts">-</div></div>
      <div class="metric"><div class="label">当前直播</div><div class="value" id="mActive">-</div></div>
      <div class="metric"><div class="label">7天直播场次</div><div class="value" id="mLives">-</div></div>
      <div class="metric"><div class="label">峰值在线</div><div class="value" id="mPeak">-</div></div>
      <div class="metric"><div class="label">总直播时长</div><div class="value" id="mDuration">-</div></div>
    </section>

    <section class="grid">
      <div class="panel">
        <h2>当前直播</h2>
        <div id="active"></div>
      </div>
      <div class="panel">
        <h2>监控账号</h2>
        <div id="accounts"></div>
      </div>
      <div class="panel">
        <h2>近期直播</h2>
        <div id="recent"></div>
      </div>
      <div class="panel">
        <h2>账号排行</h2>
        <div id="ranking"></div>
      </div>
      <div class="panel">
        <h2>复盘导出</h2>
        <div class="empty">
          <div style="display:flex; gap:10px; flex-wrap:wrap;">
            <a class="btn" href="/api/export/accounts.csv">账号状态 CSV</a>
            <a class="btn" href="/api/export/monitoring-logs.csv?days=30">检查日志 CSV</a>
            <a class="btn" href="/api/export/live-sessions.csv?days=30">直播场次 CSV</a>
            <a class="btn" href="/api/export/snapshots.csv?days=30">采样快照 CSV</a>
          </div>
        </div>
      </div>
    </section>
  </div>

  <script>
    const fmt = new Intl.NumberFormat("zh-CN");
    const $ = (id) => document.getElementById(id);
    const n = (v) => v == null ? "-" : fmt.format(v);
    const minutes = (v) => v ? `${fmt.format(v)} 分钟` : "0 分钟";
    const statusText = {
      normal: "正常",
      verify_required: "需要验证",
      login_required: "需要登录",
      blocked: "被限制",
      request_error: "请求失败",
      error: "异常",
    };
    const statusClass = (value) => value === "normal" ? "normal" : value === "verify_required" ? "warn" : "";
    const dt = (value) => value ? new Date(value).toLocaleString("zh-CN") : "未检查";

    async function getJson(path) {
      const res = await fetch(path, { cache: "no-store" });
      if (!res.ok) throw new Error(`${path} ${res.status}`);
      return res.json();
    }

    function table(headers, rows, emptyText) {
      if (!rows.length) return `<div class="empty">${emptyText}</div>`;
      return `<table><thead><tr>${headers.map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table>`;
    }

    async function load() {
      $("updated").textContent = "正在读取数据...";
      const [overview, active, accounts, recent] = await Promise.all([
        getJson("/api/operations/overview?days=7"),
        getJson("/api/lives/active"),
        getJson("/api/accounts?days=7"),
        getJson("/api/lives/recent?days=7&limit=20"),
      ]);
      const ov = overview.overview || {};
      $("mAccounts").textContent = n(ov.monitored_accounts);
      $("mActive").textContent = n(active.count);
      $("mLives").textContent = n(ov.live_count);
      $("mPeak").textContent = n(ov.peak_viewers);
      $("mDuration").textContent = minutes(ov.total_duration_minutes);

      $("active").innerHTML = table(["账号", "在线", "开播时长"], (active.active_lives || []).map(item => `
        <tr><td><div class="name">${item.account?.nickname || item.account?.douyin_id || "-"}</div><div class="url">${item.live_url || ""}</div></td><td>${n(item.current_viewers)}</td><td>${minutes(item.duration_minutes)}</td></tr>
      `), "当前没有检测到直播中账号。");

      $("accounts").innerHTML = table(["账号", "状态", "最近检查"], (accounts.accounts || []).map(item => `
        <tr><td><div class="name">${item.nickname || item.douyin_id}</div><div class="url">${item.live_url || ""}</div></td><td><span class="pill ${statusClass(item.risk_status)}">${statusText[item.risk_status] || item.risk_status || "正常"}</span></td><td class="muted">${dt(item.last_checked)}</td></tr>
      `), "还没有监控账号；在 bililive-go 粘贴 URL 后会自动同步。");

      $("recent").innerHTML = table(["账号", "标题", "时长"], (recent.recent_lives || []).map(item => `
        <tr><td>${item.nickname || item.douyin_id || "-"}</td><td>${item.title || "-"}</td><td>${minutes(item.duration_minutes)}</td></tr>
      `), "最近 7 天还没有结束的直播记录。");

      $("ranking").innerHTML = table(["账号", "场次", "峰值在线"], (overview.top_accounts || []).map(item => `
        <tr><td><div class="name">${item.nickname || item.douyin_id}</div></td><td>${n(item.live_count)}</td><td>${n(item.peak_viewers)}</td></tr>
      `), "暂无排行数据。");

      $("updated").textContent = `最后刷新：${new Date().toLocaleString("zh-CN")}`;
    }

    $("refresh").addEventListener("click", () => load().catch(err => $("updated").textContent = `读取失败：${err.message}`));
    load().catch(err => $("updated").textContent = `读取失败：${err.message}`);
    setInterval(() => load().catch(() => {}), 30000);
  </script>
</body>
</html>
    """


@app.get("/api/health")
async def health_check(db: Database = Depends(get_db)):
    try:
        accounts = await db.get_monitored_accounts(active_only=True)
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "database": "connected",
            "monitored_accounts": len(accounts),
            "version": "1.1.0",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"健康检查失败: {str(e)}")


@app.get("/api/accounts")
async def get_accounts(active_only: bool = Query(True), days: int = Query(7, ge=1, le=90), db: Database = Depends(get_db)):
    try:
        accounts = await db.get_monitored_accounts(active_only=active_only)
        result = []
        for account in accounts:
            stats = await db.get_account_stats(account.id, days=days)
            result.append({
                "id": account.id,
                "douyin_id": account.douyin_id,
                "nickname": account.nickname,
                "sec_uid": account.sec_uid,
                "live_room_id": account.live_room_id,
                "live_url": account.live_url,
                "is_active": account.is_active,
                "check_interval_minutes": account.check_interval_minutes,
                "last_checked": account.last_checked,
                "risk_status": account.risk_status,
                "cooldown_until": account.cooldown_until,
                "consecutive_failures": account.consecutive_failures,
                "last_error": account.last_error,
                "stats": stats,
            })
        return _jsonable({"count": len(result), "accounts": result})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取账号列表失败: {str(e)}")


@app.post("/api/accounts")
async def add_account(
    douyin_id: str = Query(..., description="抖音号或账号标识"),
    nickname: Optional[str] = Query(None, description="同行昵称/备注"),
    sec_uid: Optional[str] = Query(None, description="抖音 sec_uid，知道时优先填写"),
    live_room_id: Optional[str] = Query(None, description="固定直播间 room_id，知道时填写"),
    live_url: Optional[str] = Query(None, description="用户页或直播间 URL，推荐填写"),
    check_interval_minutes: int = Query(10, ge=2, le=240),
    db: Database = Depends(get_db),
):
    try:
        existing = await db.get_account_by_douyin_id(douyin_id)
        if existing:
            raise HTTPException(status_code=400, detail="账号已存在")

        account = await db.create_monitored_account(
            douyin_id=douyin_id,
            nickname=nickname,
            sec_uid=sec_uid,
            live_room_id=live_room_id,
            live_url=live_url,
            check_interval_minutes=check_interval_minutes,
        )
        return _jsonable({"success": True, "message": "账号已写入监控列表", "account": {
            "id": account.id,
            "douyin_id": account.douyin_id,
            "nickname": account.nickname,
            "sec_uid": account.sec_uid,
            "live_room_id": account.live_room_id,
            "live_url": account.live_url,
            "check_interval_minutes": account.check_interval_minutes,
        }})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"添加账号失败: {str(e)}")


@app.post("/api/rooms", status_code=201)
async def add_room(request: RoomCreateRequest, response: Response, db: Database = Depends(get_db)):
    try:
        target = normalize_douyin_url(request.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    existing = await db.get_account_by_douyin_id(target.douyin_id)
    if existing is None:
        existing = await db.get_account_by_live_url(target.normalized_url)

    created = False
    if existing:
        account = existing
        updates: Dict[str, Any] = {}
        if request.nickname and request.nickname != account.nickname:
            updates["nickname"] = request.nickname
        if target.sec_uid and target.sec_uid != account.sec_uid:
            updates["sec_uid"] = target.sec_uid
        if target.live_room_id and target.live_room_id != account.live_room_id:
            updates["live_room_id"] = target.live_room_id
        if target.normalized_url != account.live_url:
            updates["live_url"] = target.normalized_url
        if request.check_interval_minutes != account.check_interval_minutes:
            updates["check_interval_minutes"] = request.check_interval_minutes
        if not account.is_active:
            updates["is_active"] = True
        if updates:
            await db.update_monitored_account(account.id, **updates)
            account = await db.get_account_by_id(account.id)
    else:
        account = await db.create_monitored_account(
            douyin_id=target.douyin_id,
            nickname=request.nickname,
            sec_uid=target.sec_uid,
            live_room_id=target.live_room_id,
            live_url=target.normalized_url,
            check_interval_minutes=request.check_interval_minutes,
        )
        created = True

    if not created:
        response.status_code = 200

    bililive = await _add_to_bililive(target, request.enable_recording)
    status_code = "created" if created else "already_exists"
    return _jsonable({
        "success": True,
        "status": status_code,
        "message": "已加入数据监控和录制监听" if bililive.ok else "已加入数据监控，录制监听需要检查 bililive-go",
        "target": {
            "original_url": target.original_url,
            "normalized_url": target.normalized_url,
            "douyin_id": target.douyin_id,
            "sec_uid": target.sec_uid,
            "live_room_id": target.live_room_id,
        },
        "account": _account_payload(account),
        "bililive": {
            "enabled": bililive.enabled,
            "ok": bililive.ok,
            "message": bililive.message,
            "room": bililive.room,
            "error": bililive.error,
        },
    })


@app.patch("/api/accounts/{account_id}")
async def update_account(
    account_id: int,
    nickname: Optional[str] = Query(None),
    sec_uid: Optional[str] = Query(None),
    live_room_id: Optional[str] = Query(None),
    live_url: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    check_interval_minutes: Optional[int] = Query(None, ge=2, le=240),
    clear_cooldown: bool = Query(False),
    db: Database = Depends(get_db),
):
    updates: Dict[str, Any] = {}
    for key, value in {
        "nickname": nickname,
        "sec_uid": sec_uid,
        "live_room_id": live_room_id,
        "live_url": live_url,
        "is_active": is_active,
        "check_interval_minutes": check_interval_minutes,
    }.items():
        if value is not None:
            updates[key] = value
    if clear_cooldown:
        updates.update({"cooldown_until": None, "consecutive_failures": 0, "risk_status": "normal", "last_error": None})
    if not updates:
        raise HTTPException(status_code=400, detail="没有可更新字段")
    ok = await db.update_monitored_account(account_id, **updates)
    if not ok:
        raise HTTPException(status_code=404, detail="账号不存在")
    return {"success": True, "updated": updates}


@app.get("/api/lives/active")
async def get_active_lives(db: Database = Depends(get_db)):
    try:
        sessions = await db.get_active_live_sessions()
        result = []
        for session in sessions:
            snapshots = await db.get_session_snapshots(session.id, limit=1)
            latest = snapshots[0] if snapshots else None
            account = await db.get_account_by_id(session.account_id)
            result.append({
                "session_id": session.id,
                "live_id": session.live_id,
                "room_id": session.room_id,
                "title": session.title,
                "live_url": session.live_url,
                "account": {
                    "id": account.id if account else None,
                    "douyin_id": account.douyin_id if account else None,
                    "nickname": account.nickname if account else None,
                },
                "start_time": session.start_time,
                "duration_minutes": int((datetime.now() - session.start_time).total_seconds() / 60),
                "current_viewers": latest.viewers_count if latest else None,
                "current_enter_count": latest.enter_count if latest else None,
                "max_viewers": session.max_viewers,
                "avg_viewers": session.avg_viewers,
                "max_enter_count": session.max_enter_count,
                "avg_enter_count": session.avg_enter_count,
                "total_likes": session.total_likes,
                "total_comments": session.total_comments,
                "total_shares": session.total_shares,
            })
        return _jsonable({"count": len(result), "active_lives": result})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取活跃直播失败: {str(e)}")


@app.get("/api/lives/recent")
async def get_recent_lives(days: int = Query(7, ge=1, le=90), limit: int = Query(50, ge=1, le=200), db: Database = Depends(get_db)):
    rows = await db.get_live_calendar(days, limit)
    return _jsonable({"count": len(rows), "recent_lives": rows})


@app.get("/api/lives/{live_id}")
async def get_live_details(live_id: str, include_snapshots: bool = Query(False), db: Database = Depends(get_db)):
    session = await db.get_live_session_by_live_id(live_id)
    if not session:
        raise HTTPException(status_code=404, detail="直播不存在")
    account = await db.get_account_by_id(session.account_id)
    data = {
        "id": session.id,
        "live_id": session.live_id,
        "room_id": session.room_id,
        "title": session.title,
        "live_url": session.live_url,
        "account": {"id": account.id, "douyin_id": account.douyin_id, "nickname": account.nickname} if account else None,
        "start_time": session.start_time,
        "end_time": session.end_time,
        "duration_minutes": session.duration_minutes,
        "max_viewers": session.max_viewers,
        "avg_viewers": session.avg_viewers,
        "max_enter_count": session.max_enter_count,
        "avg_enter_count": session.avg_enter_count,
        "total_likes": session.total_likes,
        "total_comments": session.total_comments,
        "total_shares": session.total_shares,
        "status": session.status,
    }
    if include_snapshots:
        snapshots = await db.get_session_snapshots(session.id, limit=1000)
        data["snapshots"] = [{
            "snapshot_time": item.snapshot_time,
            "viewers_count": item.viewers_count,
            "enter_count": item.enter_count,
            "likes_count": item.likes_count,
            "comments_count": item.comments_count,
            "shares_count": item.shares_count,
            "products": item.get_products(),
        } for item in snapshots]
    return _jsonable(data)


@app.get("/api/stats/daily")
async def get_daily_stats(date: Optional[str] = Query(None), db: Database = Depends(get_db)):
    try:
        target_date = datetime.strptime(date, "%Y-%m-%d") if date else datetime.now()
        return _jsonable({"date": target_date.strftime("%Y-%m-%d"), "stats": await db.get_daily_stats(target_date)})
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式错误，请使用 YYYY-MM-DD")


@app.get("/api/stats/account/{account_id}")
async def get_account_stats(account_id: int, days: int = Query(7, ge=1, le=90), db: Database = Depends(get_db)):
    account = await db.get_account_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    return _jsonable({
        "account": {"id": account.id, "douyin_id": account.douyin_id, "nickname": account.nickname},
        "period_days": days,
        "stats": await db.get_account_stats(account_id, days),
    })


@app.get("/api/operations/overview")
async def operations_overview(days: int = Query(7, ge=1, le=90), db: Database = Depends(get_db)):
    overview = await db.get_operations_overview(days)
    ranking = await db.get_competitor_ranking(days, limit=10)
    calendar = await db.get_live_calendar(days, limit=20)
    return _jsonable({
        "period_days": days,
        "overview": overview,
        "top_accounts": ranking,
        "recent_lives": calendar,
        "notes": {
            "viewers_count": "直播在线人数口径",
            "enter_count": "采样时获取到的进房/看播人数口径；平台未返回时用在线人数兜底",
        },
    })


@app.get("/api/operations/accounts/ranking")
async def operations_account_ranking(days: int = Query(7, ge=1, le=90), limit: int = Query(50, ge=1, le=200), db: Database = Depends(get_db)):
    rows = await db.get_competitor_ranking(days=days, limit=limit)
    return _jsonable({"period_days": days, "count": len(rows), "accounts": rows})


@app.get("/api/operations/live-calendar")
async def operations_live_calendar(days: int = Query(7, ge=1, le=90), limit: int = Query(200, ge=1, le=500), db: Database = Depends(get_db)):
    rows = await db.get_live_calendar(days=days, limit=limit)
    return _jsonable({"period_days": days, "count": len(rows), "lives": rows})


@app.get("/api/export/accounts.csv")
async def export_accounts_csv(db: Database = Depends(get_db)):
    rows = await db.pg_pool.fetch(
        """
        SELECT id,
               douyin_id,
               nickname,
               live_room_id,
               live_url,
               is_active,
               check_interval_minutes,
               last_checked,
               risk_status,
               cooldown_until,
               consecutive_failures,
               last_error,
               created_at,
               updated_at
        FROM monitored_accounts
        ORDER BY id ASC
        """
    )
    headers = [
        "id", "douyin_id", "nickname", "live_room_id", "live_url", "is_active",
        "check_interval_minutes", "last_checked", "risk_status", "cooldown_until",
        "consecutive_failures", "last_error", "created_at", "updated_at",
    ]
    return _csv_response("douyin_accounts.csv", headers, [dict(row) for row in rows])


@app.get("/api/export/monitoring-logs.csv")
async def export_monitoring_logs_csv(days: int = Query(30, ge=1, le=365), db: Database = Depends(get_db)):
    rows = await db.pg_pool.fetch(
        """
        SELECT ml.id,
               ma.nickname,
               ma.douyin_id,
               ma.live_url,
               ml.check_time,
               ml.is_live,
               ml.live_id,
               ml.viewers_count,
               ml.enter_count,
               ml.response_time_ms,
               ml.success,
               ml.risk_status,
               ml.error_message
        FROM monitoring_logs ml
        JOIN monitored_accounts ma ON ma.id = ml.account_id
        WHERE ml.check_time >= $1
        ORDER BY ml.check_time DESC
        """,
        datetime.now() - timedelta(days=days),
    )
    headers = [
        "id", "nickname", "douyin_id", "live_url", "check_time", "is_live",
        "live_id", "viewers_count", "enter_count", "response_time_ms", "success",
        "risk_status", "error_message",
    ]
    return _csv_response("douyin_monitoring_logs.csv", headers, [dict(row) for row in rows])


@app.get("/api/export/live-sessions.csv")
async def export_live_sessions_csv(days: int = Query(30, ge=1, le=365), db: Database = Depends(get_db)):
    rows = await db.pg_pool.fetch(
        """
        SELECT ls.id,
               ma.nickname,
               ma.douyin_id,
               ma.live_url AS account_live_url,
               ls.live_id,
               ls.room_id,
               ls.live_url,
               ls.title,
               ls.start_time,
               ls.end_time,
               ls.duration_minutes,
               ls.max_viewers,
               ls.avg_viewers,
               ls.max_enter_count,
               ls.avg_enter_count,
               ls.total_likes,
               ls.total_comments,
               ls.total_shares,
               ls.products_count,
               ls.status
        FROM live_sessions ls
        JOIN monitored_accounts ma ON ma.id = ls.account_id
        WHERE ls.start_time >= $1
        ORDER BY ls.start_time DESC
        """,
        datetime.now() - timedelta(days=days),
    )
    headers = [
        "id", "nickname", "douyin_id", "account_live_url", "live_id", "room_id",
        "live_url", "title", "start_time", "end_time", "duration_minutes",
        "max_viewers", "avg_viewers", "max_enter_count", "avg_enter_count",
        "total_likes", "total_comments", "total_shares", "products_count", "status",
    ]
    return _csv_response("douyin_live_sessions.csv", headers, [dict(row) for row in rows])


@app.get("/api/export/snapshots.csv")
async def export_snapshots_csv(days: int = Query(30, ge=1, le=365), db: Database = Depends(get_db)):
    rows = await db.pg_pool.fetch(
        """
        SELECT snap.id,
               ma.nickname,
               ma.douyin_id,
               ls.live_id,
               ls.title,
               snap.snapshot_time,
               snap.viewers_count,
               snap.enter_count,
               snap.likes_count,
               snap.comments_count,
               snap.shares_count,
               snap.gifts_value,
               snap.products
        FROM live_snapshots snap
        JOIN live_sessions ls ON ls.id = snap.session_id
        JOIN monitored_accounts ma ON ma.id = ls.account_id
        WHERE snap.snapshot_time >= $1
        ORDER BY snap.snapshot_time DESC
        """,
        datetime.now() - timedelta(days=days),
    )
    headers = [
        "id", "nickname", "douyin_id", "live_id", "title", "snapshot_time",
        "viewers_count", "enter_count", "likes_count", "comments_count",
        "shares_count", "gifts_value", "products",
    ]
    return _csv_response("douyin_live_snapshots.csv", headers, [dict(row) for row in rows])


@app.get("/api/system/status")
async def get_system_status(db: Database = Depends(get_db)):
    try:
        accounts = await db.get_monitored_accounts(active_only=False)
        active_lives = await db.get_active_live_sessions()
        overview = await db.get_operations_overview(days=7)
        risk_accounts = [a for a in accounts if a.risk_status and a.risk_status != "normal"]
        return _jsonable({
            "timestamp": datetime.now(),
            "system": {"status": "running", "version": "1.1.0"},
            "accounts": {"total": len(accounts), "active": len([a for a in accounts if a.is_active]), "risk": len(risk_accounts)},
            "active_lives": len(active_lives),
            "overview_7days": overview,
            "risk_accounts": [{"id": a.id, "douyin_id": a.douyin_id, "nickname": a.nickname, "risk_status": a.risk_status, "cooldown_until": a.cooldown_until, "last_error": a.last_error} for a in risk_accounts[:20]],
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取系统状态失败: {str(e)}")


async def start_api_server(config_obj: Config, db_obj: Database):
    global config, db
    config = config_obj
    db = db_obj

    import uvicorn
    api_config = config.get_api_config()
    logger.info(f"启动API服务器: {api_config['host']}:{api_config['port']}")
    server_config = uvicorn.Config(
        app,
        host=api_config["host"],
        port=api_config["port"],
        log_level="info" if api_config["debug"] else "warning",
    )
    server = uvicorn.Server(server_config)
    await server.serve()
