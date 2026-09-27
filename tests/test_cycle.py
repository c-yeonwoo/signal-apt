"""현재 사이클 배지와 과거 밴드의 일관성 회귀 테스트."""

import pandas as pd

from realty_signal.signals.cycle import current_phase, cycle_history


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
    current = current_phase(kb)
    bands = cycle_history(kb)
    assert current["phase"] == "관망"
    assert bands[-1]["phase"] == current["phase"]
    assert current["demand"]["now"] is None


def test_current_and_history_use_same_phase_even_with_opposite_sentiment():
    kb = WeeklyKB([0.2] * 80, [120.0] * 76 + [80.0] * 4)
    current = current_phase(kb)
    assert current["demand"]["dir"] == "둔화"
    assert current["phase"] == cycle_history(kb)[-1]["phase"] == "상승기"


def test_new_neutral_band_not_swallowed_by_previous_trend():
    kb = WeeklyKB([0.2] * 80 + [0.0] * 32)
    current = current_phase(kb)
    assert current["phase"] == cycle_history(kb)[-1]["phase"] == "관망"


def test_insufficient_history_does_not_guess_phase():
    assert current_phase(WeeklyKB([0.2] * 25)) is None
    assert cycle_history(WeeklyKB([0.2] * 25)) == []


def test_missing_region_does_not_borrow_seoul_bands(monkeypatch):
    from realty_signal import api

    class RegionalKB(WeeklyKB):
        def series(self, region, metric):
            return super().series(region, metric) if region == "서울" else pd.Series(dtype=float)

    monkeypatch.setattr(api, "_kb", lambda: RegionalKB([0.2] * 80))
    result = api.cycle_history("경기")
    assert result == {"region": "경기", "bands": []}
