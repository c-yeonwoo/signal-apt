"""Alert Engine · Outcome 스냅샷 테스트."""

from __future__ import annotations

from realty_signal import db
from realty_signal.brain import alerts, outcomes


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


def test_outcome_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "o.db")
    db._migrated[0] = False
    r = outcomes.append_region_snapshot("2026-07-14", [
        {"region": "강남구", "signal": "BUY", "전세수급": 150},
    ])
    assert r["regions"] == 1
    snaps = outcomes.list_snapshots()
    assert snaps[0]["asof"] == "2026-07-14"
