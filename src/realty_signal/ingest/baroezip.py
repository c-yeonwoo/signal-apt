"""개인용 급매·찐매물 레이더 — baroezip 공개 spatialmarket API (인증/서명 없음).

⚠️ 개인용·로컬 확인 전용. 데이터 원천은 네이버 부동산 매물(complex_no=네이버 단지번호)이며
baroezip이 집계·노출한다. 재배포/상업이용 금지(부정경쟁방지법·각 사 ToS). 저빈도로만 호출.

- 급매: 기본(또는 scope=urgent) — is_urgent 호가. 갭 = (호가−중위시세)/중위시세.
- 찐매물: scope=all + apt_list.has_certified — 집주인 내집등록·인증 매물(realtor/customer 있음).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

_URL = "https://baroezip.com/api/apartment/spatialmarket"
_HDR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json"}


def fetch_market(lat1: float, lng1: float, lat2: float, lng2: float,
                 *, scope: str | None = None) -> list[dict]:
    """bbox 내 매물 목록(개별 호가). 단지별 market_data를 평탄화.

    scope=None/urgent → 급매 위주 피드.
    scope='all' → 급매+찐매물(has_certified) 혼합. 찐매물만 쓰려면 호출 측에서 필터.
    """
    q: dict[str, str | float] = {
        "latitude_1": lat1, "longitude_1": lng1,
        "latitude_2": lat2, "longitude_2": lng2,
    }
    if scope:
        q["scope"] = scope
    try:
        raw = urllib.request.urlopen(  # noqa: S310
            urllib.request.Request(
                f"{_URL}?{urllib.parse.urlencode(q)}", headers=_HDR), timeout=25).read()
        data = json.loads(raw).get("data", [])
    except Exception:
        return []

    out = []
    for grp in data:
        al = grp.get("apt_list", {}) or {}
        has_cert = bool(al.get("has_certified"))
        for m in grp.get("market_data", []) or []:
            호가 = m.get("deal_amount")
            중위 = m.get("median_deal_amount")
            if not 호가:
                continue
            gap = round((호가 - 중위) / 중위 * 100, 1) if 중위 else None
            urgent = bool(m.get("is_urgent"))
            # 찐매물 = 단지 인증 + (비급매 또는 집주인/중개 연결). 급매 전용 피드는 has_cert=False.
            certified = has_cert and (not urgent or bool(m.get("customer") or m.get("realtor")))
            out.append({
                "단지명": m.get("complex_name") or al.get("complex_name"),
                "complex_no": m.get("complex_no") or al.get("complex_no"),  # 네이버 단지번호
                "평형": m.get("pyeong_name"),
                "전용면적": m.get("exclusive_use_area"),
                "층": m.get("floor"),
                "방향": m.get("direction"),
                "거래": m.get("trade_type"),                 # trade=매매
                "호가": 호가,
                "중위시세": 중위,
                "급매갭": gap,                                # % (음수=시세 이하)
                "급매": urgent,
                "찐매물": certified,
                "세대수": al.get("total_household_count"),
                "연식": (al.get("use_approve_ymd") or "")[:4] or None,
                "lat": al.get("latitude") or m.get("latitude"),
                "lng": al.get("longitude") or m.get("longitude"),
                "naver_id": m.get("original"),               # 네이버 매물 id
            })
    return out


def bbox_around(lat: float, lng: float, dlat: float = 0.05, dlng: float = 0.06) -> tuple:
    """중심좌표 → (lat1,lng1,lat2,lng2). 시군구 1개를 대략 덮는 박스."""
    return (lat - dlat, lng - dlng, lat + dlat, lng + dlng)
