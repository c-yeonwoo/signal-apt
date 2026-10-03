"""The primary market API must not leak an unsafe raw grade as a display label."""

from datetime import date

import pandas as pd

from realty_signal.routes import market
from realty_signal.services import market_data as md
from realty_signal.signals.engine import SignalConfig
from realty_signal import api

from test_signal_assessment_v2 import _kb, _row


def test_primary_signal_api_keeps_raw_for_audit_but_displays_held(monkeypatch):
    row = _row("BUY")
    frame = pd.DataFrame([row])
    monkeypatch.setattr(md, "kb", lambda: _kb((-0.15,) * 4))
    monkeypatch.setattr(md, "signals_df", lambda: frame)
    monkeypatch.setattr(md, "signal_config", SignalConfig)
    md.assessed_signal_labels.cache_clear()
    labels = md.assessed_signal_labels(date(2026, 10, 3).isoformat())
    assert labels["중구"]["display_signal"] == "HELD"
    result = market.signals()
    assert result[0]["signal"] == "BUY"
    assert result[0]["display_signal"] == "HELD"
    assert result[0]["assessment_status"] == "held"


def test_listing_cards_and_timing_use_guarded_region_signal(monkeypatch):
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {}})
    monkeypatch.setattr(api, "_timing_asof", lambda: "2026-10-03")
    monkeypatch.setattr(api, "_presale", lambda: [
        {"단지명": "보류 단지", "지역": "보류구", "시그널": "BUY", "상태": "접수예정", "관리번호": "1"},
        {"단지명": "확인 단지", "지역": "확인구", "시그널": "BUY", "상태": "접수예정", "관리번호": "2"},
    ])
    monkeypatch.setattr(md, "assessed_signal_labels", lambda today: {
        "보류구": {"display_signal": "HELD", "assessment_status": "held"},
        "확인구": {"display_signal": "BUY", "assessment_status": "ready"},
    })

    rows = {row["지역"]: row for row in api._build_listings({"청약"})}
    assert rows["보류구"]["원시시그널"] == "BUY"
    assert rows["보류구"]["시그널"] == "HELD"
    assert rows["확인구"]["시그널"] == "BUY"
    assert rows["확인구"]["기회도"] > rows["보류구"]["기회도"]


def test_listing_signal_assessment_failure_fails_closed(monkeypatch):
    monkeypatch.setattr(api, "_regime", lambda: {"regions": {}})
    monkeypatch.setattr(api, "_timing_asof", lambda: "2026-10-03")
    monkeypatch.setattr(api, "_presale", lambda: [
        {"단지명": "미확인 단지", "지역": "미확인구", "시그널": "STRONG_BUY", "상태": "접수예정", "관리번호": "3"},
    ])
    def unavailable(today):
        raise RuntimeError("synthetic source failure")
    monkeypatch.setattr(md, "assessed_signal_labels", unavailable)

    row = api._build_listings({"청약"})[0]
    assert row["원시시그널"] == "STRONG_BUY"
    assert row["시그널"] == "HELD"
    assert row["판정상태"] == "held"
