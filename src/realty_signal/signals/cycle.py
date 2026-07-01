"""부동산 경기 사이클 국면 판정 — 벌집순환모형 4국면.

가격(매매증감 모멘텀) × 수요심리(매수우위 방향) 2축으로 현재 국면을 추정.
KB 광역(기본 '서울') 주간 시리즈만 사용. 심리·거래량이 가격을 선행한다는 전제.
"""

from __future__ import annotations


def _dir(mom: float, up: float = 0.03, down: float = -0.03) -> str:
    return "상승" if mom >= up else "하락" if mom <= down else "보합"


def current_phase(kb, region: str = "서울") -> dict | None:
    sale = kb.series(region, "sale_change").dropna()
    if sale.empty:
        return None
    bs = kb.series(region, "buyer_superiority").dropna()
    js = kb.series(region, "jeonse_supply").dropna()

    mom = float(sale.tail(8).mean())                     # 최근 8주 매매 모멘텀
    prev = float(sale.tail(16).head(8).mean()) if len(sale) >= 16 else mom
    accel = round(mom - prev, 3)
    price_dir = _dir(mom)

    bs_now = float(bs.tail(4).mean()) if not bs.empty else None
    bs_prev = float(bs.tail(12).head(4).mean()) if len(bs) >= 12 else bs_now
    demand_dir = "회복" if (bs_now and bs_prev and bs_now > bs_prev + 2) else \
                 "둔화" if (bs_now and bs_prev and bs_now < bs_prev - 2) else "보합"
    jsv = round(float(js.tail(4).mean()), 1) if not js.empty else None

    # 2x2 (가격 × 수요심리) → 4국면. 보합은 가속도/심리로 tiebreak.
    if price_dir == "상승":
        phase = "후퇴기" if demand_dir == "둔화" else "상승기"
    elif price_dir == "하락":
        phase = "회복기" if demand_dir == "회복" else "침체기"
    else:  # 보합
        phase = "회복기" if (demand_dir == "회복" or accel > 0.02) else \
                "후퇴기" if (demand_dir == "둔화" or accel < -0.02) else "회복기"

    reasons = [
        f"매매 모멘텀 {price_dir} (최근 8주 평균 {mom:+.2f}%, 직전 대비 {'가속' if accel > 0 else '둔화'} {accel:+.2f})",
        f"매수심리(매수우위) {demand_dir}" + (f" — 현재 {bs_now:.0f}" if bs_now else ""),
    ]
    if jsv is not None:
        reasons.append(f"전세수급 {jsv:.0f} ({'전세난' if jsv >= 170 else '타이트' if jsv >= 140 else '보통' if jsv >= 100 else '공급우위'})")

    return {
        "region": region, "phase": phase, "reasons": reasons,
        "price": {"dir": price_dir, "mom": round(mom, 2), "accel": accel},
        "demand": {"dir": demand_dir, "now": round(bs_now) if bs_now else None},
        "jeonse": jsv,
        "asof": str(sale.index[-1].date()),
    }
