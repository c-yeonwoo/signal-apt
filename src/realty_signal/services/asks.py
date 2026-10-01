"""상한 안의 확인된 호가.

지역 평단·경매 최저가·청약 추정은 여기 들어오지 않는다.
현금이 맞는 호가만 남기고, 상한에 가까운 것부터 고른다.
"""

from __future__ import annotations

from realty_signal.services import buyer_decision

ASKING = ("급매", "찐매물", "일반매물")


def _price(row: dict) -> float | None:
    try:
        price = float(row.get("총액"))
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return price


def within_ceiling(rows: list[dict], budget: float, *, limit: int | None = None) -> list[dict]:
    """가격 출처가 호가이고 총액이 상한 이하인 매물. 비싼 순."""
    if not budget or budget <= 0:
        return []
    pool = []
    for row in rows:
        if row.get("유형") not in ASKING:
            continue
        if row.get("price_kind") != "asking" and row.get("가격출처") != "매물가":
            continue
        price = _price(row)
        if price is None or price > float(budget):
            continue
        pool.append(row)
    pool.sort(key=lambda row: float(row["총액"]), reverse=True)
    picked, seen, rest = [], set(), []
    for row in pool:
        name = (row.get("단지명") or "", row.get("지역") or "")
        if name in seen:
            rest.append(row)
            continue
        seen.add(name)
        picked.append(row)
        if limit and len(picked) >= limit:
            return picked
    for row in rest:
        picked.append(row)
        if limit and len(picked) >= limit:
            break
    return picked


def known_asks(rows: list[dict], params, budget: float, *, uid=None, sido_of=None,
                limit: int = 3) -> list[dict]:
    """상한 안 호가 중 필요현금이 가정 안에 들어오는 것. 승인이 아니다."""
    out = []
    for row in within_ceiling(rows, budget):
        item = dict(row)
        item["가격출처"] = "매물가"
        item["price_kind"] = "asking"
        item = buyer_decision.annotate(item, params, uid=uid, sido_of=sido_of)
        finance = item.get("자금") or {}
        if finance.get("가능") and not finance.get("확인필요"):
            out.append(item)
        if len(out) >= limit:
            break
    return out
