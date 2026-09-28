"""2~3개 선택 매물의 동일 기준 비교. 우열 점수 없이 관측과 누락을 나란히 놓는다."""

from __future__ import annotations

from math import isfinite

from realty_signal.services import property_analysis


def _positive(value) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if isfinite(n) and n > 0 else None


def build(rows: list[dict], detail_get, profile: dict | None = None) -> dict:
    if not 2 <= len(rows) <= 3:
        raise ValueError("comparison_requires_two_or_three")
    budget = _positive(((profile or {}).get("매수력") or {}).get("최대매수가"))
    details: dict[tuple[str, str], dict] = {}
    items = []
    for row in rows:
        region, name = row.get("지역"), row.get("단지명")
        identity = (region, name)
        if region and name and identity not in details:
            try:
                details[identity] = detail_get(region, name) or {}
            except Exception:  # noqa: BLE001 - 해당 단지 가격만 보류
                details[identity] = {"status": "failed", "degraded": True}
        report = property_analysis.build(row, details.get(identity))
        listing, price = report["listing"], report["price"]
        asking, area = listing["asking_manwon"], listing["exclusive_m2"]
        items.append({"listing": listing, "price": price,
                      "asking_per_m2_manwon": round(asking / area, 1) if asking and area else None,
                      "budget_fit": ("within" if asking <= budget else "above") if asking and budget else "unknown",
                      "building": report["building"], "evidence": report["evidence"]})
    warnings = []
    areas = [x["listing"]["exclusive_m2"] for x in items]
    if None in areas or max(areas) - min(areas) > 1:
        warnings.append("전용면적이 달라 총 호가만으로 저렴한 매물을 고를 수 없습니다.")
    dates = {x["listing"]["collected_at"] for x in items}
    if len(dates) > 1 or None in dates:
        warnings.append("매물 수집 시점이 서로 다르거나 확인되지 않았습니다.")
    observed = [x for x in items if x["price"].get("상태") == "관측비교"]
    if len(observed) < len(items):
        warnings.append("일부 매물의 동일 면적 실거래 표본이 부족해 호가 비교를 보류했습니다.")
    if budget is None:
        warnings.append("확정 매수력이 없어 내 예산 충족 여부를 계산하지 않았습니다.")
    return {"items": items, "budget_manwon": budget, "warnings": warnings,
            "basis": "현재 서버 수집 매물과 각 단지의 같은 전용면적·층 조건 국토부 실거래. 매물별 수집일·거래 표본을 확인하세요.",
            "not_compared": ["동·향·수리 상태", "주차·관리비", "현장 출입구·통학 배정", "세금·대출 승인"]}
