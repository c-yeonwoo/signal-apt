"""The primary market API must not leak an unsafe raw grade as a display label."""

from datetime import date

import pandas as pd

from realty_signal.routes import market
from realty_signal.services import market_data as md
from realty_signal.signals.engine import SignalConfig

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
