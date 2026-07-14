"""퍼널 이벤트·주간 다이제스트 단위 테스트."""

from __future__ import annotations

from realty_signal import db
from realty_signal.digest import build_user_digest


def test_event_log_whitelist(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    db._migrated[0] = False
    assert db.event_log(1, "signup", {}) is True
    assert db.event_log(1, "hack_me", {}) is False
    assert db.event_log(None, "signup", {}) is False
    counts = {r["name"]: r["count"] for r in db.event_counts(30)}
    assert counts.get("signup") == 1


def test_build_user_digest_with_changes():
    changes = [
        {"region": "강남구", "old": "WATCH", "new": "BUY", "direction": "▲매수기회"},
        {"region": "부산", "old": "BUY", "new": "NEUTRAL", "direction": "▼매도경고"},
    ]
    d = build_user_digest(
        "a@b.com",
        ["강남구", "마포구"],
        changes,
        {"강남구": "BUY", "마포구": "WATCH"},
        "2026-07-14",
    )
    assert d["email"] == "a@b.com"
    assert "변동 1건" in d["subject"] or "강남구" in d["body"]
    assert "강남구: WATCH → BUY" in d["body"]
    assert "마포구: WATCH (변화 없음)" in d["body"]
    assert len(d["changes"]) == 1
