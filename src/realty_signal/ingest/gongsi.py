"""공동주택 공시가격 — VWorld NED WFS(getApartHousingPriceWFS, TYPENAME=dt_d166).

단지 좌표 주변 bbox 로 공시가격 피처를 조회한다. 실거래(국토부) 대비 공시가 비율 = 저평가·보유세 근거.
VWorld 키는 도메인 잠금 → domain 파라미터 필요(config.vworld_domain).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

_URL = "https://api.vworld.kr/ned/wfs/getApartHousingPriceWFS"


def fetch_bbox(lat: float, lng: float, key: str, domain: str = "localhost", d: float = 0.004) -> list[dict]:
    """좌표 bbox 내 공동주택 공시가격 피처 목록. 실패/빈결과 시 []."""
    q = urllib.parse.urlencode({
        "key": key, "domain": domain, "SERVICE": "WFS", "REQUEST": "GetFeature",
        "TYPENAME": "dt_d166", "BBOX": f"{lat - d},{lng - d},{lat + d},{lng + d}",
        "SRSNAME": "EPSG:4326", "maxFeatures": 60, "output": "application/json",
    })
    try:
        raw = urllib.request.urlopen(f"{_URL}?{q}", timeout=15).read()  # noqa: S310
        feats = json.loads(raw).get("features", [])
    except Exception:
        return []
    out = []
    for f in feats:
        p = f.get("properties", {})
        g = (f.get("geometry") or {}).get("coordinates") or [None, None]
        out.append({
            "단지명": p.get("aphus_nm"), "pnu": p.get("pnu"),
            "평균공시가": p.get("avrg_pblntf_pc"),        # 원(세대 평균)
            "㎡단가": p.get("unit_ar_pc"),                 # 원/㎡
            "기준연도": p.get("stdr_year"),
            "lat": g[1], "lng": g[0],
            "추이": [p.get(k) for k in (                    # 4년전 → 현재 평균공시가
                "pstyr_4_avrg_pblntf_pc", "pstyr_3_avrg_pblntf_pc",
                "pstyr_2_avrg_pblntf_pc", "pstyr_1_avrg_pblntf_pc", "avrg_pblntf_pc") if p.get(k)],
        })
    return out
