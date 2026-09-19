"""Hermetic defaults: synthetic market data, disposable user DB, no live services.

Individual tests may override these adapters. Never use a developer's real data or
API keys to make the test suite green.
"""
from functools import lru_cache
import math
import urllib.request

import pandas as pd
import pytest

from realty_signal import db
from realty_signal.ingest.kb_weekly import KBWeekly
from realty_signal.services import market_data as md


@pytest.fixture(scope="session")
def synthetic_market():
    codes = {"서울": "11000", "강남11개구": "11000", "강북14개구": "11000",
             "강남구": "11680", "노원구": "11350", "도봉구": "11320", "금천구": "11545",
             "성남시 분당구": "41135", "해운대구": "26350", "남양주시": "41360"}
    rows = []
    for region in codes:
        for i, day in enumerate(pd.date_range("2024-01-01", periods=140, freq="W-MON")):
            wave = math.sin(i / 8)
            values = {"sale_change": wave * .2, "jeonse_change": wave * .1}
            if region not in {"강남구", "노원구", "도봉구", "금천구"}:
                values.update(jeonse_supply=175 + wave * 8,
                              buyer_superiority=69.3 + wave * 5, buyer_demand=10)
            rows.extend((region, day, metric, value) for metric, value in values.items())
    return KBWeekly(pd.DataFrame(rows, columns=["region", "date", "metric", "value"]), codes)


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch, synthetic_market):
    md.clear_caches()
    monkeypatch.setattr(md, "kb", lru_cache(maxsize=1)(lambda: synthetic_market))
    monkeypatch.setattr(db, "DB", tmp_path / "isolated.db")
    monkeypatch.setattr(db, "_migrated", [False])
    for name in ("ANTHROPIC_API_KEY", "PUBLIC_DATA_KEY", "SMTP_HOST", "SMTP_FROM",
                 "TELEGRAM_BOT_TOKEN", "AI_OPUS_WHITELIST", "ADMIN_EMAILS"):
        monkeypatch.delenv(name, raising=False)

    def no_network(*args, **kwargs):
        raise RuntimeError("Live network disabled in unit tests; stub the source adapter")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    yield
    # Some tests replace md.kb with a plain function; clear caches after restoring.
    monkeypatch.undo()
    md.clear_caches()
