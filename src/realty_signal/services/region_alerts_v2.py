"""In-app changes between issued assessments for verified region favorites.

Issued records describe what was known when published. They are never treated
as a fresh current trading recommendation; the linked report rebuilds today's
safety gate. Name-only ambiguous favorites are deliberately excluded.
"""

from __future__ import annotations

import json
import time
from hashlib import sha256

from realty_signal import db

PAGE_SIZE = 50


def _important_change(old: dict, new: dict) -> dict | None:
    if old.get("assessment_id") == new.get("assessment_id"):
        return None
    old_reasons = {item["reason_id"]: item for item in old.get("reasons", [])}
    changed = []
    for item in new.get("reasons", []):
        prior = old_reasons.get(item["reason_id"])
        if prior and (prior.get("value") != item.get("value") or
                      prior.get("passing") != item.get("passing")):
            changed.append({"id": item["reason_id"], "label": item.get("label"),
                            "old": prior.get("value"), "new": item.get("value"),
                            "unit": item.get("unit"),
                            "threshold_crossed": prior.get("passing") != item.get("passing")})
    grade_changed = old.get("display_grade") != new.get("display_grade")
    safety_changed = (old.get("assessment_status") != new.get("assessment_status") or
                      old.get("risk_flags") != new.get("risk_flags"))
    method_changed = any(old.get(key) != new.get(key)
                         for key in ("version", "config_hash", "guard_version"))
    asof_changed = old.get("asof") != new.get("asof")
    old_risks = set(old.get("risk_flags") or [])
    new_risks = set(new.get("risk_flags") or [])
    if (not asof_changed and not changed and not method_changed
            and old_risks ^ new_risks == {"source_stale"}):
        return None
    # Do not notify on age alone or a routine same-date reissue. A new market
    # observation, threshold crossing or safety transition is the product event.
    if not grade_changed and not safety_changed and not changed:
        return None
    if not asof_changed and not grade_changed and not any(
            item["threshold_crossed"] for item in changed):
        if old_risks ^ new_risks <= {"source_stale"}:
            return None
    return {"region": new.get("region"), "region_id": new.get("region_id"),
            "old_grade": old.get("display_grade"), "new_grade": new.get("display_grade"),
            "old_status": old.get("assessment_status"),
            "new_status": new.get("assessment_status"),
            "risk_flags": new.get("risk_flags") or [], "changed_reasons": changed[:4],
            "asof": new.get("asof"), "source_revision": not asof_changed,
            "method_changed": method_changed,
            "issued_assessment_id": new.get("assessment_id")}


def materialize(uid: int, favorite_key: str, current: dict, *, prefs: dict | None = None) -> int:
    """Advance one favorite's baseline atomically; a disabled rule still advances."""
    c = db.conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        if not c.execute("SELECT 1 FROM favorites WHERE uid=? AND kind='region' AND key=?",
                         (uid, favorite_key)).fetchone():
            c.commit()
            return 0
        if not c.execute("SELECT 1 FROM signal_assessments WHERE id=? AND region_id=?",
                         (current["assessment_id"], current["region_id"])).fetchone():
            c.commit()
            return 0
        row = c.execute("SELECT assessment_id FROM region_watch_state_v2 "
                        "WHERE uid=? AND favorite_key=?", (uid, favorite_key)).fetchone()
        current_id = current["assessment_id"]
        if not row:
            c.execute("INSERT INTO region_watch_state_v2 VALUES(?,?,?,?)",
                      (uid, favorite_key, current_id, int(time.time())))
            c.commit()
            return 0
        if row[0] == current_id:
            c.commit()
            return 0
        old_row = c.execute("SELECT data FROM signal_assessments WHERE id=?", (row[0],)).fetchone()
        old = json.loads(old_row[0]) if old_row else None
        if old and old.get("region_id") != current.get("region_id"):
            old = None
        # A restored/older source must not roll the observed state backwards.
        if old and str(current.get("asof") or "") < str(old.get("asof") or ""):
            c.commit()
            return 0
        payload = _important_change(old, current) if old else None
        inserted = 0
        if payload and (prefs or {}).get("region_evidence", True):
            event_id = sha256(json.dumps([uid, favorite_key, "region_evidence", current_id],
                                         ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            inserted = c.execute(
                "INSERT OR IGNORE INTO alert_outbox_v2"
                "(id,uid,subject_type,subject_key,kind,evidence_revision,payload,created_at) "
                "VALUES(?,?,'region',?,'region_evidence',?,?,?)",
                (event_id, uid, favorite_key, current_id,
                 json.dumps(payload, ensure_ascii=False, allow_nan=False), int(time.time()))
            ).rowcount
        c.execute("UPDATE region_watch_state_v2 SET assessment_id=?,updated_at=? "
                  "WHERE uid=? AND favorite_key=?",
                  (current_id, int(time.time()), uid, favorite_key))
        c.commit()
        return inserted
    finally:
        c.close()


def scan_issued() -> int:
    """Read issued snapshots only; no KB refresh, crawler or paid model call."""
    from realty_signal.services import market_data as md

    c = db.conn()
    try:
        favorites = c.execute("SELECT uid,key FROM favorites WHERE kind='region'").fetchall()
        latest = c.execute(
            "SELECT region_id,data FROM (SELECT region_id,data,"
            "ROW_NUMBER() OVER (PARTITION BY region_id ORDER BY asof DESC,issued_at DESC,id DESC) AS rn "
            "FROM signal_assessments) WHERE rn=1 AND region_id LIKE 'kb:%'").fetchall()
    finally:
        c.close()
    by_id = {region_id: json.loads(data) for region_id, data in latest}
    if not favorites or not by_id:
        return 0
    count = 0
    for uid, key in favorites:
        name = db._favorite_region_name(key)
        if not name:
            continue
        region_id = key if key.startswith("kb:") else f"kb:{(md.kb().codes or {}).get(name) or ''}"
        current = by_id.get(region_id)
        if current and current.get("region") == name:
            count += materialize(uid, key, current, prefs=db.alert_prefs_get(uid))
    return count


def _valid_keys(uid: int) -> list[str]:
    return [f["key"] for f in db.fav_list(uid) if f["kind"] == "region"
            and db._favorite_region_name(f["key"])]


def list_events(uid: int, *, limit: int = PAGE_SIZE) -> dict:
    keys = _valid_keys(uid)
    if not keys:
        return {"items": [], "unread": 0}
    limit = max(1, min(PAGE_SIZE, int(limit)))
    places = ",".join("?" for _ in keys)
    where = f"uid=? AND subject_type='region' AND subject_key IN ({places})"
    args = [uid, *keys]
    c = db.conn()
    try:
        rows = c.execute("SELECT id,subject_key,kind,payload,created_at,seen_at "
                         f"FROM alert_outbox_v2 WHERE {where} "
                         "ORDER BY created_at DESC,id DESC LIMIT ?", [*args, limit]).fetchall()
        unread = c.execute(f"SELECT COUNT(*) FROM alert_outbox_v2 WHERE {where} AND seen_at IS NULL",
                           args).fetchone()[0]
        return {"items": [{"id": row[0], "subject_type": "region", "subject_key": row[1],
                           "kind": row[2], "payload": json.loads(row[3]),
                           "created_at": row[4], "seen": row[5] is not None}
                          for row in rows], "unread": unread}
    finally:
        c.close()


def mark_seen(uid: int) -> int:
    keys = _valid_keys(uid)
    if not keys:
        return 0
    places = ",".join("?" for _ in keys)
    c = db.conn()
    try:
        updated = c.execute("UPDATE alert_outbox_v2 SET seen_at=? WHERE uid=? "
                            f"AND subject_type='region' AND subject_key IN ({places}) "
                            "AND seen_at IS NULL", [int(time.time()), uid, *keys]).rowcount
        c.commit()
        return updated
    finally:
        c.close()
