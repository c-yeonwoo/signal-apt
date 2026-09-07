"""관심단지 단위 변화 — 등급이 아니라 **내가 찍어 둔 단지**가 움직였을 때.

왜 필요한가 (2026-09-06 조사):
    은마아파트·상계주공9단지·진산마을성원상떼빌이 ★ 로 등록돼 있는데, 앱이 알려 주는 건
    **지역 등급**뿐이었다. 강남구 등급이 그대로인 주에도 은마 실거래는 찍힌다.
    사용자가 실제로 기다리는 소식은 지역이 아니라 그 단지다.

무엇을 '변화' 로 보나 — 국토부 실거래(`ingest/complex.py`) 기준:
    · 새 실거래     — 월별 건수가 늘었다
    · 평단가 변화   — 단지 최근 평단가
    · 주력평형 거래가 변화 — 실제로 계약된 금액(억 단위로 읽힌다)
    · 갭 변화       — 최근매매 − 최근전세 = 필요 자기자금
    · 예산 진입/이탈 — 주력평형 거래가가 내 매수력을 넘나들었다

**정직하게 다루어야 할 것 세 가지:**

1. **총거래로 비교하지 않는다.** 24개월 롤링 윈도라 새 거래가 생겨도 오래된 달이
   빠지면서 총거래가 *줄 수 있다*. 그래서 **월별(`ym`) 건수**로 비교한다.
2. **스냅샷이 기억하지 못하는 달은 비교하지 않는다.** 보관은 최근 `KEEP_MONTHS` 개월뿐이라,
   그보다 오래된 달의 건수 차이는 '새 거래' 가 아니라 '모른다' 다.
3. **신고 지연을 밝힌다.** 계약 후 30일 내 신고라 최근 달 건수는 계속 늘어난다 —
   "지난달 거래가 이번 주에 새로 보이는 것" 도 새 소식이지만, 이번 주에 계약된 건 아니다.

스냅샷 규칙(복귀 브리핑·예산 워치와 동일):
    · 전용 키(`complex_snap:{uid}`) — 다른 기능과 공유하면 서로 지운다
    · 기준점은 **보여준 뒤에만** 옮긴다(`mark_seen`)
"""

from __future__ import annotations

import time

from realty_signal import db

KV_PREFIX = "complex_snap:"
KEEP_MONTHS = 3        # 스냅샷에 담는 월 수 — 이보다 오래된 달은 비교하지 않는다
PPY_MIN_PCT = 1.0      # 평단가: 이 % 미만은 잡음
PPY_MIN_ABS = 30       # 평단가: 만원/평
AMT_MIN = 1000         # 거래가: 만원(=1천만)
GAP_MIN = 1000         # 갭: 만원
MAX_SHOW = 12

_LAG_NOTE = ("실거래는 계약 후 30일 내 신고라 최근 달 건수는 계속 늘어납니다 — "
             "새로 보이는 거래가 이번 주에 계약된 것은 아닙니다.")


def _main_flat(data: dict) -> dict:
    """주력 평형 = 24개월 매매건수가 가장 많은 평형."""
    plist = [p for p in (data.get("평형별") or []) if p.get("평형")]
    if not plist:
        return {}
    return max(plist, key=lambda p: p.get("매매건수") or 0)


def snapshot_of(data: dict) -> dict:
    """비교에 쓰는 필드만 뽑는다. 원본 전체를 저장하면 kv 가 불필요하게 커진다."""
    if not data or data.get("지원안함") or data.get("_unavailable"):
        return {}
    # 실거래가 하나도 없는 응답과 '조회 자체가 안 되는' 응답을 구분한다 —
    # 후자를 빈 스냅샷으로 통과시키면 매주 "변화 없음" 이라고 거짓말한다
    if not (data.get("매매추이") or data.get("평형별") or data.get("거래없음")):
        return {}
    tail = (data.get("매매추이") or [])[-KEEP_MONTHS:]
    mf = _main_flat(data)
    return {
        "months": {str(r["ym"]): int(r.get("건수") or 0) for r in tail if r.get("ym")},
        "ppy": data.get("최근평단가"),
        "flat": mf.get("평형"),
        "amt": mf.get("최근매매"),
        "jeonse": mf.get("최근전세"),
        "gap": mf.get("갭"),
        "ratio": mf.get("전세가율"),
        "none": bool(data.get("거래없음")),
    }


def _new_trades(prev: dict, cur: dict) -> tuple[int, list[str]]:
    """새로 보이는 실거래 건수 + 그 달 목록.

    스냅샷이 기억하는 범위(`prev` 의 최소 ym) 밖은 세지 않는다 — 모르는 것을
    '늘었다' 로 바꾸지 않기 위해서다.
    """
    pm: dict = prev.get("months") or {}
    cm: dict = cur.get("months") or {}
    if not cm:
        return 0, []
    floor = min(pm) if pm else None
    total, months = 0, []
    for ym, n in sorted(cm.items()):
        if floor is not None and ym < floor:
            continue                    # 스냅샷이 기억하지 못하는 달 — 비교 불가
        delta = n - int(pm.get(ym, 0))
        if delta > 0:
            total += delta
            months.append(ym)
    return total, months


def _num_change(prev, cur, *, min_abs: float, min_pct: float | None = None):
    if not isinstance(prev, (int, float)) or not isinstance(cur, (int, float)):
        return None
    d = cur - prev
    if abs(d) < min_abs:
        return None
    if min_pct is not None and prev and abs(d / prev * 100) < min_pct:
        return None
    return round(d)


def diff_one(prev: dict, cur: dict, *, budget: float | None = None) -> list[dict]:
    """단지 하나의 변화 목록. 비교 불가면 빈 목록(가짜 변화를 만들지 않는다)."""
    if not prev or not cur:
        return []
    out: list[dict] = []

    n, months = _new_trades(prev, cur)
    if n:
        out.append({"kind": "new_trade", "건수": n, "월": months,
                    "말": f"새 실거래 {n}건", "note": _LAG_NOTE})

    d = _num_change(prev.get("ppy"), cur.get("ppy"),
                    min_abs=PPY_MIN_ABS, min_pct=PPY_MIN_PCT)
    if d is not None:
        pct = round(d / prev["ppy"] * 100, 1) if prev.get("ppy") else None
        out.append({"kind": "ppy", "delta": d, "pct": pct,
                    "before": prev.get("ppy"), "after": cur.get("ppy"),
                    "말": f"평단가 {abs(d):,}만원 {'상승' if d > 0 else '하락'}"})

    # 주력평형이 바뀌면 금액끼리 비교하는 의미가 없다 — 평형 교체 사실만 알린다
    if prev.get("flat") and cur.get("flat") and prev["flat"] != cur["flat"]:
        out.append({"kind": "flat_shift", "before": prev["flat"], "after": cur["flat"],
                    "말": f"주력 평형 {prev['flat']}평 → {cur['flat']}평"})
    else:
        d = _num_change(prev.get("amt"), cur.get("amt"), min_abs=AMT_MIN)
        if d is not None:
            out.append({"kind": "amt", "delta": d, "flat": cur.get("flat"),
                        "before": prev.get("amt"), "after": cur.get("amt"),
                        "말": f"{cur.get('flat')}평 거래가 {abs(d) / 10000:.1f}억 "
                              f"{'상승' if d > 0 else '하락'}"})
        d = _num_change(prev.get("gap"), cur.get("gap"), min_abs=GAP_MIN)
        if d is not None:
            out.append({"kind": "gap", "delta": d, "flat": cur.get("flat"),
                        "before": prev.get("gap"), "after": cur.get("gap"),
                        "말": f"필요 자기자금(갭) {abs(d) / 10000:.1f}억 "
                              f"{'증가' if d > 0 else '감소'}"})

    if budget and budget > 0:
        pa, ca = prev.get("amt"), cur.get("amt")
        if isinstance(pa, (int, float)) and isinstance(ca, (int, float)):
            if pa > budget >= ca:
                out.append({"kind": "budget_in", "after": ca,
                            "말": "내 매수력 안으로 들어왔습니다"})
            elif ca > budget >= pa:
                out.append({"kind": "budget_out", "after": ca,
                            "말": "내 매수력을 넘어섰습니다"})
    return out


def cache_loader():
    """실거래 **캐시만** 읽는 loader. 못 읽으면 왜 못 읽는지 같이 돌려준다.

    네트워크를 부르지 않는다 — 홈 카드가 관심단지 수만큼 국토부 API 를 때리면
    홈이 느려진다. 캐시를 채우는 건 주간 워밍과 단지 상세 조회의 몫이다.
    """
    from realty_signal import api as app_api

    def _load(region: str, name: str):
        code = app_api._code_of(region) or ""
        if not (len(code) >= 5 and code[:5].isdigit()):
            return {"_unavailable": f"'{region}' 의 지역코드를 찾지 못했습니다"}, None
        if code[2:5] == "000":
            # 11000(서울)·41000(경기) 같은 시·도 코드로는 국토부 실거래를 조회할 수 없다
            return {"_unavailable": f"'{region}' 처럼 시·도 단위로 등록된 단지는 "
                                    "실거래를 특정할 수 없습니다 — 시군구로 다시 등록해 주세요"}, None
        k = f"complex:{code[:5]}:{name}"
        data = db.kv_get(k)
        if data is None:
            return {"_unavailable": "실거래를 아직 수집하지 않았습니다 — "
                                    "단지 상세를 한 번 열면 수집됩니다"}, None
        return data, db.kv_ts(k)

    return _load


def favorites_of(uid: int) -> list[tuple[str, str]]:
    """★ 관심단지 → [(지역, 단지명)]. key 형식은 `region|name`."""
    return [tuple(f["key"].split("|", 1)) for f in db.fav_list(uid)
            if f["kind"] == "complex" and "|" in (f["key"] or "")]


def scan(favorites: list[tuple[str, str]], loader, prev_all: dict,
         *, budget: float | None = None) -> tuple[list[dict], dict, list[dict]]:
    """kv 를 건드리지 않는 순수 스캔 → (items, snaps, unavailable).

    기준점(`prev_all`)을 호출자가 준다. 그래서 **홈은 '마지막 방문 대비',
    브리핑은 '어제 대비'** 를 각자 자기 스냅샷으로 볼 수 있다 —
    같은 스냅샷을 공유하면 브리핑이 나갈 때마다 홈의 변화가 사라진다.
    """
    items, snaps, unavailable = [], {}, []
    now = time.time()

    for region, name in favorites:
        key = f"{region}|{name}"
        data, ts = loader(region, name)
        snap = snapshot_of(data or {})
        if not snap:
            # '왜 없는지' 는 loader 가 안다(시도 단위 등록·미수집 등) — 뭉개지 않고 그대로 옮긴다
            unavailable.append({
                "key": key, "단지명": name, "지역": region,
                "reason": (data or {}).get("_unavailable")
                          or "실거래를 아직 수집하지 않았습니다"})
            continue
        snaps[key] = snap
        age = round((now - ts) / 86400, 1) if ts else None
        if snap.get("none"):
            items.append({"key": key, "단지명": name, "지역": region,
                          "quiet": True, "data_days": age,
                          "말": "최근 24개월 실거래가 없습니다"})
            continue
        changes = diff_one(prev_all.get(key) or {}, snap, budget=budget)
        items.append({
            "key": key, "단지명": name, "지역": region,
            "평단가": snap.get("ppy"), "평형": snap.get("flat"),
            "거래가": snap.get("amt"), "전세가율": snap.get("ratio"),
            "data_days": age,
            "quiet": not changes,
            "first_run": not prev_all.get(key),
            "changes": changes[:MAX_SHOW],
        })

    return items, snaps, unavailable


def compute(uid: int, favorites: list[tuple[str, str]], loader,
            *, budget: float | None = None) -> dict:
    """★ 관심단지들의 변화 (홈 카드용 — 기준점은 `KV_PREFIX` 스냅샷).

    `loader(region, name)` 는 `(data, ts)` 를 돌려준다 — 캐시된 실거래와 그 수집시각.
    네트워크를 부르지 않는 loader 를 넘기면 이 함수는 절대 느려지지 않는다.
    """
    if not favorites:
        return {"ready": False, "reason": "no_favorites"}
    prev_all: dict = (db.kv_get(KV_PREFIX + str(uid)) or {}).get("items") or {}
    items, snaps, unavailable = scan(favorites, loader, prev_all, budget=budget)

    moved = [it for it in items if it.get("changes")]
    moved.sort(key=lambda it: -len(it["changes"]))
    # 비교가 가능한 단지만 센다 — 거래없음 단지를 섞으면 '첫 기록' 판정이 틀린다
    comparable = [it for it in items if not it.get("말")]
    firsts = [it for it in comparable if it.get("first_run")]
    return {
        "ready": True,
        "total": len(favorites),
        "moved": moved,
        "moved_total": len(moved),
        "items": items,
        "unavailable": unavailable,
        "quiet": not moved,
        # 0 에 이유를 붙인다 — '변화 없음' 과 '비교 대상 없음' 은 다르다
        "quiet_reason": (
            None if moved else
            "관심단지의 실거래 데이터가 없습니다" if not comparable else
            "이번이 첫 기록입니다 — 다음 방문부터 변화를 비교합니다"
            if len(firsts) == len(comparable) else
            "관심단지에 새 실거래·가격 변화가 없습니다"),
        "lag_note": _LAG_NOTE,
        "_snaps": snaps,
    }


def mark_seen(uid: int, snaps: dict) -> None:
    """기준점을 앞으로 당긴다. **보여준 뒤에만** 부른다."""
    if not snaps:
        return
    prev = (db.kv_get(KV_PREFIX + str(uid)) or {}).get("items") or {}
    db.kv_set(KV_PREFIX + str(uid), {"items": {**prev, **snaps}, "ts": time.time()})
