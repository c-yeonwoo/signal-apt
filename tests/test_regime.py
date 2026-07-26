"""수도권 급지역전(끝물) 국면 — evidence(주도 지역·상승폭) 단위 테스트."""

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
        # window=8 합이 v 가 되도록 균등 분배
        return pd.Series([v / 8.0] * 8)


def _loc(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame([{"region": r, "price": p} for r, p in rows])


def test_endgame_evidence_lists_low_tier_drivers():
    # 평단가: 싼 곳(D/C)이 비싸 곳(A/B)보다 훨씬 더 오르면 β<0 · 끝물
    prices = [
        ("가평군", 800), ("연천군", 900), ("포천시", 1000), ("동두천시", 1100),
        ("구리시", 2000), ("하남시", 2200), ("광명시", 2400), ("과천시", 2600),
        ("강남구", 5000), ("서초구", 4800), ("송파구", 4500), ("용산구", 4300),
        ("성동구", 4000), ("마포구", 3800), ("영등포구", 3600), ("양천구", 3400),
    ]
    # 하급지(싼 쪽) rise 높게
    rises = {r: (2.5 if p < 1500 else 1.8 if p < 3000 else 0.2) for r, p in prices}
    codes = {r: ("41" if r.endswith(("군", "시")) else "11") + "000" for r, _ in prices}
    # 서울 구는 11, 경기 시군은 41
    for r, _ in prices:
        codes[r] = "41000" if r.endswith(("군", "시")) else "11000"

    out = compute_regime(_FakeKB(rises), _loc(prices), codes, window=8)
    assert out["phase"] in ("끝물(매도 경고)", "하급지 순환(끝물 주의)")
    assert out["beta"] < 0
    assert out["gap"] > 0
    ev = out["evidence"]
    assert ev["window"] == 8
    assert ev["하급지평균"] is not None and ev["상급지평균"] is not None
    assert ev["하급지평균"] > ev["상급지평균"]
    assert len(ev["drivers"]) >= 1
    assert all(d["급지"] in ("C", "D") for d in ev["drivers"])
    assert "rise" in ev["drivers"][0] and "region" in ev["drivers"][0]
    assert len(ev["상급지참고"]) >= 1
    assert all(a["급지"] in ("A", "B") for a in ev["상급지참고"])
    # 막차 플래그가 있으면 drivers 앞쪽에 우선
    if any(d.get("막차") for d in ev["drivers"]):
        assert ev["drivers"][0]["막차"] is True


def test_normal_phase_has_no_evidence():
    # 상급지가 훨씬 더 오르면 β>0 — evidence 비움(Nick 오해 방지)
    prices = [
        ("가평군", 800), ("연천군", 900), ("포천시", 1000), ("동두천시", 1100),
        ("구리시", 2000), ("하남시", 2200), ("광명시", 2400), ("과천시", 2600),
        ("강남구", 5000), ("서초구", 4800), ("송파구", 4500), ("용산구", 4300),
        ("성동구", 4000), ("마포구", 3800), ("영등포구", 3600), ("양천구", 3400),
    ]
    rises = {r: (0.1 if p < 3000 else 2.5) for r, p in prices}
    codes = {r: ("41000" if r.endswith(("군", "시")) else "11000") for r, _ in prices}
    out = compute_regime(_FakeKB(rises), _loc(prices), codes, window=8)
    assert out["beta"] >= 0
    assert out["phase"] in ("상급지 주도", "광역 확산")
    assert out["evidence"] == {}


def test_regime_empty_without_localities():
    assert compute_regime(_FakeKB({}), pd.DataFrame(), {}) == {}
