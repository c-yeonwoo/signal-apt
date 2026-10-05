"""Regional price comparison uses verified MOLIT sales, never locality proxies."""

import xml.etree.ElementTree as ET

from realty_signal import api
from realty_signal.ingest import complex, complex_grade


def _deal(area: str, amount: str, month: str = "9"):
    return ET.fromstring(f"<item><excluUseAr>{area}</excluUseAr>"
                       f"<dealAmount>{amount}</dealAmount><dealYear>2026</dealYear>"
                       f"<dealMonth>{month}</dealMonth></item>")


def test_same_area_band_and_minimum_sample(monkeypatch):
    rows = [_deal("84.1", "80,000"), _deal("83.9", "81,000"),
            _deal("84", "82,000"), _deal("79", "83,000"),
            _deal("89", "84,000"), _deal("59", "50,000"),
            _deal("95", "90,000"), _deal("84", "bad")]
    monkeypatch.setattr(complex, "_items_parallel", lambda *_a: rows)
    found = complex_grade.region_trade_prices("11350", "test-key", 84)
    assert found["status"] == "ready"
    assert found["count"] == 5
    assert found["median_manwon"] == 82000
    assert found["area_band_m2"] == [79, 89]
    assert found["observed_months"] == ["2026-09", "2026-09"]
    sparse = complex_grade.region_trade_prices("11350", "test-key", 59)
    assert sparse["status"] == "insufficient_sample"
    assert sparse["count"] == 1 and sparse["median_manwon"] is None


def test_identity_gate_and_cached_observed_sample(monkeypatch):
    cache = {}
    monkeypatch.setattr(api.db, "kv_get", lambda key, max_age=None: cache.get(key))
    monkeypatch.setattr(api.db, "kv_set", lambda key, value: cache.__setitem__(key, value))
    monkeypatch.setattr(api.md, "current_region_identity", lambda ref: {
        "region_id": ref, "code": "1135000000", "name": "노원구"} if ref == "kb:1135000000" else None)
    monkeypatch.setattr(api.config, "load_env", lambda: None)
    monkeypatch.setattr(api.config, "public_data_key", lambda: "test-key")
    calls = []
    def collect(code, key, area):
        calls.append((code, key, area))
        return {"status": "ready", "count": 7, "median_manwon": 80000,
                "observed_months": ["2026-04", "2026-09"]}
    monkeypatch.setattr(complex_grade, "region_trade_prices", collect)
    assert api.region_trade_prices("중구")["status"] == "region_unverified"
    assert api.region_trade_prices("kb:1135000000", 30)["status"] == "unsupported_area"
    first = api.region_trade_prices("kb:1135000000", 84)
    assert first["source"] == "국토교통부 아파트 매매 실거래 공개자료"
    assert api.region_trade_prices("kb:1135000000", 84) == first
    assert calls == [("11350", "test-key", 84)]


def test_source_failure_is_not_zero_trades(monkeypatch):
    monkeypatch.setattr(api.md, "current_region_identity", lambda ref: {
        "region_id": ref, "code": "1135000000", "name": "노원구"})
    monkeypatch.setattr(api.db, "kv_get", lambda *_a, **_k: None)
    monkeypatch.setattr(api.config, "load_env", lambda: None)
    monkeypatch.setattr(api.config, "public_data_key", lambda: "test-key")
    def fail(*_a):
        raise complex.SourceUnavailable("secret detail")
    monkeypatch.setattr(complex_grade, "region_trade_prices", fail)
    result = api.region_trade_prices("kb:1135000000", 59)
    assert result["status"] == "source_unavailable"
    assert "secret detail" not in str(result)


def test_price_comparison_route_is_registered():
    from realty_signal.routes.complex import router
    assert any(getattr(route, "path", None) == "/api/region-trade-prices/{region_ref}"
               for route in router.routes)
