"""급매·찐매물 레이더 캐시 TTL(1일) / 스캔버전."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

from realty_signal import api


def test_radar_cache_stale_by_age(tmp_path, monkeypatch):
    p = tmp_path / "quicksale.json"
    p.write_text(json.dumps({"_scan_ver": 99, "listings": []}), encoding="utf-8")
    # mtime을 2일 전으로
    old = time.time() - 2 * 86400
    import os
    os.utime(p, (old, old))
    assert api._radar_cache_stale(p, min_ver=1) is True


def test_radar_cache_fresh(tmp_path):
    p = tmp_path / "certified.json"
    p.write_text(json.dumps({"_scan_ver": 1, "listings": []}), encoding="utf-8")
    assert api._radar_cache_stale(p, min_ver=1) is False


def test_radar_cache_old_ver(tmp_path):
    p = tmp_path / "certified.json"
    p.write_text(json.dumps({"_scan_ver": 0, "listings": []}), encoding="utf-8")
    assert api._radar_cache_stale(p, min_ver=1) is True


def test_failed_quicksale_scan_preserves_previous_cache_and_exposes_reason(tmp_path, monkeypatch):
    cache = tmp_path / "quicksale.json"
    cache.write_text(json.dumps({"ready": True, "listings": [{"단지명": "기존 결과"}],
                                 "regions": ["은평구"], "_scan_ver": api._QUICKSALE_SCAN_VER}),
                     encoding="utf-8")
    monkeypatch.setattr(api, "QUICKSALE_FILE", cache)
    monkeypatch.setattr(api, "_radar_scan_with_status", lambda *_a, **_k: ([], {
        "requested_regions": 1, "queryable_regions": 1, "successful_requests": 0,
        "failed_requests": 1, "skipped_regions": 0, "required_successes": 1,
        "usable": False, "failures": [{"region": "은평구", "error": "TimeoutError"}],
    }))

    result = api.quicksale_refresh({"regions": ["은평구"]})
    shown = api.quicksale()

    assert result["ok"] is False
    assert [row["단지명"] for row in shown["listings"]] == ["기존 결과"]
    assert shown["listings"][0]["급매갭"] is None
    assert shown["refresh"]["ok"] is False
    assert "기존 급매 결과" in shown["refresh"]["error"]


def test_quicksale_seed_does_not_require_public_data_key(monkeypatch):
    calls = []
    monkeypatch.setattr(api.config, "public_data_key", lambda: None)
    monkeypatch.setattr(api.config, "seoul_key", lambda: None)
    monkeypatch.setattr(api, "_quicksale_stale", lambda: True)
    monkeypatch.setattr(api, "_certified_stale", lambda: True)
    monkeypatch.setattr(api, "quicksale_refresh", lambda data: calls.append("급매") or {"ok": True})
    monkeypatch.setattr(api, "certified_refresh", lambda data: calls.append("찐매물") or {"ok": True})
    monkeypatch.setattr(api.md, "clear_caches", lambda: None)

    api._seed_if_missing()

    assert calls == ["급매", "찐매물"]


def test_scan_regions_keeps_only_owner_favorites_when_signal_calculation_fails(tmp_path, monkeypatch):
    sale = tmp_path / "quicksale.json"
    cert = tmp_path / "certified.json"
    sale.write_text(json.dumps({"regions": ["은평구", "없는지역"]}), encoding="utf-8")
    cert.write_text(json.dumps({"regions": ["은평구"]}), encoding="utf-8")
    monkeypatch.setattr(api, "QUICKSALE_FILE", sale)
    monkeypatch.setattr(api, "CERTIFIED_FILE", cert)
    monkeypatch.setattr(api, "_bundled_centroids", lambda: {"노원구": [37.6, 127.1], "은평구": [37.6, 126.9]})
    monkeypatch.setattr(api, "_signals_df", lambda: (_ for _ in ()).throw(FileNotFoundError("KB cache")))
    monkeypatch.setattr(api.md, "kb", lambda: SimpleNamespace(
        codes={"노원구": "1135000000", "은평구": "1138000000"},
        regions=["노원구", "은평구"], identity_verified=True))
    monkeypatch.setattr(api.config, "personal_listing_email", lambda: "owner@example.com")
    monkeypatch.setattr(api.db, "user_by_email", lambda email: {"id": 1} if email == "owner@example.com" else None)
    monkeypatch.setattr(api.db, "fav_list", lambda uid: [
        {"kind": "region", "key": "노원구"}, {"kind": "region", "key": "없는지역"}])

    assert api._scan_regions() == ["노원구"]  # 식별은 검증됐고 판정 계산만 실패한 경우.


def test_scan_targets_use_current_assessment_and_skip_retired_favorite(monkeypatch):
    import pandas as pd

    monkeypatch.setattr(api, "_signals_df", lambda: pd.DataFrame([
        {"region": "서구", "signal": "BUY"}, {"region": "강남구", "signal": "BUY"}]))
    monkeypatch.setattr(api.md, "assessed_signal_labels", lambda today: {
        "서구": {"assessment_status": "held", "display_signal": "HELD"},
        "강남구": {"assessment_status": "ready", "display_signal": "BUY"}})
    monkeypatch.setattr(api, "_bundled_centroids", lambda: {
        "서구": [37.545, 126.676], "강남구": [37.518, 127.047]})
    monkeypatch.setattr(api, "_code_of", lambda region: {"서구": "2826000000", "강남구": "1168000000"}[region])
    monkeypatch.setattr(api, "_sigungu_identity_at", lambda lat, lng: ("서해구", "인천", "28275"))
    monkeypatch.setattr(api.config, "personal_listing_email", lambda: "owner@example.com")
    monkeypatch.setattr(api.db, "user_by_email", lambda email: {"id": 1})
    monkeypatch.setattr(api.db, "fav_list", lambda uid: [{"kind": "region", "key": "서구"}])

    assert api._scan_regions() == ["강남구"]
    assert api._hanbang_regions() == ["강남구"]


def test_ambiguous_scan_center_requires_same_current_boundary_code(monkeypatch):
    monkeypatch.setattr(api, "_bundled_centroids", lambda: {"중구": [37.5638, 126.9976]})
    monkeypatch.setattr(api, "_sigungu_identity_at", lambda lat, lng: ("중구", "서울", "11140"))
    monkeypatch.setattr(api, "_code_of", lambda region: "2811000000")
    assert not api._scan_region_current("중구")
    monkeypatch.setattr(api, "_code_of", lambda region: "1114000000")
    assert api._scan_region_current("중구")


def test_radar_fetches_bundled_region_when_kb_signal_is_unavailable(monkeypatch):
    from realty_signal.ingest import baroezip

    monkeypatch.setattr(api, "_signal_map", lambda: (_ for _ in ()).throw(FileNotFoundError("KB cache")))
    monkeypatch.setattr(api, "_bundled_centroids", lambda: {"노원구": [37.6, 127.1]})
    monkeypatch.setattr(api, "_code_of", lambda region: (_ for _ in ()).throw(AssertionError("not needed")))
    monkeypatch.setattr(api, "_sido_of", lambda region: "서울")
    monkeypatch.setattr(api, "_region_centroid", lambda region, code: (37.6, 127.1))
    monkeypatch.setattr(api, "_sigungu_identity_at", lambda lat, lng: ("노원구", "서울", "11350"))
    monkeypatch.setattr(baroezip, "fetch_market_with_status", lambda *args, **kwargs: ([
        {"단지명": "테스트", "complex_no": "1", "평형": "25", "층": 10,
         "호가": 50000, "급매": True, "급매갭": -5, "lat": 37.6, "lng": 127.1}], None))

    rows, status = api._radar_scan_with_status(["노원구"])
    assert status["usable"] is True and status["signal_context"] == "unavailable"
    assert rows[0]["단지명"] == "테스트" and rows[0]["시그널"] == ""


def test_same_named_district_keeps_province_and_does_not_borrow_seoul_signal(monkeypatch):
    from realty_signal.ingest import baroezip

    monkeypatch.setattr(api, "_signal_map", lambda: {"중구": "STRONG_BUY"})
    monkeypatch.setattr(api, "_bundled_centroids", lambda: {"중구": [37.56, 126.99]})
    monkeypatch.setattr(api, "_region_centroid", lambda *_: (37.56, 126.99))
    monkeypatch.setattr(api, "_sido_of", lambda *_: "서울")
    monkeypatch.setattr(api, "_code_of", lambda *_: "1114000000")
    monkeypatch.setattr(api, "_sigungu_identity_at", lambda *_: ("중구", "인천", "28110"))
    monkeypatch.setattr(baroezip, "fetch_market_with_status", lambda *args, **kwargs: ([
        {"단지명": "동명단지", "complex_no": "1", "평형": 25, "층": 10,
         "호가": 50000, "급매": True, "급매갭": -5, "lat": 37.48, "lng": 126.62}], None))
    rows, status = api._radar_scan_with_status(["중구"])
    assert status["usable"] and rows[0]["지역"] == "중구"
    assert rows[0]["시도"] == "인천" and rows[0]["지역코드"] == "28110"
    assert rows[0]["시그널"] == ""


def test_geojson_province_identity_separates_same_named_polygons(monkeypatch):
    current = {entry[0]: entry[2] for entry in api._sigungu_polys() if entry[1] == "인천"}
    assert current["제물포구"] == "28125" and current["영종구"] == "28155"
    assert current["서해구"] == "28275" and current["검단구"] == "28290"
    assert not {"중구", "동구", "남구", "서구"} & current.keys()
    ring = [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]
    other = [[3, 0], [5, 0], [5, 2], [3, 2], [3, 0]]
    monkeypatch.setattr(api, "_sigungu_polys", lambda: [("중구", "서울", "11140", [ring], (0, 0, 2, 2)),
                                                          ("중구", "인천", "28110", [other], (3, 0, 5, 2))])
    assert api._sigungu_identity_at(1, 1) == ("중구", "서울", "11140")
    assert api._sigungu_identity_at(1, 4) == ("중구", "인천", "28110")


def test_2026_incheon_centroids_resolve_inside_new_districts():
    expected = {"제물포구": "28125", "영종구": "28155", "미추홀구": "28177",
                "서해구": "28275", "검단구": "28290"}
    for name, code in expected.items():
        lat, lng = api._bundled_centroids()[name]
        assert api._sigungu_identity_at(lat, lng) == (name, "인천", code)
    lat, lng = api._bundled_centroids()["중구"]
    assert api._sigungu_identity_at(lat, lng) == ("중구", "서울", "11140")


def test_listing_region_requires_matching_kb_code_and_source_province(monkeypatch):
    monkeypatch.setattr(api, "_code_of", lambda region: "2812500000")
    assert api._listing_region_matches_kb("제물포구", "인천", "28125")
    assert not api._listing_region_matches_kb("제물포구", "서울", "28125")
    assert not api._listing_region_matches_kb("제물포구", "인천", "28110")
    assert not api._listing_region_matches_kb("제물포구", "인천", "2812500000")
    assert not api._listing_region_matches_kb("제물포구", None)


def test_verified_hanbang_sido_can_resolve_current_code_without_guessing(monkeypatch):
    from types import SimpleNamespace

    source = SimpleNamespace(identity_verified=True, regions={"중구": None})
    monkeypatch.setattr(api, "_kb", lambda: source)
    monkeypatch.setattr(api, "_code_of", lambda region: "1114000000" if region == "중구" else "")
    assert api._verified_listing_region_code("중구", "서울") == "11140"
    assert api._verified_listing_region_code("중구", "인천") is None
    assert api._verified_listing_region_code("중구", None) is None
    source.identity_verified = False
    assert api._verified_listing_region_code("중구", "서울") is None
    source.identity_verified = True
    source.regions = {}
    assert api._verified_listing_region_code("중구", "서울") is None


def test_partial_refresh_keeps_unscanned_regions_with_stale_flag(tmp_path):
    from realty_signal.storage import atomic_json
    path = tmp_path / "quicksale.json"
    atomic_json(path, {"listings": [{"지역": "A", "naver_id": "a"}, {"지역": "B", "naver_id": "b"}]})
    out = api._preserve_unscanned(path, [{"지역": "A", "naver_id": "new-a"}], {"successful_regions": ["A"]})
    assert {row["naver_id"] for row in out} == {"new-a", "b"}
    assert out[1]["stale"] is True


def test_radar_response_distinguishes_verified_zero_from_failure(tmp_path):
    path = tmp_path / "quicksale.json"
    path.write_text(json.dumps({"ready": True, "listings": [], "regions": ["노원구"],
                                "_scan_ver": api._QUICKSALE_SCAN_VER}), encoding="utf-8")
    api._record_radar_refresh(path, {"ok": True, "failed_requests": 0})
    empty = api._radar_cached_response(path, api._QUICKSALE_SCAN_VER)
    assert empty["state"] == "empty" and empty["last_success_at"]

    api._record_radar_refresh(path, {"ok": True, "failed_requests": 1})
    assert api._radar_cached_response(path, api._QUICKSALE_SCAN_VER)["state"] == "partial_empty"

    api._record_radar_refresh(path, {"ok": False, "error": "upstream error"})
    assert api._radar_cached_response(path, api._QUICKSALE_SCAN_VER)["state"] == "stale_failed"

    path.unlink()
    failed = api._radar_cached_response(path, api._QUICKSALE_SCAN_VER)
    assert failed["state"] == "failed" and failed["listings"] == []


def test_old_radar_cache_cannot_restore_unverified_region_identity(tmp_path, monkeypatch):
    path = tmp_path / "quicksale.json"
    monkeypatch.setattr(api, "QUICKSALE_FILE", path)
    path.write_text(json.dumps({"ready": True, "_scan_ver": api._QUICKSALE_SCAN_VER - 1,
                                "listings": [{"지역": "중구", "시그널": "STRONG_BUY"}],
                                "regions": ["중구"]}), encoding="utf-8")
    api._record_radar_refresh(path, {"ok": True})
    response = api.quicksale()
    assert response["state"] == "identity_unverified" and response["listings"] == []
    assert api._radar_verified_rows(path, api._QUICKSALE_SCAN_VER) == []
    assert api._preserve_unscanned(path, [], {"successful_regions": []}) == []


def test_radar_response_marks_unverified_legacy_cache(tmp_path):
    path = tmp_path / "quicksale.json"
    path.write_text(json.dumps({"ready": True, "listings": [], "regions": [],
                                "_scan_ver": api._QUICKSALE_SCAN_VER}), encoding="utf-8")
    assert api._radar_cached_response(path, api._QUICKSALE_SCAN_VER)["state"] == "unverified"
