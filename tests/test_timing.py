"""타이밍 점수 단위 테스트."""

from __future__ import annotations

from realty_signal.signals.timing import listing_timing, region_timing


def test_listing_timing_quicksale_source_gap_does_not_raise_score():
    r = listing_timing("급매", {"급매갭": -15}, "BUY", "A", asof="2026-07-14")
    assert r.score == 15 + 8  # 지역 시그널·급지뿐, 공급사 갭은 탐색 필터로만 사용
    assert r.score == listing_timing("급매", {"급매갭": -3}, "BUY", "A").score
    assert "공급사 표시 갭 -15%" in r.reasons_text
    assert "가격 점수 미반영" in r.reasons_text
    assert r.confidence <= 0.5
    d = r.to_dict()
    assert d["기회도"] == d["타이밍점수"]
    assert d["asof"] == "2026-07-14"
    assert d["timing_version"] == "v3-source-gap-unranked"


def test_listing_timing_unrealistic_gap_low_confidence():
    r = listing_timing("급매", {"급매갭": -40}, "BUY", "B", asof="2026-07-14")
    assert r.confidence < 0.5
    assert "비현실적" in r.reasons_text


def test_certification_does_not_verify_price_or_raise_ranking_confidence():
    sale = listing_timing("급매", {"급매갭": -10}, "BUY", "B")
    cert = listing_timing("찐매물", {"급매갭": -10}, "BUY", "B")
    assert cert.score == sale.score
    assert cert.confidence == sale.confidence
    assert "가격·판매 가능 여부 미검증" in cert.reasons_text
    assert listing_timing("급매", {"급매갭": "불명"}, "BUY", "B").score == sale.score


def test_region_timing_with_backtest():
    r = region_timing("STRONG_BUY", asof="2026-07-14", backtest_up_pct=68.0)
    assert r.score >= 70
    assert r.layer == "region"
    assert r.score == region_timing("STRONG_BUY").score
    assert r.to_dict()["confidence_kind"] == "heuristic_not_probability"


def test_listing_timing_no_signal():
    r = listing_timing("경매", {"시세차익률": 20}, None, None, asof="2026-07-14")
    assert r.confidence < 0.72


def test_auction_no_bid_cannot_regain_opportunity_score_from_region_signal():
    r = listing_timing("경매", {"입찰상태": "no_bid", "총비용우위율": None,
                              "확인할것": ["목표 비용 우위 미달"]}, "STRONG_BUY", "A")
    assert r.score == 0 and r.confidence < 0.5
    assert "미달" in r.reasons_text
