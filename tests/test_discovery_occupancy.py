"""요청형 입주일 조회는 최소 필드·현재 매물 식별·사용자별 캐시를 지킨다."""

import json
from datetime import date

import pytest
from fastapi import HTTPException

from realty_signal import db
from realty_signal.routes import reports_v2
from realty_signal.services import discovery_occupancy as occupancy
from realty_signal.services import discovery_v2

from test_discovery_v2 import _row


def test_detail_date_normalization_never_promotes_approximate_or_old_date():
    today = date(2026, 10, 4)
    base = {"atlfslBscInfoPk": 12}
    exact = occupancy.normalize({**base, "mvnPsbltyDay": "20261208", "mvnDayTypeCd": ""},
                                "12", today=today)
    assert (exact["status"], exact["date"]) == ("dated", "2026-12-08")
    assert occupancy.normalize({**base, "mvnPsbltyDay": "NOW"}, "12", today=today)["status"] == "immediate"
    assert occupancy.normalize({**base, "mvnPsbltyDay": "20261208", "mvnDayTypeCd": "EARLY"},
                               "12", today=today)["status"] == "approximate"
    assert occupancy.normalize({**base, "mvnPsbltyDay": "20261003"}, "12", today=today)["status"] == "unverified"
    assert occupancy.normalize({**base, "mvnPsbltyDay": "garbage"}, "12", today=today)["status"] == "unverified"
    with pytest.raises(ValueError, match="listing_identity_mismatch"):
        occupancy.normalize({**base, "mvnPsbltyDay": "NOW"}, "13", today=today)


def test_cache_is_per_user_and_detail_response_drops_private_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "occupancy.db")
    db._migrated[0] = False
    calls = []

    def detail(path):
        calls.append(path)
        return {"data": {"content": {"atlfslBscInfoPk": 12, "mvnPsbltyDay": "NOW",
                                    "lreaTelno": "010-0000-0000", "atlfslExplnCn": "원문 설명"}}}

    monkeypatch.setattr(occupancy.hanbang, "_request", detail)
    first = occupancy.lookup(7, "일반매물:12", "12")
    assert occupancy.lookup(7, "일반매물:12", "12") == first
    assert len(calls) == 1
    assert occupancy.cached(7, {"일반매물:12": 0}) == {"일반매물:12": first}
    assert occupancy.cached(8, {"일반매물:12": 0}) == {}
    assert occupancy.cached(7, {"일반매물:12": first["checked_at"] + 1}) == {}
    occupancy.lookup(7, "일반매물:12", "12", fresh_after=first["checked_at"] + 1)
    assert len(calls) == 2
    assert all(value not in json.dumps(first, ensure_ascii=False)
               for value in ("010-0000-0000", "원문 설명"))


def test_move_in_constraint_stays_unknown_until_exact_detail_is_checked():
    row = _row("move", price=50000, area=70)
    spec = {"move_in_by": "2026-12-31", "include_exceeded": True}
    unknown = discovery_v2.discover([row], spec)
    assert unknown["groups"]["verify"][0]["constraints"][0]["status"] == "unknown"
    key = row["key"]
    dated = {"status": "dated", "date": "2026-12-08", "source": "hanbang_detail"}
    matched = discovery_v2.discover([row], spec, occupancy_by_key={key: dated})
    assert matched["groups"]["matched"][0]["constraints"][0]["status"] == "pass"
    late = discovery_v2.discover([row], spec, occupancy_by_key={key: {**dated, "date": "2027-01-08"}})
    assert late["groups"]["exceeded"][0]["constraints"][0]["status"] == "fail"
    approximate = discovery_v2.discover([row], spec, occupancy_by_key={key: {**dated, "status": "approximate"}})
    assert approximate["groups"]["verify"][0]["constraints"][0]["status"] == "unknown"
    for value in ("2026-02-30", "20261231", "2026-12-31T00:00:00", 20261231):
        with pytest.raises(ValueError, match="invalid_move_in_date"):
            discovery_v2.validate({"move_in_by": value})


def test_lookup_route_requires_personal_access_and_current_hanbang_id(monkeypatch):
    from realty_signal.services import property_analysis

    row = _row("12", price=50000, area=70)
    row["ref"]["hanbang_id"] = "12"
    monkeypatch.setattr(reports_v2.deps, "uid", lambda request: 7)
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda request: False)
    with pytest.raises(HTTPException) as denied:
        reports_v2.discovery_occupancy_lookup(None, {"key": row["key"]})
    assert denied.value.status_code == 403
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda request: True)
    monkeypatch.setattr(property_analysis, "resolve", lambda key, private_allowed: row)
    monkeypatch.setattr(occupancy, "lookup", lambda uid, key, source_id, **kwargs: {
        "status": "dated", "date": "2026-12-08", "source": "hanbang_detail"})
    result = reports_v2.discovery_occupancy_lookup(None, {"key": row["key"]})
    assert json.loads(result.body)["date"] == "2026-12-08"
    assert result.headers["cache-control"] == "private, no-store"


def test_discovery_uses_only_users_cached_detail_without_bulk_source_calls(monkeypatch):
    from realty_signal import api

    row = _row("12", price=50000, area=70)
    monkeypatch.setattr(reports_v2.deps, "uid", lambda request: 7)
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda request: True)
    monkeypatch.setattr(api, "hanbang", lambda: {"state": "ready", "listings": [row]})
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "empty", "listings": []})
    monkeypatch.setattr(api, "certified", lambda: {"state": "empty", "listings": []})
    monkeypatch.setattr(api, "_build_listings", lambda kinds, include_private: [row])
    monkeypatch.setattr(occupancy, "cached", lambda uid, fetched: {
        row["key"]: {"status": "dated", "date": "2026-12-08", "checked_at": 1791000000}})
    monkeypatch.setattr(occupancy.hanbang, "_request", lambda *_: (_ for _ in ()).throw(
        AssertionError("discovery may not bulk-request details")))
    result = json.loads(reports_v2.discovery(None, {"move_in_by": "2026-12-31"}).body)
    assert result["counts"]["matched"] == 1
    assert result["groups"]["matched"][0]["listing"]["move_in"]["date"] == "2026-12-08"
