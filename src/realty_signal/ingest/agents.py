"""근처 공인중개사 — 카카오 로컬 키워드 검색(부동산) 기반.

단지 좌표 반경 내 중개사무소를 거리순으로. 상호·전화·도로명주소·카카오맵 링크.
출처 표기 의무(카카오) 준수: 결과에 place_url(카카오맵) 포함.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"


def search_agents(lat: float, lng: float, key: str, radius: int = 1000, size: int = 8) -> list[dict]:
    """좌표 반경(m) 내 '부동산' 키워드 장소를 거리순. 중개 관련만 필터."""
    params = {"query": "부동산", "x": lng, "y": lat, "radius": min(radius, 20000),
              "sort": "distance", "size": min(size * 2, 15)}
    req = urllib.request.Request(_URL + "?" + urllib.parse.urlencode(params),
                                 headers={"Authorization": f"KakaoAK {key}"})
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())  # noqa: S310
    except Exception:
        return []
    out = []
    for d in data.get("documents", []):
        cat = d.get("category_name", "")
        nm = d.get("place_name", "")
        if "부동산" not in cat and "중개" not in nm and "부동산" not in nm:
            continue
        out.append({
            "상호": nm,
            "전화": d.get("phone") or None,
            "거리m": int(d["distance"]) if d.get("distance") else None,
            "주소": d.get("road_address_name") or d.get("address_name"),
            "url": d.get("place_url"),
        })
        if len(out) >= size:
            break
    return out
