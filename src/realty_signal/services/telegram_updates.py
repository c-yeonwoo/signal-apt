"""Deliver fresh saved-item updates to linked Telegram chats, without crawling."""

from __future__ import annotations

import json
import logging
import time

from realty_signal import config, db, telegram
from realty_signal.services import complex_watch, telegram_templates
from realty_signal.time_kst import today_kst

log = logging.getLogger("realty_signal.telegram_updates")
SNAP_PREFIX = "telegram_complex_snap:"
EVENT_MAX_AGE = 2 * 86400
COMPLEX_MAX_AGE_DAYS = 7
EVENT_PREF = {"presale_deadline": "presale_deadline", "listing_price": "listing_price",
              "listing_target": "listing_target", "new_alternative": "new_alternative"}


def _events(uid: int, email: str, linked_at: int) -> list[dict]:
    cutoff = max(int(time.time()) - EVENT_MAX_AGE, linked_at)
    c = db.conn()
    try:
        rows = c.execute(
            "SELECT e.id,e.kind,e.payload,e.created_at,w.kind "
            "FROM alert_outbox_v2 e JOIN listing_watch w "
            "ON w.uid=e.uid AND w.key=e.subject_key "
            "LEFT JOIN telegram_alert_delivery d ON d.uid=e.uid AND d.event_id=e.id "
            "WHERE e.uid=? AND e.subject_type='listing' AND e.created_at>=? "
            "AND e.kind IN ('presale_deadline','listing_price','listing_target','new_alternative') "
            "AND d.event_id IS NULL ORDER BY e.created_at,e.id LIMIT 30", (uid, cutoff)).fetchall()
    finally:
        c.close()
    prefs = db.alert_prefs_get(uid)
    allowed_private = config.personal_listing_allowed(email)
    out = []
    for event_id, kind, raw, created_at, watch_kind in rows:
        if prefs.get(EVENT_PREF[kind], True) is False:
            continue
        if watch_kind in {"일반매물", "급매", "찐매물"} and not allowed_private:
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if kind == "presale_deadline" and payload.get("date") != today_kst().isoformat():
            continue
        out.append({"id": event_id, "kind": kind, "payload": payload,
                    "created_at": created_at})
        if len(out) == 3:
            break
    return out


def _complexes(uid: int) -> tuple[list[dict], dict]:
    favorites = complex_watch.favorites_of(uid)
    if not favorites:
        return [], {}
    previous = db.kv_get(SNAP_PREFIX + str(uid)) or {}
    items, snaps, _ = complex_watch.scan(favorites, complex_watch.cache_loader(), previous)
    fresh = [item for item in items if isinstance(item.get("data_days"), (int, float))
             and 0 <= item["data_days"] <= COMPLEX_MAX_AGE_DAYS]
    picked = [item for item in fresh if item.get("changes")][:3]
    picked_keys = {item["key"] for item in picked}
    active_keys = {f"{region}|{name}" for region, name in favorites}
    advance = {key: snap for key, snap in previous.items() if key in active_keys}
    advance.update({item["key"]: snaps[item["key"]] for item in fresh
                    if item["key"] in snaps and (not item.get("changes") or item["key"] in picked_keys)})
    return picked, advance


def _commit(uid: int, event_ids: list[str], snapshot: dict, *, sent: bool = False) -> None:
    c = db.conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        now = int(time.time())
        for event_id in event_ids:
            c.execute("INSERT OR IGNORE INTO telegram_alert_delivery VALUES(?,?,?)", (uid, event_id, now))
        c.execute("INSERT OR REPLACE INTO kv(k,v,ts) VALUES(?,?,?)",
                  (SNAP_PREFIX + str(uid), json.dumps(snapshot, ensure_ascii=False), now))
        if sent:
            c.execute("INSERT OR REPLACE INTO kv(k,v,ts) VALUES(?,?,?)",
                      (f"telegram_last_sent:{uid}", '{"kind":"updates"}', now))
        c.commit()
    finally:
        c.close()


def run() -> dict:
    """At most one concise message per linked account per due run."""
    stats = {"users": 0, "sent": 0, "events": 0, "complexes": 0, "errors": 0}
    if not telegram.available():
        return stats
    for user in db.users_with_telegram():
        stats["users"] += 1
        uid, chat_id = user["id"], user["chat_id"]
        profile = db.profile_get(uid) or {}
        link = profile.get("telegram") or {}
        if telegram.chat_id_of(profile) != chat_id:
            continue
        try:
            complexes, snapshot = _complexes(uid)
            events = _events(uid, user["email"], int(link.get("linked_at") or 0))
            if not complexes and not events:
                _commit(uid, [], snapshot)
                continue
            message = telegram_templates.render_updates(complexes, events)
            if not telegram.send_message(chat_id, message):
                stats["errors"] += 1
                continue
            _commit(uid, [event["id"] for event in events], snapshot, sent=True)
            stats["sent"] += 1
            stats["events"] += len(events)
            stats["complexes"] += len(complexes)
        except Exception as exc:  # noqa: BLE001 - another account must still receive its own updates
            log.warning("telegram update failed uid=%s: %s", uid, type(exc).__name__)
            stats["errors"] += 1
    return stats
