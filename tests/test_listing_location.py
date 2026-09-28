"""입지 근거는 부재·API 실패·개인 권한을 명확히 구분한다."""

from fastapi.testclient import TestClient

from realty_signal import api, auth, db
from realty_signal.ingest import kakao_places, school_zone
from realty_signal.services import listing_location


def _row(lat=37.65, lng=127.07):
    return {"key": "일반매물:hb-1", "유형": "일반매물", "단지명": "가상단지", "지역": "노원구",
            "총액": 52000, "lat": lat, "lng": lng, "source": "hanbang", "ref": {"전용면적": 84.9}}


def test_school_polygon_hole_and_boundary():
    geom = {"points": [[0, 0], [100, 0], [100, 100], [0, 100],
                       [30, 30], [70, 30], [70, 70], [30, 70]], "parts": [0, 4]}
    assert school_zone._contains(15, 15, geom) == (True, False)
    assert school_zone._contains(50, 50, geom) == (False, False)
    assert school_zone._contains(5, 50, geom) == (True, True)


def test_missing_coordinates_do_not_query_external_sources(monkeypatch):
    monkeypatch.setattr(school_zone, "at_point", lambda *_: (_ for _ in ()).throw(AssertionError()))
    out = listing_location.build(_row(None, None))
    assert out["school"]["status"] == "unverified"
    assert out["mobility"]["status"] == "unverified"


def test_official_school_candidate_survives_missing_kakao_key(monkeypatch):
    monkeypatch.setattr(school_zone, "at_point", lambda *_: {"status": "candidate", "zones": [
        {"name": "가상 통학구역", "asof": "2026-03-20", "schools": [{"name": "가상초"}]}]})
    monkeypatch.setattr(listing_location.config, "kakao_key", lambda: None)
    out = listing_location.build(_row())
    assert out["school"]["zones"][0]["schools"][0]["name"] == "가상초"
    assert out["mobility"]["status"] == "unverified"
    assert "출입구" in out["school"]["coordinate_note"]


def test_places_and_routes_are_labeled_as_point_estimates(monkeypatch):
    monkeypatch.setattr(listing_location.config, "kakao_key", lambda: "test")
    monkeypatch.setattr(school_zone, "at_point", lambda *_: {"status": "no_match", "zones": []})
    def places(_lat, _lng, code, _key):
        return {"label": kakao_places.POI_TYPES[code], "radius_m": 1800,
                "count_within_radius": 1, "places": [{"name": "가상역", "lat": 37.66, "lng": 127.08}]}
    monkeypatch.setattr(kakao_places, "nearby", places)
    monkeypatch.setattr(kakao_places, "route", lambda *args: {"status": "observed", "minutes": 9,
        "distance_m": 700, "path": [[37.65, 127.07], [37.66, 127.08]], "origin_quality": "listing_point_unverified_entrance"})
    out = listing_location.build(_row(), {"직장": "회사", "직장lat": 37.5, "직장lng": 127.0})
    assert out["mobility"]["station_walk"]["minutes"] == 9
    assert out["mobility"]["work_transit"]["destination"] == "회사"
    assert "출입구" in out["mobility"]["reason"]


def test_kakao_parse_walk_and_public_transit(monkeypatch):
    def get(path, *_):
        if path.endswith("walk"):
            return {"status": "OK", "route": {"properties": {"totalTime": 541, "totalDistance": 701},
                "legs": [{"steps": [{"path": {"points": [[127.07, 37.65], [127.08, 37.66]]}}]}]}}
        return {"status": "OK", "routes": [{"properties": {"totalTime": 1820, "transfers": 1},
            "steps": []}]}
    monkeypatch.setattr(kakao_places, "_get", get)
    walk = kakao_places.route(37.65, 127.07, 37.66, 127.08, "walk", "x")
    transit = kakao_places.route(37.65, 127.07, 37.5, 127.0, "publictraffic", "x")
    assert walk["minutes"] == 10 and len(walk["path"]) == 2
    assert transit["minutes"] == 31 and transit["transfers"] == 1


def test_location_endpoint_rechecks_personal_permission(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "location.db")
    db._migrated[0] = False
    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "location-owner@example.com")
    monkeypatch.setattr(api, "_build_listings", lambda *_args, **_kwargs: [_row()])
    monkeypatch.setattr(listing_location, "build", lambda *_args: {"school": {"status": "candidate"}})
    owner_token, err = auth.signup("location-owner@example.com", "secret1", accept_tos=True)
    assert err is None
    guest_token, err = auth.signup("location-guest@example.com", "secret1", accept_tos=True)
    assert err is None
    owner, guest = TestClient(api.app), TestClient(api.app)
    owner.cookies.set(auth.COOKIE, owner_token)
    guest.cookies.set(auth.COOKIE, guest_token)
    url = "/api/listing-location?key=일반매물%3Ahb-1"
    assert guest.get(url).status_code == 403
    response = owner.get(url)
    assert response.status_code == 200 and response.json()["school"]["status"] == "candidate"
    assert response.headers["cache-control"] == "private, no-store"
