import pytest
import xml.etree.ElementTree as ET

from realty_signal import auction
from realty_signal.auction import Listing
from realty_signal.auction_bid_engine import evaluate
from realty_signal.time_kst import today_kst


def listing():
    return Listing(사건번호="2026타경1", 단지명="검증단지", 감정가=10_000,
                   최저매각가=6_400, 전용면적=59, 인수보증금=0,
                   권리분석={"조사완료": True, "분석": {"확인필요": False, "인수합계": 0}})


def inputs(**overrides):
    return {"purpose": "owner", "bid": 7_000, "market_low": 12_000,
            "tax_rate": .011, "buy_broker_rate": .005, "legal_cost": 100,
            "eviction_cost": 200, "management_cost": 0, "repair_cost": 300,
            "contingency": 100, "court_deposit": 640, "cash_budget": 10_000,
            "confirmed_loan": 0, "hold_months": 6, "min_saving": 1_000,
            **overrides}


def test_owner_scenario_and_exact_ceiling():
    result = evaluate(listing(), inputs())
    assert result["status"] == "assumption_only"
    assert result["review_ceiling"] is None
    assert result["scenario"]["경매총비용"] == 7_000 + 77 + 100 + 200 + 300 + 100
    assert result["scenario"]["최대필요현금"] == result["scenario"]["경매총비용"]
    cap = result["scenario_ceiling"]
    assert cap is not None
    assert evaluate(listing(), inputs(bid=cap))["scenario"]["목표충족"]
    next_row = evaluate(listing(), inputs(bid=cap + 1))["scenario"]
    assert not (next_row["목표충족"] and next_row["자금충족"])


def test_missing_rights_does_not_convert_unknown_to_zero():
    obj = listing()
    obj.인수보증금 = None
    result = evaluate(obj, inputs())
    assert result["status"] == "needs_review"
    assert result["scenario"] is None
    assert result["scenario_ceiling"] is None
    assert any("인수보증금" in reason for reason in result["missing"])


def test_self_reported_rights_only_make_an_assumption_ceiling():
    obj = listing()
    obj.인수보증금 = None
    obj.권리분석 = {}
    payload = inputs(assumed_inherited_cost=300, rights_reviewed=True,
                     court_documents_checked=True, tax_checked=True, costs_checked=True)
    result = evaluate(obj, payload)
    assert result["status"] == "assumption_only"
    assert result["scenario_ceiling"] is not None and result["review_ceiling"] is None
    assert result["scenario"]["인수보증금"] == 300
    assert "사용자 가정" in " ".join(result["missing"])
    assert obj.인수보증금 is None and obj.권리분석 == {}
    unreviewed = evaluate(obj, {**payload, "rights_reviewed": False})
    assert unreviewed["status"] == "needs_review" and unreviewed["scenario"] is None


def test_missing_exclusive_area_holds_even_with_manual_market():
    obj = listing()
    obj.전용면적 = 0
    result = evaluate(obj, inputs())
    assert result["status"] == "needs_review" and result["scenario_ceiling"] is None
    assert "전용면적 확인" in result["missing"]


def test_market_evidence_must_match_area_and_date():
    comps = [{"source": "molit", "price": price, "exclusive_m2": 59,
              "sold_at": today_kst().isoformat()} for price in (12_000, 12_100)]
    comps.append({"source": "molit", "price": 8_000, "exclusive_m2": 84,
                  "sold_at": today_kst().isoformat()})
    obj = listing()
    obj.실거래표본 = comps
    obj.실거래표본갱신일 = today_kst().isoformat()
    result = evaluate(obj, inputs(market_low=None))
    assert result["status"] == "needs_review"
    comps.append({"source": "molit", "price": 11_000, "exclusive_m2": 59,
                  "sold_at": today_kst().isoformat()})
    result = evaluate(obj, inputs(market_low=None))
    assert result["status"] == "assumption_only"
    assert "최저가 기준" in result["market_notes"][0]
    result = evaluate(listing(), inputs(market_low=None, comparables=comps))
    assert result["status"] == "needs_review"  # 임의 API 입력은 공공 수집으로 승격하지 않음


def test_checked_status_requires_fresh_official_samples_and_cost_checks():
    obj = listing()
    obj.실거래표본갱신일 = today_kst().isoformat()
    obj.실거래표본 = [{"source": "molit", "price": price,
                    "exclusive_m2": 59, "sold_at": today_kst().isoformat()}
                   for price in (12_000, 11_800, 12_100)]
    checks = {"court_documents_checked": True, "tax_checked": True, "costs_checked": True}
    result = evaluate(obj, inputs(**checks))
    assert result["status"] == "conditional_ceiling"
    assert result["review_ceiling"] == result["scenario_ceiling"]
    assert result["review_ceiling"] <= 11_800
    obj.실거래표본갱신일 = "2020-01-01"
    stale = evaluate(obj, inputs(**checks))
    assert stale["status"] == "assumption_only" and stale["review_ceiling"] is None


def test_confirmed_loan_only_and_deposit_not_reused_as_future_rent():
    data = inputs(purpose="rent", confirmed_loan=4_000, loan_rate=.05,
                  rent_monthly=70, rent_deposit=5_000, vacancy_rate=.1,
                  annual_operating=100, target_yield=.04)
    result = evaluate(listing(), data)
    scenario = result["scenario"]
    assert scenario["확인대출반영"] == 4_000
    assert scenario["보유이자"] == 100
    assert scenario["최대필요현금"] > scenario["임대후순투입현금"]
    assert scenario["연간순현금흐름"] == round(70 * 12 * .9 - 100 - 4_000 * .05)


def test_rental_search_skips_nonpositive_equity_before_finding_cap():
    result = evaluate(listing(), inputs(purpose="rent", confirmed_loan=4_000,
        loan_rate=.05, rent_monthly=100, rent_deposit=4_000, vacancy_rate=0,
        annual_operating=100, target_yield=.05, cash_budget=20_000))
    assert result["scenario"]["임대후순투입현금"] <= 0
    assert result["scenario_ceiling"] is not None


def test_flip_uses_selling_cost_and_tax_not_gross_spread():
    result = evaluate(listing(), inputs(purpose="flip", sale_low=10_000,
                                        sale_broker_rate=.005, sale_tax=300,
                                        min_profit=500))
    scenario = result["scenario"]
    assert scenario["세후순이익가정"] == round(10_000 * .995 - 300 - scenario["경매총비용"])


def test_no_bid_when_budget_misses_floor():
    result = evaluate(listing(), inputs(cash_budget=1_000))
    assert result["status"] == "no_bid"
    assert result["scenario_ceiling"] is None


@pytest.mark.parametrize("field,value", [("tax_rate", -1), ("bid", float("nan")),
                                         ("court_deposit", 8_000), ("confirmed_loan", 8_000)])
def test_invalid_financial_assumptions_rejected(field, value):
    with pytest.raises(ValueError):
        evaluate(listing(), inputs(**{field: value}))


def test_official_refresh_samples_exclude_other_area_and_partial_complex(monkeypatch):
    from realty_signal.ingest import complex as complex_ingest

    def row(name, area, price, floor):
        return ET.fromstring(f"<item><aptNm>{name}</aptNm><umdNm>테스트동</umdNm>"
            f"<excluUseAr>{area}</excluUseAr><dealAmount>{price}</dealAmount>"
            "<dealYear>2026</dealYear><dealMonth>10</dealMonth><dealDay>1</dealDay>"
            f"<floor>{floor}</floor></item>")

    monkeypatch.setattr(auction, "_recent_yms", lambda n: ["202610"])
    monkeypatch.setattr(complex_ingest, "_items", lambda *args: [
        row("검증단지", 59.04, "12,000", 10),
        row("검증단지", 59.5, "11,000", 10),
        row("검증단지", 84, "8,000", 10),
        row("검증단지2", 59, "7,000", 10),
    ])
    price, _, _, samples = auction.recent_trade_price(
        "11110", "테스트동", "검증단지", 59, "synthetic", with_samples=True)
    assert price == 12_000  # 기존 최근 가격은 별도 참고값
    assert [x["price"] for x in samples] == [12_000]


def test_targeted_market_refresh_keeps_other_listing_unchanged(tmp_path, monkeypatch):
    from realty_signal.ingest import building, complex_grade

    monkeypatch.setattr(auction, "AUCTION_FILE", tmp_path / "auction.json")
    one = auction.add({"단지명": "검증단지", "region": "테스트구", "전용면적": 59})
    two = auction.add({"단지명": "다른단지", "region": "테스트구", "전용면적": 84})
    calls = []

    def trades(*args, **kwargs):
        calls.append(args[2])
        return 12_000, None, None, [{"source": "molit", "price": 12_000,
            "exclusive_m2": 59, "sold_at": today_kst().isoformat()}]

    monkeypatch.setattr(auction, "recent_trade_price", trades)
    monkeypatch.setattr(auction, "recent_jeonse_price", lambda *args: None)
    monkeypatch.setattr(building, "fetch_building", lambda *args: None)
    monkeypatch.setattr(complex_grade, "region_grades", lambda *args: [])
    monkeypatch.setattr(complex_grade, "grade_in_region", lambda *args: None)
    assert auction.update_market({"테스트구": "1111000000"}, "synthetic", one.id) == 1
    assert calls == ["검증단지"]
    assert auction.get(one.id).실거래표본갱신일 == today_kst().isoformat()
    assert auction.get(two.id).실거래표본 == []
