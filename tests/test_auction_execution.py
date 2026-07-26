"""경매 실행 — 붙여넣기 파서 · 낙찰 후 플랜 · 권리분석 반영 API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from realty_signal import api as app_api
from realty_signal import auction, auth, db

PASTE = """
서울중앙지방법원 2024타경51234
소재지  서울특별시 노원구 상계동 1-1 상계주공7단지 703동 1102호
물건종별 아파트          감정가  850,000,000원
                        최저가  544,000,000원 (64%)
전용면적 79.07㎡
입찰기일 2026-08-20
유찰 2회
"""


@pytest.fixture()
def lst(tmp_path, monkeypatch):
    monkeypatch.setattr(auction, "AUCTION_FILE", tmp_path / "auction.json")
    return auction.add({"단지명": "상계주공7", "region": "노원구", "감정가": 85000,
                        "최저매각가": 54400, "전용면적": 79.07, "시세": 82000,
                        "입찰기일": "2026-08-20"})


# ---------- 붙여넣기 파서 ----------
def test_parse_pulls_core_fields():
    p = auction.parse_text(PASTE)
    assert p["사건번호"] == "2024타경51234"
    assert p["감정가"] == 85000 and p["최저매각가"] == 54400   # 원 → 만원
    assert p["입찰기일"] == "2026-08-20"
    assert p["전용면적"] == 79.07
    assert p["유찰횟수"] == 2
    assert p["region"] == "노원구"
    assert "상계주공7" in p["단지명"]
    assert auction.parse_confidence(p) == "high"


def test_parse_omits_missing_fields_rather_than_zeroing():
    p = auction.parse_text("아무 의미 없는 텍스트")
    assert "감정가" not in p and "입찰기일" not in p
    assert auction.parse_confidence(p) == "low"


def test_parse_confidence_medium_when_partial():
    p = auction.parse_text("2024타경51234 감정가 850,000,000원")
    assert auction.parse_confidence(p) in ("medium", "high")


# ---------- 낙찰 후 플랜 ----------
def test_plan_uses_recommended_bid_before_win(lst):
    p = auction.plan(lst)
    assert p["추정입찰가"] is True
    assert p["낙찰가"] > 0
    assert p["기준일"] == "2026-08-20"
    assert [s["D"] for s in p["steps"]] == [0, 7, 14, 44, 45, 75, 105]


def test_plan_cash_adds_up(lst):
    p = auction.plan(lst, 60000)
    assert p["추정입찰가"] is False and p["낙찰가"] == 60000
    assert p["보증금"] == round(54400 * auction.BOND_RATE)
    assert p["경락잔금대출"] == round(60000 * auction.DEFAULTS["대출비율"])
    assert p["잔금"] == 60000 - p["보증금"] - p["경락잔금대출"]
    assert p["총현금"] == p["보증금"] + p["잔금"] + p["등기비"] + p["명도비"]


def test_plan_dates_shift_to_win_date(lst):
    auction.update(lst.id, {"낙찰일": "2026-09-01", "낙찰가": 60000})
    p = auction.plan(auction.get(lst.id))
    assert p["기준일"] == "2026-09-01"
    assert p["steps"][1]["날짜"] == "2026-09-08"


# ---------- API ----------
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "app.db")
    monkeypatch.setattr(auction, "AUCTION_FILE", tmp_path / "auction.json")
    db._migrated[0] = False
    for k in ("INVITE_CODES", "STUDENT_ALLOWLIST", "RAILWAY_ENVIRONMENT", "APP_ENV"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    token, err = auth.signup("boss@example.com", "secret1", accept_tos=True)
    assert err is None
    c = TestClient(app_api.app)
    c.cookies.set(auth.COOKIE, token)
    c.listing = auction.add({"단지명": "상계주공7", "region": "노원구", "감정가": 85000,
                             "최저매각가": 54400, "전용면적": 79.07, "시세": 82000,
                             "입찰기일": "2026-08-20"})
    return c


RIGHTS = {"권리": [{"종류": "근저당권", "일자": "2019-03-05", "금액": 24000}],
          "임차인": [{"전입일": "2018-01-02", "보증금": 30000, "배당요구": False}]}


def test_rights_preview_does_not_touch_listing(client):
    d = client.post("/api/auction/rights/preview", json=RIGHTS).json()
    assert d["분석"]["인수합계"] == 30000
    assert auction.get(client.listing.id).인수보증금 == 0


def test_rights_save_feeds_bid_calculation(client):
    before = client.get(f"/api/auction/calc/{client.listing.id}").json()["recommend"]["입찰가"]
    d = client.post(f"/api/auction/rights/{client.listing.id}", json=RIGHTS).json()
    assert d["분석"]["인수합계"] == 30000
    assert auction.get(client.listing.id).인수보증금 == 30000
    after = client.get(f"/api/auction/calc/{client.listing.id}").json()["recommend"]["입찰가"]
    assert after < before          # 인수금액만큼 낼 수 있는 값이 내려간다


def test_rights_roundtrip_keeps_input(client):
    client.post(f"/api/auction/rights/{client.listing.id}", json=RIGHTS)
    d = client.get(f"/api/auction/rights/{client.listing.id}").json()
    assert d["입력"]["임차인"][0]["보증금"] == 30000
    assert d["분석"]["등급"] == "위험"
    assert d["인수보증금"] == 30000


def test_rights_write_is_admin_only(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "someone-else@example.com")
    r = client.post(f"/api/auction/rights/{client.listing.id}", json=RIGHTS)
    assert r.json().get("ok") is False


def test_rights_404_for_unknown_listing(client):
    assert client.post("/api/auction/rights/nope", json=RIGHTS).status_code == 404


def test_plan_endpoint(client):
    d = client.get(f"/api/auction/plan/{client.listing.id}").json()
    assert d["ok"] and d["단지명"] == "상계주공7"
    assert len(d["plan"]["steps"]) == 7


def test_won_records_and_replans(client):
    d = client.post(f"/api/auction/won/{client.listing.id}",
                    json={"낙찰가": 60000, "낙찰일": "2026-09-01"}).json()
    assert d["ok"] and d["plan"]["기준일"] == "2026-09-01"
    assert d["plan"]["추정입찰가"] is False


def test_parse_route_works_without_ai_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    d = client.post("/api/auction/parse", json={"text": PASTE}).json()
    assert d["ok"] and d["source"] == "rule"
    assert d["parsed"]["사건번호"] == "2024타경51234"
