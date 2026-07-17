"""타이밍 점수 단위 테스트."""

from __future__ import annotations

from realty_signal.signals.timing import listing_timing, region_timing


def test_listing_timing_quicksale_discount():
    r = listing_timing("급매", {"급매갭": -15}, "BUY", "A", asof="2026-07-14")
    assert r.score >= 40
    assert "급매갭" in r.reasons_text
    assert r.confidence > 0.5
    d = r.to_dict()
    assert d["기회도"] == d["타이밍점수"]
    assert d["asof"] == "2026-07-14"


def test_listing_timing_unrealistic_gap_low_confidence():
    r = listing_timing("급매", {"급매갭": -40}, "BUY", "B", asof="2026-07-14")
    assert r.confidence < 0.5
    assert "비현실적" in r.reasons_text


def test_region_timing_with_backtest():
    r = region_timing("STRONG_BUY", asof="2026-07-14", backtest_up_pct=68.0)
    assert r.score >= 70
    assert r.layer == "region"
    assert "12주 적중률" in r.reasons_text


def test_listing_timing_no_signal():
    r = listing_timing("경매", {"시세차익률": 20}, None, None, asof="2026-07-14")
    assert r.confidence < 0.72
