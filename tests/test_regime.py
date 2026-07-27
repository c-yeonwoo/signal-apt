"""수도권 급지역전(끝물) — A→E 계단·evidence 단위 테스트."""

from __future__ import annotations

import pandas as pd

from realty_signal.signals.regime import compute_regime


class _FakeKB:
    def __init__(self, rises: dict[str, float]):
        self._rises = rises

    def series(self, region: str, kind: str):
        assert kind == "sale_change"
        v = self._rises.get(region)
        if v is None:
            return pd.Series(dtype=float)
        return pd.Series([v / 8.0] * 8)


def _loc(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame([{"region": r, "price": p} for r, p in rows])


# 35곳(급지당 7) — 표본 가드(_MIN_METRO=30) 통과 + E 중간가 정상(~900)
_PRICES = (
    [("연천군", 700), ("가평군", 800), ("포천시", 900), ("동두천시", 1000),
     ("여주시", 1050), ("안성시", 1100), ("이천시", 1150)]  # E
    + [("양주시", 1400), ("오산시", 1500), ("평택시", 1550), ("파주시", 1600),
       ("동두천외", 1650), ("강화군", 1700), ("옹진군", 1750)]  # D
    + [("구리시", 2100), ("하남시", 2200), ("광명시", 2300), ("군포시", 2400),
       ("의왕시", 2450), ("과천외", 2500), ("시흥시", 2550)]  # C
    + [("마포구", 3200), ("영등포구", 3400), ("양천구", 3600), ("성동구", 3800),
       ("동작구", 3900), ("관악구", 4000), ("은평구", 4100)]  # B
    + [("용산구", 4500), ("송파구", 4800), ("서초구", 5200), ("강남구", 5600),
       ("분당구", 5000), ("과천시", 5400), ("용산외", 4700)]  # A
)


def _codes():
    out = {}
    for r, _ in _PRICES:
        out[r] = "11000" if r.endswith("구") else "41000"
    return out


def _rises_by_band(bands: dict[str, float]) -> dict[str, float]:
    """가격 밴드로 의도한 급지 rise 부여 (E~A)."""
    rises = {}
    for r, p in _PRICES:
        if p < 1200:
            rises[r] = bands["E"]
        elif p < 1900:
            rises[r] = bands["D"]
        elif p < 2800:
            rises[r] = bands["C"]
        elif p < 4200:
            rises[r] = bands["B"]
        else:
            rises[r] = bands["A"]
    return rises


def test_endgame_when_rise_climbs_a_to_e():
    rises = _rises_by_band({"A": 0.1, "B": 0.4, "C": 0.9, "D": 1.6, "E": 2.4})
    out = compute_regime(_FakeKB(rises), _loc(_PRICES), _codes(), window=8)
    assert out["quality"] == "ok"
    assert out["phase"] == "끝물(매도 경고)"
    assert out["endgame"] is True
    assert out["ascents"] >= 3
    assert out["gap"] > 0
    assert out["regions"]["강남구"]["급지"] == "A"
    assert out["regions"]["연천군"]["급지"] == "E"
    ev = out["evidence"]
    assert [t["급지"] for t in ev["ladder"]] == list("ABCDE")
    assert all(d["급지"] in ("D", "E") for d in ev["drivers"])


def test_normal_phase_has_no_evidence():
    rises = _rises_by_band({"A": 2.5, "B": 1.5, "C": 0.6, "D": 0.3, "E": 0.1})
    out = compute_regime(_FakeKB(rises), _loc(_PRICES), _codes(), window=8)
    assert out["quality"] == "ok"
    assert out["phase"] == "상급지 주도"
    assert out["endgame"] is False
    assert out["evidence"] == {}


def test_caution_not_full_endgame():
    # 평탄 구간 포함 상승 계단 2칸
    rises = _rises_by_band({"A": 0.4, "B": 0.4, "C": 0.9, "D": 0.9, "E": 1.5})
    out = compute_regime(_FakeKB(rises), _loc(_PRICES), _codes(), window=8)
    assert out["quality"] == "ok"
    assert out["gap"] > 0
    assert out["endgame"] is False
    assert out["phase"] == "하급지 순환(끝물 주의)"
    assert out["ascents"] == 2
    assert out["descents"] == 0


def test_zigzag_is_not_endgame():
    rises = _rises_by_band({"A": 0.8, "B": 1.0, "C": 1.2, "D": 0.2, "E": 2.0})
    out = compute_regime(_FakeKB(rises), _loc(_PRICES), _codes(), window=8)
    assert out["descents"] >= 1
    assert out["phase"] != "끝물(매도 경고)"
    assert out["endgame"] is False


def test_skewed_sample_blocks_endgame():
    # 외곽 없이 중저가(2100+)만 52곳 — E 중간가 > 2000 → 끝물 보류
    prices = [(f"지역{i}", 2100 + i * 50) for i in range(52)]
    rises = {r: (3.0 if p < 3500 else 0.5) for r, p in prices}
    codes = {r: "41000" for r, _ in prices}
    out = compute_regime(_FakeKB(rises), _loc(prices), codes, window=8)
    assert out["quality"] == "skewed"
    assert out["e_median_price"] and out["e_median_price"] > 2000
    assert out["phase"] == "급지 표본 주의"
    assert out["endgame"] is False
    assert not any(v.get("막차") for v in out["regions"].values())


def test_regime_empty_without_localities():
    assert compute_regime(_FakeKB({}), pd.DataFrame(), {}) == {}
