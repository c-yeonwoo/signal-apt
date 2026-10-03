"""Report endpoints expose deterministic evidence without opening private sources."""

import json
from datetime import date

import pandas as pd
import pytest
from fastapi import HTTPException

from realty_signal.routes import reports_v2
from realty_signal.services import market_data as md
from realty_signal.signals.engine import SignalConfig

from test_signal_assessment_v2 import _kb, _row
from test_discovery_v2 import _row as listing_row


def test_region_report_exposes_failed_conditions_and_cautions(monkeypatch):
    monkeypatch.setattr(md, "kb", lambda: _kb((-0.15,) * 4))
    monkeypatch.setattr(md, "signals_df", lambda: pd.DataFrame([_row("BUY")]))
    monkeypatch.setattr(md, "signal_config", SignalConfig)
    from realty_signal.services import signal_assessment as sa
    original = sa.build
    monkeypatch.setattr(sa, "build", lambda kb, row, config: original(
        kb, row, config, asof=date(2026, 9, 28), today=date(2026, 10, 3)))
    report = json.loads(reports_v2.region_report("중구").body)
    assert report["assessment"]["raw_grade"] == "BUY"
    assert report["assessment"]["display_grade"] == "판단 보류"
    assert "price_direction_conflict" in report["unknowns"]
    assert any(reason["reason_id"] == "sale_momentum" for reason in report["cautions"])
    by_code = json.loads(reports_v2.region_report("kb:1114000000").body)
    assert by_code["subject"] == {"region": "중구", "region_id": "kb:1114000000"}


def test_region_code_route_rejects_unverified_legacy_cache(monkeypatch):
    monkeypatch.setattr(md, "kb", lambda: _kb(identity_verified=False))
    with pytest.raises(HTTPException) as error:
        reports_v2.region_report("kb:1114000000")
    assert error.value.status_code == 404


def test_private_discovery_does_not_open_source_without_personal_access(monkeypatch):
    from realty_signal import api
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _request: False)
    monkeypatch.setattr(api, "_build_listings", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("private source accessed")))
    monkeypatch.setattr(api, "hanbang", lambda: (_ for _ in ()).throw(AssertionError("private cache accessed")))
    result = json.loads(reports_v2.discovery(None, {"max_price_manwon": 60000}).body)
    assert result["private_access"] is False
    assert result["source_state"] == "forbidden"
    assert result["counts"]["matched"] == 0


def test_discovery_reports_partial_coverage_without_hiding_good_source(monkeypatch):
    from realty_signal import api
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _request: True)
    monkeypatch.setattr(api, "hanbang", lambda: {"state": "partial", "regions": ["노원구"],
                                                  "refresh": {"failed_requests": 2, "limited_regions": ["노원구"]}})
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "failed", "regions": []})
    monkeypatch.setattr(api, "certified", lambda: {"state": "empty", "regions": ["노원구"]})
    monkeypatch.setattr(api, "_build_listings", lambda kinds, **_: [
        listing_row("one", price=50000, area=70)] if "일반매물" in kinds else [])
    result = json.loads(reports_v2.discovery(None, {"max_price_manwon": 60000}).body)
    assert result["source_state"] == "partial"
    assert result["coverage"]["regions"] == ["노원구"]
    assert result["sources"][0]["failed_requests"] == 2
    assert result["sources"][1]["state"] == "failed"
    assert len(result["groups"]["matched"]) == 1


def test_discovery_marks_failed_refresh_cache_as_verify(monkeypatch):
    from realty_signal import api
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _request: True)
    monkeypatch.setattr(api, "hanbang", lambda: {"state": "stale_failed", "regions": ["노원구"]})
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "never_scanned"})
    monkeypatch.setattr(api, "certified", lambda: {"state": "never_scanned"})
    monkeypatch.setattr(api, "_build_listings", lambda kinds, **_: [
        listing_row("old", price=50000, area=70)] if "일반매물" in kinds else [])
    result = json.loads(reports_v2.discovery(None, {"max_price_manwon": 60000}).body)
    assert not result["groups"]["matched"]
    assert result["groups"]["verify"][0]["constraints"][0]["status"] == "unknown"


def test_discovery_recovers_other_source_when_combined_read_fails(monkeypatch):
    from realty_signal import api
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _request: True)
    monkeypatch.setattr(api, "hanbang", lambda: {"state": "ready", "regions": ["노원구"]})
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "ready", "regions": ["노원구"]})
    monkeypatch.setattr(api, "certified", lambda: {"state": "empty"})

    def load(kinds, **_):
        if "급매" in kinds:
            raise ValueError("damaged source")
        return [listing_row("one", price=50000, area=70)] if "일반매물" in kinds else []

    monkeypatch.setattr(api, "_build_listings", load)
    result = json.loads(reports_v2.discovery(None, {"max_price_manwon": 60000}).body)
    assert result["source_state"] == "partial"
    assert result["sources"][1]["state"] == "failed"
    assert len(result["groups"]["matched"]) == 1


def test_discovery_does_not_call_missing_rows_verified_empty(monkeypatch):
    from realty_signal import api
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _request: True)
    monkeypatch.setattr(api, "hanbang", lambda: {"state": "ready", "listings": [{"id": 1}]})
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "empty"})
    monkeypatch.setattr(api, "certified", lambda: {"state": "empty"})
    monkeypatch.setattr(api, "_build_listings", lambda *_a, **_k: [])
    result = json.loads(reports_v2.discovery(None, {"max_price_manwon": 60000}).body)
    assert result["sources"][0]["state"] == "failed"
    assert result["source_state"] == "partial_empty"


def test_discovery_returns_conflict_when_page_source_changes(monkeypatch):
    from realty_signal import api
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _request: True)
    monkeypatch.setattr(api, "hanbang", lambda: {"state": "ready", "regions": ["노원구"]})
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "empty"})
    monkeypatch.setattr(api, "certified", lambda: {"state": "empty"})
    rows = [listing_row("one", price=50000), listing_row("two", price=51000)]
    monkeypatch.setattr(api, "_build_listings", lambda kinds, **_: rows if "일반매물" in kinds else [])
    first = json.loads(reports_v2.discovery(None, {"max_price_manwon": 60000, "limit": 1}).body)
    rows[0] = listing_row("one", price=49000)
    with pytest.raises(HTTPException) as error:
        reports_v2.discovery(None, {"max_price_manwon": 60000, "limit": 1,
                                    "cursor": first["next_cursor"]})
    assert error.value.status_code == 409


def test_discovery_cursor_restarts_when_source_health_changes(monkeypatch):
    from realty_signal import api
    monkeypatch.setattr(reports_v2.deps, "personal_listings_allowed", lambda _request: True)
    health = {"state": "ready", "regions": ["노원구"]}
    monkeypatch.setattr(api, "hanbang", lambda: health)
    monkeypatch.setattr(api, "quicksale", lambda: {"state": "empty"})
    monkeypatch.setattr(api, "certified", lambda: {"state": "empty"})
    monkeypatch.setattr(api, "_build_listings", lambda kinds, **_: [
        listing_row("one", price=50000), listing_row("two", price=51000)]
        if "일반매물" in kinds else [])
    first = json.loads(reports_v2.discovery(None, {"max_price_manwon": 60000, "limit": 1}).body)
    health["state"] = "partial"
    with pytest.raises(HTTPException) as error:
        reports_v2.discovery(None, {"max_price_manwon": 60000, "limit": 1,
                                    "cursor": first["next_cursor"]})
    assert error.value.status_code == 409
