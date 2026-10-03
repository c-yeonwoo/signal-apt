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
    result = json.loads(reports_v2.discovery(None, {"max_price_manwon": 60000}).body)
    assert result["private_access"] is False
    assert result["source_state"] == "forbidden"
    assert result["counts"]["matched"] == 0
