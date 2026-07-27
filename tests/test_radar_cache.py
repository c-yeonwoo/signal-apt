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
