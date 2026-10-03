"""Release switches fail closed for paid/optional paths without changing data permissions."""

import pytest
from fastapi import HTTPException

from realty_signal import config
from realty_signal.routes import auth as auth_routes
from realty_signal.routes import reports_v2


def test_rollout_defaults_preserve_live_paths_and_parse_false(monkeypatch):
    assert all(config.rollout_flags().values())
    monkeypatch.setenv("REPORT_V2_ENABLED", "off")
    monkeypatch.setenv("DISCOVERY_V2_ENABLED", "FALSE")
    monkeypatch.setenv("CONTEXTUAL_EXPLANATIONS_ENABLED", "0")
    monkeypatch.setenv("NICK_GLOBAL_ENTRY_ENABLED", "no")
    assert config.rollout_flags() == {
        "report_v2_enabled": False, "discovery_v2_enabled": False,
        "contextual_explanations_enabled": False, "nick_global_entry_enabled": False}


def test_disabled_report_and_discovery_stop_before_source_access(monkeypatch):
    monkeypatch.setenv("REPORT_V2_ENABLED", "0")
    monkeypatch.setenv("DISCOVERY_V2_ENABLED", "0")
    with pytest.raises(HTTPException) as region:
        reports_v2.region_report("kb:11350")
    with pytest.raises(HTTPException) as listing:
        reports_v2.listing_report(None, "일반매물:a")
    with pytest.raises(HTTPException) as discovery:
        reports_v2.discovery(None, {})
    assert {region.value.status_code, listing.value.status_code,
            discovery.value.status_code} == {503}


def test_disabled_contextual_explanation_stops_before_paid_job(monkeypatch):
    monkeypatch.setenv("CONTEXTUAL_EXPLANATIONS_ENABLED", "0")
    with pytest.raises(HTTPException) as stopped:
        reports_v2.explanation_request(None, "a" * 64, {"type": "region", "key": "노원구"})
    assert stopped.value.status_code == 503


def test_authenticated_bootstrap_exposes_only_rollout_booleans(monkeypatch):
    monkeypatch.setattr(auth_routes.auth, "current_user", lambda token: {"id": 7, "email": "x@example.com"})
    monkeypatch.setattr(auth_routes.db, "profile_get", lambda uid: {})
    monkeypatch.setenv("NICK_GLOBAL_ENTRY_ENABLED", "0")
    request = type("Request", (), {"cookies": {}})()
    result = auth_routes.auth_me(request)
    assert result["features"]["nick_global_entry_enabled"] is False
    assert result["features"]["report_v2_enabled"] is True
