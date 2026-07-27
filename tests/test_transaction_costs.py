"""매물별 매수 부대비용."""

from __future__ import annotations

from realty_signal import buying_power as bp
from realty_signal import transaction_costs as tc


def test_estimate_matches_acq_tax_and_broker():
    price = 80_000  # 8억
    out = tc.estimate(price, region="수원시 영통구", pyeong=25, homes=0, interior=0, moving=0)
    p = bp.Params(capital=0, homes=0, region="수원시 영통구", big_area=False)
    assert out["부대비용"]["취득세"] == round(bp.acq_tax(price, p))
    assert out["부대비용"]["중개비"] == round(bp.broker_fee(price))
    assert out["부대비용"]["법무비"] == tc.legal_fee(price)
    assert out["총매입가"] == out["매수가"] + out["부대비용"]["합계"]


def test_big_area_farm_tax():
    price = 90_000
    small = tc.estimate(price, exclusive_m2=84, homes=0, interior=0, moving=0)
    big = tc.estimate(price, exclusive_m2=100, homes=0, interior=0, moving=0)
    assert big["부대비용"]["취득세"] > small["부대비용"]["취득세"]
    assert big["가정"]["큰평형"] is True


def test_interior_default_and_zero():
    d = tc.default_interior(pyeong=20)
    assert d == 20 * tc.DEFAULTS["인테리어평당"]
    with_default = tc.estimate(50_000, pyeong=20, moving=0)
    assert with_default["부대비용"]["인테리어"] == d
    zero = tc.estimate(50_000, pyeong=20, interior=0, moving=0)
    assert zero["부대비용"]["인테리어"] == 0


def test_regulated_multi_home_higher_tax():
    price = 100_000
    # 서울 구 = 규제지역
    single = tc.estimate(price, region="강남구", homes=0, interior=0, moving=0)
    multi = tc.estimate(price, region="강남구", homes=2, interior=0, moving=0)
    assert multi["부대비용"]["취득세"] > single["부대비용"]["취득세"]
    assert multi["가정"]["규제지역"] is True


def test_legal_fee_brackets():
    assert tc.legal_fee(20_000) == 50
    assert tc.legal_fee(50_000) == 80
    assert tc.legal_fee(80_000) == 100
    assert tc.legal_fee(200_000) == 120
