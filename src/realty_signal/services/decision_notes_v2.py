"""User-owned, revisioned reasons for watching a region or listing."""

from __future__ import annotations

import base64
import binascii
import json
import time
from hashlib import sha256

from realty_signal import db

PAGE_SIZE = 100


def _scope(uid: int, subject_type: str | None, subject_key: str | None) -> str:
    return sha256(json.dumps([uid, subject_type, subject_key], separators=(",", ":")).encode()).hexdigest()


def _cursor_encode(updated_at: int, note_id: int, scope: str) -> str:
    raw = json.dumps({"updated_at": updated_at, "id": note_id, "scope": scope},
                     separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_decode(cursor: str, scope: str) -> tuple[int, int]:
    try:
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 512 or not cursor.isascii():
            raise ValueError("invalid_cursor")
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        payload = json.loads(raw)
        updated_at, note_id = payload["updated_at"], payload["id"]
        if (set(payload) != {"updated_at", "id", "scope"} or payload["scope"] != scope
                or type(updated_at) is not int or updated_at < 0
                or type(note_id) is not int or note_id < 1):
            raise ValueError("invalid_cursor")
        return updated_at, note_id
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error) as exc:
        raise ValueError("invalid_cursor") from exc


def _clean(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("invalid_note")
    thesis, counter = data.get("thesis"), data.get("counter_condition")
    weeks = data.get("horizon_weeks")
    if not isinstance(thesis, str) or not 1 <= len(thesis.strip()) <= 500:
        raise ValueError("invalid_thesis")
    if not isinstance(counter, str) or not 1 <= len(counter.strip()) <= 500:
        raise ValueError("invalid_counter_condition")
    if type(weeks) is not int or weeks not in {4, 12, 26}:
        raise ValueError("invalid_horizon")
    report_id = data.get("report_id")
    if report_id is not None and (not isinstance(report_id, str) or len(report_id) > 100):
        raise ValueError("invalid_report_id")
    return {"thesis": thesis.strip(), "counter_condition": counter.strip(),
            "horizon_weeks": weeks, "report_id": report_id}


def create(uid: int, subject_type: str, subject_key: str, data: dict) -> dict:
    fields = _clean(data)
    now = int(time.time())
    c = db.conn()
    try:
        cur = c.execute(
            "INSERT INTO decision_notes_v2(uid,subject_type,subject_key,thesis,counter_condition,"
            "horizon_weeks,report_id,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (uid, subject_type, subject_key, fields["thesis"], fields["counter_condition"],
             fields["horizon_weeks"], fields["report_id"], 1, now, now))
        fields.update({"id": cur.lastrowid, "uid": uid, "subject_type": subject_type,
                       "subject_key": subject_key, "revision": 1, "created_at": now,
                       "updated_at": now})
        c.execute("INSERT INTO decision_note_revisions_v2 VALUES(?,?,?)",
                  (cur.lastrowid, 1, json.dumps(fields, ensure_ascii=False)))
        c.commit()
        return fields
    finally:
        c.close()


def list_page(uid: int, subject_type: str | None = None, subject_key: str | None = None,
              cursor: str | None = None) -> dict:
    scope = _scope(uid, subject_type, subject_key)
    position = _cursor_decode(cursor, scope) if cursor is not None else None
    c = db.conn()
    try:
        sql = ("SELECT id,subject_type,subject_key,thesis,counter_condition,horizon_weeks,"
               "report_id,revision,created_at,updated_at FROM decision_notes_v2 WHERE uid=?")
        args = [uid]
        if subject_type:
            sql += " AND subject_type=?"; args.append(subject_type)
        if subject_key:
            sql += " AND subject_key=?"; args.append(subject_key)
        if position:
            sql += " AND (updated_at<? OR (updated_at=? AND id<?))"
            args.extend([position[0], position[0], position[1]])
        sql += " ORDER BY updated_at DESC,id DESC LIMIT ?"
        args.append(PAGE_SIZE + 1)
        rows = c.execute(sql, args).fetchall()
        items = [dict(zip(("id", "subject_type", "subject_key", "thesis", "counter_condition",
                           "horizon_weeks", "report_id", "revision", "created_at", "updated_at"), row))
                 for row in rows[:PAGE_SIZE]]
        next_cursor = (_cursor_encode(rows[PAGE_SIZE - 1][-1], rows[PAGE_SIZE - 1][0], scope)
                       if len(rows) > PAGE_SIZE else None)
        return {"notes": items, "next_cursor": next_cursor}
    finally:
        c.close()


def list_for(uid: int, subject_type: str | None = None, subject_key: str | None = None) -> list[dict]:
    """Compatibility for callers that need only the first page."""
    return list_page(uid, subject_type, subject_key)["notes"]


def update(uid: int, note_id: int, revision: int, data: dict) -> dict | None:
    fields = _clean(data)
    now = int(time.time())
    c = db.conn()
    try:
        cur = c.execute("UPDATE decision_notes_v2 SET thesis=?,counter_condition=?,horizon_weeks=?,"
                        "report_id=?,revision=revision+1,updated_at=? WHERE id=? AND uid=? AND revision=?",
                        (fields["thesis"], fields["counter_condition"], fields["horizon_weeks"],
                         fields["report_id"], now, note_id, uid, revision))
        if cur.rowcount != 1:
            c.rollback()
            return None
        current = c.execute(
            "SELECT id,subject_type,subject_key,thesis,counter_condition,horizon_weeks,"
            "report_id,revision,created_at,updated_at FROM decision_notes_v2 WHERE id=?", (note_id,)
        ).fetchone()
        row = dict(zip(("id", "subject_type", "subject_key", "thesis", "counter_condition",
                        "horizon_weeks", "report_id", "revision", "created_at", "updated_at"), current))
        c.execute("INSERT INTO decision_note_revisions_v2 VALUES(?,?,?)",
                  (note_id, row["revision"], json.dumps(row, ensure_ascii=False)))
        c.commit()
        return row
    finally:
        c.close()


def delete(uid: int, note_id: int) -> bool:
    c = db.conn()
    try:
        cur = c.execute("DELETE FROM decision_notes_v2 WHERE id=? AND uid=?", (note_id, uid))
        if cur.rowcount:
            c.execute("DELETE FROM decision_note_revisions_v2 WHERE note_id=?", (note_id,))
        c.commit()
        return bool(cur.rowcount)
    finally:
        c.close()
