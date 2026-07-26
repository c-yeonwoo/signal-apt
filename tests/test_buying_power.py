"""매수력 엔진 — LTV 상한 · DSR(기대출·스트레스) · 취득세 · 월상환 · 필요현금."""

from __future__ import annotations

from realty_signal import buying_power as bp


def _p(**kw):
    base = {"capital": 50_000, "rate": 0.04, "years": 30}
    base.update(kw)
    return bp.Params(**base)


def test_ltv_cap_by_homes_and_first_time():
    assert _p(homes=0).ltv_cap() == 0.70
    assert _p(homes=0, first_time=True).ltv_cap() == 0.80
    assert _p(homes=1).ltv_cap() == 0.60
    assert _p(homes=1, regulated=True).ltv_cap() == 0.30
    # 희망 LTV 는 제도 상한을 넘지 못한다
    assert _p(homes=1, ltv=0.9).ltv_cap() == 0.60
    assert _p(homes=0, ltv=0.5).ltv_cap() == 0.50


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
    st = bp.statement(_p(capital=50_000, income=8_000, homes=0, first_time=True))
    for k in ("최대매수가", "대출", "월상환", "필요현금", "제약", "비용", "가정", "LTV상한"):
        assert k in st
    assert st["가정"]["생애최초"] is True
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
