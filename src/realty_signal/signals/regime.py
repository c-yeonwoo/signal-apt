"""급지역전 지표 — 수도권 유동성 국면 (매도 타이밍 방법론).

자금은 상급지→하급지로 흐른다. 평단가(국토부)로 급지 A~E를 나누고, KB 주간 매매증감
(최근 window주 합)을 급지 순(A→E)으로 봤을 때 **상승률이 갈수록 커지면** 유동성이
외곽·최하급지까지 밀린 '끝물'로 본다.

  - ladder: 급지별 평균 상승률. A→B→C→D→E 로 오르는 계단 수(ascents).
  - endgame: 계단이 뚜렷하고 최하급(D·E) 평균이 상급(A·B)보다 큼.
  - β·gap: 참고 지표(연속 회귀 / 상·하 군집 차). 국면 판정의 정본은 ladder.

지역 BUY/STRONG_BUY 시그널과는 축이 다르다 — 지역 시그널은 그 동네 KB 수급·모멘텀,
이 모듈은 수도권 전체 유동성 국면. 둘이 겹치면 '막차 매수' 가능성으로 읽는다.

표본 가드: E급 중간 평단가가 비정상적으로 높으면(외곽 시군구 누락) 급지가 찌그러진
것이므로 끝물/주의를 내지 않는다. 예: 수원 영통이 E로 잡히는 경우.
"""

from __future__ import annotations

import math

_METRO = ("11", "41", "28")  # 서울·경기·인천
_TIERS = ("A", "B", "C", "D", "E")  # A=최상급 … E=최하급
_LO = ("D", "E")                   # 끝물 주도·막차 후보
_HI = ("A", "B")
# 계단 한 칸이 노이즈로 뒤집히지 않게 최소 상승폭(%p, 8주 누적)
_STEP_EPS = 0.05
# 수도권 시군구 풀이 이보다 작으면 5분위가 불안정
_MIN_METRO = 30  # 5분위 안정 하한(정상 수도권 ~80)
# E급 중간 평단가(만/평) 상한 — 정상 E는 연천·가평·포천대(~500–1600).
# 이보다 크면 외곽이 빠진 채 수원·서울 중저가가 E/D로 밀려 끝물이 거짓 양성됨.
_E_MEDIAN_MAX = 2000


def _linfit(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return (sxy / sxx) if sxx else 0.0


def _assign_tiers(rows: list) -> None:
    """평단가 순위 5분위 — 싼 쪽 E, 비싼 쪽 A. 빈 구간 없도록 순위 기반."""
    ranked = sorted(rows, key=lambda r: r["price"])
    n = len(ranked)
    for i, r in enumerate(ranked):
        q = min(4, (i * 5) // n)          # 0=최저가 … 4=최고가
        r["급지"] = "EDCBA"[q]


def _tier_avgs(rows: list) -> list[dict]:
    out = []
    for t in _TIERS:
        xs = [r["rise"] for r in rows if r["급지"] == t]
        if not xs:
            continue
        out.append({"급지": t, "avg_rise": round(sum(xs) / len(xs), 2), "n": len(xs)})
    return out


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[len(s) // 2]


def _ladder_stats(tier_avgs: list[dict]) -> tuple[int, int, float | None]:
    """A→E 방향 상승 계단·하락 계단 수, Spearman-ish corr(급지순위, 상승률)."""
    if len(tier_avgs) < 3:
        return 0, 0, None
    vals = [t["avg_rise"] for t in tier_avgs]
    ascents = sum(1 for i in range(len(vals) - 1) if vals[i + 1] > vals[i] + _STEP_EPS)
    descents = sum(1 for i in range(len(vals) - 1) if vals[i] > vals[i + 1] + _STEP_EPS)
    n = len(vals)
    mx = (n - 1) / 2
    my = sum(vals) / n
    sxx = sum((i - mx) ** 2 for i in range(n))
    sxy = sum((i - mx) * (v - my) for i, v in enumerate(vals))
    corr = (sxy / sxx) if sxx else 0.0
    return ascents, descents, round(corr, 3)


def _sample_skewed(rows: list) -> tuple[bool, float | None, str | None]:
    """외곽 누락으로 E/D가 중저가 도시로 채워졌는지."""
    if len(rows) < _MIN_METRO:
        return True, None, f"수도권 {len(rows)}곳만 확보(최소 {_MIN_METRO})."
    e_med = _median([r["price"] for r in rows if r["급지"] == "E"])
    if e_med is not None and e_med > _E_MEDIAN_MAX:
        return True, e_med, (
            f"E급 중간 평단가 {e_med:.0f}만/평(정상 ≤{_E_MEDIAN_MAX}). "
            "외곽 시군구가 빠져 수원·서울 중저가가 최하급으로 잡혔을 수 있어요."
        )
    return False, e_med, None


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
    if len(rows) < 15:
        return {}

    _assign_tiers(rows)
    tier_avgs = _tier_avgs(rows)
    ascents, descents, ladder_corr = _ladder_stats(tier_avgs)
    steps = max(len(tier_avgs) - 1, 1)
    skewed, e_med, skew_note = _sample_skewed(rows)

    beta = _linfit([math.log(r["price"]) for r in rows], [r["rise"] for r in rows])
    lo = [r["rise"] for r in rows if r["급지"] in _LO]
    hi = [r["rise"] for r in rows if r["급지"] in _HI]
    gap = (sum(lo) / len(lo)) - (sum(hi) / len(hi)) if lo and hi else 0.0
    lo_avg = round(sum(lo) / len(lo), 2) if lo else None
    hi_avg = round(sum(hi) / len(hi), 2) if hi else None

    av = {t["급지"]: t["avg_rise"] for t in tier_avgs}
    e_gt_a = (av.get("E") is not None and av.get("A") is not None
              and av["E"] > av["A"] + _STEP_EPS)

    if skewed:
        # 급지가 거짓말하는 상태 — 끝물/주의·막차 금지
        phase, color = "급지 표본 주의", "yellow"
        endgame, late = False, False
    elif gap > 0 and e_gt_a and descents == 0 and ascents >= 3:
        phase, color = "끝물(매도 경고)", "red"
        endgame, late = True, True
    elif gap > 0 and e_gt_a and descents <= 1 and ascents >= 2:
        phase, color = "하급지 순환(끝물 주의)", "orange"
        endgame, late = False, True
    elif descents >= 3 and ascents <= 1 and gap <= 0:
        phase, color = "상급지 주도", "green"
        endgame, late = False, False
    else:
        phase, color = "광역 확산", "yellow"
        endgame, late = False, False

    rises = sorted(r["rise"] for r in rows)
    thr = rises[int(len(rises) * 0.8)]
    for r in rows:
        r["막차"] = bool(late and r["급지"] in _LO and r["rise"] >= thr)

    # 표본 이상일 때도 계단·주도 지역을 펼쳐 보여 '왜 보류인지' 설명
    evidence = _evidence(rows, window, lo_avg, hi_avg, tier_avgs) if (late or skewed) else {}
    if skewed and evidence is not None:
        evidence["quality"] = "skewed"
        evidence["quality_note"] = skew_note

    desc = _desc(phase, ascents, gap, tier_avgs, skew_note)

    return {
        "phase": phase, "color": color, "endgame": endgame,
        "beta": round(beta, 2), "gap": round(gap, 2), "window": window,
        "ascents": ascents, "descents": descents,
        "ladder_corr": ladder_corr, "ladder_steps": steps,
        "tier_avgs": tier_avgs,
        "n_regions": len(rows),
        "e_median_price": round(e_med) if e_med is not None else None,
        "quality": "skewed" if skewed else "ok",
        "quality_note": skew_note,
        "desc": desc,
        "evidence": evidence,
        "regions": {r["region"]: {"급지": r["급지"], "평단가": round(r["price"]),
                                  "rise": r["rise"], "막차": r["막차"]} for r in rows},
    }


def _evidence(rows: list, window: int, lo_avg: float | None, hi_avg: float | None,
              tier_avgs: list[dict]) -> dict:
    """끝물 판단 근거 — 급지 계단 + D·E 주도 지역 + A급 참고."""
    drivers = sorted(
        (r for r in rows if r["급지"] in _LO),
        key=lambda r: (-int(r["막차"]), -r["rise"]),
    )[:5]
    anchors = sorted(
        (r for r in rows if r["급지"] == "A"),
        key=lambda r: -r["price"],
    )[:3]
    return {
        "window": window,
        "하급지평균": lo_avg,   # D·E
        "상급지평균": hi_avg,   # A·B
        "ladder": tier_avgs,
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


def _desc(phase: str, ascents: int, gap: float, tier_avgs: list[dict],
          skew_note: str | None = None) -> str:
    if skew_note:
        return (
            f"{skew_note} 끝물·주의 판정을 보류합니다. "
            "지역 BUY/매도 시그널은 그대로 동네 수급 정본으로 보시면 됩니다."
        )
    ladder_txt = " → ".join(
        f"{t['급지']} {t['avg_rise']:+g}%" for t in tier_avgs
    ) if tier_avgs else ""
    trust = " 지역 BUY/매도 시그널은 동네 수급·모멘텀(정본)이고, 이 국면은 수도권 유동성 경고입니다."
    if phase.startswith("끝물") and "주의" not in phase:
        base = f"A→E 상승 계단 {ascents}칸 · 최하급지가 상급지보다 더 오르는 끝물."
    elif "주의" in phase and "표본" not in phase:
        base = f"A→E 상승 계단 {ascents}칸 · 유동성이 하급지로 밀리기 시작."
    elif phase.startswith("상급지"):
        base = "비싼 급지(A·B)가 더 오르는 정상 상승 국면 — 유동성 풍부, 매수 우호."
    else:
        base = "급지별 상승률이 섞인 확산 국면 — 중반."
    if ladder_txt and ("끝물" in phase and "표본" not in phase):
        return f"{base} 계단: {ladder_txt}.{trust}"
    if "끝물" in phase and "표본" not in phase:
        return base + trust
    return base
