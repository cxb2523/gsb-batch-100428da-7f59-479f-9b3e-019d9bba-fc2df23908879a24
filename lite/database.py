"""
SQLite 数据库 - 轻量存储
"""
import json
import sqlite3
import time
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from pathlib import Path
from loguru import logger


class Database:
    """SQLite 数据库"""

    def __init__(self, db_path: str = "data/monitor.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self):
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS rooms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL UNIQUE,
                    nickname TEXT,
                    room_id TEXT,
                    is_active INTEGER DEFAULT 1,
                    check_interval INTEGER DEFAULT 10,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS live_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    is_live INTEGER NOT NULL DEFAULT 0,
                    title TEXT,
                    viewers INTEGER,
                    likes INTEGER,
                    comments INTEGER,
                    shares INTEGER,
                    products TEXT,
                    error TEXT,
                    source TEXT,
                    checked_at TEXT DEFAULT (datetime('now'))
                );

                CREATE INDEX IF NOT EXISTS idx_snapshots_room
                    ON live_snapshots(room_id, checked_at);

                CREATE TABLE IF NOT EXISTS live_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    nickname TEXT,
                    title TEXT,
                    start_at TEXT,
                    end_at TEXT,
                    duration_minutes INTEGER,
                    max_viewers INTEGER,
                    avg_viewers INTEGER,
                    total_likes INTEGER
                );
            """)
            conn.commit()
        finally:
            conn.close()

    # ======= 房间管理 =======

    def add_room(self, url: str, nickname: str = "", check_interval: int = 10) -> int:
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO rooms (url, nickname, check_interval) VALUES (?, ?, ?)",
                (url, nickname, check_interval),
            )
            conn.commit()
            if cur.lastrowid:
                return cur.lastrowid
            # 已存在，返回 ID
            row = conn.execute("SELECT id FROM rooms WHERE url = ?", (url,)).fetchone()
            return row["id"] if row else -1
        finally:
            conn.close()

    def get_rooms(self, active_only: bool = True) -> List[Dict]:
        conn = self._get_conn()
        try:
            if active_only:
                rows = conn.execute(
                    "SELECT * FROM rooms WHERE is_active = 1 ORDER BY id"
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM rooms ORDER BY id").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def remove_room(self, room_id: int):
        conn = self._get_conn()
        try:
            conn.execute("UPDATE rooms SET is_active = 0 WHERE id = ?", (room_id,))
            conn.commit()
        finally:
            conn.close()

    def get_room_by_url(self, url: str) -> Optional[Dict]:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM rooms WHERE url = ?", (url,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ======= 数据存储 =======

    def save_snapshot(self, data: Dict[str, Any]):
        """保存一次采集快照"""
        conn = self._get_conn()
        try:
            products_json = json.dumps(data.get("products", []), ensure_ascii=False)
            room_id = data.get("room_id") or data.get("web_rid") or str(hash(data.get("url", "")))
            conn.execute(
                """INSERT INTO live_snapshots
                   (room_id, url, is_live, title, viewers, likes, comments, shares,
                    products, error, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    room_id,
                    data.get("url", ""),
                    1 if data.get("is_live") else 0,
                    data.get("title", ""),
                    data.get("viewers"),
                    data.get("likes"),
                    data.get("comments"),
                    data.get("shares"),
                    products_json,
                    data.get("error"),
                    data.get("source", "http"),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_recent_snapshots(self, room_id: str, limit: int = 50) -> List[Dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT * FROM live_snapshots
                   WHERE room_id = ? ORDER BY checked_at DESC LIMIT ?""",
                (room_id, limit),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["products"] = json.loads(d["products"]) if d["products"] else []
                except Exception:
                    d["products"] = []
                result.append(d)
            return result
        finally:
            conn.close()

    def get_all_live_snapshots(self, limit: int = 200) -> List[Dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT * FROM live_snapshots
                   ORDER BY checked_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["products"] = json.loads(d["products"]) if d["products"] else []
                except Exception:
                    d["products"] = []
                result.append(d)
            return result
        finally:
            conn.close()

    def get_live_sessions(self, limit: int = 50) -> List[Dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM live_sessions ORDER BY start_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_stats(self) -> Dict:
        """获取统计信息并清理过期数据"""
        conn = self._get_conn()
        try:
            # 先清理过期数据
            retention_days = int(os.getenv("DATA_RETENTION_DAYS", "7"))
            if retention_days > 0:
                deleted = self._cleanup_old_data(conn, retention_days)
            else:
                deleted = 0

            rooms = conn.execute("SELECT COUNT(*) as c FROM rooms WHERE is_active=1").fetchone()["c"]
            snapshots = conn.execute("SELECT COUNT(*) as c FROM live_snapshots").fetchone()["c"]
            sessions = conn.execute("SELECT COUNT(*) as c FROM live_sessions").fetchone()["c"]
            live_now = conn.execute(
                "SELECT COUNT(DISTINCT room_id) as c FROM live_snapshots WHERE is_live=1 AND checked_at > datetime('now', '-10 minutes')"
            ).fetchone()["c"]
            return {
                "rooms": rooms,
                "snapshots": snapshots,
                "sessions": sessions,
                "live_now": live_now,
                "cleaned_today": deleted,
            }
        finally:
            conn.close()

    def cleanup_old_data(self, retention_days: int = 7) -> int:
        """手动清理过期数据"""
        conn = self._get_conn()
        try:
            return self._cleanup_old_data(conn, retention_days)
        finally:
            conn.close()

    def get_live_session_summaries(self) -> List[Dict]:
        """获取各房间本场直播的汇总数据

        规则：取最近 2 小时内 is_live=1 的快照，按 room_id 分组汇总。
        如果最近一次快照 is_live=0，说明直播已结束。
        """
        conn = self._get_conn()
        try:
            # 先拿各房间最近 2 小时内的直播快照
            rows = conn.execute(
                """SELECT room_id, url,
                          COUNT(*) as snap_count,
                          MAX(viewers) as max_viewers,
                          AVG(viewers) as avg_viewers,
                          MAX(likes) as max_likes,
                          MAX(comments) as max_comments,
                          MAX(shares) as max_shares,
                          MIN(checked_at) as first_seen,
                          MAX(checked_at) as last_seen,
                          (SELECT is_live FROM live_snapshots ls2
                           WHERE ls2.room_id = live_snapshots.room_id
                           ORDER BY checked_at DESC LIMIT 1) as still_live
                   FROM live_snapshots
                   WHERE is_live = 1
                     AND checked_at > datetime('now', '-2 hours')
                   GROUP BY room_id
                   ORDER BY first_seen DESC"""
            ).fetchall()

            result = []
            for r in rows:
                d = dict(r)
                # 计算时长
                try:
                    from datetime import datetime as dt
                    fmt = "%Y-%m-%d %H:%M:%S"
                    start = dt.strptime(d["first_seen"], fmt)
                    end = dt.strptime(d["last_seen"], fmt)
                    d["duration_minutes"] = int((end - start).total_seconds() / 60)
                except Exception:
                    d["duration_minutes"] = 0

                # 平均人数取整
                if d["avg_viewers"]:
                    d["avg_viewers"] = round(d["avg_viewers"])
                d["still_live"] = bool(d["still_live"])
                result.append(d)

            return result
        finally:
            conn.close()

    def _cleanup_old_data(self, conn, retention_days: int) -> int:
        """清理超过 retention_days 的数据"""
        cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
        deleted = conn.execute(
            "DELETE FROM live_snapshots WHERE checked_at < ?",
            (cutoff,),
        ).rowcount
        if deleted > 0:
            conn.commit()
            logger.info(f"[DB] 清理了 {deleted} 条超过 {retention_days} 天的旧数据")
        return deleted
