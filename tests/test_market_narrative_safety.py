"""Observed indicators must not be narrated as observed buyer migration or returns."""

import pandas as pd

from realty_signal.ingest.kb_weekly import KBWeekly
from realty_signal.signals.engine import SignalConfig, evaluate, interpret


def test_high_jeonse_index_is_not_claimed_as_observed_sale_conversion():
    text = interpret("STRONG_BUY", "매매전이", 85, "강함", "상승", 0.5, SignalConfig())
    assert "앱의 최상위 구간" in text
    assert "매매 전환·향후 가격 상승은 이 지표만으로 확인되지" in text
    assert "매매로 강하게 전이" not in text


def test_combined_jeonse_and_volume_notes_preserve_grade_without_causal_claim():
    dates = pd.date_range("2026-09-07", periods=4, freq="W-MON")
    rows = [(day, "테스트구", metric, value) for day in dates for metric, value in (
        ("sale_change", 0.3), ("jeonse_change", 0.3),
        ("jeonse_supply", 195), ("buyer_superiority", 85), ("buyer_demand", 25))]
    kb = KBWeekly(pd.DataFrame(rows, columns=["date", "region", "metric", "value"]),
                  {"테스트구": "11110"}, identity_verified=True)
    result = evaluate(kb, SignalConfig(), volumes={"테스트구": {"거래량비": 1.3}})
    row = result.iloc[0]
    assert row["signal"] == "STRONG_BUY"
    assert row["전세상태"] == "전세수급 매우 높음"
    assert row["매수상태"] == "매수세 강함(참고)"
    assert "매매 전환 미확인" in row["근거"]
    assert "거래량만으로 매수 주체" in row["해설"]
    assert "실수요 기반 바닥" not in row["해설"]
