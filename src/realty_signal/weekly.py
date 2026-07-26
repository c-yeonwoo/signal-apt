"""이번 주 변화 — 지난주 데이터로 시그널을 다시 돌려 직접 비교한다.

`signal_changes` KV 로그를 쓰지 않는 이유:

`signal watch`(CLI, 매주 토 09:00)와 서버 `_do_refresh` 가 **둘 다** 스냅샷을 앞으로
당긴다. 그런데 변화 로그를 쓰는 쪽은 서버뿐이라, CLI 가 먼저 돌면 서버는 prev == cur
을 보고 "변화 없음"을 기록한다. 실제로 2026-07-13·07-20 두 주의 변화가 통째로
비어 있었다(마지막 로그가 07-06). 로그는 조용히 0이 됐고 화면은 그걸 평온으로 읽었다.

래치·순서에 의존하지 않으려면 **데이터를 다시 재면 된다**. KB 주간 시계열은 원본에
1163주가 통째로 들어 있으므로, 마지막 주를 잘라낸 KBWeekly 로 엔진을 한 번 더 돌리면
"지난주에 봤다면 뭐였을까"가 그대로 나온다. 스냅샷이 없어도, 순서가 꼬여도, 서버가
꺼져 있었어도 같은 답이 나온다.

주의: supply·macro·regime 은 시점별 이력이 없어 양쪽에 같은 값이 들어간다. 따라서
이 diff 는 **KB 주간 갱신분만 분리해서** 보여준다 — 그게 이 화면이 답하려는 질문이다.
"""

from __future__ import annotations

import logging

from realty_signal import db, store
from realty_signal.signals.engine import SignalConfig, evaluate

log = logging.getLogger("realty_signal.weekly")

_RANK = {"SELL_RISK": 0, "NEUTRAL": 1, "WATCH": 2, "BUY": 3, "STRONG_BUY": 4}
SIG_KO = {"STRONG_BUY": "적극매수", "BUY": "매수", "WATCH": "관망",
          "NEUTRAL": "중립", "SELL_RISK": "매도주의"}

# 지표 급변으로 칠 최소폭. 주간 노이즈를 변화라고 부르지 않기 위한 문턱.
_MOVER_MIN = {"전세수급": 8.0, "매수우위지수": 6.0, "매수세우위": 4.0, "매매모멘텀": 0.25}
_MOVER_KO = {"전세수급": "전세수급", "매수우위지수": "매수우위", "매수세우위": "매수세",
             "매매모멘텀": "매매 주간증감"}
_MOVER_UNIT = {"매매모멘텀": "%"}
STALE_DAYS = 10          # KB 는 주 1회 — 10일 넘게 안 바뀌면 갱신이 멈춘 것


def _sig_map(df) -> dict[str, str]:
    return dict(zip(df["region"], df["signal"]))


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f      # NaN 제외


def compute(kb=None, supply=None) -> dict:
    """이번 주 KB 갱신으로 바뀐 것. 캐시 없이 매번 계산(느리면 `latest()` 를 쓸 것)."""
    from realty_signal.ingest.kb_weekly import KBWeekly

    kb = kb if kb is not None else store.load()
    supply = supply if supply is not None else store.load_supply()
    dates = sorted(kb.long["date"].unique())
    as_of = str(kb.last_date.date())
    stale = _stale_days(as_of)
    base = {"as_of": as_of, "prev": None, "ready": False, "stale_days": stale,
            "signals": [], "movers": [], "totals": {}}
    if len(dates) < 2:
        # 0 에는 이유를 붙인다 — '조용한 평온'과 '비교 대상 없음'은 다른 상태다
        return {**base, "blocked_reason": "주간 시계열이 2주치 미만이라 비교할 지난주가 없습니다"}

    cur_df = evaluate(kb, SignalConfig(), supply)
    prev_kb = KBWeekly(long=kb.long[kb.long["date"] < dates[-1]], codes=kb.codes)
    prev_df = evaluate(prev_kb, SignalConfig(), supply)
    prev_as_of = str(prev_kb.last_date.date())

    cur, prev = _sig_map(cur_df), _sig_map(prev_df)
    signals = []
    for region, new in cur.items():
        was = prev.get(region)
        if not was or was == new:
            continue
        delta = _RANK.get(new, 1) - _RANK.get(was, 1)
        signals.append({
            "region": region, "from": was, "to": new,
            "from_ko": SIG_KO.get(was, was), "to_ko": SIG_KO.get(new, new),
            "delta": delta, "up": delta > 0,
            "근거": (cur_df.loc[cur_df["region"] == region, "근거"].iloc[0] or "")[:160],
        })
    signals.sort(key=lambda c: (-abs(c["delta"]), c["region"]))

    movers = _movers(cur_df, prev_df)
    return {**base, "prev": prev_as_of, "ready": True, "blocked_reason": None,
            "signals": signals, "movers": movers,
            "totals": {"regions": len(cur), "up": sum(1 for s in signals if s["up"]),
                       "down": sum(1 for s in signals if not s["up"]),
                       "movers": len(movers)}}


# KB 가 광역 단위로만 발표해 시군구가 상속받는 지표. 구별로 늘어놓으면 같은 숫자가 14줄 깔린다.
_INHERITED = {"전세수급", "매수우위지수", "매수세우위"}


def _movers(cur_df, prev_df) -> list[dict]:
    """등급은 그대로여도 지표가 크게 움직인 곳.

    수급·심리는 광역 공통값이라 발표 단위(수급출처)로 묶어 한 줄로 낸다 —
    강북14개구가 같은 −7.4 로 14줄을 차지하면 목록이 아니라 벽이 된다.
    """
    prev_rows = {r["region"]: r for _, r in prev_df.iterrows()}
    seen: dict[tuple, dict] = {}
    for _, row in cur_df.iterrows():
        old = prev_rows.get(row["region"])
        if old is None:
            continue
        src = row.get("수급출처")
        for metric, floor in _MOVER_MIN.items():
            a, b = _num(row.get(metric)), _num(old.get(metric))
            if a is None or b is None or abs(a - b) < floor:
                continue
            scope = (src if (metric in _INHERITED and src) else row["region"])
            key = (scope, metric)
            hit = seen.get(key)
            if hit:
                hit["regions"] += 1
                continue
            seen[key] = {"region": scope, "metric": metric,
                         "label": _MOVER_KO.get(metric, metric),
                         "unit": _MOVER_UNIT.get(metric, ""),
                         "shared": metric in _INHERITED and bool(src),
                         "regions": 1,
                         "from": round(b, 2), "to": round(a, 2), "delta": round(a - b, 2)}
    out = sorted(seen.values(), key=lambda m: -abs(m["delta"]))
    return out


def _stale_days(as_of: str) -> int:
    from datetime import date
    try:
        y, m, d = (int(x) for x in as_of.split("-"))
    except ValueError:
        return 0
    return (date.today() - date(y, m, d)).days


def latest(kb=None, supply=None) -> dict:
    """홈에서 부르는 진입점. as_of 단위로 캐시 — 엔진을 두 번 돌리는 게 몇 초 걸린다."""
    kb = kb if kb is not None else store.load()
    as_of = str(kb.last_date.date())
    key = f"weekly_diff:{as_of}"
    cached = db.kv_get(key)
    if isinstance(cached, dict) and cached.get("as_of") == as_of:
        return {**cached, "stale_days": _stale_days(as_of), "cached": True}
    out = compute(kb, supply)
    db.kv_set(key, out)
    return out


def for_user(regions: set[str], kb=None, supply=None) -> dict:
    """내 ★ 지역·후보 지역을 앞으로 뺀 결과. 나머지는 '그 외'로 남긴다."""
    d = latest(kb, supply)
    mine = [s for s in d["signals"] if s["region"] in regions]
    rest = [s for s in d["signals"] if s["region"] not in regions]
    my_movers = [m for m in d["movers"] if m["region"] in regions]
    return {**d, "mine": mine, "rest": rest, "my_movers": my_movers,
            "watching": sorted(regions)}
