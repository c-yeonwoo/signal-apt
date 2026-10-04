"""Korean calendar boundaries must not depend on the deployment host timezone."""

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from realty_signal import api, auction, store, time_kst, weekly
from realty_signal.ingest import complex as complex_ingest, volume
from realty_signal.services import discovery_occupancy
from realty_signal.services import market_data as md
from realty_signal.services import signal_assessment as sa
from realty_signal.routes import reports_v2
from realty_signal.signals.engine import SignalConfig
from realty_signal.time_kst import previous_months, today_kst
from test_signal_assessment_v2 import _kb, _row


def test_today_kst_crosses_day_before_utc_midnight():
    assert today_kst(datetime(2026, 10, 4, 14, 59, tzinfo=timezone.utc)) == date(2026, 10, 4)
    assert today_kst(datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)) == date(2026, 10, 5)
    with pytest.raises(ValueError, match="aware datetime"):
        today_kst(datetime(2026, 10, 5, 0, 0))


def test_completed_month_windows_follow_korean_month_boundary(monkeypatch):
    before = datetime(2026, 9, 30, 14, 59, tzinfo=timezone.utc)
    after = datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)
    assert previous_months(3, before) == ["202608", "202607", "202606"]
    assert previous_months(3, after) == ["202609", "202608", "202607"]
    monkeypatch.setattr(time_kst, "today_kst", lambda now=None: date(2026, 10, 1))
    assert store._recent_months(3) == ["202609", "202608", "202607"]
    assert auction._recent_yms(3) == ["202609", "202608", "202607"]
    assert volume._yms(3) == ["202607", "202608", "202609"]
    monkeypatch.setattr(complex_ingest, "today_kst", lambda: date(2026, 10, 1))
    trades = [{"ym": "2026-04", "amt": 40000}, {"ym": "2026-05", "amt": 50000}]
    assert complex_ingest.comparison_evidence(trades)["중앙값"] == 50000


def test_weekly_staleness_uses_korean_day(monkeypatch):
    monkeypatch.setattr("realty_signal.time_kst.today_kst", lambda: date(2026, 10, 5))
    assert weekly._stale_days("2026-09-27") == 8
    assert weekly._stale_days("2026-09-26") == 9


def test_kb_data_age_uses_korean_midnight_not_server_clock(monkeypatch):
    monkeypatch.setattr(md, "kb", lambda: SimpleNamespace(last_date=datetime(2026, 9, 27)))
    monkeypatch.setattr(time_kst, "now_kst", lambda: datetime(2026, 10, 5, 0, tzinfo=time_kst.KST))
    assert md.data_age_days() == 8.0


def test_immediate_move_in_expires_at_korean_midnight(monkeypatch):
    monkeypatch.setattr(discovery_occupancy, "today_kst", lambda: date(2026, 10, 5))
    row = {"status": "immediate", "date": "2026-10-05", "checked_at": 1}
    assert discovery_occupancy._usable(row)
    assert not discovery_occupancy._usable({**row, "date": "2026-10-04"})


def test_display_signal_assessment_receives_korean_date(monkeypatch):
    seen = []
    monkeypatch.setattr(api, "today_kst", lambda: date(2026, 10, 5))
    monkeypatch.setattr(api, "_signal_map", lambda: {"노원구": "BUY"})
    monkeypatch.setattr(api.md, "assessed_signal_labels", lambda day: seen.append(day) or {
        "노원구": {"display_signal": "BUY", "assessment_status": "ready"}})
    assert api._display_signal_map() == {"노원구": "BUY"}
    assert seen == ["2026-10-05"]


def test_region_report_and_issued_assessment_use_korean_day_at_stale_boundary(monkeypatch):
    kb = _kb()
    monkeypatch.setattr(sa, "today_kst", lambda: date(2026, 10, 7))
    direct = sa.build(kb, _row(), SignalConfig(), asof=date(2026, 9, 28))
    assert direct["assessment_status"] == "held"
    assert "source_stale" in direct["risk_flags"]

    monkeypatch.setattr(md, "kb", lambda: kb)
    monkeypatch.setattr(md, "signals_df", lambda: pd.DataFrame([_row()]))
    monkeypatch.setattr(md, "signal_config", SignalConfig)
    monkeypatch.setattr(md, "region_for_ref", lambda ref: "중구")
    report = json.loads(reports_v2.region_report("중구").body)
    assert report["status"] == "held"
    assert report["assessment"]["display_grade"] == "판단 보류"
