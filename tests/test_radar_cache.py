"""급매·찐매물 레이더 캐시 TTL(1일) / 스캔버전."""

from __future__ import annotations

import json
import time

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
    assert shown["listings"] == [{"단지명": "기존 결과"}]
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


def test_scan_regions_keeps_only_owner_favorites_without_kb(tmp_path, monkeypatch):
    sale = tmp_path / "quicksale.json"
    cert = tmp_path / "certified.json"
    sale.write_text(json.dumps({"regions": ["은평구", "없는지역"]}), encoding="utf-8")
    cert.write_text(json.dumps({"regions": ["은평구"]}), encoding="utf-8")
    monkeypatch.setattr(api, "QUICKSALE_FILE", sale)
    monkeypatch.setattr(api, "CERTIFIED_FILE", cert)
    monkeypatch.setattr(api, "_bundled_centroids", lambda: {"노원구": [37.6, 127.1], "은평구": [37.6, 126.9]})
    monkeypatch.setattr(api, "_signals_df", lambda: (_ for _ in ()).throw(FileNotFoundError("KB cache")))
    monkeypatch.setattr(api.config, "personal_listing_email", lambda: "owner@example.com")
    monkeypatch.setattr(api.db, "user_by_email", lambda email: {"id": 1} if email == "owner@example.com" else None)
    monkeypatch.setattr(api.db, "fav_list", lambda uid: [
        {"kind": "region", "key": "노원구"}, {"kind": "region", "key": "없는지역"}])

    assert api._scan_regions() == ["노원구"]  # 과거 캐시는 다른 계정의 관심지역일 수 있다.


def test_radar_fetches_bundled_region_when_kb_signal_is_unavailable(monkeypatch):
    from realty_signal.ingest import baroezip

    monkeypatch.setattr(api, "_signal_map", lambda: (_ for _ in ()).throw(FileNotFoundError("KB cache")))
    monkeypatch.setattr(api, "_bundled_centroids", lambda: {"노원구": [37.6, 127.1]})
    monkeypatch.setattr(api, "_code_of", lambda region: (_ for _ in ()).throw(AssertionError("not needed")))
    monkeypatch.setattr(api, "_sido_of", lambda region: "서울")
    monkeypatch.setattr(api, "_region_centroid", lambda region, code: (37.6, 127.1))
    monkeypatch.setattr(api, "_sigungu_identity_at", lambda lat, lng: ("노원구", "서울"))
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
    monkeypatch.setattr(api, "_sigungu_identity_at", lambda *_: ("중구", "인천"))
    monkeypatch.setattr(baroezip, "fetch_market_with_status", lambda *args, **kwargs: ([
        {"단지명": "동명단지", "complex_no": "1", "평형": 25, "층": 10,
         "호가": 50000, "급매": True, "급매갭": -5, "lat": 37.48, "lng": 126.62}], None))
    rows, status = api._radar_scan_with_status(["중구"])
    assert status["usable"] and rows[0]["지역"] == "중구"
    assert rows[0]["시도"] == "인천" and rows[0]["시그널"] == ""


def test_geojson_province_identity_separates_same_named_polygons(monkeypatch):
    actual = {entry[1] for entry in api._sigungu_polys() if entry[0] == "중구"}
    assert actual == {"서울", "인천"}
    ring = [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]
    other = [[3, 0], [5, 0], [5, 2], [3, 2], [3, 0]]
    monkeypatch.setattr(api, "_sigungu_polys", lambda: [("중구", "서울", [ring], (0, 0, 2, 2)),
                                                          ("중구", "인천", [other], (3, 0, 5, 2))])
    assert api._sigungu_identity_at(1, 1) == ("중구", "서울")
    assert api._sigungu_identity_at(1, 4) == ("중구", "인천")


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
