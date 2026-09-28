"""한방 일반 아파트 매매: 필드 최소화·페이지 상태·개인 계정 격리."""

import json

from fastapi.testclient import TestClient

from realty_signal import api, auth, db
from realty_signal.ingest import hanbang


def _raw(source_id=1, **overrides):
    return {"atlfslBscInfoPk": source_id, "atlfslKndCd": "01", "dlngSeCd": "A1",
            "useYn": "Y", "trdeAmt": 53_000, "prvuseArea": 84.5,
            "hsmpNm": "상계주공 102동\\n서울 노원구 상계동", "sggNm": "노원구",
            "hsmpInfoPk": 123, "flrCnt": 10, "atlfslLat": 37.6, "atlfslLot": 127.1,
            "atlfslTrsmDt": "2026-09-29", "lreaTelno": "010-0000-0000",
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
    assert all(word not in json.dumps(row, ensure_ascii=False)
               for word in ("010-0000-0000", "010-1111-1111", "원문 설명"))
    assert hanbang.normalize(_raw(dlngSeCd="B1")) is None
    assert hanbang.normalize(_raw(atlfslKndCd="05")) is None
    assert hanbang.normalize(_raw(useYn="N")) is None
    assert hanbang.normalize(_raw(trdeAmt=0)) is None


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


def test_source_error_is_not_empty_success(monkeypatch):
    monkeypatch.setattr(hanbang, "_request", lambda *_: (_ for _ in ()).throw(TimeoutError("timeout")))
    rows, status = hanbang.fetch_region_with_status(37.6, 127.1)
    assert rows == [] and not status["ok"] and "TimeoutError" in status["error"]


def test_private_cache_and_integrated_listings_do_not_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "owner@example.com")
    cache = tmp_path / "hanbang.json"
    cache.write_text(json.dumps({"ready": True, "listings": [
        {**hanbang.normalize(_raw()), "지역": "노원구", "fetched_at": 1_780_000_000}],
        "regions": ["노원구"], "_scan_ver": 1}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(api, "HANBANG_FILE", cache)
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {"노원구": {"급지": "C"}}})
    owner, guest = _client("owner@example.com"), _client("guest@example.com")
    assert len(owner.get("/api/general-listings").json()["listings"]) == 1
    assert guest.get("/api/general-listings").json()["listings"] == []
    assert guest.get("/api/listings/all?types=일반매물").json()["listings"] == []
    listing = owner.get("/api/listings/all?types=일반매물").json()["listings"][0]
    assert listing["key"] == "일반매물:1" and listing["총액"] == 53_000
    assert owner.post("/api/listing-watch", json={"key": listing["key"]}).json()["ok"]
    assert guest.post("/api/listing-watch", json={"key": listing["key"]}).status_code == 403
    assert guest.get("/api/listing-watch").json()["items"] == []


def test_failed_refresh_preserves_previous_cache(tmp_path, monkeypatch):
    cache = tmp_path / "hanbang.json"
    original = '{"ready":true,"listings":[{"hanbang_id":"1"}]}'
    cache.write_text(original, encoding="utf-8")
    monkeypatch.setattr(api, "HANBANG_FILE", cache)
    monkeypatch.setattr(api, "_bundled_centroids", lambda: {"노원구": [37.6, 127.1]})
    monkeypatch.setattr(api, "_hanbang_scan_with_status", lambda regions: ([], {
        "usable": False, "queryable_regions": 1, "successful_regions": [],
        "failed_requests": 1, "requested_regions": 1}))
    result = api.hanbang_refresh({"regions": ["노원구"]})
    assert not result["ok"] and cache.read_text(encoding="utf-8") == original
    assert api._radar_refresh_status(cache)["ok"] is False


def test_limited_region_keeps_only_previous_rows_in_current_scope(tmp_path, monkeypatch):
    cache = tmp_path / "hanbang.json"
    cache.write_text(json.dumps({"listings": [
        {"hanbang_id": "old-in-scope", "지역": "노원구"},
        {"hanbang_id": "old-out-of-scope", "지역": "서초구"},
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(api, "HANBANG_FILE", cache)
    kept = api._hanbang_preserve_unscanned([], {
        "regions": ["노원구"], "complete_regions": [], "limited_regions": ["노원구"]})
    assert [row["hanbang_id"] for row in kept] == ["old-in-scope"]
    assert kept[0]["stale"] is True
