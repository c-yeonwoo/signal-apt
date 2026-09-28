"""사용자가 지도에서 지정한 경로 시작점은 검증된 건물 출입구가 아니다."""

from __future__ import annotations

import math

from realty_signal.ingest.kakao_places import valid_point
from realty_signal.services.property_analysis import snapshot


MAX_OFFSET_M = 800


def validate(row: dict, lat: object, lng: object) -> tuple[float, float]:
    try:
        point = (float(lat), float(lng))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_coordinate") from exc
    if not valid_point(*point):
        raise ValueError("invalid_coordinate")
    original = snapshot(row)["coordinate"]
    if not original:
        raise ValueError("listing_coordinate_unavailable")
    p1, p2 = math.radians(original[0]), math.radians(point[0])
    dp, dl = p2-p1, math.radians(point[1]-original[1])
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    distance = 2*6371000*math.asin(min(1, math.sqrt(a)))
    if distance > MAX_OFFSET_M:
        raise ValueError("too_far_from_listing")
    return round(point[0], 7), round(point[1], 7)
