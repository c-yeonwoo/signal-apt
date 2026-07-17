"""Alert Accountability (Phase 5) 테스트."""

from __future__ import annotations

import pandas as pd

from realty_signal import db
from realty_signal.brain import alert_track
from realty_signal.ingest.kb_weekly import KBWeekly


def _kb_with_sale(region: str, weekly_pct: list[float], start: str = "2026-01-04") -> KBWeekly:
    """주간 매매증감률(%) 시퀀스로만 이뤄진 합성 KB."""
    dates = pd.date_range(start, periods=len(weekly_pct), freq="W")
    rows = [
        {"date": d, "region": region, "metric": "sale_change", "value": v}
        for d, v in zip(dates, weekly_pct)
    ]
    return KBWeekly(long=pd.DataFrame(rows), codes={})


def test_score_change_hit_up():
    # 알림 발생 시점 이후 12주 동안 꾸준히 상승 → 승급(up) 예측이 적중해야 함
    weekly = [0.0] + [0.3] * 20
    kb = _kb_with_sale("강남구", weekly)
    change = {"region": "강남구", "from": "WATCH", "to": "BUY", "date": str(kb.series("강남구", "sale_change").index[0].date())}
    r = alert_track.score_change(kb, change, horizon_weeks=12)
    assert r is not None
    assert r["pending"] is False
    assert r["direction"] == "up"
    assert r["hit"] is True
    assert r["pct"] > 0


def test_score_change_miss_down_direction():
    # 강등(down) 알림인데 실제로는 계속 올랐다 → miss
    weekly = [0.0] + [0.3] * 20
    kb = _kb_with_sale("마포구", weekly)
    change = {"region": "마포구", "from": "BUY", "to": "WATCH", "date": str(kb.series("마포구", "sale_change").index[0].date())}
    r = alert_track.score_change(kb, change, horizon_weeks=12)
    assert r is not None
    assert r["direction"] == "down"
    assert r["hit"] is False


def test_score_change_pending_when_insufficient_future_data():
    # 발생일 이후 12주치가 아직 안 쌓임 → pending
    weekly = [0.0] + [0.1] * 5
    kb = _kb_with_sale("송파구", weekly)
    change = {"region": "송파구", "from": "WATCH", "to": "BUY", "date": str(kb.series("송파구", "sale_change").index[0].date())}
    r = alert_track.score_change(kb, change, horizon_weeks=12)
    assert r is not None
    assert r["pending"] is True
    assert "hit" not in r


def test_score_change_none_for_unscorable():
    weekly = [0.0] * 20
    kb = _kb_with_sale("강남구", weekly)
    # 방향 판정 불가(등급 랭크 동일) → None
    same = {"region": "강남구", "from": "WATCH", "to": "WATCH", "date": "2026-01-04"}
    assert alert_track.score_change(kb, same) is None
    # region 정보 없음 → None
    assert alert_track.score_change(kb, {"from": "WATCH", "to": "BUY", "date": "2026-01-04"}) is None
    # 지역 데이터 없음 → None
    missing_region = {"region": "부산", "from": "WATCH", "to": "BUY", "date": "2026-01-04"}
    assert alert_track.score_change(kb, missing_region) is None


def test_track_record_aggregates_hit_rate():
    weekly = [0.0] + [0.3] * 20
    kb = _kb_with_sale("강남구", weekly)
    d0 = str(kb.series("강남구", "sale_change").index[0].date())
    changes = [
        {"region": "강남구", "from": "WATCH", "to": "BUY", "date": d0},   # up, hit
        {"region": "강남구", "from": "BUY", "to": "WATCH", "date": d0},   # down, miss
        {"region": "강남구", "from": "WATCH", "to": "WATCH", "date": d0},  # 방향없음 → skipped
    ]
    tr = alert_track.track_record(kb, changes, horizon_weeks=12)
    assert tr["scored"] == 2
    assert tr["skipped"] == 1
    assert tr["hit_rate"] == 50.0
    assert tr["hit_rate_up"] == 100.0
    assert tr["hit_rate_down"] == 0.0
    assert len(tr["recent"]) == 2


def test_track_record_counts_pending():
    weekly = [0.0] + [0.1] * 3
    kb = _kb_with_sale("강남구", weekly)
    d0 = str(kb.series("강남구", "sale_change").index[0].date())
    changes = [{"region": "강남구", "from": "WATCH", "to": "BUY", "date": d0}]
    tr = alert_track.track_record(kb, changes, horizon_weeks=12)
    assert tr["scored"] == 0
    assert tr["pending"] == 1
    assert tr["hit_rate"] is None


def test_feedback_summary_aggregates_events(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "fb.db")
    db._migrated[0] = False
    db.event_log(1, "alert_feedback", {"kind": "signal_upgrade", "useful": True})
    db.event_log(1, "alert_feedback", {"kind": "signal_upgrade", "useful": False})
    db.event_log(1, "alert_feedback", {"kind": "high_timing", "useful": True})
    summary = alert_track.feedback_summary(days=90)
    assert summary["total"] == 3
    assert summary["by_kind"]["signal_upgrade"] == {"useful": 1, "not_useful": 1}
    assert summary["by_kind"]["high_timing"] == {"useful": 1, "not_useful": 0}


def test_feedback_kind_whitelist_rejects_unknown():
    assert "signal_upgrade" in alert_track.FEEDBACK_KINDS
    assert "high_timing" in alert_track.FEEDBACK_KINDS
    assert "nbhd_change" in alert_track.FEEDBACK_KINDS
    assert "bogus_kind" not in alert_track.FEEDBACK_KINDS
