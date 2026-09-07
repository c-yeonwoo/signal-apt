"""내 예산 안에 새로 들어온 매물 — 진짜 굿뉴스.

왜 필요한가 (2026-09-06 조사):
    재방문 유인 중 유일하게 **매수자에게 직접 이득인** 항목이다. 등급 변화는 맥락이지만
    "9.3억 예산 안에 3건이 새로 들어왔다" 는 바로 행동으로 이어진다.

'새로 들어옴' 은 세 가지 다른 사건이다. **뭉개면 거짓이 된다:**
    · 신규 등장   — 지난주엔 없던 매물
    · 가격 인하   — 있었지만 예산을 넘었고, 호가가 내려와 들어왔다
    · 매수력 상향 — 매물은 그대로인데 내 예산이 올라 들어왔다
그래서 원인을 구분해 붙인다.

식별자는 `_build_listings` 가 붙이는 `key` 를 쓴다 — **호가와 무관**해야
가격이 바뀔 때마다 "새 매물" 로 오인하지 않는다.

스냅샷 규칙(복귀 브리핑과 같은 규율):
    · 전용 키(`budget_snap:{uid}`)를 쓴다. 다른 기능과 공유하면 서로 지운다.
    · 기준점은 **보여준 뒤에** 옮긴다.
    · 예산 밖도 1.3배까지 담아 둔다 — 그래야 '가격 인하로 진입' 을 구분할 수 있다.
"""

from __future__ import annotations

from realty_signal import db

KV_PREFIX = "budget_snap:"
KEEP_RATIO = 1.3       # 예산의 이 배수까지 스냅샷에 담는다(진입 원인 판별용)
MAX_SHOW = 8


def _snapshot(rows: list[dict], budget: float) -> dict:
    cap = budget * KEEP_RATIO
    return {str(r["key"]): r.get("총액")
            for r in rows
            if r.get("key") and isinstance(r.get("총액"), (int, float)) and r["총액"] <= cap}


def _brief(r: dict) -> dict:
    return {"key": r["key"], "유형": r.get("유형"), "단지명": r.get("단지명"),
            "지역": r.get("지역"), "시그널": r.get("시그널"), "총액": r.get("총액"),
            "평형": r.get("평형"), "급매갭": r.get("지표값") if r.get("유형") in ("급매", "찐매물") else None}


def compute(uid: int, rows: list[dict], budget: float | None) -> dict:
    """예산 기준 주간 변화. 예산이 없으면 계산하지 않는다(무의미하다)."""
    if not budget or budget <= 0:
        return {"ready": False, "reason": "no_budget"}

    prev = db.kv_get(KV_PREFIX + str(uid)) or {}
    prev_items: dict = prev.get("items") or {}
    prev_budget = prev.get("budget")

    # 가격을 아는 매물만 — 가격 미상(청약 등)을 '예산 내' 로 치지 않는다(N2 와 같은 원칙)
    priced = [r for r in rows
              if r.get("key") and isinstance(r.get("총액"), (int, float)) and r["총액"] > 0]
    now_in = {str(r["key"]): r for r in priced if r["총액"] <= budget}
    now_all = {str(r["key"]): r for r in priced}

    if not prev_items:
        return {"ready": False, "reason": "first_run", "in_budget": len(now_in),
                "budget": budget}

    entered, left, cheaper = [], [], []
    for k, r in now_in.items():
        was = prev_items.get(k)
        if was is None:
            entered.append({**_brief(r), "cause": "new", "cause_ko": "신규 등장"})
        elif prev_budget and was > prev_budget:
            cause = ("price_cut" if r["총액"] < was
                     else "budget_up" if budget > prev_budget else "price_cut")
            entered.append({**_brief(r), "cause": cause,
                            "cause_ko": "가격 인하" if cause == "price_cut" else "매수력 상향",
                            "before": was, "drop": round(was - r["총액"])})
        elif r["총액"] < was:
            cheaper.append({**_brief(r), "before": was, "drop": round(was - r["총액"])})

    for k, was in prev_items.items():
        if prev_budget and was > prev_budget:
            continue                          # 애초에 예산 밖이었다 — 이탈이 아니다
        cur = now_all.get(k)
        if cur is None:
            left.append({"key": k, "총액": was, "cause": "gone", "cause_ko": "사라짐"})
        elif cur["총액"] > budget:
            left.append({**_brief(cur), "cause": "over", "cause_ko": "예산 초과",
                         "before": was})

    entered.sort(key=lambda x: x["총액"])
    cheaper.sort(key=lambda x: -(x.get("drop") or 0))
    return {
        "ready": True, "budget": budget, "prev_budget": prev_budget,
        "in_budget": len(now_in),
        "entered": entered[:MAX_SHOW], "entered_total": len(entered),
        "cheaper": cheaper[:MAX_SHOW], "cheaper_total": len(cheaper),
        "left": left[:MAX_SHOW], "left_total": len(left),
        "quiet": not (entered or cheaper or left),
    }


def mark_seen(uid: int, rows: list[dict], budget: float | None) -> None:
    """기준점을 앞으로 당긴다. **보여준 뒤에만** 부른다."""
    if not budget or budget <= 0:
        return
    db.kv_set(KV_PREFIX + str(uid), {"budget": budget, "items": _snapshot(rows, budget)})
