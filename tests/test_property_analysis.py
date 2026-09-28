"""선택 매물 문맥, 실거래 보류, 개인 계정 격리 계약."""

from datetime import date

from fastapi.testclient import TestClient

from realty_signal import api, auth, db
from realty_signal.services import property_analysis as analysis


def _row(key="일반매물:hb-1", price=52000):
    return {"key": key, "유형": "일반매물", "단지명": "가상단지", "지역": "노원구",
            "총액": price, "lat": None, "lng": None, "source": "hanbang",
            "fetched_at": 1790630400, "ref": {"hanbang_id": "hb-1", "hanbang_complex_id": "cx-1",
                                         "전용면적": 84.9, "층": 12}}


def _detail(status="single_observed"):
    sample = [{"층": floor, "가격": amount, "거래월": "2026-09"}
              for floor, amount in ((10, 48000), (11, 50000), (13, 51000))]
    return {"identity_status": status, "총거래": 3,
            "평형별": [{"전용㎡": 84.9, "비교거래": {"상태": "관측", "건수": 3,
                       "기준일": date.today().isoformat(), "중앙값": 50000,
                       "최저": 48000, "최고": 51000, "층별표본": sample}}]}


def test_selected_listing_is_resolved_on_server_and_private_denied_before_read(monkeypatch):
    monkeypatch.setattr(api, "_build_listings", lambda *_args, **_kwargs: [_row()])
    assert analysis.resolve("일반매물:hb-1", private_allowed=True)["총액"] == 52000
    try:
        analysis.resolve("일반매물:hb-1", private_allowed=False)
    except PermissionError:
        pass
    else:
        raise AssertionError("personal listing was exposed")


def test_report_uses_exact_area_and_holds_unverified_identity():
    result = analysis.build(_row(), _detail())
    assert result["price"]["상태"] == "관측비교"
    assert result["price"]["중앙값"] == 50000
    assert result["price"]["표본수"] == 3
    assert len(result["trades"]) == 3
    assert result["listing"]["location_quality"] == "unknown"
    assert result["mobility"]["status"] == "unverified"
    ambiguous = analysis.build(_row(), _detail("ambiguous"))
    assert ambiguous["price"]["상태"] == "보류"
    assert ambiguous["trades"] == []


def test_analysis_endpoint_rechecks_personal_access_and_price(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "property.db")
    db._migrated[0] = False
    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "analysis-owner@example.com")
    monkeypatch.setattr(api, "_build_listings", lambda *_args, **_kwargs: [_row()])
    monkeypatch.setattr(api, "complex_detail", lambda *_args: _detail())
    owner_token, err = auth.signup("analysis-owner@example.com", "secret1", accept_tos=True)
    assert err is None
    guest_token, err = auth.signup("analysis-guest@example.com", "secret1", accept_tos=True)
    assert err is None
    owner, guest = TestClient(api.app), TestClient(api.app)
    owner.cookies.set(auth.COOKIE, owner_token)
    guest.cookies.set(auth.COOKIE, guest_token)
    path = "/api/listing-analysis?key=일반매물%3Ahb-1"
    denied = guest.get(path)
    assert denied.status_code == 403
    base = owner.get(path + "&stage=base")
    assert base.status_code == 200 and base.json()["listing"]["asking_manwon"] == 52000
    assert base.headers["cache-control"] == "private, no-store"
    full = owner.get(path)
    assert full.status_code == 200 and full.json()["price"]["상태"] == "관측비교"
    assert owner.get("/api/listing-analysis?key=일반매물%3Amissing").status_code == 404
