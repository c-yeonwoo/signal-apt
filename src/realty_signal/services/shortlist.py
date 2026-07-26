"""단지 숏리스트 — 매수력 × 라이프스타일 적합도로 후보를 3곳까지 좁힌다.

시군구 해상도의 시그널·저평가·입지를 단지 해상도의 '가볼 곳'으로 내리는 다리.
탈락한 후보는 사유별로 세어 함께 돌려준다 — 왜 나머지를 안 보는지가 보여야
선택 과부하가 실제로 줄어든다.
"""

from __future__ import annotations

import json

from realty_signal import buying_power, db, store

MAX_REGIONS = 5          # 국토부 실거래 호출 비용 상한
MAX_PER_REGION = 2       # 한 동네가 상위를 독식하지 않도록
MAX_COMMUTE_MIN = 70     # 이보다 멀면 후보에서 제외
COMMUTE_BEST_MIN = 20    # 이하면 통근 만점
COMMUTE_TTL = 30 * 86400
FETCH_WORKERS = 5        # 지역별 실거래 조회 병렬도

WEIGHTS = {"통근": 0.30, "예산": 0.20, "시그널": 0.18, "저평가": 0.17, "급지": 0.15}
SIGNAL_SCORE = {"STRONG_BUY": 100, "BUY": 80, "WATCH": 55, "NEUTRAL": 40, "SELL_RISK": 10}
BUDGET_SWEET_LOW = 0.80  # 예산의 80~100% 구간이 만점(너무 싸면 급지를 낮춘 것)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _commute_score(minutes: int | None) -> float | None:
    if minutes is None:
        return None
    return _clamp(100 - max(0, minutes - COMMUTE_BEST_MIN) * 2)


def _budget_score(ratio: float) -> float:
    if ratio >= BUDGET_SWEET_LOW:
        return 100.0
    return _clamp(100 - (BUDGET_SWEET_LOW - ratio) * 250)


def _grade_score(top_pct) -> float:
    """구 내 평단가 상위 백분위 → 점수. 상위 0% (최상급지) = 100."""
    if top_pct is None:
        return 50.0
    return _clamp(100 - float(top_pct))


def _locality_map() -> dict:
    df = store.load_localities()
    if df.empty:
        return {}
    return {r["region"]: r
            for r in json.loads(df.to_json(orient="records", force_ascii=False))}


def _region_commute(region: str, work: tuple[float, float] | None) -> dict | None:
    """지역 중심 → 직장 대중교통 소요(분). 30일 캐시. 키 없으면 None."""
    if not work:
        return None
    from realty_signal import api as app_api
    from realty_signal.ingest import locality

    wlat, wlng = work
    ckey = f"shortlist_commute:{region}:{round(wlat, 3)},{round(wlng, 3)}"
    cached = db.kv_get(ckey, max_age=COMMUTE_TTL)
    if cached is not None:
        return cached or None
    c = app_api._region_centroid(region, app_api._code_of(region))
    if not c:
        return None
    try:
        r = locality.transit_between(c[1], c[0], wlng, wlat)  # sx=경도, sy=위도
    except Exception:  # noqa: BLE001
        return None
    if r and r.get("min"):
        db.kv_set(ckey, r)
        return r
    return None


def _fetch_grades(regions: list[str]) -> dict:
    """지역별 단지 급지 랭킹을 병렬로. 콜드 캐시에서 지역당 수 초 걸린다."""
    from concurrent.futures import ThreadPoolExecutor

    from realty_signal import api as app_api

    if not regions:
        return {}
    with ThreadPoolExecutor(max_workers=min(FETCH_WORKERS, len(regions))) as ex:
        return dict(zip(regions, ex.map(app_api._region_grades, regions)))


def _diversify(scored: list[dict], limit: int) -> list[dict]:
    """한 동네가 상위를 독식하지 않게 지역당 MAX_PER_REGION 까지만."""
    out, used = [], {}
    for c in scored:
        if used.get(c["region"], 0) >= MAX_PER_REGION:
            continue
        used[c["region"]] = used.get(c["region"], 0) + 1
        out.append(c)
        if len(out) >= limit:
            break
    return out


def _candidate_regions(favs: list[str], signal_map: dict, loc: dict) -> list[str]:
    """★ 관심지역 우선 → BUY+ 를 시그널 강도, 그 다음 저평가도 순으로 채운다."""
    out = [r for r in favs if r in signal_map]
    buyplus = [r for r, s in signal_map.items()
               if s in ("STRONG_BUY", "BUY") and r not in out]
    buyplus.sort(key=lambda r: (SIGNAL_SCORE.get(signal_map.get(r, ""), 0),
                                loc.get(r, {}).get("저평가도") or 0), reverse=True)
    out.extend(buyplus)
    return out[:MAX_REGIONS]


def _weights(has_work: bool, override: dict | None = None) -> dict:
    w = dict(WEIGHTS)
    if override:
        w.update({k: float(v) for k, v in override.items() if k in w and v is not None})
    if not has_work:
        w.pop("통근", None)
    total = sum(w.values()) or 1.0
    return {k: v / total for k, v in w.items()}


def build(profile: dict, budget: float, *, limit: int = 3,
          weight_override: dict | None = None) -> dict:
    """예산 안에서 라이프스타일 적합도 상위 단지 + 탈락 사유 집계."""
    from realty_signal import api as app_api

    signal_map = app_api._signal_map()
    loc = _locality_map()
    uid_favs = profile.get("_favs") or []
    regions = _candidate_regions(uid_favs, signal_map, loc)
    pyeong = buying_power.pyeong_of(profile.get("관심평수"))
    work = None
    if profile.get("직장lat") and profile.get("직장lng"):
        work = (float(profile["직장lat"]), float(profile["직장lng"]))
    w = _weights(bool(work), weight_override)

    params = buying_power.params_from_profile(profile)
    rejected = {"예산초과": 0, "통근초과": 0, "시그널": 0, "데이터없음": 0}
    checked = 0
    scored: list[dict] = []
    grade_map = _fetch_grades(regions)

    for region in regions:
        grades = grade_map.get(region) or []
        if not grades:
            rejected["데이터없음"] += 1
            continue
        sig = signal_map.get(region, "")
        commute = _region_commute(region, work)
        cmin = commute.get("min") if commute else None
        if cmin is not None and cmin > MAX_COMMUTE_MIN:
            rejected["통근초과"] += len(grades)
            checked += len(grades)
            continue
        if sig == "SELL_RISK":
            rejected["시그널"] += len(grades)
            checked += len(grades)
            continue
        lr = loc.get(region, {})
        uv = lr.get("저평가도") or 0
        parts = {
            "시그널": float(SIGNAL_SCORE.get(sig, 45)),
            "저평가": _clamp(50 + uv * 2.5),
        }
        cs = _commute_score(cmin)
        if "통근" in w:
            parts["통근"] = cs if cs is not None else 50.0
        for g in grades:
            checked += 1
            ppy = g.get("평단가")
            if not ppy:
                continue
            est = round(ppy * pyeong)
            if est > budget:
                rejected["예산초과"] += 1
                continue
            p = dict(parts)
            p["예산"] = _budget_score(est / budget if budget else 0)
            p["급지"] = _grade_score(g.get("상위"))
            scored.append({
                "단지": g["단지"], "region": region, "시그널": sig,
                "평단가": ppy, "예상가": est, "급지": g.get("급지"),
                "상위": g.get("상위"), "중앙대비": g.get("중앙대비"),
                "저평가도": uv, "입지점수": lr.get("입지점수"),
                "통근": commute, "점수": round(sum(w[k] * p[k] for k in w if k in p), 1),
                "분해": {k: round(v) for k, v in p.items()},
            })

    # 점수 동률이면 예산을 더 쓴 쪽(= 같은 조건에서 더 좋은 물건)을 앞에
    scored.sort(key=lambda c: (c["점수"], c["예상가"]), reverse=True)
    top = _diversify(scored, limit)
    for c in top:
        c["자금"] = buying_power.for_price(c["예상가"], params)
        c["근거"] = _reason(c)

    return {
        "ready": bool(top),
        "budget": round(budget),
        "pyeong": pyeong,
        "candidates": top,
        "검토": checked,
        "통과": len(scored),
        "탈락": rejected,
        "지역": regions,
        "가중치": {k: round(v, 2) for k, v in w.items()},
        "직장": bool(work),
    }


def _reason(c: dict) -> str:
    """왜 이 단지인지 한 문장 — 금액은 카드가 따로 보여주므로 판단 근거만."""
    bits = [f"{c['region']}"]
    if c.get("급지"):
        bits.append(f"구 내 급지{c['급지']}(상위 {c.get('상위')}%)")
    sig_label = {"STRONG_BUY": "적극매수", "BUY": "매수", "WATCH": "관망",
                 "NEUTRAL": "중립"}.get(c.get("시그널") or "", "")
    if sig_label:
        bits.append(f"지역 {sig_label}")
    if (c.get("저평가도") or 0) > 0:
        bits.append(f"저평가 {c['저평가도']}")
    if c.get("통근") and c["통근"].get("min"):
        bits.append(f"직장 {c['통근']['min']}분")
    return " · ".join(bits)
