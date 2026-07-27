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


# 20곳 — 5분위가 각 4곳. 가격 800→5000 균등.
_PRICES = [
    ("연천군", 800), ("가평군", 900), ("포천시", 1000), ("동두천시", 1100),  # E
    ("이천시", 1400), ("안성시", 1500), ("여주시", 1600), ("양주시", 1700),  # D
    ("구리시", 2200), ("하남시", 2300), ("광명시", 2400), ("군포시", 2500),  # C
    ("마포구", 3400), ("영등포구", 3600), ("양천구", 3800), ("성동구", 4000),  # B
    ("용산구", 4300), ("송파구", 4500), ("서초구", 4800), ("강남구", 5000),  # A
]


def _codes():
    return {r: ("41000" if r.endswith(("군", "시")) else "11000") for r, _ in _PRICES}


def test_endgame_when_rise_climbs_a_to_e():
    # A→E 로 상승률이 갈수록 커짐
    tier_rise = {"A": 0.1, "B": 0.4, "C": 0.9, "D": 1.6, "E": 2.4}
    # 가격 순으로 E←…←A 배정되므로 가격대별 rise 지정
    rises = {}
    for r, p in _PRICES:
        if p < 1200:
            rises[r] = tier_rise["E"]
        elif p < 1900:
            rises[r] = tier_rise["D"]
        elif p < 2800:
            rises[r] = tier_rise["C"]
        elif p < 4200:
            rises[r] = tier_rise["B"]
        else:
            rises[r] = tier_rise["A"]

    out = compute_regime(_FakeKB(rises), _loc(_PRICES), _codes(), window=8)
    assert out["phase"] == "끝물(매도 경고)"
    assert out["endgame"] is True
    assert out["ascents"] >= 3
    assert out["gap"] > 0
    grades = {v["급지"] for v in out["regions"].values()}
    assert grades == set("ABCDE")
    # E는 최저가 쪽 — 서울 강남이 E일 수 없음
    assert out["regions"]["강남구"]["급지"] == "A"
    assert out["regions"]["연천군"]["급지"] == "E"
    ev = out["evidence"]
    assert ev["ladder"]
    assert [t["급지"] for t in ev["ladder"]] == list("ABCDE")
    assert all(d["급지"] in ("D", "E") for d in ev["drivers"])
    assert all(a["급지"] == "A" for a in ev["상급지참고"])


def test_normal_phase_has_no_evidence():
    # 상급지가 더 오름 → 하락 계단, evidence 없음
    rises = {}
    for r, p in _PRICES:
        if p < 1200:
            rises[r] = 0.1
        elif p < 1900:
            rises[r] = 0.3
        elif p < 2800:
            rises[r] = 0.6
        elif p < 4200:
            rises[r] = 1.5
        else:
            rises[r] = 2.5
    out = compute_regime(_FakeKB(rises), _loc(_PRICES), _codes(), window=8)
    assert out["phase"] == "상급지 주도"
    assert out["endgame"] is False
    assert out["evidence"] == {}


def test_caution_not_full_endgame():
    # 계단 2칸 + 하급지>상급지 → 주의 (endgame False)
    rises = {}
    for r, p in _PRICES:
        if p < 1200:
            rises[r] = 1.5
        elif p < 1900:
            rises[r] = 1.4
        elif p < 2800:
            rises[r] = 0.5
        elif p < 4200:
            rises[r] = 0.4
        else:
            rises[r] = 0.2
    out = compute_regime(_FakeKB(rises), _loc(_PRICES), _codes(), window=8)
    assert "끝물" in out["phase"]
    assert out["ascents"] >= 2
    # 빨간 끝물은 계단 3+ 필요 — 이 데이터는 주의일 수 있음
    if out["ascents"] < 3:
        assert out["endgame"] is False
        assert "주의" in out["phase"]


def test_regime_empty_without_localities():
    assert compute_regime(_FakeKB({}), pd.DataFrame(), {}) == {}
