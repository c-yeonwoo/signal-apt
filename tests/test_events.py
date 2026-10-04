"""Funnel event whitelist and frontend/server contract."""

from __future__ import annotations

import re
from pathlib import Path

from realty_signal import db


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
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    db._migrated[0] = False
    for name in ("weekly_open", "buying_power_confirm", "evidence_open"):
        assert db.event_log(1, name, {}) is True
    assert db.event_log(1, "report_task_feedback", {"type": "listing", "answer": "yes"}) is True
    assert db.event_log(1, "report_task_feedback", {"type": "listing", "answer": "no"}) is True
    assert db.event_log(2, "report_task_feedback", {"type": "listing", "answer": "yes"}) is True
    assert db.report_feedback_counts() == {"yes": {"count": 2, "users": 2},
                                           "no": {"count": 1, "users": 1}}
    counts = {r["name"]: r["count"] for r in db.event_counts(30)}
    assert counts.get("weekly_open") == 1
    assert counts.get("buying_power_confirm") == 1
    assert counts.get("evidence_open") == 1


def test_listing_funnel_counts_unique_accounts_without_identifiers(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "listing-events.db")
    db._migrated[0] = False
    for uid in (1, 2):
        assert db.event_log(uid, "listing_analysis_start", {})
        assert db.event_log(uid, "listing_analysis_ready", {"kind": "일반매물"})
    assert db.event_log(1, "listing_analysis_start", {})
    for event in ("listing_evidence_open", "listing_compare_open", "listing_commute_compare",
                  "listing_watch_add", "imjang_visit_save"):
        assert db.event_log(1, event, {})
    starts = next(x for x in db.event_counts(30) if x["name"] == "listing_analysis_start")
    assert starts["count"] == 3 and starts["users"] == 2


def test_frontend_event_names_match_server_whitelist():
    html = Path("src/realty_signal/web/index.html").read_text(encoding="utf-8")
    block = re.search(r"const EVENTS = \{(.*?)\};", html, re.S)
    assert block
    names = set(re.findall(r"'([a-z_]+)'", block.group(1)))
    assert names
    missing = names - db._ALLOWED_EVENTS
    assert not missing, f"서버 화이트리스트에 없는 프론트 이벤트: {missing}"


def test_ranking_consumes_events_that_are_never_emitted():
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
    assert never == {"listing_click", "timing_card_expand"}
