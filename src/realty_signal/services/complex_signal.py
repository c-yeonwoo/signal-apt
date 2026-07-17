"""단지 시그널 · 주력 평형 지표 (api.py 에서 분리)."""

from __future__ import annotations

from functools import lru_cache

from realty_signal import store
from realty_signal.services import market_data as md

_CX_SIG_GRADE = [(75, "STRONG_BUY"), (62, "BUY"), (50, "WATCH"), (40, "NEUTRAL"), (0, "SELL_RISK")]


@lru_cache(maxsize=1)
def uv_map() -> dict:
    """시군구 → 저평가도 (localities 캐시)."""
    import json
    df = store.load_localities()
    if df.empty:
        return {}
    return {r["region"]: r.get("저평가도")
            for r in json.loads(df.to_json(orient="records", force_ascii=False))}


def clear_uv_cache() -> None:
    uv_map.cache_clear()


def main_flat_metrics(data: dict) -> dict:
    """주력 평형(매매건수 max)의 전세가율·갭 + 실거래 spark."""
    plist = data.get("평형별") or []
    main = max(plist, key=lambda p: p.get("매매건수", 0) or 0) if plist else {}
    spark = [x.get("평단가") for x in (data.get("매매추이") or []) if x.get("평단가") is not None][-12:]
    return {
        "주력평형": main.get("평형"),
        "전세가율": main.get("전세가율"),
        "갭": main.get("갭"),
        "최근매매": main.get("최근매매"),
        "최근전세": main.get("최근전세"),
        "spark": spark,
    }


def complex_signal(region: str, data: dict, signal: str | None,
                   gongsi_ratio: float | None = None) -> dict:
    """단지 시그널 — 사이클·지역·단지·가격 정규화 가중합(참고용)."""
    def clamp(v, lo=0.0, hi=100.0):
        return max(lo, min(hi, v))

    regime = md.regime()
    rg = (regime.get("regions") or {}).get(region) or {}
    if rg.get("막차"):
        cyc = 15.0
    elif regime.get("endgame"):
        cyc = 25.0
    else:
        cyc = 68.0
    reg = {"STRONG_BUY": 90, "BUY": 72, "WATCH": 52, "NEUTRAL": 42, "SELL_RISK": 18}.get(signal or "", 45.0)
    uv = uv_map().get(region)
    if uv is not None:
        reg = clamp(reg + uv * 0.4)
    comp = 50.0
    tr = data.get("추세pct")
    if tr is not None:
        comp += clamp(tr * 1.2, -14, 14)
    plist = data.get("평형별") or []
    main = max(plist, key=lambda p: p.get("매매건수", 0) or 0) if plist else {}
    has_jeonse = main.get("전세가율") is not None
    if has_jeonse:
        comp += (main["전세가율"] - 60) * 0.55
    if data.get("총거래"):
        comp += min(15, data["총거래"] / 12)
    comp = clamp(comp)
    price = 50.0
    if gongsi_ratio:
        price = clamp(50 + (1.5 - gongsi_ratio) * 90)
    else:
        ts = [x.get("평단가") for x in (data.get("매매추이") or []) if x.get("평단가")]
        if len(ts) >= 3 and sum(ts):
            avg = sum(ts) / len(ts)
            price = clamp(50 + (avg - ts[-1]) / avg * 100 * 1.5)
    w_reg, w_comp = (0.40, 0.25) if has_jeonse else (0.48, 0.17)
    total = round(cyc * 0.15 + reg * w_reg + comp * w_comp + price * 0.20)
    grade = next(g for th, g in _CX_SIG_GRADE if total >= th)
    out = {"등급": grade, "점수": total,
           "분해": {"사이클": round(cyc), "지역": round(reg), "단지": round(comp), "가격": round(price)}}
    if not has_jeonse:
        out["근거부족"] = ["전세가율"]
        out["주의"] = "전세가율 없음 — 단지 가중 축소, 지역시그널 비중↑"
    return out
