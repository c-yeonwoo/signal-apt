"""제휴 외부 매물은 개인 계정 응답에만 보이고 다른 로그인 계정에는 섞이지 않는다."""

import json

from fastapi.testclient import TestClient

from realty_signal import api, auth, briefing, config, db
from realty_signal.routes import market


def _client(email):
    token, err = auth.signup(email, "secret1", accept_tos=True)
    assert err is None
    client = TestClient(api.app)
    client.cookies.set(auth.COOKIE, token)
    return client, db.session_user(token)["id"]


def test_single_admin_fallback_is_fail_closed_for_multiple_admins(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "owner@example.com")
    assert config.personal_listing_allowed("OWNER@example.com")
    monkeypatch.setenv("ADMIN_EMAILS", "owner@example.com,other@example.com")
    assert not config.personal_listing_allowed("owner@example.com")
    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "owner@example.com")
    assert config.personal_listing_allowed("owner@example.com")
    assert not config.personal_listing_allowed("other@example.com")


def test_radar_api_and_integrated_listing_do_not_leak_to_other_users(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "owner@example.com")
    sale = tmp_path / "quicksale.json"
    cert = tmp_path / "certified.json"
    listing = {"단지명": "개인 매물", "지역": "노원구", "시도": "서울", "지역코드": "11350", "호가": 50000,
               "평형": 25, "급매갭": -8, "시그널": "BUY", "naver_id": "personal-1"}
    for path in (sale, cert):
        path.write_text(json.dumps({"ready": True, "listings": [listing], "regions": ["노원구"],
                                    "_scan_ver": 99}), encoding="utf-8")
    monkeypatch.setattr(api, "QUICKSALE_FILE", sale)
    monkeypatch.setattr(api, "CERTIFIED_FILE", cert)
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {"노원구": {"급지": "C"}}})
    owner, owner_uid = _client("owner@example.com")
    guest, guest_uid = _client("guest@example.com")

    for path in ("/api/quicksale", "/api/certified"):
        assert guest.get(path).json()["state"] == "personal_only"
        assert guest.get(path).json()["listings"] == []
        response = owner.get(path)
        assert response.json()["listings"][0]["단지명"] == "개인 매물"
        assert response.headers["cache-control"] == "private, no-store"

    assert guest.get("/api/listings/all?types=급매,찐매물").json()["listings"] == []
    assert len(owner.get("/api/listings/all?types=급매").json()["listings"]) == 1
    combined = owner.get("/api/listings/all?types=급매,찐매물").json()
    assert len(combined["listings"]) == 1
    assert combined["meta"]["duplicate_records_collapsed"] == 1
    assert set(combined["listings"][0]["listing_aliases"]) == {"급매:personal-1", "찐매물:personal-1"}
    assert "quicksale" not in {s["key"] for s in guest.get("/api/freshness").json()["sources"]}
    assert "quicksale" in {s["key"] for s in owner.get("/api/freshness").json()["sources"]}
    assert api._advisor_tool("get_listings", {"kind": "급매"}, uid=guest_uid)["reason"] == "personal_only"
    owner_tool = api._advisor_tool("get_listings", {"kind": "급매"}, uid=owner_uid)
    assert owner_tool["급매"][0]["단지명"] == "개인 매물"
    assert "동일 전용면적·거래조건이 확인되지 않은 중위가격과 갭은 제공하지 않습니다" in owner_tool["가격근거주의"]
    assert owner_tool["급매"][0]["급매갭"] is None
    assert briefing._quicksales({"노원구"}, 60000, uid=guest_uid) == []
    monkeypatch.setattr(briefing, "QUICKSALE_FILE", sale)
    assert briefing._quicksales({"노원구"}, 60000, uid=owner_uid)[0]["단지명"] == "개인 매물"
    db.nbhd_snap_save(guest_uid, "노원구", "2026-W38", {"급매": 7, "시그널": "BUY"})
    db.nbhd_snap_save(guest_uid, "노원구", "2026-W39", {"급매": 2, "시그널": "WATCH"})
    diffs = api._user_nbhd_diffs(guest_uid, {"노원구"})["노원구"]
    assert all(change["key"] != "급매" for change in diffs)


def test_private_freshness_exposes_scan_outcome_without_raw_failure_or_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "freshness-owner@example.com")
    paths = {}
    for key, field in (("quicksale", "QUICKSALE_FILE"), ("certified", "CERTIFIED_FILE"),
                       ("hanbang", "HANBANG_FILE")):
        path = tmp_path / f"{key}.json"
        path.write_text("{}", encoding="utf-8")
        paths[key] = path
        monkeypatch.setattr(api, field, path)
    api._record_radar_refresh(paths["hanbang"], {
        "attempted_at": 1_800_000_000.5, "ok": False, "failed_requests": 2,
        "limited_regions": ["A"], "error": "private source error",
    })
    owner, _ = _client("freshness-owner@example.com")
    guest, _ = _client("freshness-guest@example.com")
    owner_source = {item["key"]: item for item in owner.get("/api/freshness").json()["sources"]}
    guest_source = {item["key"]: item for item in guest.get("/api/freshness").json()["sources"]}
    assert not set(paths) & set(guest_source)
    assert owner_source["hanbang"]["last_attempt_at"] == 1_800_000_000
    assert owner_source["hanbang"]["last_refresh_ok"] is False
    assert owner_source["hanbang"]["failed_region_count"] == 2
    assert owner_source["hanbang"]["page_limited_region_count"] == 1
    assert "private source error" not in str(owner_source["hanbang"])
    assert str(tmp_path) not in str(owner_source["hanbang"])
    assert market._listing_refresh_summary(paths["quicksale"])["last_refresh_ok"] is None
