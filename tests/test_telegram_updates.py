"""Telegram templates, source-gated reminders, and successful-send deduplication."""

from __future__ import annotations

import json
import time

import pytest

from realty_signal import auth, db, telegram
from realty_signal.services import complex_watch, presale_alerts_v2, telegram_templates, telegram_updates
from realty_signal.time_kst import today_kst


@pytest.fixture
def linked(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "telegram.db")
    db._migrated[0] = False
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test:token")
    monkeypatch.delenv("INVITE_CODES", raising=False)
    monkeypatch.delenv("STUDENT_ALLOWLIST", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    token, error = auth.signup("owner@example.com", "secret1", accept_tos=True)
    assert error is None
    uid = db.session_user(token)["id"]
    db.profile_set(uid, {"telegram": {"chat_id": 777, "linked_at": int(time.time()) - 10}})
    return uid


def _presale(uid: int, *, date: str | None = None):
    key = "청약:notice-1"
    db.listing_watch_add(uid, {"key": key, "유형": "청약", "단지명": "새봄아파트",
                               "지역": "노원구", "총액": None})
    deadline = date or today_kst().isoformat()
    count = presale_alerts_v2.materialize(
        uid, [{"key": key, "name": "새봄아파트", "region": "노원구"}],
        [{"key": key, "유형": "청약", "지표값": "접수예정",
          "ref": {"다음일정": deadline}, "source_checked_at": time.time()}],
        today=today_kst())
    return count


def test_presale_event_sends_once_and_does_not_mark_app_alert_seen(linked, monkeypatch):
    assert _presale(linked) == 1
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda chat, body: sent.append((chat, body)) or True)
    first = telegram_updates.run()
    assert first["sent"] == 1 and first["events"] == 1
    assert sent[0][0] == 777
    assert "[청약 일정] 새봄아파트" in sent[0][1]
    assert "자격·접수 가능 여부는 모집공고" in sent[0][1]
    assert telegram_updates.run()["sent"] == 0 and len(sent) == 1
    c = db.conn()
    try:
        assert c.execute("SELECT seen_at FROM alert_outbox_v2 WHERE uid=?", (linked,)).fetchone() == (None,)
    finally:
        c.close()


def test_failed_send_retries_without_advancing_delivery(linked, monkeypatch):
    assert _presale(linked) == 1
    monkeypatch.setattr(telegram, "send_message", lambda *_: False)
    assert telegram_updates.run()["errors"] == 1
    c = db.conn()
    try:
        assert c.execute("SELECT COUNT(*) FROM telegram_alert_delivery").fetchone()[0] == 0
    finally:
        c.close()
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda chat, body: sent.append(body) or True)
    assert telegram_updates.run()["sent"] == 1 and len(sent) == 1


def test_old_or_relinked_presale_event_is_not_replayed(linked, monkeypatch):
    assert _presale(linked) == 1
    profile = db.profile_get(linked)
    profile["telegram"]["linked_at"] = int(time.time()) + 1
    db.profile_set(linked, profile)
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda chat, body: sent.append(body) or True)
    assert telegram_updates.run()["sent"] == 0
    assert sent == []


def test_private_listing_event_respects_current_owner_access(linked, monkeypatch):
    key = "일반매물:private-1"
    db.listing_watch_add(linked, {"key": key, "유형": "일반매물", "단지명": "비공개단지",
                                  "지역": "노원구", "총액": 50000})
    c = db.conn()
    try:
        c.execute("INSERT INTO alert_outbox_v2 VALUES(?,?,?,?,?,?,?,?,NULL)",
                  ("event-private", linked, "listing", key, "listing_price", "1",
                   json.dumps({"name": "비공개단지", "old_price": 55000, "new_price": 50000}),
                   int(time.time())))
        c.commit()
    finally:
        c.close()
    monkeypatch.setattr("realty_signal.config.personal_listing_allowed", lambda email: False)
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda chat, body: sent.append(body) or True)
    assert telegram_updates.run()["sent"] == 0 and sent == []


def test_complex_update_has_first_snapshot_then_change_without_duplication(linked, monkeypatch):
    monkeypatch.setattr(complex_watch, "favorites_of", lambda uid: [("kb:1135000000", "새봄아파트")])
    monkeypatch.setattr(complex_watch, "cache_loader", lambda: None)
    latest = {"price": 50000}

    def scan(_favorites, _loader, previous):
        key = "kb:1135000000|새봄아파트"
        old = previous.get(key)
        changes = ([{"말": "주력 평형 거래가 0.1억 상승"}]
                   if old and old["price"] != latest["price"] else [])
        return ([{"key": key, "단지명": "새봄아파트", "지역": "노원구",
                  "data_days": 0.1, "changes": changes}], {key: dict(latest)}, [])

    monkeypatch.setattr(complex_watch, "scan", scan)
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda chat, body: sent.append(body) or True)
    assert telegram_updates.run()["sent"] == 0
    latest["price"] = 51000
    assert telegram_updates.run()["complexes"] == 1
    assert "관심단지 변화" in sent[0] and "30일 내 신고" in sent[0]
    assert telegram_updates.run()["sent"] == 0


def test_stale_complex_is_not_pushed_and_removed_favorite_clears_baseline(linked, monkeypatch):
    key = "kb:1135000000|새봄아파트"
    monkeypatch.setattr(complex_watch, "favorites_of", lambda uid: [("kb:1135000000", "새봄아파트")])
    monkeypatch.setattr(complex_watch, "cache_loader", lambda: None)
    monkeypatch.setattr(complex_watch, "scan", lambda *_: (
        [{"key": key, "단지명": "새봄아파트", "지역": "노원구", "data_days": 14,
          "changes": [{"말": "오래된 변화"}]}], {key: {"price": 51000}}, []))
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda chat, body: sent.append(body) or True)
    assert telegram_updates.run()["sent"] == 0 and sent == []
    assert db.kv_get(telegram_updates.SNAP_PREFIX + str(linked)) == {}
    db.kv_set(telegram_updates.SNAP_PREFIX + str(linked), {key: {"price": 50000}})
    monkeypatch.setattr(complex_watch, "favorites_of", lambda uid: [])
    assert telegram_updates.run()["sent"] == 0
    assert db.kv_get(telegram_updates.SNAP_PREFIX + str(linked)) == {}


def test_template_is_plain_and_distinguishes_asking_from_sale():
    text = telegram_templates.render_updates([], [{"kind": "listing_price", "payload": {
        "name": "테스트단지", "region": "노원구", "old_price": 80000, "new_price": 79000}}])
    assert "호가 변화" in text and "수집 호가 변화" in text
    assert "현재 판매 여부는 확인되지 않았습니다" in text
    assert "실거래가" not in text
