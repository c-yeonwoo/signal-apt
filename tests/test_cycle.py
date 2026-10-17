"""현재 사이클 배지와 과거 밴드의 일관성 회귀 테스트."""

from datetime import timedelta

import pandas as pd

from realty_signal.signals.cycle import current_phase, cycle_history
from realty_signal.signals.freshness import MAX_KB_OBSERVATION_AGE_DAYS


class WeeklyKB:
    def __init__(self, changes, sentiment=None):
        dates = pd.date_range("2024-01-01", periods=len(changes), freq="W")
        self.sale = pd.Series(changes, index=dates)
        self.sentiment = pd.Series(sentiment or [], index=dates[:len(sentiment or [])], dtype=float)

    def series(self, region, metric):
        if metric == "sale_change":
            return self.sale
        if metric == "buyer_superiority":
            return self.sentiment
        return pd.Series(dtype=float)


def test_flat_market_is_neutral_without_sentiment():
    kb = WeeklyKB([0.0] * 80)
    current = current_phase(kb, today=kb.sale.index[-1].date())
    bands = cycle_history(kb)
    assert current["phase"] == "관망"
    assert bands[-1]["phase"] == current["phase"]
    assert current["demand"]["now"] is None


def test_current_and_history_use_same_phase_even_with_opposite_sentiment():
    kb = WeeklyKB([0.2] * 80, [120.0] * 76 + [80.0] * 4)
    current = current_phase(kb, today=kb.sale.index[-1].date())
    assert current["demand"]["dir"] == "둔화"
    assert current["phase"] == cycle_history(kb)[-1]["phase"] == "상승기"


def test_new_neutral_band_not_swallowed_by_previous_trend():
    kb = WeeklyKB([0.2] * 80 + [0.0] * 32)
    current = current_phase(kb, today=kb.sale.index[-1].date())
    assert current["phase"] == cycle_history(kb)[-1]["phase"] == "관망"


def test_insufficient_history_does_not_guess_phase():
    assert current_phase(WeeklyKB([0.2] * 25)) is None
    assert cycle_history(WeeklyKB([0.2] * 25)) == []


def test_current_phase_holds_after_shared_kb_freshness_boundary():
    kb = WeeklyKB([0.2] * 80)
    asof = kb.sale.index[-1].date()
    ready = current_phase(kb, today=asof + timedelta(days=MAX_KB_OBSERVATION_AGE_DAYS))
    held = current_phase(kb, today=asof + timedelta(days=MAX_KB_OBSERVATION_AGE_DAYS + 1))
    assert ready["status"] == "ready" and ready["phase"] == "상승기"
    assert held["status"] == "held" and held["phase"] is None
    assert held["observed_phase"] == "상승기" and held["asof"] == asof.isoformat()
    assert current_phase(kb, today=asof - timedelta(days=1))["status"] == "held"


def test_companion_sentiment_from_another_week_is_not_called_current():
    kb = WeeklyKB([0.2] * 80, [120.0] * 76 + [80.0] * 3)
    current = current_phase(kb, today=kb.sale.index[-1].date())
    assert current["status"] == "ready" and current["demand"]["now"] is None
    assert any("자료 없음" in reason for reason in current["reasons"])


def test_missing_region_does_not_borrow_seoul_bands(monkeypatch):
    from realty_signal import api

    class RegionalKB(WeeklyKB):
        def series(self, region, metric):
            return super().series(region, metric) if region == "서울" else pd.Series(dtype=float)

    monkeypatch.setattr(api, "_kb", lambda: RegionalKB([0.2] * 80))
    result = api.cycle_history("경기")
    assert result == {"region": "경기", "bands": []}
