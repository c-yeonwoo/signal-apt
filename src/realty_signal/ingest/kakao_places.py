"""카카오 공식 장소·보행·대중교통 API의 제한된 분석 어댑터.

응답 전체를 영구 저장하지 않는다. 화면에는 매물 표시 좌표 기준의 참고 경로로 표시한다.
"""

from __future__ import annotations

import math
import urllib.parse
import urllib.request

from realty_signal import config, jsonx

HOST = "https://dapi.kakao.com"
DOCS = "https://developers.kakao.com/docs/ko/kakaomap/rest-api"
POI_TYPES = {"SW8": "지하철역", "MT1": "대형마트", "HP8": "병원", "CS2": "편의점"}


def _number(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def valid_point(lat, lng) -> bool:
    return (lat is not None and lng is not None and 33 <= lat <= 39 and 124 <= lng <= 132)


def _get(path: str, params: dict, key: str) -> dict:
    url = HOST + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": "KakaoAK " + key,
                                              "User-Agent": "realty-signal/1.0"})
    with urllib.request.urlopen(req, timeout=7) as response:  # noqa: S310 - fixed official host
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("place response too large")
    return jsonx.loads(raw)


def nearby(lat: float, lng: float, category: str, key: str, radius: int = 1800) -> dict:
    if category not in POI_TYPES or not valid_point(lat, lng):
        raise ValueError("invalid place query")
    raw = _get("/v2/local/search/category.json", {
        "category_group_code": category, "x": lng, "y": lat,
        "radius": radius, "size": 5, "sort": "distance"}, key)
    if not isinstance(raw.get("meta"), dict) or not isinstance(raw.get("documents"), list):
        raise ValueError("invalid place response")
    docs = []
    for item in raw.get("documents") or []:
        ilat, ilng = _number(item.get("y")), _number(item.get("x"))
        if not valid_point(ilat, ilng):
            continue
        docs.append({"name": item.get("place_name"), "lat": ilat, "lng": ilng,
                     "distance_m": _number(item.get("distance")),
                     "address": item.get("road_address_name") or item.get("address_name")})
    return {"label": POI_TYPES[category], "count_within_radius": (raw.get("meta") or {}).get("total_count"),
            "radius_m": radius, "places": docs[:5], "source": DOCS}


def route(start_lat: float, start_lng: float, end_lat: float, end_lng: float,
          mode: str, key: str) -> dict:
    if mode not in {"walk", "publictraffic"} or not all((valid_point(start_lat, start_lng),
                                                           valid_point(end_lat, end_lng))):
        raise ValueError("invalid route query")
    raw = _get("/v2/routing/" + mode, {
        "start_x": start_lng, "start_y": start_lat, "end_x": end_lng, "end_y": end_lat}, key)
    if raw.get("status") != "OK":
        return {"status": "unavailable", "provider_status": raw.get("status") or "unknown"}
    if mode == "walk":
        route_data = raw.get("route") or {}
        props = route_data.get("properties") or {}
        steps = [step for leg in route_data.get("legs") or [] for step in leg.get("steps") or []]
        url = props.get("landingUrl")
    else:
        routes = raw.get("routes") or []
        if not routes:
            return {"status": "unavailable", "provider_status": "NO_RESULTS"}
        route_data = min(routes, key=lambda r: _number((r.get("properties") or {}).get("totalTime")) or float("inf"))
        props = route_data.get("properties") or {}
        steps = route_data.get("steps") or []
        url = (raw.get("properties") or {}).get("landingURL")
    seconds, meters = _number(props.get("totalTime")), _number(props.get("totalDistance"))
    if not seconds or seconds <= 0:
        return {"status": "unavailable", "provider_status": "MISSING_TIME"}
    points = []
    for step in steps:
        for point in (step.get("path") or {}).get("points") or []:
            if len(point) != 2:
                continue
            px, py = _number(point[0]), _number(point[1])
            if valid_point(py, px):
                points.append([py, px])
    if len(points) > 500:
        stride = math.ceil(len(points) / 500)
        points = points[::stride] + [points[-1]]
    return {"status": "observed", "minutes": math.ceil(seconds / 60),
            "distance_m": round(meters) if meters is not None else None,
            "transfers": props.get("transfers") if mode == "publictraffic" else None,
            "mode": mode, "path": points,
            "url": url if isinstance(url, str) and url.startswith("https://map.kakao.com/") else None,
            "source": DOCS, "origin_quality": "listing_point_unverified_entrance"}
