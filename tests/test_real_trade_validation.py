"""V-2 실거래 검증의 룩어헤드·시장기준선 규칙."""

import json

import pandas as pd

from realty_signal import real_trade_validation as v2


class _KB:
    codes = {"A구": "11110", "B구": "11170"}

    @staticmethod
    def latest():
        return pd.DataFrame(index=["A구", "B구"])


def _write(cache, lawd, ym, price, transactions=10):
    path = cache / lawd / f"{ym}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"lawd": lawd, "ym": ym, "median_ppy": price,
                                "transactions": transactions}), encoding="utf-8")


def test_v2_uses_month_after_signal_end_and_same_month_market_baseline(tmp_path, monkeypatch):
    # 2020-12 종료 신호는 2021-01 → 2021-04만 본다. 종료월 12월의 200은 사용하면 안 된다.
    for lawd, dec, jan, apr in [("11110", 200, 100, 110), ("11170", 50, 100, 100)]:
        _write(tmp_path, lawd, "202012", dec)
        _write(tmp_path, lawd, "202101", jan)
        _write(tmp_path, lawd, "202104", apr)
    monkeypatch.setattr(v2, "signal_history", lambda *_a, **_k: [
        {"signal": "STRONG_BUY", "end": "2020-12-25"},
    ])

    result = v2.summary(_KB(), tmp_path, horizon_months=3, min_transactions=1)
    row = result["by_signal"][0]

    assert row["평가수"] == 2
    assert row["상승률"] == 50.0
    assert row["시장평균"] == 50.0
    assert row["평균수익"] == 5.0
    assert row["시장평균수익"] == 5.0


def test_v2_skips_months_with_too_few_transactions(tmp_path, monkeypatch):
    _write(tmp_path, "11110", "202101", 100, transactions=1)
    _write(tmp_path, "11110", "202104", 120, transactions=1)
    monkeypatch.setattr(v2, "signal_history", lambda *_a, **_k: [
        {"signal": "BUY", "end": "2020-12-25"},
    ])

    result = v2.summary(_KB(), tmp_path, min_transactions=5)

    assert result["ready"] is False
    assert result["coverage"]["가격시계열지역수"] == 0
