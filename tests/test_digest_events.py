"""퍼널 이벤트·주간 다이제스트 단위 테스트."""

from __future__ import annotations

from realty_signal import db
from realty_signal.digest import build_user_digest


def test_event_log_whitelist(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    db._migrated[0] = False
    assert db.event_log(1, "signup", {}) is True
    assert db.event_log(1, "listing_detail_open", {"kind": "급매"}) is True
    assert db.event_log(1, "hack_me", {}) is False
    assert db.event_log(None, "signup", {}) is False
    counts = {r["name"]: r["count"] for r in db.event_counts(30)}
    assert counts.get("signup") == 1
    assert counts.get("listing_detail_open") == 1


def test_new_funnel_events_are_allowed(tmp_path, monkeypatch):
    """2026-08-17 진단 — 못 재고 있던 세 가지를 심었다. 화이트리스트에 없으면 조용히 버려진다."""
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    db._migrated[0] = False
    for name in ("weekly_open", "buying_power_confirm", "evidence_open"):
        assert db.event_log(1, name, {}) is True, f"{name} 이 화이트리스트에 없다"
    counts = {r["name"]: r["count"] for r in db.event_counts(30)}
    assert counts.get("weekly_open") == 1
    assert counts.get("buying_power_confirm") == 1
    assert counts.get("evidence_open") == 1


def test_frontend_event_names_match_server_whitelist():
    """프론트 `EVENTS` 상수와 서버 화이트리스트가 갈라지면 이벤트가 조용히 유실된다.

    프론트가 서버에 없는 이름을 보내면 `event_log` 가 False 를 돌려주고 끝이다 —
    화면에는 아무 표시도 안 난다. 그래서 드리프트를 테스트로 막는다.
    """
    import re
    from pathlib import Path

    html = Path("src/realty_signal/web/index.html").read_text(encoding="utf-8")
    block = re.search(r"const EVENTS = \{(.*?)\};", html, re.S)
    assert block, "index.html 에서 EVENTS 상수를 찾지 못했다"
    names = set(re.findall(r"'([a-z_]+)'", block.group(1)))
    assert names, "EVENTS 상수가 비었다"
    missing = names - db._ALLOWED_EVENTS
    assert not missing, f"서버 화이트리스트에 없는 프론트 이벤트: {missing}"


def test_ranking_consumes_events_that_are_never_emitted():
    """인기도 랭킹의 입력 3개 중 2개는 아무도 발생시키지 않는다 — 현 상태를 기록해 둔다.

    `listing_click`·`timing_card_expand` 는 `brain/ranking.py` 가 집계하는데 emit 하는 곳이 없다.
    즉 인기도 가점은 사실상 `listing_detail_open` 하나로만 계산된다.
    고치는 방법은 둘 중 하나이고 아직 정하지 않았다 — (a) 두 이벤트를 실제로 심는다
    (b) `_RANK_EVENTS` 에서 뺀다. 어느 쪽이든 이 테스트가 먼저 깨져서 알려 준다.
    """
    import re
    from pathlib import Path

    from realty_signal.brain.ranking import _RANK_EVENTS

    html = Path("src/realty_signal/web/index.html").read_text(encoding="utf-8")
    emitted = set(re.findall(r"track\(\s*(?:EVENTS\.\w+|'([a-z_]+)')", html))
    emitted |= {
        v for v in re.findall(r"[A-Z_]+:\s*'([a-z_]+)'",
                              re.search(r"const EVENTS = \{(.*?)\};", html, re.S).group(1))
    }
    never = {e for e in _RANK_EVENTS if e not in emitted} - {""}
    assert never == {"listing_click", "timing_card_expand"}, (
        f"랭킹 입력 이벤트의 emit 상태가 바뀌었다: {never}"
    )


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
