"""Idempotent in-app events for actively watched listings.

Source caches are read before calling ``materialize``. A missing or stale row
does not imply a sale or price change, and the first complete observation only
establishes a baseline. Event creation and baseline advancement share one
SQLite transaction so repeated scheduler runs never duplicate a change.
"""

from __future__ import annotations

import json
import time
from hashlib import sha256

from realty_signal import db
from realty_signal.services import listing_watch

PAGE_SIZE = 50


def _event_id(uid: int, key: str, kind: str, revision: int) -> str:
    return sha256(json.dumps([uid, key, kind, revision],
                             ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _price(value) -> float | None:
    try:
        result = float(value)
        return result if 0 < result < 1_000_000_000 else None
    except (TypeError, ValueError):
        return None


def materialize(uid: int, saved: list[dict], current: list[dict], *,
                private_allowed: bool, prefs: dict | None = None) -> int:
    """Record changes in already collected, fresh rows for one owner only."""
    prefs = prefs or {}
    permitted = [row for row in saved
                 if private_allowed or row.get("kind") not in listing_watch.PRIVATE]
    if not permitted:
        return 0
    visible_current = (current if private_allowed else
                       [row for row in current if row.get("유형") not in listing_watch.PRIVATE])
    current_by_key = {row.get("key"): row for row in visible_current if row.get("key")}
    built = listing_watch.build(permitted, visible_current)
    now = int(time.time())
    count = 0
    c = db.conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        for watch, view in zip(permitted, built):
            key = watch["key"]
            row = current_by_key.get(key)
            # Auction minimum bids and presale notices are not sale asking prices.
            # They need their own source-specific freshness and D-day contracts.
            if row is None or row.get("stale") or row.get("price_kind") != "asking":
                continue
            observed_price = _price(row.get("총액"))
            alternatives = [alt for alt in view["alternatives"]
                            if alt.get("key") and not alt.get("stale")]
            alt_keys = sorted({alt["key"] for alt in alternatives})
            state = c.execute("SELECT last_price,alternative_keys,revision FROM listing_watch_state_v2 "
                              "WHERE uid=? AND key=?", (uid, key)).fetchone()
            if state is None:
                c.execute("INSERT INTO listing_watch_state_v2 VALUES(?,?,?,?,?,?)",
                          (uid, key, observed_price, json.dumps(alt_keys, ensure_ascii=False), 1, now))
                continue
            old_price, old_alt_json, revision = state
            old_alt_keys = set(json.loads(old_alt_json))
            changed_price = (observed_price is not None and old_price is not None
                             and observed_price != old_price)
            new_keys = set(alt_keys) - old_alt_keys
            if changed_price or new_keys:
                revision += 1
            if changed_price and prefs.get("listing_price", True):
                payload = {"name": watch["name"], "region": watch.get("region"),
                           "kind": watch["kind"], "old_price": old_price,
                           "new_price": observed_price,
                           "observed_at": row.get("fetched_at") or row.get("등록일")}
                count += _insert(c, uid, key, "listing_price", revision, payload, now)
            if new_keys and prefs.get("new_alternative", True):
                payload = {"name": watch["name"], "region": watch.get("region"),
                           "kind": watch["kind"],
                           "alternatives": [{"key": alt["key"], "name": alt.get("name"),
                                             "kind": alt.get("kind"), "price": alt.get("price")}
                                            for alt in alternatives if alt["key"] in new_keys][:3]}
                count += _insert(c, uid, key, "new_alternative", revision, payload, now)
            c.execute("UPDATE listing_watch_state_v2 SET last_price=?,alternative_keys=?,"
                      "revision=?,updated_at=? WHERE uid=? AND key=?",
                      (observed_price if observed_price is not None else old_price,
                       json.dumps(alt_keys, ensure_ascii=False), revision, now, uid, key))
        c.commit()
        return count
    finally:
        c.close()


def _insert(c, uid: int, key: str, kind: str, revision: int, payload: dict, now: int) -> int:
    cur = c.execute("INSERT OR IGNORE INTO alert_outbox_v2"
                    "(id,uid,subject_type,subject_key,kind,evidence_revision,payload,created_at) "
                    "VALUES(?,?,'listing',?,?,?,?,?)",
                    (_event_id(uid, key, kind, revision), uid, key, kind, str(revision),
                     json.dumps(payload, ensure_ascii=False, allow_nan=False), now))
    return cur.rowcount


def scan_cached_sources() -> int:
    """Run after source refresh or on the due clock; never starts a crawler."""
    from realty_signal import api, config

    c = db.conn()
    try:
        rows = c.execute("SELECT w.uid,u.email,w.key,w.kind,w.name,w.region,w.saved_price,w.created_at "
                         "FROM listing_watch w JOIN users u ON u.id=w.uid "
                         "WHERE w.kind IN ('급매','찐매물','일반매물') ORDER BY w.uid").fetchall()
    finally:
        c.close()
    if not rows:
        return 0
    owners: dict[int, dict] = {}
    for uid, email, key, kind, name, region, saved_price, created_at in rows:
        owner = owners.setdefault(uid, {"email": email, "saved": []})
        owner["saved"].append({"key": key, "kind": kind, "name": name,
                               "region": region, "saved_price": saved_price,
                               "created_at": created_at})
    allowed = {uid for uid, owner in owners.items()
               if config.personal_listing_allowed(owner["email"])}
    kinds = {watch["kind"] for uid, owner in owners.items() if uid in allowed
             for watch in owner["saved"]}
    if not kinds:
        return 0
    current = api._build_listings(kinds, include_private=bool(allowed))
    count = 0
    for uid, owner in owners.items():
        count += materialize(uid, owner["saved"], current,
                             private_allowed=uid in allowed, prefs=db.alert_prefs_get(uid))
    return count


def list_events(uid: int, *, private_allowed: bool, limit: int = PAGE_SIZE) -> dict:
    limit = max(1, min(PAGE_SIZE, int(limit)))
    where = ("e.uid=? AND (e.subject_type!='listing' OR EXISTS ("
             "SELECT 1 FROM listing_watch w WHERE w.uid=e.uid AND w.key=e.subject_key))")
    args: list = [uid]
    if not private_allowed:
        where += (" AND (e.subject_type!='listing' OR NOT EXISTS ("
                  "SELECT 1 FROM listing_watch w WHERE w.uid=e.uid AND w.key=e.subject_key "
                  "AND w.kind IN ('급매','찐매물','일반매물')))")
    c = db.conn()
    try:
        rows = c.execute("SELECT e.id,e.subject_type,e.subject_key,e.kind,e.payload,e.created_at,e.seen_at "
                         f"FROM alert_outbox_v2 e WHERE {where} "
                         "ORDER BY e.created_at DESC,e.id DESC LIMIT ?", [*args, limit]).fetchall()
        unread = c.execute(f"SELECT COUNT(*) FROM alert_outbox_v2 e WHERE {where} "
                           "AND e.seen_at IS NULL", args).fetchone()[0]
        return {"items": [{"id": row[0], "subject_type": row[1], "subject_key": row[2],
                           "kind": row[3], "payload": json.loads(row[4]),
                           "created_at": row[5], "seen": row[6] is not None}
                          for row in rows], "unread": unread}
    finally:
        c.close()


def mark_seen(uid: int, *, private_allowed: bool) -> int:
    where = ("uid=? AND seen_at IS NULL AND (subject_type!='listing' OR EXISTS ("
             "SELECT 1 FROM listing_watch w WHERE w.uid=alert_outbox_v2.uid "
             "AND w.key=alert_outbox_v2.subject_key))")
    if not private_allowed:
        where += (" AND (subject_type!='listing' OR NOT EXISTS ("
                  "SELECT 1 FROM listing_watch w WHERE w.uid=alert_outbox_v2.uid "
                  "AND w.key=alert_outbox_v2.subject_key "
                  "AND w.kind IN ('급매','찐매물','일반매물')))")
    c = db.conn()
    try:
        cur = c.execute(f"UPDATE alert_outbox_v2 SET seen_at=? WHERE {where}",
                        (int(time.time()), uid))
        c.commit()
        return cur.rowcount
    finally:
        c.close()
