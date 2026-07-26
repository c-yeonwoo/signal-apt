"""Nick 데일리 브리핑 — 변화가 있을 때만, 변화한 것만."""

from __future__ import annotations

import pytest

from realty_signal import api as app_api
from realty_signal import auth, briefing, db
from realty_signal.services import shortlist as sl

GRADES = {
    "노원구": [{"단지": "상계주공7", "평단가": 3000, "급지": 1, "상위": 10},
             {"단지": "중계그린", "평단가": 2800, "급지": 2, "상위": 30}],
    "금천구": [{"단지": "독산한양", "평단가": 2600, "급지": 1, "상위": 15}],
}


@pytest.fixture()
def uid(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "app.db")
    db._migrated[0] = False
    for k in ("INVITE_CODES", "STUDENT_ALLOWLIST", "RAILWAY_ENVIRONMENT", "APP_ENV"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(app_api, "_signal_map", lambda: {"노원구": "BUY", "금천구": "BUY"})
    monkeypatch.setattr(app_api, "_region_grades", lambda r: GRADES.get(r, []))
    monkeypatch.setattr(sl, "_locality_map",
                        lambda: {"노원구": {"region": "노원구", "저평가도": 10},
                                 "금천구": {"region": "금천구", "저평가도": 8}})
    monkeypatch.setattr(sl, "_region_commute", lambda region, work: None)
    monkeypatch.setattr(briefing, "QUICKSALE_FILE", tmp_path / "none.json")
    token, err = auth.signup("brief@example.com", "secret1", accept_tos=True)
    assert err is None
    u = db.session_user(token)["id"]
    db.profile_set(u, {"가용자본": 50000, "연소득": 8000,
                       "매수력": {"최대매수가": 90000}, "관심평수": "84"})
    return u


def test_no_budget_is_skipped(uid):
    db.profile_set(uid, {})
    assert briefing.build(uid) == {"send": False, "reason": "no_budget"}


def test_first_briefing_lists_candidates(uid):
    b = briefing.build(uid)
    assert b["send"] and b["first"]
    assert "이번 주 볼 단지" in b["text"]
    assert "상계주공7" in b["text"]
    assert "오늘 할 일" in b["text"]


def test_unchanged_day_is_not_sent(uid):
    first = briefing.build(uid)
    db.kv_set(briefing.SNAP_KEY.format(uid=uid), first["snapshot"])
    again = briefing.build(uid)
    if briefing.today_kst().weekday() == 0:      # 월요일은 변화가 없어도 발송
        assert again["send"]
    else:
        assert again == {"send": False, "reason": "no_news", "snapshot": again["snapshot"]}


def test_force_sends_even_without_change(uid):
    db.kv_set(briefing.SNAP_KEY.format(uid=uid), briefing.build(uid)["snapshot"])
    assert briefing.build(uid, force=True)["send"]


def test_new_candidate_is_reported(uid):
    snap = briefing.build(uid)["snapshot"]
    snap["candidates"].pop("노원구|상계주공7")
    db.kv_set(briefing.SNAP_KEY.format(uid=uid), snap)
    b = briefing.build(uid)
    assert b["send"] and "후보 변화" in b["text"]
    assert "+ 상계주공7" in b["text"]
    assert "상계주공7" in b["text"].split("오늘 할 일")[1]   # 할 일도 신규 후보를 가리킨다


def test_dropped_candidate_is_reported(uid):
    snap = briefing.build(uid)["snapshot"]
    snap["candidates"]["노원구|사라진단지"] = 80000
    db.kv_set(briefing.SNAP_KEY.format(uid=uid), snap)
    b = briefing.build(uid)
    assert "- 사라진단지 (노원구)" in b["text"]


def test_price_move_is_reported(uid):
    snap = briefing.build(uid)["snapshot"]
    key = next(iter(snap["candidates"]))
    snap["candidates"][key] -= 3000
    db.kv_set(briefing.SNAP_KEY.format(uid=uid), snap)
    b = briefing.build(uid)
    assert "▲3,000만" in b["text"]


def test_tiny_price_move_is_noise(uid):
    snap = briefing.build(uid)["snapshot"]
    key = next(iter(snap["candidates"]))
    snap["candidates"][key] -= briefing.MOVE_MIN - 1
    db.kv_set(briefing.SNAP_KEY.format(uid=uid), snap)
    b = briefing.build(uid)
    assert b.get("news", 0) == 0


def test_signal_change_is_reported(uid, monkeypatch):
    snap = briefing.build(uid)["snapshot"]
    snap["signals"]["노원구"] = "WATCH"
    db.kv_set(briefing.SNAP_KEY.format(uid=uid), snap)
    b = briefing.build(uid)
    assert "↑ 노원구: 관망 → 매수" in b["text"]


def test_imminent_bid_leads_the_todo(uid, monkeypatch, tmp_path):
    from datetime import timedelta

    from realty_signal import auction

    monkeypatch.setattr(auction, "AUCTION_FILE", tmp_path / "auction.json")
    day = (briefing.today_kst() + timedelta(days=1)).isoformat()
    auction.add({"단지명": "상계주공7", "region": "노원구", "사건번호": "2024타경51234",
                 "감정가": 85000, "최저매각가": 54400, "입찰기일": day})
    b = briefing.build(uid, force=True)
    assert "[경매]" in b["text"] and "D-1 입찰" in b["text"]
    assert "상계주공7 입찰가 확정" in b["text"].split("오늘 할 일 → ")[1]


def test_far_off_bid_is_not_shown(uid, monkeypatch, tmp_path):
    from datetime import timedelta

    from realty_signal import auction

    monkeypatch.setattr(auction, "AUCTION_FILE", tmp_path / "auction.json")
    auction.add({"단지명": "먼단지", "region": "노원구",
                 "입찰기일": (briefing.today_kst() + timedelta(days=30)).isoformat()})
    assert "먼단지" not in briefing.build(uid, force=True)["text"]


def test_post_win_step_appears(uid, monkeypatch, tmp_path):
    from datetime import timedelta

    from realty_signal import auction

    monkeypatch.setattr(auction, "AUCTION_FILE", tmp_path / "auction.json")
    won = (briefing.today_kst() - timedelta(days=13)).isoformat()   # D+14 매각허가확정이 내일
    auction.add({"단지명": "낙찰단지", "region": "노원구", "감정가": 85000,
                 "최저매각가": 54400, "낙찰가": 60000, "낙찰일": won})
    t = briefing.build(uid, force=True)["text"]
    assert "낙찰단지" in t and "매각허가결정 확정" in t


def test_run_sends_and_moves_snapshot(uid, monkeypatch):
    from realty_signal import telegram

    db.profile_set(uid, {**db.profile_get(uid), "telegram": {"chat_id": 55}})
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda cid, txt: sent.append((cid, txt)) or True)
    stats = briefing.run(send=True, quiet=True)
    assert stats["sent"] == 1 and sent[0][0] == 55
    assert briefing.run(send=True, quiet=True)["skipped"] == 1   # 두 번째는 변화 없음


def test_run_failure_keeps_snapshot_for_retry(uid, monkeypatch):
    from realty_signal import telegram

    db.profile_set(uid, {**db.profile_get(uid), "telegram": {"chat_id": 55}})
    monkeypatch.setattr(telegram, "send_message", lambda cid, txt: False)
    assert briefing.run(send=True, quiet=True)["errors"] == 1
    assert db.kv_get(briefing.SNAP_KEY.format(uid=uid)) is None

    monkeypatch.setattr(telegram, "send_message", lambda cid, txt: True)
    assert briefing.run(send=True, quiet=True)["sent"] == 1


def test_dry_run_does_not_move_snapshot(uid, monkeypatch):
    db.profile_set(uid, {**db.profile_get(uid), "telegram": {"chat_id": 55}})
    stats = briefing.run(send=False, quiet=True)
    assert stats["dry_run"] == 1
    assert db.kv_get(briefing.SNAP_KEY.format(uid=uid)) is None
