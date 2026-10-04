"""개인용 한방 매물의 입주 가능일을 요청 시에만 조회하고 최소 필드로 캐시한다."""

from __future__ import annotations

from datetime import date
import json
import re
import time
from urllib.parse import urlencode

from realty_signal import db
from realty_signal.ingest import hanbang
from realty_signal.time_kst import today_kst

CACHE_SECONDS = 86400
SOURCE_URL = hanbang.SOURCE_URL


def _key(uid: int, listing_key: str) -> str:
    return f"discovery_occupancy:{uid}:{listing_key}"


def _usable(value: object) -> bool:
    if not isinstance(value, dict) or value.get("status") not in {
            "dated", "immediate", "approximate", "unverified"}:
        return False
    if type(value.get("checked_at")) is not int or value["checked_at"] <= 0:
        return False
    if value["status"] not in {"dated", "immediate"}:
        return True
    try:
        day = date.fromisoformat(value["date"])
    except (KeyError, TypeError, ValueError):
        return False
    if day.isoformat() != value["date"]:
        return False
    if value["status"] == "immediate":
        return day == today_kst()
    return day >= today_kst()


def normalize(content: dict, source_id: str, *, today: date | None = None) -> dict:
    """날짜와 '즉시'만 확정 표현으로 읽는다. 초·중·하순은 확인 필요."""
    if not isinstance(content, dict) or str(content.get("atlfslBscInfoPk")) != str(source_id):
        raise ValueError("listing_identity_mismatch")
    today = today or today_kst()
    raw = str(content.get("mvnPsbltyDay") or "").strip()
    date_type = str(content.get("mvnDayTypeCd") or "").strip()
    base = {"source": "hanbang_detail", "source_url": SOURCE_URL,
            "checked_at": int(time.time())}
    if raw == "NOW" and not date_type:
        return {**base, "status": "immediate", "date": today.isoformat()}
    if re.fullmatch(r"\d{8}", raw):
        try:
            day = date.fromisoformat(f"{raw[:4]}-{raw[4:6]}-{raw[6:]}")
        except ValueError:
            day = None
        if day and day >= today and not date_type:
            return {**base, "status": "dated", "date": day.isoformat()}
        if day and date_type:
            return {**base, "status": "approximate", "date": day.isoformat()}
    return {**base, "status": "unverified", "date": None}


def lookup(uid: int, listing_key: str, source_id: str, *, fresh_after: float = 0) -> dict:
    existing = db.kv_get(_key(uid, listing_key), max_age=CACHE_SECONDS)
    if _usable(existing) and existing["checked_at"] >= int(fresh_after):
        return existing
    raw = hanbang._request("/mptl/atlfsl/atlfslDetail?" + urlencode({
        "atlfslBscInfoPk": source_id}))
    payload = normalize((raw.get("data") or {}).get("content"), source_id)
    db.kv_set(_key(uid, listing_key), payload)
    return payload


def cached(uid: int, listing_fetched_at: dict[str, float]) -> dict[str, dict]:
    """전체 매물 수만큼 DB를 열지 않고 이 사용자의 유효 상세 조회분만 한 번 읽는다."""
    if not listing_fetched_at:
        return {}
    prefix = f"discovery_occupancy:{uid}:"
    c = db.conn()
    try:
        rows = c.execute("SELECT k,v FROM kv WHERE k LIKE ? AND ts>=?",
                         (prefix + "%", int(time.time()) - CACHE_SECONDS)).fetchall()
    finally:
        c.close()
    out = {}
    for key, value in rows:
        listing_key = key[len(prefix):]
        if listing_key not in listing_fetched_at:
            continue
        try:
            data = json.loads(value)
        except (TypeError, ValueError):
            continue
        if _usable(data) and data["checked_at"] >= int(listing_fetched_at[listing_key] or 0):
            out[listing_key] = data
    return out
