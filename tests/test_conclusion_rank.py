"""결론 추천 — 통합 매물 점수(STRONG_BUY·예산 적합)."""

from realty_signal.services import recommend as rec


def test_prefers_near_budget_over_cheap_undervalued_via_budget_fit():
    # 같은 BUY: 예산 97% vs 68% → 전자 승 (의정부류 억제)
    a = rec.score_listing(
        {"유형": "급매", "단지명": "권선", "지역": "수원시 권선구", "시그널": "BUY",
         "총액": 39_000, "기회도": 50},
        budget=40_000, pyeong=25.7)
    b = rec.score_listing(
        {"유형": "급매", "단지명": "의정부", "지역": "의정부시", "시그널": "BUY",
         "총액": 27_000, "기회도": 50},
        budget=40_000, pyeong=25.7)
    assert a["예산내"] and b["예산내"]
    assert a["_score"] > b["_score"]


def test_strong_buy_beats_buy_even_if_cheaper_buy():
    cheap_buy = rec.score_listing(
        {"유형": "급매", "단지명": "외곽", "지역": "의정부시", "시그널": "BUY",
         "총액": 25_000, "기회도": 80, "지표값": -10},
        budget=40_000, pyeong=25.7)
    strong = rec.score_listing(
        {"유형": "급매", "단지명": "노원", "지역": "노원구", "시그널": "STRONG_BUY",
         "총액": 36_000, "기회도": 60, "지표값": -3},
        budget=40_000, pyeong=25.7)
    assert strong["_score"] > cheap_buy["_score"]
