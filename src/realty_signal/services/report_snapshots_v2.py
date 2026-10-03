"""Explicitly saved, immutable personal listing and region reports.

The payload is server-generated. Reading a historical copy never refreshes a
listing, but private-source permission must still be valid at read time.
"""

from __future__ import annotations

import json
import time
from hashlib import sha256

from realty_signal import db

PRIVATE_LISTING_KINDS = frozenset({"일반매물", "급매", "찐매물"})
PRIVATE_KINDS = PRIVATE_LISTING_KINDS | {"비교:개인"}


def save(uid: int, report: dict) -> dict:
    subject = report.get("subject") or {}
    if report.get("type") == "region":
        key, kind = subject["region_id"], "지역"
    elif report.get("type") == "comparison":
        listings = [item.get("listing") or {} for item in report.get("items") or []]
        keys = [listing["key"] for listing in listings]
        key = "comparison:" + sha256(json.dumps(keys, ensure_ascii=False).encode()).hexdigest()
        kind = "비교:개인" if any(listing.get("kind") in PRIVATE_LISTING_KINDS for listing in listings) else "비교"
    else:
        key, kind = subject["key"], subject["kind"]
    report_id = report["report_id"]
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if len(payload.encode("utf-8")) > 250_000:
        raise ValueError("report_too_large")
    now = int(time.time())
    c = db.conn()
    try:
        c.execute("INSERT OR IGNORE INTO report_snapshots_v2"
                  "(uid,report_id,subject_key,kind,data,saved_at) VALUES(?,?,?,?,?,?)",
                  (uid, report_id, key, kind, payload, now))
        row = c.execute("SELECT saved_at FROM report_snapshots_v2 WHERE uid=? AND report_id=?",
                        (uid, report_id)).fetchone()
        c.commit()
        return {"report_id": report_id, "saved_at": row[0]}
    finally:
        c.close()


def list_for(uid: int, *, key: str | None = None, private_allowed: bool) -> list[dict]:
    c = db.conn()
    try:
        sql = "SELECT report_id,subject_key,kind,data,saved_at FROM report_snapshots_v2 WHERE uid=?"
        args: list = [uid]
        if key:
            sql += " AND subject_key=?"
            args.append(key)
        if not private_allowed:
            sql += " AND kind NOT IN (" + ",".join("?" for _ in PRIVATE_KINDS) + ")"
            args.extend(sorted(PRIVATE_KINDS))
        sql += " ORDER BY saved_at DESC,report_id DESC LIMIT 50"
        rows = c.execute(sql, args).fetchall()
        result = []
        for report_id, subject_key, kind, data, saved_at in rows:
            report = json.loads(data)
            result.append({"report_id": report_id, "subject_key": subject_key, "kind": kind,
                           "name": ((report.get("subject") or {}).get("name") or
                                    (report.get("subject") or {}).get("region") or
                                    f"{len(report.get('items') or [])}개 매물 비교"),
                           "asof": report.get("asof"), "saved_at": saved_at})
        return result
    finally:
        c.close()


def get(uid: int, report_id: str, *, private_allowed: bool) -> dict | None:
    c = db.conn()
    try:
        row = c.execute("SELECT kind,data,saved_at FROM report_snapshots_v2 "
                        "WHERE uid=? AND report_id=?", (uid, report_id)).fetchone()
    finally:
        c.close()
    if row is None or row[0] in PRIVATE_KINDS and not private_allowed:
        return None
    return {"report": json.loads(row[1]), "saved_at": row[2]}


def delete(uid: int, report_id: str) -> bool:
    """Owner may erase a copy even after source-read permission was revoked."""
    c = db.conn()
    try:
        changed = c.execute("DELETE FROM report_snapshots_v2 WHERE uid=? AND report_id=?",
                            (uid, report_id)).rowcount
        c.commit()
        return changed == 1
    finally:
        c.close()
