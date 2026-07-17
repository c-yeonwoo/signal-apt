"""시장 시그널 · 타이밍 · 강도 · 메타 · 시계열."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from realty_signal import db, store
from realty_signal.routes import deps
from realty_signal.services import market_data as md

router = APIRouter(tags=["market"])

_METRIC_LABEL = {
    "jeonse_supply": "전세수급지수",
    "buyer_demand": "매수세우위",
    "buyer_superiority": "매수우위지수",
    "sale_change": "매매증감%",
    "jeonse_change": "전세증감%",
}
_SEOUL_AGG = {"강남11개구", "강북14개구"}


def _region_group(region: str, code: str | None) -> str:
    if region in _SEOUL_AGG or (code and code.startswith("11")):
        return "서울"
    if code and code.startswith("41"):
        return "경기"
    if code and code.startswith("28"):
        return "인천"
    return "지방·광역"


def _file_mtime(p) -> int | None:
    try:
        return int(p.stat().st_mtime)
    except Exception:
        return None


@router.post("/api/refresh")
def refresh(request: Request):
    if err := deps.require_admin(request):
        return err
    from realty_signal import api as app_api
    return app_api._do_refresh()


@router.get("/api/backtest")
def backtest():
    return {**md.backtest(), "data_age_days": round(md.data_age_days() or 0, 1)}


@router.get("/api/meta")
def meta():
    kb = md.kb()
    c = md.signal_config()
    from realty_signal.brain.config_store import active_meta
    cfg_meta = active_meta()
    jeonse_zones = [
        {"from": 0, "to": c.jeonse_oversupply, "label": "공급우위", "color": "#3b82f6",
         "desc": "전세 공급이 수요보다 많음. 전세가 안정·약세."},
        {"from": c.jeonse_oversupply, "to": c.jeonse_tight, "label": "보통", "color": "#64748b",
         "desc": "전세 수급 균형 구간."},
        {"from": c.jeonse_tight, "to": c.jeonse_crunch, "label": "타이트", "color": "#eab308",
         "desc": "전세 매물이 마르기 시작. 전세난 전환 관찰 구간."},
        {"from": c.jeonse_crunch, "to": c.jeonse_spillover, "label": "전세난", "color": "#f97316",
         "desc": "전세 구하기 어려움. 수요가 매매로 넘어올 압력."},
        {"from": c.jeonse_spillover, "to": 200, "label": "매매전이", "color": "#ef4444",
         "desc": "전세난 심화 → 매매가 상승 압력으로 전이되는 구간."},
    ]
    return {
        "regions": kb.regions,
        "metrics": [{"key": k, "label": _METRIC_LABEL.get(k, k)} for k in kb.metrics],
        "last_date": str(kb.last_date.date()),
        "signal_config_version": cfg_meta.get("version", "v1"),
        "zones": {
            "jeonse_supply": jeonse_zones,
            "buyer_demand_buy": c.demand_buy,
            "buyer_idx_strong": c.buyeridx_strong,
        },
    }


@router.get("/api/freshness")
def freshness():
    from realty_signal.auction import AUCTION_FILE
    from realty_signal import api as app_api
    last_date = str(md.kb().last_date.date())
    qs = getattr(app_api, "QUICKSALE_FILE", store.CACHE_DIR / "quicksale.json")
    sources = [
        {"key": "signal", "label": "시장 시그널 (KB 매매·전세·수급)", "asof": last_date,
         "ts": db.kv_ts("last_kb_fetch"), "cycle": "주 1회 자동",
         "note": "KB국민은행 주간 시계열로 전세수급·매수우위·매매모멘텀·국면을 산출. 기준일이 곧 분석 기준입니다."},
        {"key": "trade", "label": "국토부 실거래", "ts": db.kv_max_ts("complex:"),
         "cycle": "조회 시 · 14일 캐시", "note": "단지 조회 시 국토부 실거래를 수집(14일 캐시), 관심단지는 주 1회 자동 프리페치."},
        {"key": "quicksale", "label": "급매 스캔", "ts": _file_mtime(qs),
         "cycle": "관리자 스캔 시", "note": "BUY+ 시그널 지역의 시세 이하 호가를 스캔해 적재."},
        {"key": "auction", "label": "경매 물건", "ts": _file_mtime(AUCTION_FILE),
         "cycle": "관리자 등록·갱신 시", "note": "법원경매 물건과 시세를 관리자가 등록·갱신."},
        {"key": "presale", "label": "청약", "ts": None, "cycle": "실시간",
         "note": "청약홈(applyhome) API를 조회 시점에 실시간 반영."},
        {"key": "redev", "label": "재건축·정비사업", "ts": db.kv_ts("redev_zones") or db.kv_max_ts("redev_cand:"),
         "cycle": "주 1회 자동", "note": "서울 정비사업 단계 + 국토부 실거래로 잠재력·가치를 산출."},
        {"key": "gongsi", "label": "공동주택 공시가격", "ts": db.kv_max_ts("gongsi:"),
         "cycle": "연 1회 · 90일 캐시", "note": "국토부 공시가격(VWorld). 실거래/공시 배수로 저평가·보유세 근거."},
        {"key": "news", "label": "부동산 뉴스", "ts": db.kv_ts("news_fetched"),
         "cycle": "조회 시 갱신", "note": "네이버 뉴스에서 부동산 관련 기사를 수집·요약."},
        {"key": "volume", "label": "국토부 거래량", "ts": _file_mtime(store.VOLUME_FILE),
         "cycle": "signal volumes 시", "note": "시군구 월별 거래건수·거래량비. 시장강도 프록시 입력."},
        {"key": "strength", "label": "시장강도 프록시",
         "ts": _file_mtime(store.CACHE_DIR / "market_strength.json"),
         "cycle": "KB 갱신 시 자동", "note": "거래량비+급매 밀도 프록시(부동산지인/아실 대체)."},
    ]
    from realty_signal.ingest import pipeline
    return {"기준일": last_date, "now": int(__import__("time").time()),
            "sources": sources, "pipeline": pipeline.cache_health()}


@router.get("/api/signals")
def signals(only: str | None = None):
    import json
    df = md.signals_df()
    if only:
        keep = {s.strip().upper() for s in only.split(",")}
        df = df[df["signal"].isin(keep)]
    recs = json.loads(df.to_json(orient="records", force_ascii=False))
    codes = md.kb().codes
    for r in recs:
        r["group"] = _region_group(r["region"], codes.get(r["region"]))
    return recs


@router.get("/api/timing")
def timing_api(region: str | None = None):
    if not region or not region.strip():
        return JSONResponse({"error": "region required"}, status_code=400)
    from realty_signal import api as app_api
    return app_api._region_timing_row(region.strip())


@router.get("/api/strength")
def strength_api(region: str | None = None):
    from realty_signal.ingest import pipeline
    data = pipeline.load_market_strength()
    regions = data.get("regions") or {}
    if not regions:
        try:
            data = pipeline.build_market_strength(md.signal_map())
            regions = data.get("regions") or {}
        except Exception:  # noqa: BLE001
            regions = {}
    if region and region.strip():
        r = region.strip()
        hit = regions.get(r) or next((v for k, v in regions.items() if r in k), None)
        if not hit:
            ent = pipeline.region_entity(r, signal=md.signal_map().get(r))
            return ent.to_dict()
        return {"region": r, **hit, "asof": data.get("asof"), "source": data.get("source")}
    top = sorted(regions.items(), key=lambda kv: kv[1].get("시장강도") or 0, reverse=True)[:30]
    return {"asof": data.get("asof"), "source": data.get("source"),
            "regions": [{"region": k, **v} for k, v in top]}


@router.get("/api/pipeline/health")
def pipeline_health():
    from realty_signal.ingest import pipeline
    return pipeline.cache_health()


@router.get("/api/regime")
def regime():
    r = dict(md.regime())
    r.pop("regions", None)
    return r


@router.get("/api/macro")
def macro():
    return store.load_macro()


@router.get("/api/signal-history/{region}")
def signal_hist(region: str):
    from realty_signal.signals.engine import signal_history
    return {"intervals": signal_history(md.kb(), region, md.signal_config())}


@router.get("/api/series/{region}")
def series(region: str):
    kb = md.kb()
    if region not in kb.regions:
        raise HTTPException(404, f"unknown region: {region}")
    out = {"region": region, "metrics": {}}
    for m in kb.metrics:
        s = kb.series(region, m)
        out["metrics"][m] = {
            "label": _METRIC_LABEL.get(m, m),
            "dates": [str(d.date()) for d in s.index],
            "values": [round(float(v), 3) for v in s.values],
        }
    from realty_signal.signals.engine import price_index_from
    pidx = price_index_from(kb.series(region, "sale_change"))
    if not pidx.empty:
        out["metrics"]["price_index"] = {
            "label": "매매가격지수",
            "dates": [str(d.date()) for d in pidx.index],
            "values": [round(float(v), 1) for v in pidx.values],
        }
    vol = store.load_volumes().get(region)
    if vol:
        out["volume"] = vol
    return out


@router.get("/api/listings/all")
def listings_all(request: Request, types: str = "경매,급매,청약"):
    from realty_signal import api as app_api
    return app_api.listings_all(request, types)
