"""타이밍 점수 단위 테스트."""

from __future__ import annotations

from realty_signal.signals.timing import listing_timing, region_timing


def test_listing_timing_quicksale_source_gap_does_not_raise_score():
    r = listing_timing("급매", {"급매갭": -15}, "BUY", "A", asof="2026-07-14")
    assert r.score == 15 + 8  # 지역 시그널·급지뿐, 공급사 갭은 비교 근거가 아니다.
    assert r.score == listing_timing("급매", {"급매갭": -3}, "BUY", "A").score
    assert "동일 면적·조건 가격 비교 필요" in r.reasons_text
    assert "-15" not in r.reasons_text
    assert "가격 점수 미반영" in r.reasons_text
    assert r.confidence <= 0.5
    d = r.to_dict()
    assert d["기회도"] == d["타이밍점수"]
    assert d["asof"] == "2026-07-14"
    assert d["timing_version"] == "v5-no-unverified-price-gap"


def test_listing_timing_unrealistic_gap_low_confidence():
    r = listing_timing("급매", {"급매갭": -40}, "BUY", "B", asof="2026-07-14")
    assert r.confidence < 0.5
    assert "동일 면적·조건 가격 비교 필요" in r.reasons_text
    assert r == listing_timing("급매", {"급매갭": 0}, "BUY", "B", asof="2026-07-14")


def test_certification_does_not_verify_price_or_raise_ranking_confidence():
    sale = listing_timing("급매", {"급매갭": -10}, "BUY", "B")
    cert = listing_timing("찐매물", {"급매갭": -10}, "BUY", "B")
    assert cert.score == sale.score
    assert cert.confidence == sale.confidence
    assert "가격·판매 가능 여부 미검증" in cert.reasons_text
    assert listing_timing("급매", {"급매갭": "불명"}, "BUY", "B").score == sale.score


def test_general_listing_has_no_price_advantage_bonus():
    general = listing_timing("일반매물", {"호가": 50_000}, "BUY", "A")
    quicksale = listing_timing("급매", {"급매갭": None}, "BUY", "A")
    assert general.score == quicksale.score
    assert "가격 우위 점수 미반영" in general.reasons_text
    assert general.confidence <= 0.5


def test_region_timing_with_backtest():
    r = region_timing("STRONG_BUY", asof="2026-07-14", backtest_up_pct=68.0)
    assert r.score >= 70
    assert r.layer == "region"
    assert r.score == region_timing("STRONG_BUY").score
    assert r.to_dict()["confidence_kind"] == "heuristic_not_probability"


def test_listing_timing_no_signal():
    r = listing_timing("경매", {"시세차익률": 20}, None, None, asof="2026-07-14")
    assert r.confidence < 0.72


def test_held_signal_has_no_buy_bonus_and_states_the_limit():
    held = listing_timing("급매", {"급매갭": -5}, "HELD", None)
    buy = listing_timing("급매", {"급매갭": -5}, "BUY", None)
    assert held.score < buy.score
    assert "지역 판정 보류" in held.reasons_text
    assert "시그널 가산 없음" in held.reasons_text
    assert held.confidence < buy.confidence


def test_auction_no_bid_cannot_regain_opportunity_score_from_region_signal():
    r = listing_timing("경매", {"입찰상태": "no_bid", "총비용우위율": None,
                              "확인할것": ["목표 비용 우위 미달"]}, "STRONG_BUY", "A")
    assert r.score == 0 and r.confidence < 0.5
    assert "미달" in r.reasons_text
