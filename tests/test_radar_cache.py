"""급매·찐매물 레이더 캐시 TTL(1일) / 스캔버전."""

from __future__ import annotations

import json
import time

from realty_signal import api


def test_radar_cache_stale_by_age(tmp_path, monkeypatch):
    p = tmp_path / "quicksale.json"
    p.write_text(json.dumps({"_scan_ver": 99, "listings": []}), encoding="utf-8")
    # mtime을 2일 전으로
    old = time.time() - 2 * 86400
    import os
    os.utime(p, (old, old))
    assert api._radar_cache_stale(p, min_ver=1) is True


def test_radar_cache_fresh(tmp_path):
    p = tmp_path / "certified.json"
    p.write_text(json.dumps({"_scan_ver": 1, "listings": []}), encoding="utf-8")
    assert api._radar_cache_stale(p, min_ver=1) is False


def test_radar_cache_old_ver(tmp_path):
    p = tmp_path / "certified.json"
    p.write_text(json.dumps({"_scan_ver": 0, "listings": []}), encoding="utf-8")
    assert api._radar_cache_stale(p, min_ver=1) is True


def test_failed_quicksale_scan_preserves_previous_cache_and_exposes_reason(tmp_path, monkeypatch):
    cache = tmp_path / "quicksale.json"
    cache.write_text(json.dumps({"ready": True, "listings": [{"단지명": "기존 결과"}],
                                 "regions": ["은평구"], "_scan_ver": api._QUICKSALE_SCAN_VER}),
                     encoding="utf-8")
    monkeypatch.setattr(api, "QUICKSALE_FILE", cache)
    monkeypatch.setattr(api, "_radar_scan_with_status", lambda *_a, **_k: ([], {
        "requested_regions": 1, "queryable_regions": 1, "successful_requests": 0,
        "failed_requests": 1, "skipped_regions": 0, "required_successes": 1,
        "usable": False, "failures": [{"region": "은평구", "error": "TimeoutError"}],
    }))

    result = api.quicksale_refresh({"regions": ["은평구"]})
    shown = api.quicksale()

    assert result["ok"] is False
    assert shown["listings"] == [{"단지명": "기존 결과"}]
    assert shown["refresh"]["ok"] is False
    assert "기존 급매 결과" in shown["refresh"]["error"]


def test_quicksale_seed_does_not_require_public_data_key(monkeypatch):
    calls = []
    monkeypatch.setattr(api.config, "public_data_key", lambda: None)
    monkeypatch.setattr(api.config, "seoul_key", lambda: None)
    monkeypatch.setattr(api, "_quicksale_stale", lambda: True)
    monkeypatch.setattr(api, "_certified_stale", lambda: True)
    monkeypatch.setattr(api, "quicksale_refresh", lambda data: calls.append("급매") or {"ok": True})
    monkeypatch.setattr(api, "certified_refresh", lambda data: calls.append("찐매물") or {"ok": True})
    monkeypatch.setattr(api.md, "clear_caches", lambda: None)

    api._seed_if_missing()

    assert calls == ["급매", "찐매물"]
