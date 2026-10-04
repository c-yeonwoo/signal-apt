"""제휴 범위의 개인 계정용 급매·찐매물 레이더 — baroezip spatialmarket API.

사용자가 바로이집과의 제휴를 확인했다. 계약의 구체적 이용 범위는 이 코드에 포함되지
않으므로 수집 결과는 개인 계정에만 노출하고 저빈도로 호출한다. 공개 응답이라는 사실만으로
다른 서비스/사용자에 대한 재사용 권한을 추론하지 않는다.

- 급매: 기본(또는 scope=urgent) — 공급사 is_urgent 표시. 자체 가격 판정이 아니다.
- 찐매물: scope=all + apt_list.has_certified — 집주인 내집등록·인증 매물(realtor/customer 있음).
"""

from __future__ import annotations

import json

from realty_signal import jsonx
import urllib.parse
import urllib.request

_URL = "https://baroezip.com/api/apartment/spatialmarket"
_HDR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json"}


def without_unverified_comparison(row: dict) -> dict:
    """구 캐시도 동일 면적·거래조건이 증명되지 않은 중위가격과 갭을 공개하지 않는다.

    공급사 표시·실제 호가·면적·원천 ID는 보존한다. 새 가격 비교는 실거래 근거를
    갖춘 별도 계약으로 제공해야 하며 이 레거시 필드를 다시 할인율로 쓰지 않는다.
    """
    return {**row, "급매갭": None, "중위시세": None,
            "가격비교상태": "비교조건 미확인", "급매판정출처": "공급사 표시"}


def safe_market_rows(rows: object) -> list[dict]:
    """기존 갭순 캐시의 순위도 제거한다. 원본 행이나 캐시 파일은 수정하지 않는다."""
    if not isinstance(rows, list):
        return []
    return sorted((without_unverified_comparison(row) for row in rows if isinstance(row, dict)),
                  key=lambda row: (str(row.get("단지명") or ""), str(row.get("naver_id") or "")))


def fetch_market_with_status(lat1: float, lng1: float, lat2: float, lng2: float,
                             *, scope: str | None = None) -> tuple[list[dict], str | None]:
    """매물과 원천 호출 상태를 함께 반환한다.

    빈 매물은 정상 응답일 수 있으므로, 네트워크·응답 형식 오류와 구분해야 한다.
    레이더 갱신에서 전체 원천 장애를 새 ``0건`` 캐시로 덮어쓰지 않기 위해 쓴다.

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
        data = jsonx.loads(raw).get("data", [])
        if not isinstance(data, list):
            return [], "응답 data가 목록 형식이 아님"
    except Exception as e:  # noqa: BLE001 - 호출자는 원천 장애를 표시해야 한다
        return [], f"{type(e).__name__}: {str(e)[:160]}"

    out = []
    for grp in data:
        al = grp.get("apt_list", {}) or {}
        has_cert = bool(al.get("has_certified"))
        for m in grp.get("market_data", []) or []:
            호가 = m.get("deal_amount")
            if not 호가:
                continue
            urgent = bool(m.get("is_urgent"))
            # 찐매물 = 단지 인증 + (비급매 또는 집주인/중개 연결). 급매 전용 피드는 has_cert=False.
            certified = has_cert and (not urgent or bool(m.get("customer") or m.get("realtor")))
            out.append(without_unverified_comparison({
                "단지명": m.get("complex_name") or al.get("complex_name"),
                "complex_no": m.get("complex_no") or al.get("complex_no"),  # 네이버 단지번호
                "평형": m.get("pyeong_name"),
                "전용면적": m.get("exclusive_use_area"),
                "층": m.get("floor"),
                "방향": m.get("direction"),
                "거래": m.get("trade_type"),                 # trade=매매
                "호가": 호가,
                "급매": urgent,
                "찐매물": certified,
                "세대수": al.get("total_household_count"),
                "연식": (al.get("use_approve_ymd") or "")[:4] or None,
                "lat": al.get("latitude") or m.get("latitude"),
                "lng": al.get("longitude") or m.get("longitude"),
                "naver_id": m.get("original"),               # 네이버 매물 id
            }))
    return out, None


def fetch_market(lat1: float, lng1: float, lat2: float, lng2: float,
                 *, scope: str | None = None) -> list[dict]:
    """bbox 내 매물 목록(개별 호가). 기존 호출자용 호환 래퍼."""
    rows, _ = fetch_market_with_status(lat1, lng1, lat2, lng2, scope=scope)
    return rows


def bbox_around(lat: float, lng: float, dlat: float = 0.05, dlng: float = 0.06) -> tuple:
    """중심좌표 → (lat1,lng1,lat2,lng2). 시군구 1개를 대략 덮는 박스."""
    return (lat - dlat, lng - dlng, lat + dlat, lng + dlng)
