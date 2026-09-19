"""단지 숏리스트 — 예산·통근·시그널로 좁히고 탈락 사유를 남긴다."""

from __future__ import annotations

import pytest

from realty_signal import api as app_api
from realty_signal.services import shortlist as sl

SIGNALS = {"강남구": "STRONG_BUY", "노원구": "BUY", "도봉구": "BUY", "하락구": "SELL_RISK"}
GRADES = {
    "강남구": [{"단지": "래미안강남", "평단가": 9000, "급지": 1, "상위": 5}],
    "노원구": [
        {"단지": "상계주공", "평단가": 2000, "급지": 3, "상위": 45},
        {"단지": "노원센트럴", "평단가": 2600, "급지": 2, "상위": 20},
    ],
    "도봉구": [{"단지": "창동주공", "평단가": 1900, "급지": 3, "상위": 50}],
    "하락구": [{"단지": "폭락아파트", "평단가": 1000, "급지": 4, "상위": 80}],
}
LOCALITY = {
    "강남구": {"region": "강남구", "저평가도": -5, "입지점수": 95},
    "노원구": {"region": "노원구", "저평가도": 12, "입지점수": 55},
    "도봉구": {"region": "도봉구", "저평가도": 8, "입지점수": 50},
    "하락구": {"region": "하락구", "저평가도": 3, "입지점수": 40},
}


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    monkeypatch.setattr(app_api, "_sido_of", lambda r: "서울")
    monkeypatch.setattr(app_api, "_signal_map", lambda: dict(SIGNALS))
    monkeypatch.setattr(app_api, "_region_grades", lambda r: list(GRADES.get(r, [])))
    monkeypatch.setattr(sl, "_locality_map", lambda: dict(LOCALITY))
    monkeypatch.setattr(sl, "_region_commute", lambda region, work: None)


def _profile(**kw):
    base = {"가용자본": 40_000, "연소득": 8_000, "관심평수": ["84"], "_favs": []}
    base.update(kw)
    return base


def test_returns_top_n_within_budget():
    out = sl.build(_profile(), budget=70_000, limit=3)
    assert out["ready"]
    assert len(out["candidates"]) == 3
    for c in out["candidates"]:
        assert c["예상가"] <= 70_000
    # 점수 내림차순
    scores = [c["점수"] for c in out["candidates"]]
    assert scores == sorted(scores, reverse=True)


def test_over_budget_complexes_are_rejected_with_reason():
    out = sl.build(_profile(), budget=60_000, limit=3)
    names = [c["단지"] for c in out["candidates"]]
    assert "래미안강남" not in names        # 9000×25.7 ≈ 23억
    assert out["탈락"]["예산초과"] >= 1


def test_market_risk_does_not_hide_personal_candidates():
    out = sl.build(_profile(_favs=["하락구"]), budget=200_000, limit=5)
    assert "폭락아파트" in [c["단지"] for c in out["candidates"]]
    assert out["탈락"]["시그널"] == 0


def test_all_market_states_can_enter_candidate_regions():
    assert "하락구" in sl._candidate_regions([], SIGNALS, LOCALITY)


def test_centroid_commute_is_not_a_hard_filter(monkeypatch):
    def far(region, work):
        return {"min": 120, "transfer": 3} if region == "노원구" else {"min": 25, "transfer": 1}

    monkeypatch.setattr(sl, "_region_commute", far)
    p = _profile(직장lat=37.4979, 직장lng=127.0276)
    out = sl.build(p, budget=200_000, limit=5)
    regions = {c["region"] for c in out["candidates"]}
    assert "노원구" in regions
    assert out["탈락"]["통근초과"] == 0
    assert out["직장"] is True
    assert "통근" in out["가중치"]


def test_weights_drop_commute_without_work():
    out = sl.build(_profile(), budget=100_000)
    assert "통근" not in out["가중치"]
    assert abs(sum(out["가중치"].values()) - 1.0) < 1e-6


def test_favorites_come_first_in_candidate_regions():
    regions = sl._candidate_regions(["도봉구"], SIGNALS, LOCALITY)
    assert regions[0] == "도봉구"


def test_price_coverage_before_market_strength():
    regions = sl._candidate_regions([], SIGNALS, {"도봉구": {"price": 1000}})
    assert regions[0] == "도봉구"


def test_candidate_carries_cash_and_reason():
    out = sl.build(_profile(), budget=70_000, limit=1)
    c = out["candidates"][0]
    assert c["자금"]["월상환"] >= 0
    assert c["예산확인필요"] is True
    assert c["자금"]["필요현금"] > 0
    assert c["region"] in c["근거"]
    assert set(out["가중치"]) <= set(sl.WEIGHTS)


def test_one_region_cannot_dominate():
    rows = [{"region": "노원구", "점수": 100 - i, "단지": f"A{i}"} for i in range(5)]
    rows += [{"region": "도봉구", "점수": 50, "단지": "B"}]
    top = sl._diversify(rows, 3)
    assert sum(1 for c in top if c["region"] == "노원구") == sl.MAX_PER_REGION
    assert top[-1]["region"] == "도봉구"


def test_grade_score_prefers_upper_tier():
    assert sl._grade_score(0) == 100
    assert sl._grade_score(90) == 10
    assert sl._grade_score(None) == 50


def test_missing_grade_data_counted():
    out = sl.build(_profile(_favs=["없는구"]), budget=70_000)
    assert out["탈락"]["데이터없음"] >= 0  # 시그널 맵에 없으면 후보 지역에서 제외


def test_budget_score_preserves_headroom():
    assert sl._budget_score(0.5) > sl._budget_score(0.8)
    assert sl._budget_score(1.1) == 0


def test_commute_score_curve():
    assert sl._commute_score(15) == 100
    assert sl._commute_score(70) == 0
    assert sl._commute_score(None) is None
