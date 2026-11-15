"""
Small RPC client for bililive-go.
"""

from dataclasses import dataclass
from typing import Any, Optional

import aiohttp


@dataclass
class BililiveResult:
    enabled: bool
    ok: bool
    message: str
    room: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class BililiveClient:
    def __init__(self, base_url: str, timeout_seconds: int = 20):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def add_room(self, url: str) -> BililiveResult:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        payload = [{"url": url, "listen": True}]
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(f"{self.base_url}/api/lives", json=payload) as response:
                    text = await response.text()
                    if response.status >= 400:
                        return BililiveResult(
                            enabled=True,
                            ok=False,
                            message="bililive-go 添加直播间失败",
                            error=f"HTTP {response.status}: {text[:500]}",
                        )

                await self._persist_config(session)
                room = await self._find_room(session, url)
                return BililiveResult(
                    enabled=True,
                    ok=True,
                    message="bililive-go 已加入录制监听",
                    room=room,
                )
        except Exception as exc:
            return BililiveResult(
                enabled=True,
                ok=False,
                message="bililive-go 不可用或请求超时",
                error=str(exc),
            )

    async def list_rooms(self) -> list[dict[str, Any]]:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{self.base_url}/api/lives") as response:
                response.raise_for_status()
                rooms = await response.json()
        if not isinstance(rooms, list):
            return []
        return [room for room in rooms if isinstance(room, dict)]

    async def _persist_config(self, session: aiohttp.ClientSession) -> None:
        try:
            async with session.put(f"{self.base_url}/api/config") as response:
                await response.read()
        except Exception:
            # Adding the live room is the important part. Persistence can be retried
            # from the bililive-go UI if this best-effort save fails.
            return

    async def _find_room(self, session: aiohttp.ClientSession, url: str) -> Optional[dict[str, Any]]:
        try:
            async with session.get(f"{self.base_url}/api/lives") as response:
                if response.status >= 400:
                    return None
                rooms = await response.json()
        except Exception:
            return None

        if not isinstance(rooms, list):
            return None
        for room in rooms:
            if isinstance(room, dict) and self._room_url(room) == url:
                return room
        return rooms[-1] if rooms else None

    @staticmethod
    def _room_url(room: dict[str, Any]) -> Optional[str]:
        for key in ("url", "live_url", "LiveUrl", "LiveURL"):
            value = room.get(key)
            if isinstance(value, str) and value:
                return value
        return None
