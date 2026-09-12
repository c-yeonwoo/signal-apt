"""임계 근접 워치 — 다음 주에 뒤집힐 수 있는 지역.

**예측이 아니라 거리다.** "오를 것"이라고 말하지 않고 "임계까지 0.7 남았다"고만 말한다.
확신형 예측은 이 제품의 반가치다(DESIGN.md).

왜 필요한가 (2026-09-06 조사):
    등급은 계단 함수라 시장이 연속적으로 식는 동안 화면은 몇 달 정지했다가
    어느 주에 한꺼번에 뒤집힌다. 실측 — 강남권 매수우위지수가 71.46 → 69.29 로
    2.17 움직이자 **11개 구가 같은 주에 동시 강등**됐고, 지금은 임계에서 0.71 아래다.
    그 0.71 을 보여주면 "몇 달 아무 일 없다"가 "다음 주가 궁금하다"로 바뀐다.

거리를 **주당 평균 변동폭으로 나눠** 환산한다. 지표마다 스케일이 달라(전세수급 0~200,
모멘텀 %) 원시 거리만으로는 서로 비교할 수 없기 때문이다.
"""

from __future__ import annotations

import pandas as pd

from realty_signal.signals.engine import (
    _SEOUL_GANGBUK,
    _SEOUL_GANGNAM,
    SignalConfig,
    _classify,
    _jeonse_state,
)

CACHE_VER = 2           # 응답 스키마 버전 — 바꾸면 옛 캐시가 자동 무효화된다
VOL_WEEKS = 8          # 주간 변동폭을 재는 창
NEAR_WEEKS = 2.0       # 이 정도 움직임이면 닿는다 → '근접'으로 본다
EPS = 0.01             # 임계를 살짝 넘겨 재분류할 때 쓰는 폭

_KO = {"STRONG_BUY": "강력매수", "BUY": "매수", "WATCH": "관망",
       "NEUTRAL": "중립", "SELL_RISK": "매도주의"}

# (표시명, KB metric, SignalConfig 속성, 넘었을 때 붙는 조건 이름)
_WATCHED = [
    ("매수우위지수", "buyer_superiority", "buyeridx_strong", "매수 강세"),
    ("전세수급", "jeonse_supply", "jeonse_crunch", "전세난"),
]


def _parent(kb, region: str) -> str | None:
    code = (kb.codes or {}).get(region, "") or ""
    if region in _SEOUL_GANGNAM:
        return "강남11개구"
    if region in _SEOUL_GANGBUK:
        return "강북14개구"
    return {"11": "서울", "41": "경기", "28": "인천", "46": "전남"}.get(code[:2])


def _series_map(kb, metrics: list[str], weeks: int) -> dict:
    """최근 `weeks` 주치를 (지역, 지표) → Series 로 한 번에 뽑는다.

    지역마다 `kb.series()` 를 부르면 boolean mask + set_index 가 수백 번 돈다
    (117지역 × 지표 = 24초 실측). groupby 한 번으로 줄인다.
    """
    long = kb.long
    dates = sorted(long["date"].unique())[-weeks:]
    sub = long[long["metric"].isin(metrics) & long["date"].isin(dates)]
    return {
        k: g.set_index("date")["value"].dropna().sort_index()
        for k, g in sub.groupby(["region", "metric"], sort=False)
    }


def _pick(smap: dict, kb, region: str, metric: str) -> tuple[pd.Series, str | None]:
    """(시계열, 상속받은 부모). 원본이 있으면 부모는 None."""
    s = smap.get((region, metric))
    if s is not None and not s.empty:
        return s, None
    p = _parent(kb, region)
    ps = smap.get((p, metric)) if p else None
    return (ps if ps is not None else pd.Series(dtype=float)), p


def _weekly_move(s: pd.Series) -> float | None:
    """최근 주간 변동폭의 평균(절대값). 0 이면 거리 환산이 불가능하다."""
    d = s.tail(VOL_WEEKS + 1).diff().dropna().abs()
    if d.empty:
        return None
    m = float(d.mean())
    return m if m > 1e-9 else None


def compute(kb, config: SignalConfig | None = None, *, favs: set[str] | None = None) -> dict:
    """임계에 붙어 있는 (지역, 지표) 목록. 가까운 순."""
    c = config or SignalConfig()
    favs = favs or set()
    regions = list(kb.latest().index)
    need = ["jeonse_supply", "buyer_superiority", "buyer_demand", "sale_change"]
    smap = _series_map(kb, need, VOL_WEEKS + c.momentum_weeks + 2)

    # 상속으로 값을 공유하는 지역 수 — 하나가 뒤집히면 같이 뒤집힌다
    shared: dict[tuple[str, str], int] = {}
    owner: dict[str, str | None] = {}
    for r in regions:
        for _, metric, _, _ in _WATCHED:
            _, p = _pick(smap, kb, r, metric)
            key = (metric, p or r)
            shared[key] = shared.get(key, 0) + 1
            owner[f"{r}|{metric}"] = p

    items = []
    for r in regions:
        sale = smap.get((r, "sale_change"))
        if sale is None or sale.empty:
            continue
        js, _ = _pick(smap, kb, r, "jeonse_supply")
        bs, _ = _pick(smap, kb, r, "buyer_superiority")
        bd, _ = _pick(smap, kb, r, "buyer_demand")
        if js.empty or bs.empty:
            continue
        jv, bv, dv = float(js.iloc[-1]), float(bs.iloc[-1]), (
            float(bd.iloc[-1]) if not bd.empty else float("nan"))
        mom = float(sale.tail(c.momentum_weeks).mean())
        mom_lbl = ("상승" if mom >= c.momentum_up
                   else "하락" if mom <= c.momentum_down else "보합")
        now, _ = _classify(jv, bv, dv, _jeonse_state(jv, c), mom_lbl, c)

        for label, metric, attr, cond_name in _WATCHED:
            s = js if metric == "jeonse_supply" else bs
            val = jv if metric == "jeonse_supply" else bv
            thr = float(getattr(c, attr))
            gap = val - thr
            move = _weekly_move(s)
            if move is None:
                continue
            weeks = abs(gap) / move
            if weeks > NEAR_WEEKS:
                continue
            # 임계를 살짝 넘겼을 때의 등급 — 정본 함수로 다시 분류한다(추측 금지)
            nudged = thr + EPS if gap < 0 else thr - EPS
            if metric == "jeonse_supply":
                after, _ = _classify(nudged, bv, dv, _jeonse_state(nudged, c), mom_lbl, c)
            else:
                after, _ = _classify(jv, nudged, dv, _jeonse_state(jv, c), mom_lbl, c)
            if after == now:
                continue                      # 넘어도 등급이 안 바뀌면 알릴 가치가 없다
            items.append({
                "region": r, "mine": r in favs,
                "metric": label, "condition": cond_name,
                "value": round(val, 2), "threshold": thr,
                "need": round(abs(gap), 2),
                "direction": "up" if gap < 0 else "down",
                "weekly_move": round(move, 2),
                "weeks_away": round(weeks, 1),
                "now": now, "now_ko": _KO.get(now, now),
                "if_crossed": after, "if_crossed_ko": _KO.get(after, after),
                "up": after in ("STRONG_BUY", "BUY") and now not in ("STRONG_BUY", "BUY")
                      or (after == "STRONG_BUY" and now == "BUY"),
                "inherited": owner.get(f"{r}|{metric}"),
                "shared_with": shared.get((metric, owner.get(f"{r}|{metric}") or r), 1),
            })

    # 광역 상속 지표는 **발표 단위로 묶는다** — `weekly._movers` 와 같은 규칙.
    # 구별로 늘어놓으면 강남권 12개 구가 같은 69.29 로 12줄을 차지한다(실측).
    grouped: dict[tuple, dict] = {}
    for x in items:
        scope = x["inherited"] or x["region"]
        key = (scope, x["metric"])
        hit = grouped.get(key)
        if hit:
            hit["regions"] += 1
            hit["members"].append(x["region"])
            hit["mine"] = hit["mine"] or x["mine"]
            if x["mine"]:
                hit["my_regions"].append(x["region"])
            continue
        grouped[key] = {**x, "scope": scope,
                        # shared 는 묶은 **뒤에** 정한다 — 발표 단위 자신(예: 강남11개구)은
                        # inherited 가 None 이지만 12곳을 대표한다
                        "shared": False,
                        "regions": 1,
                        # 구성원을 남긴다 — 캐시는 사용자와 무관하므로 ★ 는 나중에 입힌다
                        "members": [x["region"]],
                        "my_regions": [x["region"]] if x["mine"] else []}

    out_items = sorted(grouped.values(), key=lambda x: (not x["mine"], x["weeks_away"]))
    for x in out_items:
        x["shared"] = x["regions"] > 1      # 한 줄이 여러 지역을 대표하는가
        x.pop("inherited", None)
        x.pop("shared_with", None)
    return {
        "as_of": str(kb.last_date.date()),
        "items": out_items,
        "mine": [x for x in out_items if x["mine"]],
        "count": len(out_items),
        # 이 카드가 무엇이 아닌지 화면이 말해야 한다
        "note": "방향은 예측하지 않습니다. 임계까지 남은 거리만 표시합니다.",
    }
