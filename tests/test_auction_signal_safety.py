"""Auction ranking must not turn a held raw BUY into a user-facing advantage."""

from realty_signal import api, auction
from realty_signal.routes import auction as auction_routes


def _listing(region="보류구"):
    return auction.Listing(단지명="검토단지", region=region, 감정가=10_000,
                           최저매각가=6_400, 시세=14_000, 전용면적=59,
                           인수보증금=0,
                           권리분석={"조사완료": True, "분석": {"확인필요": False, "인수합계": 0}})


def test_auction_routes_use_current_safe_grades_for_buy_regions_and_rank(monkeypatch):
    monkeypatch.setattr(api, "_display_signal_map", lambda: {"보류구": "HELD", "준비구": "BUY"})
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {"보류구": {"급지": "B"}}})
    monkeypatch.setattr(auction, "load", lambda: [_listing()])
    assert auction_routes.buy_regions() == [{"region": "준비구", "signal": "BUY"}]
    result = auction_routes.auction_listings()
    held = result["listings"][0]
    raw = auction.enrich([_listing()], {"보류구": "STRONG_BUY"})[0]
    assert held["지역시그널"] == "HELD"
    assert held["지역급지"] == "B"
    assert held["우선순위점수"] == raw["우선순위점수"] - 20


def test_integrated_auction_keeps_raw_for_audit_but_ranks_with_hold(monkeypatch):
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {}})
    monkeypatch.setattr(api.md, "assessed_signal_labels", lambda today: {
        "보류구": {"display_signal": "HELD", "assessment_status": "held"}})
    monkeypatch.setattr(api, "_signal_map", lambda: {"보류구": "STRONG_BUY"})
    monkeypatch.setattr(api, "_timing_asof", lambda: "2026-10-03")
    monkeypatch.setattr(auction, "load", lambda: [_listing()])
    seen_signals = []
    original_enrich = auction.enrich

    def enrich_with_audit(listings, signals, overrides=None):
        seen_signals.append(signals)
        return original_enrich(listings, signals, overrides)

    monkeypatch.setattr(auction, "enrich", enrich_with_audit)

    row = api._build_listings({"경매"})[0]
    assert row["원시시그널"] == "STRONG_BUY"
    assert row["시그널"] == "HELD"
    assert row["판정상태"] == "held"
    assert seen_signals == [{}]


def test_auction_enrichment_without_assessment_is_held():
    row = auction.enrich([_listing()], {})[0]
    assert row["지역시그널"] == "HELD"


def test_name_only_auction_in_same_named_district_cannot_borrow_buy(monkeypatch):
    monkeypatch.setattr(api, "_display_signal_map", lambda: {"중구": "BUY", "노원구": "BUY"})
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {}})
    monkeypatch.setattr(api.md, "assessed_signal_labels", lambda today: {
        "중구": {"display_signal": "BUY", "assessment_status": "ready"}})
    monkeypatch.setattr(api, "_signal_map", lambda: {"중구": "BUY"})
    monkeypatch.setattr(api, "_timing_asof", lambda: "2026-10-04")
    monkeypatch.setattr(auction, "load", lambda: [_listing("중구")])
    assert auction_routes.buy_regions() == [{"region": "노원구", "signal": "BUY"}]
    ambiguous = auction_routes.auction_listings()["listings"][0]
    assert ambiguous["지역시그널"] == "HELD"
    assert ambiguous["지역급지"] is None
    row = api._build_listings({"경매"})[0]
    assert row["시그널"] == "HELD"
    assert row["지역식별상태"] == "held"
    assert row["원시시그널"] == "BUY"
