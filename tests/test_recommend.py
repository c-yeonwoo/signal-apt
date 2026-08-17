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


def test_unknown_price_is_not_counted_as_in_budget():
    """가격을 모르는 매물을 '예산 내'로 치면 안 된다.

    예전엔 `est is None` 이면 무조건 통과 + 기본 적합도 40 이라, 예산의 56% 짜리
    **실제** 매물보다 높은 점수를 받았다. "가격을 모르는 쪽이 유리"해지는 구조였다.
    """
    rows = [
        {"유형": "청약", "단지명": "가격미상", "지역": "미지의구", "시그널": "STRONG_BUY",
         "기회도": 90},
        {"유형": "급매", "단지명": "실제싼매물", "지역": "노원구", "시그널": "STRONG_BUY",
         "총액": 22_000, "기회도": 50},
    ]
    out = rec.rank_listings(rows, budget=40_000, pyeong=25.7,
                            loc_price_of=lambda r: None)
    by = {x["단지명"]: x for x in out}
    assert by["가격미상"]["예산내"] is False
    assert by["가격미상"]["예산확인필요"] is True
    assert by["가격미상"]["가격출처"] is None
    assert by["실제싼매물"]["예산내"] is True
    # 가격을 아는 예산 내 매물이 항상 앞선다
    assert out[0]["단지명"] == "실제싼매물"


def test_region_average_estimate_is_labelled():
    """지역평단 근사는 그 매물의 가격이 아니다. 출처를 붙여 화면이 구분할 수 있게 한다."""
    rows = [{"유형": "청약", "단지명": "근사", "지역": "노원구", "시그널": "BUY"}]
    out = rec.rank_listings(rows, budget=40_000, pyeong=25.7,
                            loc_price_of=lambda r: 1_000)
    assert out[0]["가격출처"] == "지역평단추정"
    assert out[0]["추정가"] == 25_700
    assert out[0]["예산내"] is True          # 근사라도 값이 있으면 예산 판정은 한다
    assert out[0]["예산확인필요"] is False


def test_buy_pool_is_never_discarded_wholesale():
    """STRONG_BUY 가 많아도 BUY 를 통째로 버리지 않는다.

    STRONG_BUY 는 광역 지표 상속 탓에 특정 시기 특정 광역에 몰린다 —
    실측(2026-08-10)에서 STRONG_BUY 26개 지역이 전부 서울이었다. 등급으로 후보를
    미리 버리면 경기·인천 매물이 예산 심사 전에 사라진다.
    """
    rows = [
        {"유형": "급매", "단지명": f"서울{i}", "지역": f"서울{i}구",
         "시그널": "STRONG_BUY", "총액": 39_000, "기회도": 60}
        for i in range(10)
    ] + [
        {"유형": "급매", "단지명": "경기싼곳", "지역": "화성시",
         "시그널": "BUY", "총액": 20_000, "기회도": 60},
    ]
    out = rec.rank_listings(rows, budget=40_000, pyeong=25.7,
                            loc_price_of=lambda r: None, prefer_strong=True)
    assert "경기싼곳" in {x["단지명"] for x in out}


def test_one_region_cannot_dominate_the_list():
    """한 동네가 목록을 독식하지 않는다 (숏리스트 MAX_PER_REGION 과 같은 이유).

    실측에서 노원구 하나가 상위 40건 중 11건을 차지했다.
    """
    rows = [
        {"유형": "급매", "단지명": f"노원{i}", "지역": "노원구",
         "시그널": "STRONG_BUY", "총액": 30_000, "기회도": 90}
        for i in range(10)
    ] + [
        {"유형": "급매", "단지명": "다른동네", "지역": "화성시",
         "시그널": "BUY", "총액": 30_000, "기회도": 10},
    ]
    # 상한이 걸리는 만큼만 자르면 지역당 최대 3곳
    out = rec.rank_listings(rows, budget=40_000, pyeong=25.7,
                            loc_price_of=lambda r: None, limit=4, max_per_region=3)
    assert [x["지역"] for x in out] == ["노원구", "노원구", "노원구", "화성시"]

    # 자리가 남으면 넘친 것으로 채운다 — 빈손보다 낫다. 단 다른 동네가 **먼저** 온다.
    out = rec.rank_listings(rows, budget=40_000, pyeong=25.7,
                            loc_price_of=lambda r: None, limit=6, max_per_region=3)
    names = [x["단지명"] for x in out]
    assert names.index("다른동네") < names.index("노원3")
    assert sum(1 for x in out if x["지역"] == "노원구") == 5


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
