"""통근 조건은 현재 매물·저장 직장·선택 조회에만 근거한다."""

import json

import pytest
from fastapi import HTTPException

from realty_signal import db
from realty_signal.routes import reports_v2
from realty_signal.services import discovery_commute as commute
from realty_signal.services import discovery_v2

from test_discovery_v2 import _row


def _located(key="one"):
    row = _row(key, price=50000, area=70)
    row.update(lat=37.65, lng=127.06, fetched_at=0)
    return row


def _profile(lat=37.50):
    return {"직장": "직장", "직장lat": lat, "직장lng": 127.02}


def test_commute_condition_has_pass_unknown_fail_and_strict_input():
    row = _located()
    row["fetched_at"] = 1790000000
    spec = {"max_commute_minutes": 60, "include_exceeded": True}
    assert discovery_v2.discover([row], spec)["counts"]["verify"] == 1
    passing = discovery_v2.discover([row], spec, commute_by_key={row["key"]: {
        "status": "observed", "minutes": 55}})
    assert passing["groups"]["matched"][0]["constraints"][0]["status"] == "pass"
    failing = discovery_v2.discover([row], spec, commute_by_key={row["key"]: {
        "status": "observed", "minutes": 65}})
    assert failing["groups"]["exceeded"][0]["constraints"][0]["status"] == "fail"
    assert failing["single_condition_relaxations"] == {"max_commute_minutes": 1}
    for invalid in (0, 241, 60.5, True, "60"):
        with pytest.raises(ValueError, match="invalid_condition_value"):
            discovery_v2.validate({"max_commute_minutes": invalid})


def test_commute_search_surfaces_fetchable_candidates_before_missing_coordinates():
    missing = _row("missing", price=50000)
    missing["fetched_at"] = 1791000000
    located = _located("located")
    located["fetched_at"] = 1790000000
    first = discovery_v2.discover([missing, located], {"max_commute_minutes": 60, "limit": 1})
    assert first["groups"]["verify"][0]["listing"]["name"] == "located"
    second = discovery_v2.discover([missing, located], {
        "max_commute_minutes": 60, "limit": 1, "cursor": first["next_cursor"]})
    assert second["groups"]["verify"][0]["listing"]["name"] == "missing"


def test_selected_route_is_per_user_and_profile_location_and_listing_version(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "commute.db")
    db._migrated[0] = False
    monkeypatch.setattr(commute.config, "kakao_key", lambda: "test-key")
    calls = []

    def route(*args):
        calls.append(args)
        return {"status": "observed", "minutes": 54, "path": [[37.6, 127.0]],
                "url": "https://map.kakao.com/", "transfers": 2}

    monkeypatch.setattr(commute.kakao_places, "route", route)
    row = _located()
    first = commute.lookup(7, row, _profile())
    assert first["minutes"] == 54
    assert "path" not in first and "직장" not in json.dumps(first, ensure_ascii=False)
    assert commute.lookup(7, row, _profile()) == first
    assert len(calls) == 1
    assert commute.cached(7, [row], _profile()) == {row["key"]: first}
    displayed = discovery_v2.discover([row], {"max_commute_minutes": 60},
                                      commute_by_key={row["key"]: first})["groups"]["verify"][0]["listing"]["commute"]
    assert "destination_hash" not in displayed and "origin" not in displayed
    assert commute.cached(8, [row], _profile()) == {}
    assert commute.cached(7, [row], _profile(37.51)) == {}
    second = commute.lookup(7, row, _profile(37.51))
    assert len(calls) == 2
    row["fetched_at"] = second["checked_at"] + 1
    assert commute.cached(7, [row], _profile(37.51)) == {}
    commute.lookup(7, row, _profile(37.51))
    assert len(calls) == 3


def test_unavailable_route_and_missing_origin_remain_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "commute.db")
    db._migrated[0] = False
    monkeypatch.setattr(commute.config, "kakao_key", lambda: "test-key")
    monkeypatch.setattr(commute.kakao_places, "route", lambda *args: {"status": "unavailable"})
    row = _located()
    result = commute.lookup(3, row, _profile())
    assert result["status"] == "unavailable"
    assert discovery_v2.discover([row], {"max_commute_minutes": 60},
                                 commute_by_key={row["key"]: result})["counts"]["verify"] == 1
    row.pop("lat")
    assert commute.lookup(3, row, _profile())["status"] == "unverified"
    with pytest.raises(ValueError, match="missing_work"):
        commute.lookup(3, _located(), {})


def test_route_checks_access_and_search_never_bulk_calls_provider(monkeypatch):
    from realty_signal import api
    from realty_signal.services import property_analysis

    row = _located()
    monkeypatch.setattr(reports_v2.deps, "uid", lambda request: 7)
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda request: False)
    with pytest.raises(HTTPException) as denied:
        reports_v2.discovery_commute_lookup(None, {"key": row["key"]})
    assert denied.value.status_code == 403
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda request: True)
    monkeypatch.setattr(property_analysis, "resolve", lambda key, private_allowed: row)
    monkeypatch.setattr(db, "profile_get", lambda uid: _profile())
    monkeypatch.setattr(commute, "lookup", lambda uid, selected, profile: {
        "status": "observed", "minutes": 54, "destination_hash": "private"})
    response = reports_v2.discovery_commute_lookup(None, {"key": row["key"]})
    assert json.loads(response.body)["minutes"] == 54
    assert "private" not in response.body.decode()
    assert response.headers["cache-control"] == "private, no-store"
    monkeypatch.setattr(api, "hanbang", lambda: {"state": "ready", "listings": [row]})
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "empty", "listings": []})
    monkeypatch.setattr(api, "certified", lambda: {"state": "empty", "listings": []})
    monkeypatch.setattr(api, "_build_listings", lambda kinds, include_private: [row])
    monkeypatch.setattr(commute, "work_context", lambda profile: {"status": "ready", "identity": "test"})
    monkeypatch.setattr(commute, "cached", lambda uid, rows, profile: {})
    monkeypatch.setattr(commute.kakao_places, "route", lambda *args: (_ for _ in ()).throw(
        AssertionError("discovery must not call provider")))
    result = json.loads(reports_v2.discovery(None, {"max_commute_minutes": 60}).body)
    assert result["counts"]["verify"] == 1
