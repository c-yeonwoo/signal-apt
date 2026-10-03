"""Alert Engine · Outcome 스냅샷 테스트."""

from __future__ import annotations

from realty_signal import db
from realty_signal.brain import alerts, calibrate, config_store, outcomes


def test_alert_merge_prefs():
    p = alerts.merge_prefs({"high_timing": False, "timing_min": 80})
    assert p["high_timing"] is False
    assert p["signal_upgrade"] is True
    assert p["timing_min"] == 80


def test_filter_signal_upgrade_only_up():
    changes = [
        {"region": "강남구", "from": "WATCH", "to": "BUY", "date": "2026-07-14"},
        {"region": "마포구", "from": "BUY", "to": "WATCH", "date": "2026-07-14"},
    ]
    out = alerts.filter_signal_changes(changes, {"강남구", "마포구"}, upgrade_only=True)
    assert len(out) == 1
    assert out[0]["region"] == "강남구"


def test_high_timing_listings():
    items = alerts.high_timing_listings([
        {"지역": "강남구", "단지명": "A", "유형": "급매", "타이밍점수": 75},
        {"지역": "부산", "단지명": "B", "유형": "급매", "타이밍점수": 90},
    ], {"강남구"}, timing_min=70)
    assert len(items) == 1
    assert items[0]["name"] == "A"


def test_evaluate_payload():
    payload = alerts.evaluate(
        {"강남구"},
        {},
        signal_changes=[{"region": "강남구", "from": "WATCH", "to": "BUY", "date": "2026-07-15"}],
        signal_map={"강남구": "BUY"},
        listings=[],
        nbhd_diffs={},
        seen_before="2026-07-14",
    )
    assert payload["unread"] >= 1
    assert payload["digest"][0]["region"] == "강남구"


def test_raw_upgrade_history_does_not_notify_when_current_assessment_is_held_or_changed():
    history = [{"region": "강남구", "from": "WATCH", "to": "BUY", "date": "2026-07-15"}]
    for current in ("HELD", "WATCH"):
        payload = alerts.evaluate({"강남구"}, {}, signal_changes=history,
                                  signal_map={"강남구": current}, seen_before="2026-07-14")
        assert payload["changes"][0]["to"] == "BUY"  # immutable raw history
        assert payload["changes"][0]["current_signal"] == current
        assert payload["changes"][0]["actionable"] is False
        assert payload["digest"] == [{"region": "강남구", "signal": current}]
        assert payload["unread"] == 0
    ready = alerts.evaluate({"강남구"}, {}, signal_changes=history,
                            signal_map={"강남구": "BUY"}, seen_before="2026-07-14")
    assert ready["changes"][0]["actionable"] is True
    assert ready["unread"] == 1


def test_alert_route_uses_current_safe_signal_map(monkeypatch):
    from realty_signal import api as app_api
    from realty_signal.routes import alerts as alerts_route

    monkeypatch.setattr(alerts_route.deps, "uid", lambda request: 7)
    monkeypatch.setattr(db, "fav_list", lambda uid: [{"kind": "region", "key": "강남구"}])
    monkeypatch.setattr(db, "kv_get", lambda key: (
        [{"region": "강남구", "from": "WATCH", "to": "BUY", "date": "2026-07-15"}]
        if key == "signal_changes" else "2026-07-14"))
    monkeypatch.setattr(db, "alert_prefs_get", lambda uid: {"high_timing": False})
    monkeypatch.setattr(app_api, "_user_nbhd_diffs", lambda uid, favs: {})
    monkeypatch.setattr(app_api, "_display_signal_map", lambda: {"강남구": "HELD"})
    monkeypatch.setattr(alerts_route.md, "signal_map", lambda: (_ for _ in ()).throw(
        AssertionError("raw signal map must not reach alerts")))

    payload = alerts_route.alerts(object())
    assert payload["changes"][0]["actionable"] is False
    assert payload["digest"] == [{"region": "강남구", "signal": "HELD"}]
    assert payload["unread"] == 0


def test_config_store_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "c.db")
    db._migrated[0] = False
    from realty_signal.brain.evaluation import params_hash
    params = config_store.config_to_dict(config_store.config_from_dict({"demand_buy": 18}))
    db.kv_set("evaluation:synthetic", {"promotable": True, "config_hash": params_hash(params)})
    applied = config_store.apply_config("v2-test", params, note="test", validation_id="synthetic")
    assert applied["version"] == "v2-test"
    assert config_store.active_config().demand_buy == 18
    assert config_store.list_history()[0]["version"] == "v2-test"


def test_calibrate_proposal_structure():
    from realty_signal.ingest.kb_weekly import KBWeekly
    import pandas as pd

    dates = pd.date_range("2020-01-01", periods=80, freq="W")
    rows = []
    for d in dates:
        for region in ("강남구",):
            for metric, val in (("sale_change", 0.1), ("jeonse_supply", 150.0),
                                ("buyer_superiority", 55.0), ("buyer_demand", 8.0)):
                rows.append({"date": d, "region": region, "metric": metric, "value": val})
    kb = KBWeekly(long=pd.DataFrame(rows), codes={"강남구": "11680"})
    prop = calibrate.build_proposal(kb)
    assert "suggestions" in prop
    assert "baseline_hits" in prop
    assert prop.get("disclaimer")


def test_outcome_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "o.db")
    db._migrated[0] = False
    r = outcomes.append_region_snapshot("2026-07-14", [
        {"region": "강남구", "signal": "BUY", "전세수급": 150},
    ])
    assert r["regions"] == 1
    assert outcomes.list_snapshots()[0]["asof"] == "2026-07-14"
