"""지오코딩 — 단지명/주소 → 좌표. 캐시는 SQLite(db.py geocode 테이블) + OSM Nominatim.

Nominatim 정책상 1req/s·식별 UA 필수. 캐시 우선, 미스만 호출(요청당 상한).
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from realty_signal import jsonx
from realty_signal import db

_URL = "https://nominatim.openstreetmap.org/search"
_UA = "realty-signal-map/1.0 (personal home-buying study tool)"
_last = [0.0]  # 마지막 호출 시각(1req/s 준수)


def _query_osm(q: str) -> tuple[float, float] | None:
    dt = time.time() - _last[0]
    if dt < 1.1:
        time.sleep(1.1 - dt)
    _last[0] = time.time()
    url = _URL + "?" + urllib.parse.urlencode({"q": q, "format": "json", "limit": 1, "countrycodes": "kr"})
    try:
        data = jsonx.loads(urllib.request.urlopen(  # noqa: S310
            urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=15).read())
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None


def lookup_cached(queries: list[str]) -> dict[str, list]:
    """캐시에 있는 좌표만 즉시 반환 {q: [lat,lng]} (호출 없음)."""
    return db.geo_get_many(queries)


def geocode_batch(queries: list[str], max_miss: int = 20) -> dict:
    """캐시 우선 + 미스를 최대 max_miss건 OSM 조회(1req/s)하여 캐시·반환."""
    cached, miss = {}, []
    for q in dict.fromkeys(q for q in queries if q):
        row = db.geo_get(q)
        if row is None:
            miss.append(q)
        elif row[0] is not None:
            cached[q] = [row[0], row[1]]
    for q in miss[:max_miss]:
        r = _query_osm(q)
        db.geo_set(q, r[0] if r else None, r[1] if r else None)
        if r:
            cached[q] = [r[0], r[1]]
    return {"coords": cached, "remaining": max(0, len(miss) - max_miss)}
