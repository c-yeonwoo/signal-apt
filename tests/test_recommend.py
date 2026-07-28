"""통합 매물 추천 점수 — STRONG_BUY·예산 적합도."""

from realty_signal.services import recommend as rec


def test_strong_buy_outranks_buy_same_price():
    rows = [
        {"유형": "급매", "단지명": "A", "지역": "강남구", "시그널": "BUY",
         "총액": 35_000, "기회도": 80, "지표값": -5},
        {"유형": "급매", "단지명": "B", "지역": "노원구", "시그널": "STRONG_BUY",
         "총액": 35_000, "기회도": 70, "지표값": -3},
    ]
    out = rec.rank_listings(rows, budget=40_000, pyeong=25.7,
                            loc_price_of=lambda r: None, prefer_strong=True)
    assert out[0]["단지명"] == "B"
    assert out[0]["시그널"] == "STRONG_BUY"


def test_over_budget_sorted_after_in_budget():
    rows = [
        {"유형": "급매", "단지명": "비쌈", "지역": "강남구", "시그널": "STRONG_BUY",
         "총액": 80_000, "기회도": 90},
        {"유형": "급매", "단지명": "적정", "지역": "노원구", "시그널": "BUY",
         "총액": 30_000, "기회도": 50},
    ]
    out = rec.rank_listings(rows, budget=40_000, pyeong=25.7,
                            loc_price_of=lambda r: None, prefer_strong=False)
    assert out[0]["단지명"] == "적정"
    assert out[0]["예산내"] is True
    assert out[-1]["예산내"] is False


def test_prefer_strong_falls_back_to_buy_when_few():
    rows = [
        {"유형": "급매", "단지명": "S1", "지역": "A", "시그널": "STRONG_BUY",
         "총액": 20_000, "기회도": 60},
        {"유형": "급매", "단지명": "B1", "지역": "B", "시그널": "BUY",
         "총액": 22_000, "기회도": 60},
    ]
    out = rec.rank_listings(rows, budget=40_000, pyeong=25.7,
                            loc_price_of=lambda r: None, prefer_strong=True)
    assert {x["단지명"] for x in out} == {"S1", "B1"}


def test_aggregate_regions_counts_kinds():
    scored = [
        {"유형": "급매", "단지명": "X", "지역": "노원구", "시그널": "STRONG_BUY",
         "총액": 20_000, "추정가": 20_000, "예산내": True, "_score": 100, "지표값": -8},
        {"유형": "경매", "단지명": "Y", "지역": "노원구", "시그널": "STRONG_BUY",
         "총액": 18_000, "추정가": 18_000, "예산내": True, "_score": 90, "지표값": 12},
    ]
    cards = rec.aggregate_regions(scored, locmap={"노원구": {"price": 1000, "저평가도": 10}},
                                  budget=40_000, pyeong=25.7)
    assert len(cards) == 1
    assert cards[0]["급매건수"] == 1 and cards[0]["경매건수"] == 1
    assert cards[0]["매물수"] == 2
