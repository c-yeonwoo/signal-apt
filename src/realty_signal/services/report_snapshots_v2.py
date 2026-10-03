"""Explicitly saved, immutable personal listing and region reports.

The payload is server-generated. Reading a historical copy never refreshes a
listing, but private-source permission must still be valid at read time.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from hashlib import sha256

from realty_signal import db

PRIVATE_LISTING_KINDS = frozenset({"일반매물", "급매", "찐매물"})
PRIVATE_KINDS = PRIVATE_LISTING_KINDS | {"비교:개인"}
PAGE_SIZE = 50


def _scope(uid: int, key: str | None, private_allowed: bool) -> str:
    return sha256(json.dumps([uid, key or "", private_allowed], separators=(",", ":")).encode()).hexdigest()


def _cursor_encode(saved_at: int, report_id: str, scope: str) -> str:
    raw = json.dumps({"saved_at": saved_at, "report_id": report_id, "scope": scope},
                     separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_decode(cursor: str, scope: str) -> tuple[int, str]:
    try:
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 512 or not cursor.isascii():
            raise ValueError("invalid_cursor")
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        payload = json.loads(raw)
        saved_at, report_id = payload["saved_at"], payload["report_id"]
        if (set(payload) != {"saved_at", "report_id", "scope"} or payload["scope"] != scope
                or type(saved_at) is not int or saved_at < 0 or not isinstance(report_id, str)
                or len(report_id) != 64 or any(ch not in "0123456789abcdef" for ch in report_id)):
            raise ValueError("invalid_cursor")
        return saved_at, report_id
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error) as exc:
        raise ValueError("invalid_cursor") from exc


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


def list_page(uid: int, *, key: str | None = None, private_allowed: bool,
              cursor: str | None = None) -> dict:
    scope = _scope(uid, key, private_allowed)
    position = _cursor_decode(cursor, scope) if cursor else None
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
        if position:
            sql += " AND (saved_at<? OR (saved_at=? AND report_id<?))"
            args.extend([position[0], position[0], position[1]])
        sql += " ORDER BY saved_at DESC,report_id DESC LIMIT ?"
        args.append(PAGE_SIZE + 1)
        rows = c.execute(sql, args).fetchall()
        result = []
        for report_id, subject_key, kind, data, saved_at in rows[:PAGE_SIZE]:
            report = json.loads(data)
            result.append({"report_id": report_id, "subject_key": subject_key, "kind": kind,
                           "name": ((report.get("subject") or {}).get("name") or
                                    (report.get("subject") or {}).get("region") or
                                    f"{len(report.get('items') or [])}개 매물 비교"),
                           "asof": report.get("asof"), "saved_at": saved_at})
        next_cursor = (_cursor_encode(rows[PAGE_SIZE - 1][4], rows[PAGE_SIZE - 1][0], scope)
                       if len(rows) > PAGE_SIZE else None)
        return {"items": result, "next_cursor": next_cursor,
                "policy": {"retention": "until_deleted", "sharing": "private_only",
                           "backup_note": "삭제 후에도 순환 백업에는 일정 기간 남을 수 있습니다."}}
    finally:
        c.close()


def list_for(uid: int, *, key: str | None = None, private_allowed: bool) -> list[dict]:
    """Compatibility for callers that only need the first page."""
    return list_page(uid, key=key, private_allowed=private_allowed)["items"]


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
