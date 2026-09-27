"""KB 주간 가격 흐름 기반의 지역 경기국면 추정.

현재 배지와 과거 밴드는 같은 26주 가격 모형을 사용한다. 매수심리는
보조 지표로만 표시하며, 가격이 보합이면 회복/상승을 단정하지 않는다.
"""

from __future__ import annotations


def _dir(mom: float, up: float = 0.03, down: float = -0.03) -> str:
    return "상승" if mom >= up else "하락" if mom <= down else "보합"


def current_phase(kb, region: str = "서울") -> dict | None:
    sale = kb.series(region, "sale_change").dropna()
    if sale.empty:
        return None
    bands = cycle_history(kb, region)
    if not bands:
        return None
    bs = kb.series(region, "buyer_superiority").dropna()
    js = kb.series(region, "jeonse_supply").dropna()

    mom = float(sale.tail(8).mean())                     # 최근 8주 매매 모멘텀
    prev = float(sale.tail(16).head(8).mean()) if len(sale) >= 16 else mom
    accel = round(mom - prev, 3)
    price_dir = _dir(mom)

    bs_now = float(bs.tail(4).mean()) if not bs.empty else None
    bs_prev = float(bs.tail(12).head(4).mean()) if len(bs) >= 12 else bs_now
    demand_dir = "회복" if (bs_now is not None and bs_prev is not None and bs_now > bs_prev + 2) else \
                 "둔화" if (bs_now is not None and bs_prev is not None and bs_now < bs_prev - 2) else "보합"
    jsv = round(float(js.tail(4).mean()), 1) if not js.empty else None

    phase = bands[-1]["phase"]

    reasons = [
        "국면 기준: 매매증감률 26주 평균과 이전 26주 대비 변화",
        f"단기 매매 모멘텀 {price_dir} (최근 8주 평균 {mom:+.2f}%, 직전 대비 {accel:+.2f}%p)",
        f"보조 매수심리(매수우위) {demand_dir}" + (f" — 현재 {bs_now:.0f}" if bs_now is not None else " (자료 없음)"),
    ]
    if jsv is not None:
        reasons.append(f"전세수급 {jsv:.0f} ({'전세난' if jsv >= 170 else '타이트' if jsv >= 140 else '보통' if jsv >= 100 else '공급우위'})")

    return {
        "region": region, "phase": phase, "reasons": reasons,
        "price": {"dir": price_dir, "mom": round(mom, 2), "accel": accel},
        "demand": {"dir": demand_dir, "now": round(bs_now) if bs_now is not None else None},
        "jeonse": jsv,
        "asof": str(sale.index[-1].date()),
    }


def cycle_history(kb, region: str = "서울") -> list[dict]:
    """매매 모멘텀·가속도 기반 국면 밴드. 현재 배지도 이 마지막 밴드를 사용한다.

    가격만으로 산출(지역별 고유) → 지역마다 곡선이 다름. 잡음 방지 위해 26주 평활 + 짧은 밴드 반복 병합.
    """
    import pandas as pd
    sale = kb.series(region, "sale_change").dropna()
    if len(sale) < 38:
        return []
    # 매크로 사이클용 평활 — 26주(반년) 모멘텀 방향×가속으로 판정해 상승↔후퇴 잔파동 억제
    mom = sale.rolling(26, min_periods=12).mean()
    prev = mom.shift(26)
    seq = []
    for d in sale.index:
        m, p = mom.get(d), prev.get(d)
        if m is None or p is None or pd.isna(m) or pd.isna(p):
            continue
        # 작은 변화는 방향이 아니라 관망으로 둔다. 0과 양수/음수의 경계에서
        # 관찰 노이즈가 자동 매수 신호로 승격되는 것을 막는다.
        if abs(m) < 0.03:
            phase = "관망"
        elif m >= 0.03:
            phase = "후퇴기" if m - p < -0.02 else "상승기"
        else:
            phase = "회복기" if m - p > 0.02 else "침체기"
        seq.append((d, phase))
    if not seq:
        return []
    # 연속 동일 국면 병합
    bands = []
    for d, ph in seq:
        if bands and bands[-1]["phase"] == ph:
            bands[-1]["end"] = str(d.date()); bands[-1]["_n"] += 1
        else:
            bands.append({"start": str(d.date()), "end": str(d.date()), "phase": ph, "_n": 1})
    # 짧은 밴드(<20주 ≈ 5개월) 를 반복적으로 직전 밴드에 흡수 → 매크로 국면만 남김
    changed = True
    while changed and len(bands) > 1:
        changed = False
        merged = []
        for i, b in enumerate(bands):
            # 같은 국면 인접 → 합치고, 짧은(<20주) 밴드 → 직전에 흡수(직전 국면 유지)
            recent_neutral = i == len(bands) - 1 and b["phase"] == "관망" and b["_n"] >= 4
            if merged and (merged[-1]["phase"] == b["phase"] or
                           (b["_n"] < 20 and not recent_neutral)):
                merged[-1]["end"] = b["end"]; merged[-1]["_n"] += b["_n"]; changed = True
            else:
                merged.append(b)
        bands = merged
    for b in bands:
        b.pop("_n", None)
    return bands
