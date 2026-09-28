"""선택한 매물의 서버 확인 문맥과 근거 기반 분석 카드."""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite

from realty_signal.services import quote_check

ANALYZABLE = frozenset({"일반매물", "급매", "찐매물", "경매"})
PRIVATE = frozenset({"일반매물", "급매", "찐매물"})


def resolve(key: str, *, private_allowed: bool) -> dict:
    """클라이언트 필드 대신 현재 서버 수집분을 조회한다."""
    if not isinstance(key, str) or len(key) > 180 or ":" not in key:
        raise ValueError("invalid_listing_key")
    kind = key.split(":", 1)[0]
    if kind not in ANALYZABLE:
        raise ValueError("unsupported_listing_kind")
    if kind in PRIVATE and not private_allowed:
        raise PermissionError("personal_listing")
    from realty_signal import api
    row = next((r for r in api._build_listings({kind}, include_private=private_allowed)
                if r.get("key") == key), None)
    if row is None:
        raise LookupError("listing_not_found")
    return row


def _number(value: object) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if isfinite(n) and n > 0 else None


def _date(value: object) -> str | None:
    if isinstance(value, (int, float)) and value > 0:
        try:
            return datetime.fromtimestamp(value, timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        return value.strip()[:24]
    return None


def snapshot(row: dict) -> dict:
    """LLM/브라우저에 전달할 검증된 최소 매물 정보."""
    ref = row.get("ref") or {}
    kind = row.get("유형")
    price = _number(row.get("총액"))
    area = _number(ref.get("전용면적"))
    floor = ref.get("층")
    coords = None
    lat, lng = _number(row.get("lat")), _number(row.get("lng"))
    if lat is not None and lng is not None and 33 <= lat <= 39 and 124 <= lng <= 132:
        coords = [lat, lng]
    return {
        "key": row.get("key"), "kind": kind, "name": row.get("단지명"),
        "region": row.get("지역"), "asking_manwon": price,
        "exclusive_m2": area, "floor": floor, "source": row.get("source") or kind,
        "published_at": row.get("published_at") or ref.get("등록일"),
        "collected_at": _date(row.get("fetched_at")), "stale": bool(row.get("stale")),
        "coordinate": coords,
        "location_quality": "listing_point_unverified_entrance" if coords else "unknown",
        "complex_source_id": ref.get("hanbang_complex_id") or ref.get("complex_no"),
    }


def buyer_fit(listing: dict, profile: dict | None = None) -> dict:
    """확정 매수력과 호가만 비교한다. 구매 가능성/대출 승인이 아니다."""
    budget = _number(((profile or {}).get("매수력") or {}).get("최대매수가"))
    asking = _number(listing.get("asking_manwon"))
    if budget is None:
        return {"status": "unknown", "reason": "확정 매수력이 없어 예산 적합성을 판단하지 않았습니다."}
    if asking is None:
        return {"status": "unknown", "reason": "호가가 없어 확정 매수력과 비교할 수 없습니다."}
    return {"status": "within" if asking <= budget else "above", "budget_manwon": budget,
            "asking_manwon": asking, "gap_manwon": round(budget - asking),
            "note": "호가와 확정 매수력만 비교합니다. 취득세·수리비·대출 승인·월 부담은 별도로 확인해야 합니다."}


def _price(detail: dict, listing: dict) -> dict:
    asking, area = listing["asking_manwon"], listing["exclusive_m2"]
    if not asking or not area:
        return {"상태": "보류", "이유": "호가 또는 전용면적이 없어 실거래와 비교할 수 없습니다.",
                "표본수": 0, "중앙값": None, "호가차이율": None}
    try:
        floor = quote_check.validate_floor(listing.get("floor"))
    except ValueError:
        floor = None
    return quote_check.assess(detail, asking=asking, exclusive_m2=area, floor=floor)


def build(row: dict, detail: dict | None, building: dict | None = None,
          profile: dict | None = None) -> dict:
    """수치 해석은 결정적인 코드로 계산하고, 누락은 보류로 노출한다."""
    listing = snapshot(row)
    detail = detail or {}
    building = building or {}
    price = _price(detail, listing)
    exact_area = next((p for p in detail.get("평형별") or []
                       if listing["exclusive_m2"] and _number(p.get("전용㎡"))
                       and round(float(p["전용㎡"]), 1) == round(listing["exclusive_m2"], 1)), None)
    observed = (exact_area or {}).get("비교거래") or {}
    floor = price.get("입력층")
    trades = [
        {"month": t.get("거래월"), "price_manwon": t.get("가격"), "floor": t.get("층")}
        for t in observed.get("층별표본") or []
        if isinstance(t.get("가격"), (int, float)) and t["가격"] > 0
        and (floor is None or isinstance(t.get("층"), int) and abs(t["층"] - floor) <= 2)
    ][:40] if price.get("상태") == "관측비교" else []
    evidence = [
        {"id": "listing", "label": "수집 매물", "source": listing["source"],
         "asof": listing["collected_at"] or listing["published_at"], "grain": "매물",
         "status": "지난 결과" if listing["stale"] else "수집 결과"},
        {"id": "trades", "label": "국토부 동일 단지 실거래", "source": "국토교통부 아파트매매 실거래 상세",
         "url": "https://www.data.go.kr/data/15126468/openapi.do",
         "asof": price.get("비교기준일"), "grain": "단지·전용면적·층 조건",
         "status": "관측" if price.get("상태") == "관측비교" else "보류"},
    ]
    if building:
        evidence.append({"id": "building", "label": "건축물대장", "source": "국토교통부 건축물대장",
                         "url": "https://www.data.go.kr/data/15136560/openapi.do",
                         "asof": None, "grain": "건물·대지", "status": "조회 결과"})
    pros, cautions = [], []
    if price.get("상태") == "관측비교":
        diff = price.get("호가차이율")
        if isinstance(diff, (int, float)):
            sentence = f"호가가 비교거래 중앙값보다 {abs(diff):g}% {'낮습니다' if diff < 0 else '높습니다' if diff > 0 else '같습니다'} (거래 {price['표본수']}건)."
            (pros if diff < 0 else cautions).append({"text": sentence, "evidence": "trades"})
    else:
        cautions.append({"text": price.get("이유") or "가격 비교 근거가 부족합니다.", "evidence": "trades"})
    if listing["stale"]:
        cautions.append({"text": "매물 정보가 지난 수집 결과입니다. 판매 여부와 호가를 다시 확인하세요.",
                         "evidence": "listing"})
    if not listing["coordinate"]:
        cautions.append({"text": "확인된 단지 좌표가 없어 도보·통학구역을 판정하지 않았습니다.",
                         "evidence": "listing"})
    else:
        cautions.append({"text": "표시 좌표는 출입구가 검증되지 않았습니다. 실제 도보 경로는 출입구 확인 후 비교하세요.",
                         "evidence": "listing"})
    return {
        "listing": listing, "buyer_fit": buyer_fit(listing, profile), "price": price, "trades": trades,
        "complex": {k: detail.get(k) for k in ("총거래", "기간", "추세pct", "최근평단가",
                                               "identity_status", "status", "시그널", "단지시그널")
                    if detail.get(k) is not None},
        "building": {k: building.get(k) for k in ("사용승인일", "세대수", "최고층", "용적률", "건폐율")
                     if building.get(k) is not None},
        "mobility": {"status": "unverified", "reason": "출입구·목적지 경로는 아직 검증되지 않았습니다."},
        "school": {"status": "unverified", "reason": "통학구역 자료를 연결하지 않았습니다."},
        "amenities": {"status": "unverified", "reason": "시설 접근성 자료를 연결하지 않았습니다."},
        "development": {"status": "unverified", "reason": "이 단지와 연결된 공식 사업 자료를 확인하지 않았습니다."},
        "pros": pros[:3], "cautions": cautions[:3], "evidence": evidence,
        "questions": ["현재 판매 가능 여부와 실제 호가는?", "동·향·수리 상태와 추가 비용은?",
                      "출입구에서 직장·학교까지 실제 동선은?"],
    }
