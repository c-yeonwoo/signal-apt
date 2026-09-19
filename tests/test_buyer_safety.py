import math

import pytest

from realty_signal import buying_power as bp
from realty_signal.services import recommend as rec


def test_cash_rich_buyer_does_not_have_to_borrow_maximum():
    p = bp.Params(capital=100_000, income=6000, region="강남구", rate_type="고정")
    assert bp.for_price(90_000, p)["대출"] == 0
    assert bp.safe_purchase(p)["매수가"] >= 90_000


def test_unknown_income_is_not_unlimited_credit():
    p = bp.Params(capital=30_000)
    assert bp.for_price(50_000, p)["상태"] == "확인필요"
    assert bp.for_price(50_000, p)["가능"] is False
    assert bp.for_price(20_000, p)["가능"] is True


def test_reserve_and_existing_debt_are_preserved():
    p = bp.Params(capital=50_000, reserve_cash=10_000, income=8000,
                  existing_debt_annual=1200, monthly_budget=180)
    st = bp.safe_purchase(p)
    assert st["잔여현금"] >= 10_000
    assert st["총월상환"] <= 180
    assert bp.for_price(st["매수가"], p)["가능"]


@pytest.mark.parametrize("kw", [{"capital": math.nan}, {"income": math.inf},
                                    {"rate": -1}, {"years": 0}, {"reserve_cash": -1}])
def test_invalid_money_inputs_are_rejected(kw):
    with pytest.raises(ValueError):
        bp.Params(**({"capital": 100} | kw))


def test_cleared_income_does_not_restore_saved_income():
    p = bp.params_from_profile({"가용자본": 100, "연소득": 0,
                               "매수력": {"가정": {"연소득": 8000}}})
    assert p.income == 0


@pytest.mark.parametrize("capital", [20000, 50000, 100000, 200000, 400000])
def test_maximum_is_feasible_without_rounding_overrun(capital):
    p = bp.Params(capital=capital, income=8000, first_time=True, reserve_cash=1000)
    price, _ = bp.max_purchase(p)
    assert bp.for_price(price, p)["가능"]


def test_diversification_never_promotes_infeasible_above_feasible():
    rows = [{"유형": "급매", "지역": "A", "총액": 10000, "시그널": "NEUTRAL"}] * 5
    rows += [{"유형": "급매", "지역": "B", "총액": 90000, "시그널": "STRONG_BUY"}]
    out = rec.rank_listings(rows, budget=20000, pyeong=25.7, loc_price_of=lambda _: None)
    assert all(x["예산내"] for x in out[:5])
    assert not out[5]["예산내"]


def test_auction_bid_is_not_move_in_purchase_price():
    row = rec.score_listing({"유형": "경매", "총액": 10000}, budget=20000, pyeong=25.7)
    assert row["예산확인필요"] and not row["예산내"]


def test_finance_failure_overrides_attractive_signal_and_quote():
    out = rec.rank_listings([{"유형": "급매", "총액": 10000, "시그널": "STRONG_BUY"}],
        budget=20000, pyeong=25.7, loc_price_of=lambda _: None,
        finance_of=lambda *_: {"가능": False, "확인필요": False})
    assert out[0]["판단"] == "조건초과"


def test_region_estimate_never_makes_card_affordable():
    items = rec.rank_listings([{"유형": "청약", "지역": "A"}],
        budget=40000, pyeong=25.7, loc_price_of=lambda _: 1000)
    cards = rec.aggregate_regions(items, locmap={"A": {"price": 1000}}, budget=40000, pyeong=25.7)
    assert not cards[0]["예산내"]
    assert cards[0]["대표매물"]["총액"] is None
