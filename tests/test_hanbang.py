"""한방 일반 아파트 매매: 필드 최소화·페이지 상태·개인 계정 격리."""

import json
import time

from fastapi.testclient import TestClient

from realty_signal import api, auth, db
from realty_signal.ingest import hanbang


def _raw(source_id=1, **overrides):
    return {"atlfslBscInfoPk": source_id, "atlfslKndCd": "01", "dlngSeCd": "A1",
            "useYn": "Y", "trdeAmt": 53_000, "prvuseArea": 84.5,
            "hsmpNm": "상계주공 102동\\n서울 노원구 상계동", "sggNm": "노원구",
            "hsmpInfoPk": 123, "flrCnt": 10, "atlfslLat": 37.6, "atlfslLot": 127.1,
            "atlfslTrsmDt": "2026-09-29", "roomCnt": 3, "lreaTelno": "010-0000-0000",
            "picTelno": "010-1111-1111", "atlfslExplnCn": "원문 설명",
            **overrides}


def _client(email):
    token, err = auth.signup(email, "secret1", accept_tos=True)
    assert err is None
    client = TestClient(api.app)
    client.cookies.set(auth.COOKIE, token)
    return client


def test_normalize_apartment_sale_only_and_whitelists_private_fields():
    row = hanbang.normalize(_raw())
    assert row["단지명"] == "상계주공" and row["호가"] == 53_000
    assert row["hanbang_id"] == "1" and row["평형"] == 25.6
    assert row["동"] == "상계동"
    assert row["방수"] == 3
    assert all(word not in json.dumps(row, ensure_ascii=False)
               for word in ("010-0000-0000", "010-1111-1111", "원문 설명"))
    assert hanbang.normalize(_raw(dlngSeCd="B1")) is None
    assert hanbang.normalize(_raw(atlfslKndCd="05")) is None
    assert hanbang.normalize(_raw(useYn="N")) is None
    assert hanbang.normalize(_raw(trdeAmt=0)) is None
    assert hanbang.normalize(_raw(roomCnt=0))["방수"] is None
    assert hanbang.normalize(_raw(roomCnt="three"))["방수"] is None
    assert hanbang.normalize(_raw(roomCnt=2.5))["방수"] is None
    assert hanbang.normalize(_raw(hsmpNm="상계주공\\n서울 강남구 역삼동"))["동"] is None
    assert hanbang.normalize(_raw(hsmpNm="상계주공\\n서울 노원구 102동"))["동"] is None


def test_pagination_marks_complete_only_after_short_page(monkeypatch):
    calls = []

    def fake_request(path, payload=None):
        if payload is None:
            return {"code": 200, "data": {"locInfo": {"ctpvCdPk": 1, "sggCdPk": 2,
                                                      "sggNm": "노원구", "ctpvNm": "서울특별시"}}}
        calls.append(payload)
        page = payload["page"]["page"]
        rows = [_raw(1), _raw(2)] if page == 1 else [_raw(3)]
        return {"code": 200, "data": {str(i): [row] for i, row in enumerate(rows)}}

    monkeypatch.setattr(hanbang, "_request", fake_request)
    monkeypatch.setattr(hanbang.time, "sleep", lambda _: None)
    rows, status = hanbang.fetch_region_with_status(37.6, 127.1, page_size=2)
    assert [row["hanbang_id"] for row in rows] == ["1", "2", "3"]
    assert status["complete"] and not status["capped"] and len(calls) == 2
    assert calls[0]["atlfslKndCdList"] == ["01"]
    assert calls[0]["dlngList"][0]["types"] == ["A1"]
    rows, status = hanbang.fetch_region_with_status(37.6, 127.1, max_pages=1, page_size=2)
    assert len(rows) == 2 and status["capped"] and not status["complete"]


def test_named_region_lookup_uses_exact_source_identity_and_same_listing_filter(monkeypatch):
    calls = []

    def fake_request(path, payload=None):
        calls.append((path, payload))
        if path.startswith("/com/api/restapi/getsggcd?"):
            assert "ctpvNm=%EC%84%9C%EC%9A%B8%ED%8A%B9%EB%B3%84%EC%8B%9C" in path
            assert "sggNm=%EB%85%B8%EC%9B%90%EA%B5%AC" in path
            return {"code": 200, "data": {"ctpvCdPk": 1, "sggCdPk": 151,
                                          "ctpvNm": "서울특별시", "sggNm": "노원구"}}
        return {"code": 200, "data": {"a": [_raw(1), _raw(2, dlngSeCd="B1")]}}

    monkeypatch.setattr(hanbang, "_request", fake_request)
    rows, status = hanbang.fetch_named_region_with_status("서울", "노원구", page_size=30)
    assert status["ok"] and status["complete"] and status["source_ctpv"] == "서울특별시"
    assert [row["hanbang_id"] for row in rows] == ["1"]
    assert calls[1][1]["rgnCode"] == [{"ctpv": 1, "sgg": 151, "emd": 0}]
    assert calls[1][1]["atlfslKndCdList"] == ["01"]
    assert calls[1][1]["dlngList"][0]["types"] == ["A1"]


def test_named_region_lookup_rejects_wrong_province_before_listing_request(monkeypatch):
    calls = []

    def fake_request(path, payload=None):
        calls.append(path)
        return {"code": 200, "data": {"ctpvCdPk": 4, "sggCdPk": 44,
                                       "ctpvNm": "인천광역시", "sggNm": "중구"}}

    monkeypatch.setattr(hanbang, "_request", fake_request)
    rows, status = hanbang.fetch_named_region_with_status("서울", "중구")
    assert rows == [] and not status["ok"] and len(calls) == 1
    assert "요청 지역과 다릅니다" in status["error"]


def test_source_error_is_not_empty_success(monkeypatch):
    monkeypatch.setattr(hanbang, "_request", lambda *_: (_ for _ in ()).throw(TimeoutError("timeout")))
    rows, status = hanbang.fetch_region_with_status(37.6, 127.1)
    assert rows == [] and not status["ok"] and "TimeoutError" in status["error"]


def test_favorite_complexes_select_regions_and_drop_other_names(monkeypatch):
    monkeypatch.setattr(api, "_bundled_centroids", lambda: {
        "노원구": [37.6, 127.1], "강남구": [37.5, 127.0]})
    monkeypatch.setattr(api, "_scan_region_current", lambda region: region == "노원구")
    monkeypatch.setattr(api.config, "personal_listing_email", lambda: "owner@example.com")
    monkeypatch.setattr(api.db, "user_by_email", lambda email: {"id": 7})
    monkeypatch.setattr(api.db, "fav_list", lambda uid: [
        {"kind": "region", "key": "강남구"},
        {"kind": "complex", "key": "노원구|상계주공아파트"},
        {"kind": "complex", "key": "강남구|은마"},
    ])
    assert api._hanbang_regions() == ["노원구"]
    monkeypatch.setattr(api, "_sido_of", lambda region: "서울")
    monkeypatch.setattr(api, "_signal_map", lambda: {"노원구": "BUY"})
    monkeypatch.setattr(hanbang, "fetch_region_with_status", lambda *_: (
        [hanbang.normalize(_raw(1)), hanbang.normalize(_raw(2, hsmpNm="다른단지\\n서울 노원구 상계동"))],
        {"ok": True, "source_sgg": "노원구", "source_ctpv": "서울특별시", "complete": True}))
    rows, status = api._hanbang_scan_with_status(
        ["노원구"], names={"노원구": {"상계주공아파트"}})
    assert [row["단지명"] for row in rows] == ["상계주공"]
    assert status["successful_regions"] == ["노원구"]


def test_refresh_without_favorite_complexes_replaces_the_district_sample(tmp_path, monkeypatch):
    cache = tmp_path / "hanbang.json"
    cache.write_text(json.dumps({"ready": True, "listings": [{"hanbang_id": "old"}],
                                 "_scan_ver": api._HANBANG_SCAN_VER}), encoding="utf-8")
    monkeypatch.setattr(api, "HANBANG_FILE", cache)
    monkeypatch.setattr(api, "_radar_refresh_status", lambda path: {})
    monkeypatch.setattr(api, "_hanbang_complex_targets", lambda: {})
    result = api.hanbang_refresh({})
    assert result["ok"] and result["scan"]["empty_reason"] == "no_favorite_complexes"
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert saved["listings"] == []


def test_scan_rejects_cross_province_same_named_district(monkeypatch):
    monkeypatch.setattr(api, "_bundled_centroids", lambda: {"중구": [37.56, 126.99]})
    monkeypatch.setattr(api, "_sido_of", lambda region: "서울")
    monkeypatch.setattr(api, "_signal_map", lambda: {"중구": "BUY"})
    monkeypatch.setattr(hanbang, "fetch_region_with_status", lambda *_: (
        [hanbang.normalize(_raw(sggNm="중구"))],
        {"ok": True, "source_sgg": "중구", "source_ctpv": "인천광역시", "complete": True}))
    rows, status = api._hanbang_scan_with_status(["중구"])
    assert rows == [] and status["failed_requests"] == 1
    assert status["successful_regions"] == []


def test_scan_recovers_from_coordinate_endpoint_failure_via_exact_named_region(monkeypatch):
    monkeypatch.setattr(api, "_bundled_centroids", lambda: {"노원구": [37.6, 127.1]})
    monkeypatch.setattr(api, "_sido_of", lambda region: "서울")
    monkeypatch.setattr(api, "_signal_map", lambda: {})
    monkeypatch.setattr(hanbang, "fetch_region_with_status", lambda *_: (
        [], {"ok": False, "phase": "location", "error": "ValueError: 원천 code -500"}))
    monkeypatch.setattr(hanbang, "fetch_named_region_with_status", lambda sido, region: (
        [hanbang.normalize(_raw())],
        {"ok": True, "source_sgg": "노원구", "source_ctpv": "서울특별시", "complete": True}))
    rows, status = api._hanbang_scan_with_status(["노원구"])
    assert len(rows) == 1 and rows[0]["지역"] == "노원구"
    assert status["successful_regions"] == ["노원구"] and status["failed_requests"] == 0
    assert status["named_lookup_regions"] == ["노원구"]


def test_scan_does_not_retry_named_lookup_after_listing_endpoint_failure(monkeypatch):
    monkeypatch.setattr(api, "_bundled_centroids", lambda: {"노원구": [37.6, 127.1]})
    monkeypatch.setattr(api, "_sido_of", lambda region: "서울")
    monkeypatch.setattr(api, "_signal_map", lambda: {})
    monkeypatch.setattr(hanbang, "fetch_region_with_status", lambda *_: (
        [], {"ok": False, "phase": "list", "error": "TimeoutError: 목록 지연"}))
    def unexpected_lookup(*_):
        raise AssertionError("목록 장애에 이름 조회를 재시도하면 안 됩니다")
    monkeypatch.setattr(hanbang, "fetch_named_region_with_status", unexpected_lookup)
    rows, status = api._hanbang_scan_with_status(["노원구"])
    assert rows == [] and status["failed_requests"] == 1 and not status["named_lookup_regions"]


def test_scan_drops_neighborhood_when_embedded_city_disagrees(monkeypatch):
    monkeypatch.setattr(api, "_bundled_centroids", lambda: {"노원구": [37.6, 127.1]})
    monkeypatch.setattr(api, "_sido_of", lambda region: "서울")
    monkeypatch.setattr(api, "_signal_map", lambda: {})
    monkeypatch.setattr(hanbang, "fetch_region_with_status", lambda *_: (
        [hanbang.normalize(_raw(hsmpNm="상계주공\\n인천 노원구 상계동"))],
        {"ok": True, "source_sgg": "노원구", "source_ctpv": "서울특별시", "complete": True}))
    rows, _ = api._hanbang_scan_with_status(["노원구"])
    assert len(rows) == 1 and rows[0]["동"] is None
    assert "_동표기시도" not in rows[0]


def test_scan_rejects_mixed_source_district_without_claiming_empty_success(monkeypatch):
    monkeypatch.setattr(api, "_bundled_centroids", lambda: {"중구": [37.56, 126.99]})
    monkeypatch.setattr(api, "_sido_of", lambda region: "서울")
    monkeypatch.setattr(api, "_signal_map", lambda: {"중구": "BUY"})
    monkeypatch.setattr(hanbang, "fetch_region_with_status", lambda *_: (
        [hanbang.normalize(_raw(1, sggNm="중구")),
         hanbang.normalize(_raw(2, sggNm="동구"))],
        {"ok": True, "source_sgg": "중구", "source_ctpv": "서울특별시", "complete": True}))
    rows, status = api._hanbang_scan_with_status(["중구"])
    assert rows == [] and status["failed_requests"] == 1
    assert status["successful_regions"] == []


def test_private_cache_and_integrated_listings_do_not_leak(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "owner@example.com")
    cache = tmp_path / "hanbang.json"
    cache.write_text(json.dumps({"ready": True, "listings": [
        {**hanbang.normalize(_raw()), "지역": "노원구", "시도": "서울", "fetched_at": time.time()}],
        "regions": ["노원구"], "_scan_ver": api._HANBANG_SCAN_VER}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(api, "HANBANG_FILE", cache)
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {"노원구": {"급지": "C"}}})
    last_date = api._kb().last_date
    monkeypatch.setattr(api, "_kb", lambda: SimpleNamespace(identity_verified=True,
                                                            regions={"노원구": None}, last_date=last_date))
    monkeypatch.setattr(api, "_code_of", lambda region: "1135000000" if region == "노원구" else "")
    owner, guest = _client("owner@example.com"), _client("guest@example.com")
    assert len(owner.get("/api/general-listings").json()["listings"]) == 1
    assert guest.get("/api/general-listings").json()["listings"] == []
    public = guest.get("/api/listings/all?types=일반매물").json()
    assert public["listings"] == [] and public["meta"]["private_access"] is False
    assert public["meta"]["general_refresh_failed"] is False
    assert public["meta"]["general_scope"] is None
    current = owner.get("/api/listings/all?types=일반매물").json()["listings"][0]
    assert current["동"] == "상계동"
    assert guest.post("/api/listing-locality", json={"key": current["key"], "dong": "상계동"}).status_code == 403
    assert owner.post("/api/listing-locality", json={"key": current["key"], "dong": "102동"}).status_code == 422
    assert owner.post("/api/listing-locality", json={"key": current["key"], "dong": "하계동"}).status_code == 200
    # A source-provided neighborhood takes precedence over a manual note.
    assert owner.get("/api/listings/all?types=일반매물").json()["listings"][0]["동"] == "상계동"
    source = json.loads(cache.read_text(encoding="utf-8"))
    source["listings"][0]["동"] = None
    cache.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    manual = owner.get("/api/listings/all?types=일반매물").json()["listings"][0]
    assert manual["동"] == "하계동" and manual["동출처"] == "직접 입력"
    assert guest.get("/api/listings/all?types=일반매물").json()["listings"] == []
    private = owner.get("/api/listings/all?types=일반매물").json()
    assert private["meta"]["private_access"] is True
    assert private["meta"]["general_refresh_failed"] is False
    assert private["meta"]["general_scope"] is None
    listing = private["listings"][0]
    assert listing["key"] == "일반매물:1" and listing["총액"] == 53_000
    assert listing["지역코드"] == "11350"
    assert listing["ref"]["방수"] == 3
    assert not listing["stale"]
    api._record_radar_refresh(cache, {"ok": False, "attempted_at": time.time()})
    failed = owner.get("/api/listings/all?types=일반매물").json()
    assert failed["listings"][0]["stale"]
    assert failed["meta"]["general_refresh_failed"] is True
    assert failed["meta"]["general_scope"] is None
    assert guest.get("/api/listings/all?types=일반매물").json()["meta"]["general_refresh_failed"] is False
    assert owner.get("/api/listings/all?types=청약").json()["meta"]["general_refresh_failed"] is False
    api._record_radar_refresh(cache, {"ok": True, "failed_requests": 1,
                                      "limited_regions": ["노원구"], "requested_regions": 2})
    partial = owner.get("/api/listings/all?types=일반매물").json()
    assert partial["meta"]["general_refresh_failed"] is False
    assert partial["meta"]["general_scope"] == {"failed_regions": 1, "page_limited_regions": 1}
    assert guest.get("/api/listings/all?types=일반매물").json()["meta"]["general_scope"] is None
    assert owner.get("/api/listings/all?types=청약").json()["meta"]["general_scope"] is None
    assert owner.post("/api/listing-watch", json={"key": listing["key"]}).json()["ok"]
    assert guest.post("/api/listing-watch", json={"key": listing["key"]}).status_code == 403
    assert guest.get("/api/listing-watch").json()["items"] == []


def test_failed_refresh_preserves_previous_cache(tmp_path, monkeypatch):
    cache = tmp_path / "hanbang.json"
    original = '{"ready":true,"listings":[{"hanbang_id":"1"}]}'
    cache.write_text(original, encoding="utf-8")
    monkeypatch.setattr(api, "HANBANG_FILE", cache)
    monkeypatch.setattr(api, "_bundled_centroids", lambda: {"노원구": [37.6, 127.1]})
    monkeypatch.setattr(api, "_hanbang_scan_with_status", lambda regions, names=None: ([], {
        "usable": False, "queryable_regions": 1, "successful_regions": [],
        "failed_requests": 1, "requested_regions": 1}))
    result = api.hanbang_refresh({"regions": ["노원구"]})
    assert not result["ok"] and cache.read_text(encoding="utf-8") == original
    assert api._radar_refresh_status(cache)["ok"] is False


def test_old_unverified_cache_is_hidden_from_general_and_integrated_listings(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "owner@example.com")
    cache = tmp_path / "hanbang.json"
    cache.write_text(json.dumps({"ready": True, "_scan_ver": 1, "listings": [
        {**hanbang.normalize(_raw()), "지역": "노원구", "fetched_at": time.time()}],
        "regions": ["노원구"]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(api, "HANBANG_FILE", cache)
    owner = _client("owner@example.com")
    general = owner.get("/api/general-listings").json()
    assert general["state"] == "identity_unverified" and general["listings"] == []
    assert api._hanbang_verified_rows() == []
    assert owner.get("/api/listings/all?types=일반매물").json()["listings"] == []


def test_limited_region_keeps_only_previous_rows_in_current_scope(tmp_path, monkeypatch):
    cache = tmp_path / "hanbang.json"
    cache.write_text(json.dumps({"_scan_ver": api._HANBANG_SCAN_VER, "listings": [
        {"hanbang_id": "old-in-scope", "지역": "노원구"},
        {"hanbang_id": "old-out-of-scope", "지역": "서초구"},
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(api, "HANBANG_FILE", cache)
    kept = api._hanbang_preserve_unscanned([], {
        "regions": ["노원구"], "complete_regions": [], "limited_regions": ["노원구"]})
    assert [row["hanbang_id"] for row in kept] == ["old-in-scope"]
    assert kept[0]["stale"] is True
