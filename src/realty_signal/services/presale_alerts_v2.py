"""D-0 in-app reminders for saved presale announcements with a fresh source check.

The date is an announced *next milestone*, not proof of eligibility, allocation
or completion. A missing/failed announcement refresh never becomes a deadline.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime
from hashlib import sha256
from zoneinfo import ZoneInfo

from realty_signal import db


def korea_today() -> date:
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def materialize(uid: int, saved: list[dict], current: list[dict], *,
                today: date | None = None, prefs: dict | None = None) -> int:
    """Emit at most one event per owner, announcement and exact milestone date."""
    today = today or korea_today()
    if (prefs or {}).get("presale_deadline", True) is False:
        return 0
    by_key = {item.get("key"): item for item in current if item.get("key")}
    c = db.conn()
    count = 0
    try:
        c.execute("BEGIN IMMEDIATE")
        for watch in saved:
            key = watch["key"]
            row = by_key.get(key)
            if not row or row.get("stale") or row.get("유형") != "청약":
                continue
            ref = row.get("ref") or {}
            raw_date = ref.get("다음일정")
            try:
                milestone = date.fromisoformat(raw_date)
            except (TypeError, ValueError):
                continue
            if milestone != today:
                continue
            if not c.execute("SELECT 1 FROM listing_watch WHERE uid=? AND key=? AND kind='청약'",
                             (uid, key)).fetchone():
                continue
            event_id = sha256(json.dumps([uid, key, "presale_deadline", raw_date],
                                         ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            payload = {"name": watch["name"], "region": watch.get("region"), "kind": "청약",
                       "date": raw_date, "status": row.get("지표값"),
                       "source_checked_at": row.get("source_checked_at")}
            count += c.execute(
                "INSERT OR IGNORE INTO alert_outbox_v2"
                "(id,uid,subject_type,subject_key,kind,evidence_revision,payload,created_at) "
                "VALUES(?,?,'listing',?,'presale_deadline',?,?,?)",
                (event_id, uid, key, raw_date,
                 json.dumps(payload, ensure_ascii=False, allow_nan=False), int(time.time()))
            ).rowcount
        c.commit()
        return count
    finally:
        c.close()


def scan_fresh_announcements() -> int:
    """Refresh the public notice once per due job, then inspect saved notices."""
    from realty_signal import api, config

    c = db.conn()
    try:
        rows = c.execute("SELECT uid,key,name,region FROM listing_watch "
                         "WHERE kind='청약' ORDER BY uid").fetchall()
    finally:
        c.close()
    if not rows or not config.public_data_key():
        return 0
    started = time.time()
    api._presale.cache_clear()
    announcements = api._presale()
    source = db.kv_get("presale_fetch_status") or {}
    if source.get("ok") is not True or float(source.get("ts") or 0) < started - 1:
        raise RuntimeError("presale_source_unavailable")
    current = []
    for item in announcements:
        key = api._listing_key("청약", item, {"관리번호": item.get("관리번호")},
                               item.get("단지명"), item.get("지역"))
        current.append({"key": key, "유형": "청약", "지표값": item.get("상태"),
                        "ref": {"다음일정": item.get("다음일정")},
                        "source_checked_at": source.get("ts")})
    owners: dict[int, list[dict]] = {}
    for uid, key, name, region in rows:
        owners.setdefault(uid, []).append({"key": key, "name": name, "region": region})
    return sum(materialize(uid, saved, current, prefs=db.alert_prefs_get(uid))
               for uid, saved in owners.items())
