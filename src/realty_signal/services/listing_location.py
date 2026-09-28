"""선택 매물 좌표를 공식 위치 자료와 연결하되 확정 배정·출입구로 오인하지 않는다."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from realty_signal import config
from realty_signal.ingest import kakao_places, school_zone
from realty_signal.services import listing_entrance
from realty_signal.services.property_analysis import snapshot


def _safe(call, fallback: dict) -> dict:
    try:
        return call()
    except Exception:  # noqa: BLE001 - 외부 자료 실패를 '시설 없음'으로 바꾸지 않는다.
        return fallback


def build(row: dict, profile: dict | None = None, entrance: dict | None = None) -> dict:
    listing = snapshot(row)
    point = listing["coordinate"]
    selected = False
    if entrance:
        try:
            listing_entrance.validate(row, entrance.get("lat"), entrance.get("lng"))
            selected = True
        except ValueError:
            pass
    route_point = [entrance["lat"], entrance["lng"]] if selected else point
    origin_source = "user_marked_candidate" if selected else "listing_point"
    origin_note = ("내가 지도에서 지정한 출입구 후보 기준입니다. 실제 출입구·보행 가능 여부는 현장에서 확인하세요."
                   if selected else "매물 표시 좌표 기준이며 아파트 출입구와 목적지 출입구가 검증되지 않았습니다.")
    missing = {"status": "unverified", "reason": "표시 좌표가 없어 위치를 판정하지 않았습니다."}
    if not point:
        return {"listing": listing, "mobility": missing, "school": missing,
                "amenities": missing, "evidence": []}
    lat, lng = point
    school = _safe(lambda: school_zone.at_point(lat, lng),
                   {"status": "unavailable", "reason": "통학구역 파일을 조회할 수 없습니다.", "zones": []})
    school["coordinate_note"] = "통학구역은 출입구 후보가 아닌 원래 매물 표시 좌표 기준입니다. 실제 주소의 배정은 교육청에 확인하세요."
    lat, lng = route_point
    origin = {"origin_source": origin_source, "origin_coordinate": route_point,
              "origin_updated_at": entrance.get("updated_at") if selected else None,
              "reason": origin_note}
    evidence = []
    if school.get("status") == "candidate":
        evidence.append({"label": "학구도안내서비스 공개 통학구역", "url": school_zone.SOURCE_PAGE,
                         "status": "표시 좌표 기준 후보", "asof": max(
                             (z.get("asof") or "" for z in school.get("zones") or []), default="")})
    key = config.kakao_key()
    if not key:
        return {"listing": listing, "school": school,
                "mobility": {"status": "unverified", **origin,
                             "api_reason": "경로 API 키가 없어 도보·통근을 확인하지 못했습니다."},
                "amenities": {"status": "unverified", "reason": "장소 API 키가 없어 주변 시설을 확인하지 못했습니다."},
                "evidence": evidence}
    categories = tuple(kakao_places.POI_TYPES)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {code: pool.submit(_safe, lambda c=code: kakao_places.nearby(lat, lng, c, key),
                                     {"status": "unavailable", "label": kakao_places.POI_TYPES[code],
                                      "reason": "장소 조회에 실패했습니다.", "places": []}) for code in categories}
        places = {code: future.result() for code, future in futures.items()}
    station = (places["SW8"].get("places") or [None])[0]
    work = profile or {}
    try:
        work_lat, work_lng = float(work.get("직장lat")), float(work.get("직장lng"))
    except (TypeError, ValueError):
        work_lat = work_lng = None
    with ThreadPoolExecutor(max_workers=2) as pool:
        station_future = pool.submit(_safe,
            lambda: kakao_places.route(lat, lng, station["lat"], station["lng"], "walk", key),
            {"status": "unavailable", "reason": "역까지 도보 경로를 조회하지 못했습니다."}) if station else None
        commute_future = pool.submit(_safe,
            lambda: kakao_places.route(lat, lng, work_lat, work_lng, "publictraffic", key),
            {"status": "unavailable", "reason": "직장까지 대중교통 경로를 조회하지 못했습니다."}) \
            if kakao_places.valid_point(work_lat, work_lng) else None
        station_route = station_future.result() if station_future else None
        commute_route = commute_future.result() if commute_future else None
    if station_route:
        station_route["destination"] = station["name"]
        station_route["origin_quality"] = origin_source
    if commute_route:
        commute_route["destination"] = str(work.get("직장") or "저장된 직장")[:80]
        commute_route["origin_quality"] = origin_source
    mobility = {"status": "partial" if station_route or commute_route else "unverified",
                "station_walk": station_route, "work_transit": commute_route,
                **origin,
                "transit_note": "대중교통 안내 시간은 실제 출퇴근 시간대의 소요시간을 보증하지 않습니다."}
    amenities = {"status": "partial" if any("count_within_radius" in p for p in places.values())
                 else "unavailable", "by_category": places, "coordinate_note": origin_note}
    evidence.append({"label": "Kakao 장소·길찾기", "url": kakao_places.DOCS,
                     "status": "API 응답", "asof": "조회 시점"})
    return {"listing": listing, "mobility": mobility, "school": school,
            "amenities": amenities, "evidence": evidence}
