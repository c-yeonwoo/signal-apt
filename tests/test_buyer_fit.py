"""확정 매수력과 선택 매물·대안 통근의 분리된 판단."""

from fastapi.testclient import TestClient

from realty_signal import api, auth, db
from realty_signal.ingest import kakao_places
from realty_signal.services import listing_discovery, property_analysis


def _row(key, name="시험단지", price=50000, lat=37.65):
    return {"key": key, "유형": "일반매물", "단지명": name, "지역": "노원구",
            "총액": price, "lat": lat, "lng": 127.07, "ref": {"전용면적": 84.9}}


def test_budget_fit_uses_confirmed_buying_power_only():
    listing = property_analysis.snapshot(_row("일반매물:a"))
    assert property_analysis.buyer_fit(listing, {})["status"] == "unknown"
    assert property_analysis.buyer_fit(listing, {"가용자본": 80000})["status"] == "unknown"
    within = property_analysis.buyer_fit(listing, {"매수력": {"최대매수가": 52000}})
    assert within["status"] == "within" and within["gap_manwon"] == 2000
    assert "대출 승인" in within["note"]
    above = property_analysis.buyer_fit(listing, {"매수력": {"최대매수가": 48000}})
    assert above["status"] == "above" and above["gap_manwon"] == -2000


def test_commute_is_on_demand_bounded_and_does_not_expose_work_coordinates(monkeypatch):
    calls = []
    def route(lat, lng, wlat, wlng, mode, key):
        calls.append((lat, lng, wlat, wlng, mode, key))
        if lat == 37.66:
            raise OSError("one provider failure")
        return {"status": "observed", "minutes": 38 if lat == 37.65 else 29, "path": [[lat, lng]]}
    monkeypatch.setattr(kakao_places, "route", route)
    row = _row("일반매물:a")
    choices = [_row("일반매물:b", lat=37.64), _row("일반매물:c", lat=37.66)]
    out = listing_discovery.commute_candidates(row, choices,
        {"직장lat": 37.5, "직장lng": 127.0}, "test")
    assert out["selected"]["minutes"] == 38
    assert out["alternatives"][0]["minutes"] == 29
    assert out["alternatives"][1]["status"] == "unavailable"
    assert len(calls) == 3 and "37.5" not in str(out)
    assert "path" not in str(out)
    assert listing_discovery.commute_candidates(row, choices, {}, "test")["status"] == "missing_work"
    assert len(calls) == 3


def test_commute_caps_candidates_and_uses_valid_user_origin(monkeypatch):
    calls = []
    monkeypatch.setattr(kakao_places, "route", lambda lat, lng, *_: (
        calls.append((lat, lng)) or {"status": "observed", "minutes": 30}))
    row = _row("일반매물:a")
    choices = [_row(f"일반매물:{i}", name=f"후보{i}", lat=37.64) for i in range(9)]
    result = listing_discovery.commute_candidates(row, choices,
        {"직장lat": 37.5, "직장lng": 127.0}, "test",
        entrance_get=lambda key: {"lat": 37.6505, "lng": 127.0705} if key == row["key"] else None)
    assert len(result["alternatives"]) == 6 and len(calls) == 7
    assert result["selected"]["origin_source"] == "user_marked_candidate"
    assert (37.6505, 127.0705) in calls


def test_commute_endpoint_rechecks_private_listing_permission(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "buyer-fit.db")
    db._migrated[0] = False
    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "fit-owner@example.com")
    monkeypatch.setattr(api, "_build_listings", lambda *_args, **_kwargs: [
        _row("일반매물:a"), _row("일반매물:b", lat=37.64)])
    monkeypatch.setattr(listing_discovery, "commute_candidates", lambda row, candidates, *_args: {
        "status": "partial", "selected": {"key": row["key"]},
        "alternatives": [{"key": x["key"]} for x in candidates]})
    owner_token, err = auth.signup("fit-owner@example.com", "secret1", accept_tos=True)
    assert err is None
    guest_token, err = auth.signup("fit-guest@example.com", "secret1", accept_tos=True)
    assert err is None
    owner, guest = TestClient(api.app), TestClient(api.app)
    owner.cookies.set(auth.COOKIE, owner_token)
    guest.cookies.set(auth.COOKIE, guest_token)
    path = "/api/listing-discovery/commute?key=일반매물%3Aa"
    assert guest.get(path).status_code == 403
    response = owner.get(path)
    assert response.status_code == 200 and response.json()["selected"]["key"] == "일반매물:a"
    assert response.headers["cache-control"] == "private, no-store"
