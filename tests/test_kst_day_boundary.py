"""Korean calendar boundaries must not depend on the deployment host timezone."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from realty_signal import api, time_kst, weekly
from realty_signal.services import discovery_occupancy
from realty_signal.services import market_data as md
from realty_signal.time_kst import today_kst


def test_today_kst_crosses_day_before_utc_midnight():
    assert today_kst(datetime(2026, 10, 4, 14, 59, tzinfo=timezone.utc)) == date(2026, 10, 4)
    assert today_kst(datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)) == date(2026, 10, 5)
    with pytest.raises(ValueError, match="aware datetime"):
        today_kst(datetime(2026, 10, 5, 0, 0))


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
