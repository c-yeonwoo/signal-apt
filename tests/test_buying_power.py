"""매수력 엔진 — LTV 상한 · DSR(기대출·스트레스) · 취득세 · 월상환 · 필요현금."""

from __future__ import annotations

import pytest

from realty_signal import buying_power as bp


def _p(**kw):
    base = {"capital": 50_000, "rate": 0.04, "years": 30}
    base.update(kw)
    return bp.Params(**base)


def test_ltv_cap_by_homes_and_first_time():
    # 지역 미지정 = 규제지역 보수 가정
    assert _p(homes=0).ltv_cap() == 0.40
    assert _p(homes=0, first_time=True).ltv_cap() == 0.70
    assert _p(homes=1).ltv_cap() == 0.40                    # 처분·전입 약정 기준
    assert _p(homes=1, dispose=False).ltv_cap() == 0.00
    # 비규제(지방) — 지역을 명시해야 완화된다
    local = dict(region="해운대구", sido="부산")
    assert _p(homes=0, **local).ltv_cap() == 0.70
    assert _p(homes=0, first_time=True, **local).ltv_cap() == 0.80
    assert _p(homes=1, **local).ltv_cap() == 0.70
    assert _p(homes=1, dispose=False, **local).ltv_cap() == 0.60
    # 규제지역 — 10·15 대책
    assert _p(homes=0, region="강남구").ltv_cap() == 0.40
    assert _p(homes=0, first_time=True, region="강남구").ltv_cap() == 0.70
    assert _p(homes=2, region="강남구").ltv_cap() == 0.00
    # 희망 LTV 는 제도 상한을 넘지 못한다
    assert _p(homes=0, first_time=True, ltv=0.9).ltv_cap() == 0.70
    assert _p(homes=0, first_time=True, ltv=0.5).ltv_cap() == 0.50


def test_no_region_assumes_regulated_metro():
    p = _p(first_time=True)
    assert p.regulated is True and p.metro is True
    assert p.loan_cap(100_000) == 60_000
    st = bp.statement(p)
    assert st["규제"]["지역가정"] is True
    assert any("보수 계산" in n for n in st["안내"])


def test_special_borrower_ltv_needs_income_and_price():
    # 서민·실수요자: 무주택 + 소득 9천 이하 + 주택가 8억 이하 → 규제지역 60%
    p = _p(homes=0, income=8_000, regulated=True)
    assert p.ltv_cap(70_000) == 0.60
    assert p.ltv_cap(90_000) == 0.40      # 8억 초과면 일반 무주택
    assert _p(homes=0, income=12_000, regulated=True).ltv_cap(70_000) == 0.40


def test_region_drives_regulation():
    assert _p(region="강남구").regulated is True
    assert _p(region="성남시 분당구").regulated is True
    assert _p(region="화성시 동탄구").regulated is True     # 2026.7.1 추가 지정
    assert _p(region="남양주시").regulated is False
    assert _p(region="남양주시", sido="경기").metro is True  # 비규제 수도권도 절대한도
    assert _p(region="해운대구", sido="부산").metro is False


def test_metro_loan_cap_by_price():
    p = _p(region="강남구")
    assert p.loan_cap(140_000) == 60_000
    assert p.loan_cap(200_000) == 40_000
    assert p.loan_cap(300_000) == 20_000
    assert _p(region="해운대구", sido="부산").loan_cap(300_000) == float("inf")


def test_loan_for_takes_lowest_of_three():
    # 생애최초 규제지역 12억: LTV 70%=8.4억 이지만 절대한도 6억이 먼저 막는다
    lim = bp.loan_for(120_000, _p(region="강남구", first_time=True, income=20_000))
    assert lim["대출"] == 60_000
    assert lim["제약"] == "한도"
    # 소득이 낮으면 DSR 이 먼저 막는다
    assert bp.loan_for(120_000, _p(region="강남구", first_time=True, income=5_000))["제약"] == "DSR"


def test_metro_stress_and_term_clamp():
    metro = _p(region="강남구", years=40)
    assert metro.term() == 30                       # 수도권·규제 만기 30년
    assert metro.stress() == pytest.approx(0.018)   # 3.0%p × 혼합형 60%
    assert _p(region="강남구", rate_type="변동").stress() == pytest.approx(0.03)
    local = _p(region="해운대구", sido="부산", years=40)
    assert local.term() == 40
    assert local.stress() == pytest.approx(0.0045)  # 지방 0.75%p 유예 × 60%


def test_regulation_shrinks_buying_power():
    kw = {"capital": 50_000, "income": 10_000, "first_time": True}
    seoul, _ = bp.max_purchase(_p(region="강남구", **kw))
    local, _ = bp.max_purchase(_p(region="해운대구", sido="부산", **kw))
    assert seoul < local


def test_existing_debt_reduces_dsr_cap():
    clean = bp.dsr_loan_cap(_p(income=8_000))
    burdened = bp.dsr_loan_cap(_p(income=8_000, existing_debt_annual=1_500))
    assert burdened < clean
    # 기대출이 DSR 한도를 다 먹으면 신규 대출 0
    assert bp.dsr_loan_cap(_p(income=5_000, existing_debt_annual=5_000)) == 0.0
    assert bp.dsr_loan_cap(_p(income=None)) == float("inf")


def test_stress_rate_lowers_limit():
    soft = bp.dsr_loan_cap(_p(income=8_000, stress_bp=0.0))
    hard = bp.dsr_loan_cap(_p(income=8_000, stress_bp=0.015))
    assert hard < soft


def test_acq_tax_brackets_and_first_time_relief():
    cheap = bp.acq_tax(50_000, _p())          # 5억 · 1% 대
    pricey = bp.acq_tax(150_000, _p())        # 15억 · 3% 대
    assert cheap / 50_000 < 0.02
    assert pricey / 150_000 > 0.03
    # 다주택 중과
    assert bp.acq_tax(50_000, _p(homes=2)) > cheap
    # 생애최초 감면(12억 이하)
    assert bp.acq_tax(50_000, _p(first_time=True)) == max(0.0, cheap - bp.DEFAULTS["생애최초감면"])


def test_broker_fee_bracket_limit():
    assert bp.broker_fee(4_000) == min(4_000 * 0.006, 25)
    assert bp.broker_fee(100_000) == 100_000 * 0.005


def test_max_purchase_consumes_capital_and_reports_monthly():
    p = _p(capital=50_000, income=8_000)
    price, detail = bp.max_purchase(p)
    assert price > 0
    assert detail["월상환"] > 0
    # 필요현금이 자기자본을 넘지 않아야 한다
    cash = bp.cash_needed(price, p, detail["대출"])
    assert cash["합계"] <= p.capital + 1
    # 1만원만 더 비싸면 자본 초과
    assert not bp.for_price(price + 500, p)["가능"]


def test_more_capital_buys_more():
    a, _ = bp.max_purchase(_p(capital=30_000))
    b, _ = bp.max_purchase(_p(capital=60_000))
    assert b > a


def test_safe_purchase_respects_income_ratio():
    p = _p(capital=50_000, income=6_000)
    safe = bp.safe_purchase(p)
    assert safe is not None
    ceiling = p.income / 12 * bp.DEFAULTS["안전상환비율"]
    assert safe["월상환"] <= ceiling + 1
    price, _ = bp.max_purchase(p)
    assert safe["매수가"] <= price
    assert bp.safe_purchase(_p(income=None)) is None


def test_statement_shape():
    st = bp.statement(_p(capital=50_000, income=8_000, homes=0, first_time=True,
                         region="강남구"))
    for k in ("최대매수가", "대출", "월상환", "필요현금", "제약", "비용", "가정",
              "LTV상한", "규제", "안내"):
        assert k in st
    assert st["가정"]["생애최초"] is True
    assert st["규제"]["규제지역"] is True
    assert st["규제"]["자격"] == "생애최초"
    assert st["규제"]["적용만기"] == 30
    assert any("전입" in n for n in st["안내"])
    assert st["DSR"] > 0


def test_params_from_profile_maps_korean_keys():
    p = bp.params_from_profile({
        "가용자본": 40_000, "연소득": 7_000, "기대출연원리금": 600,
        "주택수": 1, "생애최초": 0,
    })
    assert p.capital == 40_000
    assert p.income == 7_000
    assert p.existing_debt_annual == 600
    assert p.homes == 1
    assert p.first_time is False
    # override 우선
    assert bp.params_from_profile({"가용자본": 1}, capital=2).capital == 2


def test_pyeong_of():
    assert bp.pyeong_of(["84"]) == 25.7
    assert bp.pyeong_of(["84", "59"]) == 17.4   # 가장 작은 평형 기준
    assert bp.pyeong_of(None) == 25.7
    assert bp.pyeong_of("m") == 25.7
