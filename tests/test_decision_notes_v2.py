from realty_signal import db
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
