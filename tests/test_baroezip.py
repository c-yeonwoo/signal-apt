"""baroezip spatialmarket 파서 — 급매 / 찐매물 구분."""

from __future__ import annotations

import json
from unittest.mock import patch

from realty_signal.ingest import baroezip


def _fake_response(payload: dict):
    class _R:
        def read(self):
            return json.dumps(payload).encode()

    return _R()


def test_fetch_market_marks_urgent_and_certified():
    payload = {
        "data": [
            {
                "apt_list": {
                    "complex_name": "급매단지",
                    "complex_no": "1",
                    "has_certified": False,
                    "latitude": 37.5,
                    "longitude": 127.0,
                    "total_household_count": 100,
                    "use_approve_ymd": "201001",
                },
                "market_data": [{
                    "complex_name": "급매단지",
                    "deal_amount": 90000,
                    "median_deal_amount": 100000,
                    "is_urgent": True,
                    "pyeong_name": "25",
                    "floor": 3,
                    "direction": "남향",
                    "trade_type": "trade",
                    "original": 111,
                    "customer": None,
                    "realtor": None,
                }],
            },
            {
                "apt_list": {
                    "complex_name": "인증단지",
                    "complex_no": "2",
                    "has_certified": True,
                    "latitude": 37.51,
                    "longitude": 127.01,
                    "total_household_count": 200,
                    "use_approve_ymd": "201501",
                },
                "market_data": [{
                    "complex_name": "인증단지",
                    "deal_amount": 95000,
                    "median_deal_amount": 100000,
                    "is_urgent": False,
                    "pyeong_name": "30",
                    "floor": 10,
                    "direction": "동향",
                    "trade_type": "trade",
                    "original": 222,
                    "customer": 1,
                    "realtor": 2,
                }],
            },
        ]
    }
    with patch("realty_signal.ingest.baroezip.urllib.request.urlopen",
               return_value=_fake_response(payload)):
        rows = baroezip.fetch_market(37.4, 126.9, 37.6, 127.1, scope="all")

    assert len(rows) == 2
    urgent = next(r for r in rows if r["단지명"] == "급매단지")
    cert = next(r for r in rows if r["단지명"] == "인증단지")
    assert urgent["급매"] is True and urgent["찐매물"] is False
    assert urgent["급매갭"] == -10.0
    assert cert["급매"] is False and cert["찐매물"] is True
    assert cert["급매갭"] == -5.0


def test_fetch_market_passes_scope_query():
    seen = {}

    def _open(req, timeout=0):  # noqa: ARG001
        seen["url"] = req.full_url
        return _fake_response({"data": []})

    with patch("realty_signal.ingest.baroezip.urllib.request.urlopen", side_effect=_open):
        baroezip.fetch_market(1, 2, 3, 4, scope="all")
    assert "scope=all" in seen["url"]


def test_listing_timing_certified_bonus():
    from realty_signal.signals.timing import listing_timing
    r = listing_timing("찐매물", {"급매갭": -10}, "BUY", "B", asof="2026-07-14")
    assert "찐매물" in r.reasons_text
    assert "시세갭" in r.reasons_text
    assert r.confidence > 0.5
