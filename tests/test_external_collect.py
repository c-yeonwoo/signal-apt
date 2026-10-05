"""외부 수집은 가짜 전송으로만 검사한다."""

import json

from realty_signal import api, auction
from realty_signal.ingest import external
from realty_signal.ingest.external import (
    card_from_hank, cards_from_rows, collect, collect_hank, ensure_hank_cache,
    hank_row, parse_place, refresh_hank_cache, write_hank_cache,
)
from realty_signal.routes import auction as auction_routes


class FakeTransport:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        page = url.split("page=")[1].split("&")[0]
        return self.pages[page]


def test_hank_row_swaps_latlng_and_drops_bad_points():
    row = hank_row({"id": 1, "unique_id": "2026-1", "latlng": [127.0, 37.5], "category": "아파트"}, "서울")
    assert row["lat"] == 37.5 and row["lng"] == 127.0
    assert hank_row({"latlng": [10, 10]}, "서울") is None
    assert hank_row({"latlng": None}, "서울") is None


def test_collect_hank_pages_both_regions_and_stops_without_next():
    body = {
        "1": {"results": [{"id": 1, "latlng": [127.01, 37.51], "fb_count": 0,
                           "special_condition": ""}], "next": "x"},
        "2": {"results": [{"id": 2, "latlng": [126.9, 37.4], "fb_count": 1}], "next": None},
    }
    transport = FakeTransport(body)
    result = collect_hank(transport, regions=("서울",), page_size=1, max_pages=5)
    assert result.ok and result.count == 2 and not result.truncated
    assert len(transport.urls) == 2
    assert "type=real" in transport.urls[0] and "failure_max=1" in transport.urls[0]


def test_default_collect_is_hank_only_and_aptgin_skips_without_calling():
    transport = FakeTransport({"1": {"results": [{"id": 3, "latlng": [127.0, 37.5]}], "next": None}})
    hank = collect(["hank"], transport)
    assert hank[0].ok and hank[0].count == 1
    assert len(transport.urls) == 2
    before = len(transport.urls)
    skipped = collect(["aptgin"], transport)
    assert skipped[0].skipped and not skipped[0].ok
    assert len(transport.urls) == before


def test_cache_writes_count_not_a_secret(tmp_path):
    result = collect_hank(FakeTransport({"1": {"results": [{"id": 9, "latlng": [127.0, 37.5]}], "next": None}}),
                          regions=("경기",))
    path = tmp_path / "external_hank.json"
    write_hank_cache(result, path)
    saved = json.loads(path.read_text())
    assert saved["count"] == 1 and saved["rows"][0]["region"] == "경기"


def _sample(**extra):
    row = {
        "id": 9, "unique_id": "2026타경9", "status": "유찰",
        "address": "서울특별시 노원구 상계동 1",
        "apsl_amount": 700_000_000, "minb_amount": 560_000_000,
        "bldg_sqm": 84.2, "special_condition": "대항력 있는 임차인",
        "bid_dttm": "2026-10-20T10:00:00", "lat": 37.65, "lng": 127.06, "fb_count": 1,
    }
    row.update(extra)
    return row


def test_place_parser_reads_district_and_dong():
    assert parse_place("서울특별시 노원구 상계동 1") == {"시도": "서울", "지역": "노원구", "동": "상계동"}
    assert parse_place("경기도 성남시 분당구 정자동 12") == {
        "시도": "경기", "지역": "성남시 분당구", "동": "정자동"}
    assert parse_place("경기도 화성시 봉담읍 1")["지역"] == "화성시"
    assert parse_place("경기도 양평군 양평읍 공흥리") == {"시도": "경기", "지역": "양평군", "동": "양평읍"}
    assert parse_place("경기 연천군 전곡읍 은대리")["지역"] == "연천군"


def test_hank_card_converts_won_and_leaves_the_bid_empty():
    card = card_from_hank(_sample())
    assert card["id"] == "hank:9"
    assert card["입찰상태"] == "needs_review"
    assert card["권장입찰가"] is None
    assert "전용면적" not in card
    assert card["감정가"] == 70000 and card["최저매각가"] == 56000
    assert card["건물면적"] == 84.2
    assert card["입찰기일"] == "2026-10-20"
    assert card["확인할것"][0] == "목록의 특수조건: 대항력 있는 임차인"
    assert card_from_hank(_sample(status="매각")) is None
    assert card_from_hank(_sample(lat=None)) is None


def test_refresh_writes_rows_and_tests_do_not_fetch(monkeypatch, tmp_path):
    transport = FakeTransport({"1": {"results": [{"id": 9, "latlng": [127.06, 37.65],
                                                  "unique_id": "2026타경9",
                                                  "address": "서울특별시 노원구 상계동 1",
                                                  "minb_amount": 560_000_000}], "next": None}})
    path = tmp_path / "external_hank.json"
    saved = refresh_hank_cache(transport, path=path)
    assert saved["refreshed"] is True and saved["count"] == 1
    cards = cards_from_rows(json.loads(path.read_text())["rows"])
    assert cards[0]["최저매각가"] == 56000 and cards[0]["권장입찰가"] is None
    called = []
    monkeypatch.setattr(external, "refresh_hank_cache", lambda: called.append(1))
    ensure_hank_cache()
    assert called == []


def _quiet(monkeypatch):
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {"노원구": {"급지": "B"}}})
    monkeypatch.setattr(api.md, "assessed_signal_labels", lambda today: {})
    monkeypatch.setattr(api, "_signal_map", lambda: {})
    monkeypatch.setattr(api, "_timing_asof", lambda: "2026-10-05")


def test_listing_card_imports_hank_without_a_recommended_bid(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(auction, "load", lambda: [])
    monkeypatch.setattr(external, "read_hank_cards", lambda: [card_from_hank(_sample())])
    row = api._build_listings({"경매"})[0]
    assert row["유형"] == "경매"
    assert row["key"] == "경매:hank:9"
    assert row["source"] == "hank"
    assert row["입찰상태"] == "needs_review"
    assert row["검토용상한"] is None
    assert row["총액"] == 56000
    assert row["lat"] == 37.65 and row["lng"] == 127.06
    assert row["지역"] == "노원구" and row["동"] == "상계동"
    assert row["평형"] is None
    assert row["시그널"] == "HELD"
    assert row["ref"]["건물면적"] == 84.2


def test_same_case_number_stays_on_the_manual_listing(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr(auction, "load", lambda: [
        auction.Listing(사건번호="2026타경9", 단지명="직접입력", region="노원구",
                        감정가=10000, 최저매각가=8000)])
    monkeypatch.setattr(external, "read_hank_cards", lambda: [card_from_hank(_sample())])
    rows = api._build_listings({"경매"})
    assert [row["단지명"] for row in rows] == ["직접입력"]


def test_auction_screen_appends_hank_pin_without_a_bid(monkeypatch):
    monkeypatch.setattr(api, "_auction_signal_map", lambda: {"노원구": "BUY"})
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {"노원구": {"급지": "B"}}})
    monkeypatch.setattr(auction, "load", lambda: [])
    monkeypatch.setattr(external, "read_hank_cards", lambda: [card_from_hank(_sample(address="서울특별시 중구 필동 1"))])
    held = auction_routes.auction_listings()["listings"][0]
    assert held["지역시그널"] == "HELD" and held["지역급지"] is None
    assert held["권장입찰가"] is None and held["lat"] == 37.65
    monkeypatch.setattr(external, "read_hank_cards", lambda: [card_from_hank(_sample())])
    shown = auction_routes.auction_listings()["listings"][0]
    assert shown["지역시그널"] == "BUY" and shown["지역급지"] == "B"
    assert shown["입찰상태"] == "needs_review" and shown["source"] == "hank"
