"""
Douyin URL normalization helpers.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse


DOUYIN_HOST_SUFFIXES = (
    "douyin.com",
    "iesdouyin.com",
    "amemv.com",
)


@dataclass(frozen=True)
class DouyinRoomTarget:
    original_url: str
    normalized_url: str
    douyin_id: str
    sec_uid: Optional[str] = None
    live_room_id: Optional[str] = None
    nickname: Optional[str] = None


def normalize_douyin_url(url: str) -> DouyinRoomTarget:
    raw = (url or "").strip()
    if not raw:
        raise ValueError("URL 不能为空")

    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw

    parsed = urlparse(raw)
    host = parsed.netloc.lower().split("@")[-1].split(":")[0]
    if not any(host == suffix or host.endswith("." + suffix) for suffix in DOUYIN_HOST_SUFFIXES):
        raise ValueError("只支持抖音直播间或主页 URL")

    path = unquote(parsed.path or "").strip("/")
    parts = [part for part in path.split("/") if part]
    query = parse_qs(parsed.query)

    live_room_id = _extract_room_id(host, parts, query)
    sec_uid = _extract_sec_uid(parts, query)
    normalized_url = _build_normalized_url(host, live_room_id, sec_uid, raw)
    douyin_id = _build_stable_id(live_room_id, sec_uid, normalized_url)

    return DouyinRoomTarget(
        original_url=url,
        normalized_url=normalized_url,
        douyin_id=douyin_id,
        sec_uid=sec_uid,
        live_room_id=live_room_id,
    )


def _extract_room_id(host: str, parts: list[str], query: dict[str, list[str]]) -> Optional[str]:
    for key in ("room_id", "web_rid", "rid"):
        value = _first_query_value(query, key)
        if value and re.match(r"^[A-Za-z0-9_-]{3,80}$", value):
            return value

    if host == "live.douyin.com" and parts:
        candidate = parts[0]
        if re.match(r"^[A-Za-z0-9_-]{3,80}$", candidate):
            return candidate

    if parts and parts[0] == "live" and len(parts) > 1:
        candidate = parts[1]
        if re.match(r"^[A-Za-z0-9_-]{3,80}$", candidate):
            return candidate

    return None


def _extract_sec_uid(parts: list[str], query: dict[str, list[str]]) -> Optional[str]:
    value = _first_query_value(query, "sec_uid")
    if value:
        return value[:200]

    if parts and parts[0] == "user" and len(parts) > 1:
        return parts[1][:200]

    return None


def _first_query_value(query: dict[str, list[str]], key: str) -> Optional[str]:
    values = query.get(key)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def _build_normalized_url(host: str, live_room_id: Optional[str], sec_uid: Optional[str], raw: str) -> str:
    if live_room_id:
        return f"https://live.douyin.com/{live_room_id}"
    if sec_uid:
        return f"https://www.douyin.com/user/{sec_uid}"
    parsed = urlparse(raw)
    return f"https://{host}{parsed.path or ''}".rstrip("/")


def _build_stable_id(live_room_id: Optional[str], sec_uid: Optional[str], normalized_url: str) -> str:
    if live_room_id:
        return f"room_{_clean_identifier(live_room_id)}"[:100]
    if sec_uid:
        return f"user_{_clean_identifier(sec_uid)}"[:100]
    digest = hashlib.sha1(normalized_url.encode("utf-8")).hexdigest()[:16]
    return f"url_{digest}"


def _clean_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return cleaned or hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
