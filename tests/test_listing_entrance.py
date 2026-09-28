"""내가 지정한 경로 시작점은 개인별 후보이며 학구 판정 좌표를 바꾸지 않는다."""

from fastapi.testclient import TestClient

from realty_signal import api, auth, db
from realty_signal.ingest import school_zone
from realty_signal.services import listing_entrance, listing_location


def _row():
    return {"key": "일반매물:hb-1", "유형": "일반매물", "단지명": "가상단지", "지역": "노원구",
            "총액": 52000, "lat": 37.65, "lng": 127.07, "source": "hanbang", "ref": {"전용면적": 84.9}}


def test_entrance_distance_and_original_coordinate_required():
    assert listing_entrance.validate(_row(), 37.6505, 127.0705) == (37.6505, 127.0705)
    for lat, lng in ((37.7, 127.07), (91, 127.07)):
        try:
            listing_entrance.validate(_row(), lat, lng)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid entrance accepted")
    missing = {**_row(), "lat": None, "lng": None}
    try:
        listing_entrance.validate(missing, 37.65, 127.07)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown original coordinate accepted")


def test_user_marker_changes_routes_but_not_school_polygon(monkeypatch):
    looked_up = []
    monkeypatch.setattr(school_zone, "at_point", lambda lat, lng: (looked_up.append((lat, lng)) or
        {"status": "no_match", "zones": []}))
    monkeypatch.setattr(listing_location.config, "kakao_key", lambda: None)
    out = listing_location.build(_row(), {}, {"lat": 37.6505, "lng": 127.0705, "updated_at": 1})
    assert looked_up == [(37.65, 127.07)]
    assert out["mobility"]["origin_coordinate"] == [37.6505, 127.0705]
    assert out["mobility"]["origin_source"] == "user_marked_candidate"
    assert "원래 매물 표시 좌표" in out["school"]["coordinate_note"]


def test_entrance_api_owner_only_and_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "entrance.db")
    db._migrated[0] = False
    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "entrance-owner@example.com")
    monkeypatch.setattr(api, "_build_listings", lambda *_args, **_kwargs: [_row()])
    monkeypatch.setattr(school_zone, "at_point", lambda *_: {"status": "no_match", "zones": []})
    monkeypatch.setattr(listing_location.config, "kakao_key", lambda: None)
    owner_token, err = auth.signup("entrance-owner@example.com", "secret1", accept_tos=True)
    assert err is None
    guest_token, err = auth.signup("entrance-guest@example.com", "secret1", accept_tos=True)
    assert err is None
    owner, guest = TestClient(api.app), TestClient(api.app)
    owner.cookies.set(auth.COOKIE, owner_token)
    guest.cookies.set(auth.COOKIE, guest_token)
    path = "/api/listing-entrance"
    payload = {"key": "일반매물:hb-1", "lat": 37.6505, "lng": 127.0705}
    assert guest.put(path, json=payload).status_code == 403
    assert owner.put(path, json=payload).status_code == 200
    location = owner.get("/api/listing-location?key=일반매물%3Ahb-1")
    assert location.json()["mobility"]["origin_source"] == "user_marked_candidate"
    assert owner.put(path, json={**payload, "lat": 38.0}).status_code == 422
    assert owner.delete(path + "?key=일반매물%3Ahb-1").status_code == 200
    assert owner.get("/api/listing-location?key=일반매물%3Ahb-1").json()["mobility"]["origin_source"] == "listing_point"
    assert owner.put(path, json=payload).status_code == 200
    owner_uid = db.user_by_email("entrance-owner@example.com")["id"]
    monkeypatch.setattr(api, "_build_listings", lambda *_args, **_kwargs: [])
    assert owner.delete(path + "?key=일반매물%3Ahb-1").status_code == 200
    assert db.entrance_get(owner_uid, "일반매물:hb-1") is None
