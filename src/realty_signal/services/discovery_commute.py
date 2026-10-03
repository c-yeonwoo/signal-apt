"""사용자가 고른 매물의 저장 직장 대중교통 안내시간만 조회한다."""

from __future__ import annotations

from hashlib import sha256
import json
import time

from realty_signal import config, db
from realty_signal.ingest import kakao_places
from realty_signal.services.property_analysis import snapshot

CACHE_SECONDS = 86400
FAILURE_SECONDS = 300


def work_context(profile: dict | None) -> dict:
    profile = profile or {}
    try:
        lat, lng = float(profile.get("직장lat")), float(profile.get("직장lng"))
    except (TypeError, ValueError):
        return {"status": "missing_work"}
    if not kakao_places.valid_point(lat, lng):
        return {"status": "missing_work"}
    identity = sha256(json.dumps([lat, lng]).encode()).hexdigest()
    return {"status": "ready" if config.kakao_key() else "unconfigured",
            "coordinate": [lat, lng], "identity": identity}


def _key(uid: int, listing_key: str) -> str:
    return f"discovery_commute:{uid}:{listing_key}"


def public(value: dict) -> dict:
    """캐시 무효화용 직장 지문·좌표는 브라우저에 보내지 않는다."""
    return {key: value.get(key) for key in (
        "status", "minutes", "checked_at", "origin_quality", "source", "source_url")}


def _usable(value: object, *, identity: str, point: list, fresh_after: float = 0) -> bool:
    if not isinstance(value, dict) or value.get("destination_hash") != identity or value.get("origin") != point:
        return False
    checked = value.get("checked_at")
    if type(checked) is not int or checked < int(fresh_after):
        return False
    age = time.time() - checked
    if value.get("status") == "observed":
        return type(value.get("minutes")) is int and 0 < value["minutes"] <= 1440 and age < CACHE_SECONDS
    return value.get("status") == "unavailable" and age < FAILURE_SECONDS


def lookup(uid: int, row: dict, profile: dict) -> dict:
    work = work_context(profile)
    if work["status"] != "ready":
        raise ValueError(work["status"])
    listing = snapshot(row)
    point = listing["coordinate"]
    if not point:
        return {"status": "unverified", "reason": "매물 표시 좌표가 없습니다."}
    key = _key(uid, listing["key"])
    existing = db.kv_get(key, max_age=CACHE_SECONDS)
    if _usable(existing, identity=work["identity"], point=point,
               fresh_after=row.get("fetched_at") or 0):
        return existing
    try:
        route = kakao_places.route(*point, *work["coordinate"], "publictraffic", config.kakao_key())
    except Exception:  # noqa: BLE001 - 외부 장애를 통근시간 0분이나 조건 충족으로 바꾸지 않는다.
        route = {"status": "unavailable"}
    result = {"status": "observed" if route.get("status") == "observed" else "unavailable",
              "minutes": route.get("minutes") if route.get("status") == "observed" else None,
              "checked_at": int(time.time()), "destination_hash": work["identity"],
              "origin": point, "origin_quality": "listing_point_unverified_entrance",
              "source": "Kakao 대중교통 경로", "source_url": kakao_places.DOCS}
    db.kv_set(key, result)
    return result


def cached(uid: int, rows: list[dict], profile: dict) -> dict[str, dict]:
    work = work_context(profile)
    if work["status"] != "ready" or not rows:
        return {}
    points = {row["key"]: (snapshot(row)["coordinate"], row.get("fetched_at") or 0)
              for row in rows if row.get("key")}
    prefix = f"discovery_commute:{uid}:"
    c = db.conn()
    try:
        records = c.execute("SELECT k,v FROM kv WHERE k LIKE ? AND ts>=?",
                            (prefix + "%", int(time.time()) - CACHE_SECONDS)).fetchall()
    finally:
        c.close()
    out = {}
    for key, raw in records:
        listing_key = key[len(prefix):]
        if listing_key not in points:
            continue
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            continue
        point, fresh_after = points[listing_key]
        if point and _usable(value, identity=work["identity"], point=point, fresh_after=fresh_after):
            out[listing_key] = value
    return out
