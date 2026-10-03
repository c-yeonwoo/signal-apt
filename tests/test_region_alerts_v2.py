"""Issued region changes are not current recommendations."""

import json

from realty_signal import db
from realty_signal.services import region_alerts_v2 as alerts


def _assessment(identity="first", *, asof="2026-09-21", grade="매수", value=180,
                status="ready", risks=None, passing=True):
    return {"assessment_id": identity, "region_id": "kb:11350", "region": "노원구",
            "asof": asof, "display_grade": grade, "assessment_status": status,
            "risk_flags": risks or [], "version": "v1", "config_hash": "rules1",
            "guard_version": "g1", "reasons": [{"reason_id": "jeonse_pressure",
                                                 "label": "전세수급 압력", "value": value,
                                                 "unit": "지수", "passing": passing}]}


def _issue(*items):
    c = db.conn()
    try:
        for item in items:
            c.execute("INSERT INTO signal_assessments VALUES(?,?,?,?,?,?)",
                      (item["assessment_id"], item["region_id"], item["region"],
                       item["asof"], len(item["assessment_id"]),
                       json.dumps(item, ensure_ascii=False)))
        c.commit()
    finally:
        c.close()


def test_first_issued_assessment_is_baseline_then_change_once_only(monkeypatch):
    monkeypatch.setattr(db, "_favorite_region_name", lambda key: "노원구" if key == "kb:11350" else None)
    db.fav_add(7, "region", "kb:11350", "노원구")
    first = _assessment()
    second = _assessment("second", asof="2026-09-28", grade="관망", value=160,
                         passing=False)
    _issue(first, second)
    assert alerts.materialize(7, "kb:11350", first) == 0
    assert alerts.materialize(7, "kb:11350", second) == 1
    assert alerts.materialize(7, "kb:11350", second) == 0
    assert alerts.list_events(7)["unread"] == 1
    event = alerts.list_events(7)["items"][0]
    assert event["payload"]["old_grade"] == "매수"
    assert event["payload"]["new_grade"] == "관망"
    assert event["payload"]["changed_reasons"][0]["threshold_crossed"] is True
    assert alerts.list_events(8)["items"] == []
    assert alerts.mark_seen(7) == 1
    assert alerts.list_events(7)["unread"] == 0


def test_muted_rule_advances_baseline_and_favorite_removal_clears_history(monkeypatch):
    monkeypatch.setattr(db, "_favorite_region_name", lambda key: "노원구" if key == "kb:11350" else None)
    db.fav_add(7, "region", "kb:11350", "노원구")
    first = _assessment()
    second = _assessment("second", asof="2026-09-28", value=182)
    _issue(first, second)
    alerts.materialize(7, "kb:11350", first)
    assert alerts.materialize(7, "kb:11350", second,
                              prefs={"region_evidence": False}) == 0
    assert alerts.materialize(7, "kb:11350", second) == 0
    third = _assessment("third", asof="2026-10-05", value=184)
    _issue(third)
    assert alerts.materialize(7, "kb:11350", third) == 1
    db.fav_remove(7, "region", "kb:11350")
    assert alerts.list_events(7) == {"items": [], "unread": 0}
    db.fav_add(7, "region", "kb:11350", "노원구")
    assert alerts.materialize(7, "kb:11350", third) == 0


def test_age_only_revision_and_older_source_do_not_alert():
    db.fav_add(7, "region", "kb:11350", "노원구")
    first = _assessment()
    old = _assessment("older", asof="2026-09-14", value=150)
    stale = _assessment("stale", status="held", grade="판단 보류",
                        risks=["source_stale"])
    _issue(first, old, stale)
    alerts.materialize(7, "kb:11350", first)
    assert alerts.materialize(7, "kb:11350", old) == 0
    assert alerts.materialize(7, "kb:11350", stale) == 0
    assert alerts.list_events(7)["unread"] == 0


def test_scan_uses_verified_favorites_and_issued_snapshots_only(monkeypatch):
    db.fav_add(7, "region", "kb:11350", "노원구")
    db.fav_add(8, "region", "중구", "중구")
    monkeypatch.setattr(db, "verified_region_favorite_identity", lambda key: (
        {"region_id": "kb:11350", "name": "노원구"} if key == "kb:11350" else None))
    first = _assessment()
    _issue(first)
    assert alerts.scan_issued() == 0
    second = _assessment("second", asof="2026-09-28", value=182)
    _issue(second)
    assert alerts.scan_issued() == 1
    assert alerts.scan_issued() == 0
    assert alerts.list_events(7)["unread"] == 1
    assert alerts.list_events(8)["unread"] == 0


def test_region_event_hidden_if_identity_is_no_longer_verified(monkeypatch):
    db.fav_add(7, "region", "kb:11350", "노원구")
    first, second = _assessment(), _assessment("second", asof="2026-09-28", value=182)
    _issue(first, second)
    alerts.materialize(7, "kb:11350", first)
    alerts.materialize(7, "kb:11350", second)
    monkeypatch.setattr(db, "_favorite_region_name", lambda key: None)
    assert alerts.list_events(7) == {"items": [], "unread": 0}
    assert alerts.mark_seen(7) == 0


def test_missing_favorite_does_not_create_orphan_event():
    assert alerts.materialize(7, "kb:11350", _assessment()) == 0
    c = db.conn()
    try:
        assert c.execute("SELECT COUNT(*) FROM region_watch_state_v2").fetchone()[0] == 0
    finally:
        c.close()


def test_alert_api_includes_issued_region_change_and_marks_seen(monkeypatch):
    from realty_signal import api
    from realty_signal.routes import alerts as routes

    db.fav_add(7, "region", "kb:11350", "노원구")
    monkeypatch.setattr(db, "_favorite_region_name", lambda key: "노원구" if key == "kb:11350" else None)
    first, second = _assessment(), _assessment("second", asof="2026-09-28", value=182)
    _issue(first, second)
    alerts.materialize(7, "kb:11350", first)
    alerts.materialize(7, "kb:11350", second)
    monkeypatch.setattr(routes.deps, "uid", lambda request: 7)
    monkeypatch.setattr(routes.deps, "personal_listings_allowed", lambda request: False)
    monkeypatch.setattr(db, "actionable_region_favs", lambda uid: [])
    monkeypatch.setattr(api, "_display_signal_map", lambda: {})
    result = routes.alerts(None)
    assert result["unread"] == 1
    assert result["region_events"][0]["payload"]["region"] == "노원구"
    assert routes.alerts_seen(None) == {"ok": True}
    assert routes.alerts(None)["unread"] == 0
