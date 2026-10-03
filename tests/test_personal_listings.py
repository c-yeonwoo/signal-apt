"""제휴 외부 매물은 개인 계정 응답에만 보이고 다른 로그인 계정에는 섞이지 않는다."""

import json

from fastapi.testclient import TestClient

from realty_signal import api, auth, briefing, config, db


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
    assert "quicksale" not in {s["key"] for s in guest.get("/api/freshness").json()["sources"]}
    assert "quicksale" in {s["key"] for s in owner.get("/api/freshness").json()["sources"]}
    assert api._advisor_tool("get_listings", {"kind": "급매"}, uid=guest_uid)["reason"] == "personal_only"
    owner_tool = api._advisor_tool("get_listings", {"kind": "급매"}, uid=owner_uid)
    assert owner_tool["급매"][0]["단지명"] == "개인 매물"
    assert "국토부 실거래로 검증한 할인율이 아닙니다" in owner_tool["가격근거주의"]
    assert briefing._quicksales({"노원구"}, 60000, uid=guest_uid) == []
    monkeypatch.setattr(briefing, "QUICKSALE_FILE", sale)
    assert briefing._quicksales({"노원구"}, 60000, uid=owner_uid)[0]["단지명"] == "개인 매물"
    db.nbhd_snap_save(guest_uid, "노원구", "2026-W38", {"급매": 7, "시그널": "BUY"})
    db.nbhd_snap_save(guest_uid, "노원구", "2026-W39", {"급매": 2, "시그널": "WATCH"})
    diffs = api._user_nbhd_diffs(guest_uid, {"노원구"})["노원구"]
    assert all(change["key"] != "급매" for change in diffs)
