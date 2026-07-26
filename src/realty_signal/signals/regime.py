"""급지역전 지표 — 수도권 유동성 국면 (매도 타이밍 방법론).

자금은 상급지→하급지로 흐른다. 하급지(싼 곳)가 상급지보다 더 오르면 = 유동성이 외곽까지
밀려난 '끝물' 신호. 평단가(국토부)로 급지를 나누고, KB 주간 매매증감으로 상승률을 재서:
  - β: 상승률 ~ log(평단가) 회귀 기울기. β<0 = 하급지 주도 = 끝물.
  - 군집갭: (C·D급 평균 상승률) − (A·B급 평균 상승률). >0 = 끝물.
수도권 통합 권역 1개. 지방은 시군구 평단가 부족으로 미산출.
"""

from __future__ import annotations

import math

_METRO = ("11", "41", "28")  # 서울·경기·인천


def _linfit(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return (sxy / sxx) if sxx else 0.0


def compute_regime(kb, loc_df, codes: dict, window: int = 8) -> dict:
    """수도권 급지역전 국면 산출. loc_df: 저평가 캐시(시군구 평단가)."""
    if loc_df is None or loc_df.empty:
        return {}
    rows = []
    for _, lr in loc_df.iterrows():
        region = lr["region"]
        code = codes.get(region, "") or ""
        if code[:2] not in _METRO or not lr.get("price"):
            continue
        s = kb.series(region, "sale_change")
        if s.empty:
            continue
        rows.append({"region": region, "price": float(lr["price"]),
                     "rise": round(float(s.tail(window).sum()), 2)})
    if len(rows) < 12:
        return {}

    prices = sorted(r["price"] for r in rows)
    q1, q2, q3 = prices[len(prices) // 4], prices[len(prices) // 2], prices[3 * len(prices) // 4]
    for r in rows:
        p = r["price"]
        r["급지"] = "D" if p < q1 else "C" if p < q2 else "B" if p < q3 else "A"

    beta = _linfit([math.log(r["price"]) for r in rows], [r["rise"] for r in rows])
    lo = [r["rise"] for r in rows if r["급지"] in ("C", "D")]
    hi = [r["rise"] for r in rows if r["급지"] in ("A", "B")]
    gap = (sum(lo) / len(lo)) - (sum(hi) / len(hi))

    # 국면 4단계 (β 우선, 군집갭 보조)
    if beta >= 0.5:
        phase, color = "상급지 주도", "green"
    elif beta >= 0:
        phase, color = "광역 확산", "yellow"
    elif beta > -0.5:
        phase, color = "하급지 순환(끝물 주의)", "orange"
    else:
        phase, color = "끝물(매도 경고)", "red"
    endgame = beta < 0 and gap > 0  # 끝물 종합 판정

    # 막차 경고: 하급지(C/D)인데 최근 상승 상위20% + 끝물 국면
    rises = sorted(r["rise"] for r in rows)
    thr = rises[int(len(rises) * 0.8)]
    for r in rows:
        r["막차"] = bool(r["급지"] in ("C", "D") and r["rise"] >= thr and beta < 0)

    lo_avg = round(sum(lo) / len(lo), 2) if lo else None
    hi_avg = round(sum(hi) / len(hi), 2) if hi else None
    evidence = _evidence(rows, window, lo_avg, hi_avg)

    return {
        "phase": phase, "color": color, "endgame": endgame,
        "beta": round(beta, 2), "gap": round(gap, 2), "window": window,
        "desc": _desc(phase, beta, gap),
        "evidence": evidence,
        "regions": {r["region"]: {"급지": r["급지"], "평단가": round(r["price"]),
                                  "rise": r["rise"], "막차": r["막차"]} for r in rows},
    }


def _evidence(rows: list, window: int, lo_avg: float | None, hi_avg: float | None) -> dict:
    """끝물 판단 근거 — 하급지 주도 지역·상승폭 + 상급지 참고."""
    drivers = sorted(
        (r for r in rows if r["급지"] in ("C", "D")),
        key=lambda r: (-int(r["막차"]), -r["rise"]),
    )[:5]
    anchors = sorted(
        (r for r in rows if r["급지"] in ("A", "B")),
        key=lambda r: -r["price"],
    )[:3]
    return {
        "window": window,
        "하급지평균": lo_avg,
        "상급지평균": hi_avg,
        "drivers": [
            {"region": r["region"], "급지": r["급지"], "rise": r["rise"],
             "평단가": round(r["price"]), "막차": r["막차"]}
            for r in drivers
        ],
        "상급지참고": [
            {"region": r["region"], "급지": r["급지"], "rise": r["rise"],
             "평단가": round(r["price"])}
            for r in anchors
        ],
    }


def _desc(phase: str, beta: float, gap: float) -> str:
    if beta >= 0.5:
        return "상급지가 하급지보다 더 오르는 정상 상승 국면 — 유동성 풍부, 매수 우호."
    if beta >= 0:
        return "상·하급지 상승률이 비슷해지는 확산 국면 — 중반."
    if beta > -0.5:
        return "하급지가 상급지보다 더 오르기 시작 — 유동성이 외곽으로 밀리는 끝물 진입 주의."
    return "하급지 상승률이 상급지를 크게 추월 — 유동성 끝물, 매도/관망 경고."
