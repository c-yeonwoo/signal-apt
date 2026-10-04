import json

import pytest
from fastapi import HTTPException

from realty_signal import db
from realty_signal.routes import reports_v2
from realty_signal.services import decision_notes_v2 as notes


def test_notes_are_owned_versioned_and_recover_no_old_text_after_delete():
    saved = notes.create(12, "region", "노원구", {
        "thesis": "거래 회복을 지켜본다", "counter_condition": "거래가 다시 줄면 재검토",
        "horizon_weeks": 12, "report_id": "report-1",
    })
    assert notes.list_for(13) == []
    assert notes.update(13, saved["id"], 1, {**saved, "thesis": "다른 계정"}) is None
    revised = notes.update(12, saved["id"], 1, {**saved, "thesis": "가격 인하를 지켜본다"})
    assert revised["revision"] == 2
    assert notes.update(12, saved["id"], 1, {**saved, "thesis": "경합 변경"}) is None
    c = db.conn()
    try:
        assert c.execute("SELECT count(*) FROM decision_note_revisions_v2 WHERE note_id=?",
                         (saved["id"],)).fetchone()[0] == 2
    finally:
        c.close()
    assert notes.delete(13, saved["id"]) is False
    assert notes.delete(12, saved["id"]) is True
    assert notes.list_for(12) == []
    c = db.conn()
    try:
        assert c.execute("SELECT count(*) FROM decision_note_revisions_v2 WHERE note_id=?",
                         (saved["id"],)).fetchone()[0] == 0
    finally:
        c.close()


def test_note_pages_reach_older_entries_with_equal_timestamps_and_scope_cursors():
    c = db.conn()
    try:
        c.executemany(
            "INSERT INTO decision_notes_v2(uid,subject_type,subject_key,thesis,counter_condition,"
            "horizon_weeks,report_id,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(12, "region", "노원구", f"판단 {i}", "재검토", 12, None, 1, 100, 100)
             for i in range(205)] +
            [(13, "region", "노원구", "다른 사용자", "재검토", 12, None, 1, 100, 100)],
        )
        c.commit()
    finally:
        c.close()
    first = notes.list_page(12, "region", "노원구")
    second = notes.list_page(12, "region", "노원구", first["next_cursor"])
    third = notes.list_page(12, "region", "노원구", second["next_cursor"])
    ids = [item["id"] for page in (first, second, third) for item in page["notes"]]
    assert [len(page["notes"]) for page in (first, second, third)] == [100, 100, 5]
    assert len(set(ids)) == 205
    assert ids == sorted(ids, reverse=True)
    assert third["next_cursor"] is None
    for uid, kind, key in ((13, "region", "노원구"), (12, "listing", "노원구"),
                           (12, "region", "다른구"), (12, None, None)):
        with pytest.raises(ValueError, match="invalid_cursor"):
            notes.list_page(uid, kind, key, first["next_cursor"])
    for cursor in ("", "bad cursor"):
        with pytest.raises(ValueError, match="invalid_cursor"):
            notes.list_page(12, "region", "노원구", cursor)


def test_notes_route_rejects_invalid_page_cursor(monkeypatch):
    monkeypatch.setattr(reports_v2.deps, "uid", lambda _request: 12)
    with pytest.raises(HTTPException) as error:
        reports_v2.decision_notes_list(None, "region", "노원구", "not-a-cursor")
    assert error.value.status_code == 422
    response = reports_v2.decision_notes_list(None, "region", "노원구")
    assert json.loads(response.body) == {"notes": [], "next_cursor": None}


def test_notes_route_explains_invalid_fields_without_exposing_error_codes(monkeypatch):
    monkeypatch.setattr(reports_v2.deps, "uid", lambda _request: 12)
    monkeypatch.setattr(reports_v2, "_note_subject", lambda *_args: None)
    with pytest.raises(HTTPException) as error:
        reports_v2.decision_note_create(None, {
            "subject_type": "region", "subject_key": "노원구", "thesis": " ",
            "counter_condition": "가격 하락", "horizon_weeks": 12,
        })
    assert error.value.status_code == 422
    assert error.value.detail == "관심 이유를 1~500자로 입력해 주세요."
