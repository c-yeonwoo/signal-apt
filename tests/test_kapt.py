"""공식 단지명 매칭과 개인정보 권한을 독립적으로 검증한다."""

from fastapi.testclient import TestClient

from realty_signal import api, auth, db
from realty_signal.ingest import kapt


def _raw(body):
    return {"header": {"resultCode": "00"}, "body": body}


def _mock_sources(monkeypatch, *, duplicate=False, detail_error=False):
    calls = []
    entry = {"kaptCode": "A10000001", "kaptName": "상계주공3단지", "bjdCode": "1135010500"}

    def get(url, params):
        calls.append((url, params))
        if url == kapt.LIST_URL:
            entries = [entry, {**entry, "kaptCode": "A10000002"}] if duplicate else [entry]
            return _raw({"items": entries, "totalCount": len(entries)})
        if url == kapt.BASIC_URL:
            return _raw({"item": {"kaptCode": "A10000001", "kaptName": "상계주공3단지",
                                   "kaptAddr": "서울 노원구 상계동", "kaptdaCnt": 220,
                                   "kaptBcompany": "시험건설", "kaptUsedate": "1990-01-01"}})
        if detail_error:
            raise OSError("temporary detail failure")
        return _raw({"item": {"kaptCode": "A10000001", "kaptName": "상계주공3단지",
                               "kaptdPcnt": 50, "kaptdPcntu": 100}})

    monkeypatch.setattr(kapt, "_get", get)
    return calls


def test_official_match_and_cache(monkeypatch):
    cache = {}
    monkeypatch.setattr(kapt.db, "kv_get", lambda key, max_age=None: cache.get(key))
    monkeypatch.setattr(kapt.db, "kv_set", lambda key, value: cache.__setitem__(key, value))
    monkeypatch.setattr(kapt.config, "public_data_key", lambda: "a%2Bb")
    calls = _mock_sources(monkeypatch)
    found = kapt.lookup("상계 주공 3단지", "11350")
    assert found["status"] == "observed" and found["households"] == 220
    assert found["parking_spaces"] == 150 and found["parking_per_household"] == 0.68
    assert calls[0][1]["serviceKey"] == "a+b" and calls[2][1]["ServiceKey"] == "a+b"
    assert kapt.lookup("상계주공3단지", "11350")["parking_spaces"] == 150
    assert len(calls) == 3


def test_ambiguous_or_missing_is_not_assigned(monkeypatch):
    monkeypatch.setattr(kapt.db, "kv_get", lambda *args, **kwargs: None)
    monkeypatch.setattr(kapt.db, "kv_set", lambda *args: None)
    monkeypatch.setattr(kapt.config, "public_data_key", lambda: "key")
    calls = _mock_sources(monkeypatch, duplicate=True)
    assert kapt.lookup("상계주공3단지", "11350")["status"] == "ambiguous"
    assert len(calls) == 1
    assert kapt.lookup("다른단지", "11350")["status"] == "unmatched"


def test_detail_failure_does_not_fabricate_parking(monkeypatch):
    monkeypatch.setattr(kapt.db, "kv_get", lambda *args, **kwargs: None)
    monkeypatch.setattr(kapt.db, "kv_set", lambda *args: None)
    monkeypatch.setattr(kapt.config, "public_data_key", lambda: "key")
    _mock_sources(monkeypatch, detail_error=True)
    found = kapt.lookup("상계주공3단지", "11350")
    assert found["status"] == "observed" and found["parking_status"] == "unavailable"
    assert found["parking_spaces"] is None and found["parking_per_household"] is None


def test_incomplete_official_list_cannot_make_false_unique_match(monkeypatch):
    monkeypatch.setattr(kapt.db, "kv_get", lambda *args, **kwargs: None)
    monkeypatch.setattr(kapt.db, "kv_set", lambda *args: None)
    monkeypatch.setattr(kapt.config, "public_data_key", lambda: "key")
    monkeypatch.setattr(kapt, "_get", lambda *_: _raw({"items": [
        {"kaptCode": "A10000001", "kaptName": "상계주공3단지", "bjdCode": "1135010500"}],
        "totalCount": None}))
    assert kapt.lookup("상계주공3단지", "11350")["status"] == "unavailable"


def test_kapt_endpoint_rechecks_personal_permission(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "kapt.db")
    db._migrated[0] = False
    monkeypatch.setenv("PERSONAL_LISTING_EMAIL", "kapt-owner@example.com")
    monkeypatch.setattr(api, "_build_listings", lambda *_args, **_kwargs: [{
        "key": "일반매물:hb-1", "유형": "일반매물", "단지명": "상계주공3단지", "지역": "노원구"}])
    monkeypatch.setattr(api, "_code_of", lambda *_: "11350")
    monkeypatch.setattr(kapt, "lookup", lambda name, code: {"status": "observed", "name": name, "code": code})
    owner_token, err = auth.signup("kapt-owner@example.com", "secret1", accept_tos=True)
    assert err is None
    guest_token, err = auth.signup("kapt-guest@example.com", "secret1", accept_tos=True)
    assert err is None
    owner, guest = TestClient(api.app), TestClient(api.app)
    owner.cookies.set(auth.COOKIE, owner_token)
    guest.cookies.set(auth.COOKIE, guest_token)
    url = "/api/listing-kapt?key=일반매물%3Ahb-1"
    assert guest.get(url).status_code == 403
    response = owner.get(url)
    assert response.status_code == 200 and response.json()["code"] == "11350"
    assert response.headers["cache-control"] == "private, no-store"
    owner_uid = db.user_by_email("kapt-owner@example.com")["id"]
    guest_uid = db.user_by_email("kapt-guest@example.com")["id"]
    assert api.advisor_tools(owner_uid, listing_key="일반매물:hb-1")(
        "get_selected_listing_kapt", {})["status"] == "observed"
    assert "error" in api.advisor_tools(guest_uid, listing_key="일반매물:hb-1")(
        "get_selected_listing_kapt", {})
