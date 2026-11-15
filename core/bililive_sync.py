"""
Synchronize rooms added in bililive-go into the Douyin monitor database.
"""

from typing import Any, Optional

from loguru import logger

from core.bililive_client import BililiveClient
from core.config import Config
from core.database import Database
from core.url_parser import normalize_douyin_url


class BililiveSync:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    async def sync_once(self) -> dict[str, Any]:
        if not self.config.bililive_enabled or not self.config.bililive_sync_enabled:
            return {"enabled": False, "seen": 0, "created": 0, "updated": 0, "skipped": 0}

        client = BililiveClient(self.config.bililive_rpc_url, self.config.bililive_timeout_seconds)
        rooms = await client.list_rooms()
        created = 0
        updated = 0
        skipped = 0

        for room in rooms:
            url = _room_url(room)
            if not url:
                skipped += 1
                continue
            try:
                target = normalize_douyin_url(url)
            except ValueError:
                skipped += 1
                continue

            nickname = _room_name(room)
            existing = await self.db.get_account_by_douyin_id(target.douyin_id)
            if existing is None:
                existing = await self.db.get_account_by_live_url(target.normalized_url)

            if existing:
                updates = {}
                if nickname and nickname != existing.nickname:
                    updates["nickname"] = nickname
                if target.sec_uid and target.sec_uid != existing.sec_uid:
                    updates["sec_uid"] = target.sec_uid
                if target.live_room_id and target.live_room_id != existing.live_room_id:
                    updates["live_room_id"] = target.live_room_id
                if target.normalized_url != existing.live_url:
                    updates["live_url"] = target.normalized_url
                if not existing.is_active:
                    updates["is_active"] = True
                if updates:
                    await self.db.update_monitored_account(existing.id, **updates)
                    updated += 1
                continue

            await self.db.create_monitored_account(
                douyin_id=target.douyin_id,
                nickname=nickname,
                sec_uid=target.sec_uid,
                live_room_id=target.live_room_id,
                live_url=target.normalized_url,
                check_interval_minutes=self.config.check_interval_normal,
            )
            created += 1

        if created or updated:
            logger.info(f"bililive-go 同步完成: seen={len(rooms)}, created={created}, updated={updated}, skipped={skipped}")
        return {"enabled": True, "seen": len(rooms), "created": created, "updated": updated, "skipped": skipped}


def _room_url(room: dict[str, Any]) -> Optional[str]:
    for key in ("url", "live_url", "LiveUrl", "LiveURL"):
        value = room.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _room_name(room: dict[str, Any]) -> Optional[str]:
    for key in ("host_name", "room_name", "nick_name", "name", "Name"):
        value = room.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return None
