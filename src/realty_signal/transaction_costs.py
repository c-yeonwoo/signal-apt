"""매물별 매수 시점 부대비용 — 호가·실거래가 기준 총매입가.

매수력(예산 한도)과 분리: 특정 매수가에 붙는 취득세·중개·법무·이사·인테리어만 다룬다.
취득세·중개보수는 `buying_power` 를 재사용한다. 세율·요율은 실무 근사.
"""

from __future__ import annotations

from realty_signal import buying_power as bp
from realty_signal import regulation as reg

# 법무/이전비 근사 — (가격상한 만원, 법무비 만원)
LEGAL_BRACKETS = [
    (30_000, 50),
    (60_000, 80),
    (90_000, 100),
    (float("inf"), 120),
]

DEFAULTS = {
    "이사비": bp.DEFAULTS["이사비"],   # 만원
    "인테리어평당": 150,               # 만원/전용평
}


def legal_fee(price: float) -> float:
    """법무·소유권이전 등기 대행비 근사(만원)."""
    for cap, fee in LEGAL_BRACKETS:
        if price < cap:
            return float(fee)
    return float(LEGAL_BRACKETS[-1][1])


def _pyeong(exclusive_m2: float | None, pyeong: float | None) -> float | None:
    if pyeong is not None and pyeong > 0:
        try:
            return float(pyeong)
        except (TypeError, ValueError):
            pass
    if exclusive_m2 is not None and exclusive_m2 > 0:
        try:
            return round(float(exclusive_m2) / 3.3058, 1)
        except (TypeError, ValueError):
            pass
    return None


def _exclusive_m2(exclusive_m2: float | None, pyeong: float | None) -> float | None:
    if exclusive_m2 is not None and exclusive_m2 > 0:
        try:
            return float(exclusive_m2)
        except (TypeError, ValueError):
            pass
    py = _pyeong(None, pyeong)
    if py is not None:
        return round(py * 3.3058, 1)
    return None


def default_interior(exclusive_m2: float | None = None,
                     pyeong: float | None = None) -> float:
    """기본 인테리어(만원) — 평당 × 전용평. 평형 없으면 0."""
    py = _pyeong(exclusive_m2, pyeong)
    if py is None:
        return 0.0
    return round(py * DEFAULTS["인테리어평당"])


def estimate(
    price: float,
    *,
    region: str | None = None,
    exclusive_m2: float | None = None,
    pyeong: float | None = None,
    homes: int = 0,
    first_time: bool = False,
    moving: float | None = None,
    interior: float | None = None,
    sido: str | None = None,
) -> dict:
    """매수가(만원) 기준 매수 부대비용 분해.

    interior=None → 평당 기본값 적용. interior=0 → 인테리어 제외.
    moving=None → DEFAULTS 이사비.
    """
    price = max(0.0, float(price or 0))
    area = _exclusive_m2(exclusive_m2, pyeong)
    py = _pyeong(exclusive_m2, pyeong)
    big = bool(area and area > 85)

    p = bp.Params(
        capital=0,
        homes=int(homes or 0),
        first_time=bool(first_time),
        region=region or None,
        big_area=big,
        sido=sido,
        moving_cost=DEFAULTS["이사비"] if moving is None else max(0.0, float(moving)),
        repair_cost=0,
    )
    tax = bp.acq_tax(price, p)
    fee = bp.broker_fee(price)
    legal = legal_fee(price)
    move = float(p.moving_cost)
    if interior is None:
        inter = default_interior(area, py)
    else:
        inter = max(0.0, float(interior))

    costs = {
        "취득세": round(tax),
        "중개비": round(fee),
        "법무비": round(legal),
        "이사비": round(move),
        "인테리어": round(inter),
    }
    costs["합계"] = sum(costs.values())
    total = round(price) + costs["합계"]

    notes = [
        f"규제 기준일 {reg.AS_OF} · 중개보수는 법정 상한, 실제 협의 가능",
        "법무·인테리어는 근사치이며 단지·시공 범위에 따라 달라집니다",
        "최종 세액·비용은 세무사·중개사·법무사 확인이 필요합니다",
    ]
    if first_time and int(homes or 0) == 0:
        notes.insert(0, "생애최초 취득세 감면(한도) 반영")
    if big:
        notes.insert(0, "전용 85㎡ 초과 — 농어촌특별세 포함")

    return {
        "매수가": round(price),
        "부대비용": costs,
        "총매입가": total,
        "가정": {
            "지역": p.region,
            "규제지역": bool(p.regulated),
            "수도권": bool(p.metro),
            "주택수": int(p.homes),
            "생애최초": bool(p.first_time),
            "전용㎡": area,
            "평형": py,
            "큰평형": big,
            "인테리어평당": DEFAULTS["인테리어평당"],
            "기준일": reg.AS_OF,
        },
        "notes": notes,
    }
