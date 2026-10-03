"""User-facing regional filters must not turn a substring into another place."""

import json
from types import SimpleNamespace

import pandas as pd

from realty_signal import api
from realty_signal import auction


def test_myfeed_counts_only_exact_and_verified_region_rows(monkeypatch, tmp_path):
    source = tmp_path / "quicksale.json"
    source.touch()
    monkeypatch.setattr(api, "QUICKSALE_FILE", source)
    monkeypatch.setattr(api, "_uid", lambda _request: 7)
    monkeypatch.setattr(api.db, "fav_list", lambda _uid: [])
    monkeypatch.setattr(api.db, "actionable_region_favs", lambda _uid: ["분당구"])
    monkeypatch.setattr(api, "_personal_listings_allowed", lambda **_kwargs: True)
    monkeypatch.setattr(api, "_display_signal_map", lambda: {"분당구": "HELD"})
    monkeypatch.setattr(api, "_listing_region_matches_kb", lambda *_args: True)
    monkeypatch.setattr(api, "_radar_verified_rows", lambda *_args: [
        {"지역": "성남시 분당구", "급매갭": -8}, {"지역": "분당구", "급매갭": -3}])
    monkeypatch.setattr(api, "_presale", lambda: [
        {"지역": "성남시 분당구", "_signal_region": None, "Dday": 2, "단지명": "다른 단지"},
        {"지역": "분당구", "_signal_region": None, "Dday": 2, "단지명": "검증 안 된 단지"},
        {"지역": "분당구", "_signal_region": "분당구", "Dday": 2, "단지명": "확인 단지"}])
    monkeypatch.setattr(api, "_kb", lambda: SimpleNamespace(last_date=pd.Timestamp("2026-10-04")))

    item = api.myfeed(object())["items"][0]
    assert item["급매"] == 1 and item["급매갭"] == -3
    assert item["청약임박"] == 1 and item["청약단지"] == "확인 단지"


def test_nick_region_and_listing_filters_require_exact_region(monkeypatch):
    monkeypatch.setattr(api, "signals", lambda: [
        {"region": "성남시 분당구", "display_signal": "BUY", "assessment_status": "ready"}])
    assert "error" in api._advisor_tool("get_region_signal", {"region": "분당구"})
    monkeypatch.setattr(api, "_presale_visible_items", lambda: [
        {"지역": "분당구", "시도": "부산", "주소": "분당구", "Dday": 1},
        {"지역": "분당구", "시도": "경기", "주소": "분당구", "Dday": 1}])
    monkeypatch.setattr(api, "_listing_region_matches_kb", lambda _region, sido: sido == "경기")
    assert len(api._advisor_tool("get_presale", {"region": "분당구"})["presales"]) == 1
    monkeypatch.setattr(api, "_build_listings", lambda *_args, **_kwargs: [
        {"지역": "성남시 분당구", "타이밍점수": 99}, {"지역": "분당구", "타이밍점수": 2}])
    assert [row["지역"] for row in api._advisor_tool(
        "get_timing", {"layer": "listing", "region": "분당구"})["listings"]] == ["분당구"]


def test_nick_auction_filter_does_not_claim_ambiguous_district(monkeypatch, tmp_path):
    source = tmp_path / "auction.json"
    source.write_text(json.dumps([
        {"region": "중구", "단지명": "출처 미확인"},
        {"region": "성남시 분당구", "단지명": "다른 지역"},
        {"region": "분당구", "단지명": "정확한 지역"}], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(auction, "AUCTION_FILE", source)
    assert "result" in api._advisor_tool("get_listings", {"kind": "경매", "region": "중구"})
    rows = api._advisor_tool("get_listings", {"kind": "경매", "region": "분당구"})["경매"]
    assert [row["단지명"] for row in rows] == ["정확한 지역"]


def test_nick_quicksale_filter_does_not_include_parent_or_child_name(monkeypatch):
    monkeypatch.setattr(api, "_personal_listings_allowed", lambda **_kwargs: True)
    monkeypatch.setattr(api, "_display_signal_map", lambda: {"분당구": "HELD"})
    monkeypatch.setattr(api, "_listing_region_matches_kb", lambda *_args: True)
    monkeypatch.setattr(api, "_radar_verified_rows", lambda *_args: [
        {"단지명": "다른 지역", "지역": "성남시 분당구", "급매갭": -10},
        {"단지명": "정확한 지역", "지역": "분당구", "급매갭": -5}])
    result = api._advisor_tool("get_listings", {"kind": "급매", "region": "분당구"}, uid=7)
    assert [row["단지명"] for row in result["급매"]] == ["정확한 지역"]
