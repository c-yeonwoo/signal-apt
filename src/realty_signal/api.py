"""FastAPI 백엔드 — 시그널 테이블 + 지역별 시계열을 제공하고 대시보드를 서빙."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from realty_signal import auction, store
from realty_signal.signals.engine import SignalConfig, evaluate

log = logging.getLogger("realty_signal")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 캐시가 없으면(신규 배포 등) KB 데이터허브에서 최초 1회 수집.
    if not store.CACHE_FILE.exists():
        log.warning("캐시 없음 — KB 데이터허브에서 최초 수집 중…")
        try:
            store.fetch()
        except Exception as e:  # 수집 실패해도 서버는 기동(이후 갱신 버튼으로 재시도)
            log.error("초기 수집 실패: %s", e)
    yield


app = FastAPI(title="realty-signal-map", lifespan=lifespan)

WEB_DIR = Path(__file__).parent / "web"
_METRIC_LABEL = {
    "jeonse_supply": "전세수급지수",
    "buyer_demand": "매수세우위",
    "buyer_superiority": "매수우위지수",
    "sale_change": "매매증감%",
    "jeonse_change": "전세증감%",
}


@lru_cache(maxsize=1)
def _kb():
    return store.load()


@lru_cache(maxsize=1)
def _signals_df():
    return evaluate(_kb(), SignalConfig(), store.load_supply())


@app.get("/", response_class=HTMLResponse)
def index():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


@app.post("/api/refresh")
def refresh():
    """KB 데이터허브에서 최신 지표를 재수집하고 캐시/시그널을 갱신."""
    kb = store.fetch()
    _kb.cache_clear()
    _signals_df.cache_clear()
    return {"ok": True, "last_date": str(kb.last_date.date()), "regions": len(kb.regions)}


@app.get("/api/meta")
def meta():
    kb = _kb()
    c = SignalConfig()
    # 전세수급지수 구간(색·설명) — 차트 배경 밴드용
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
        "zones": {
            "jeonse_supply": jeonse_zones,
            "buyer_demand_buy": c.demand_buy,        # 매수세우위 매수신호선
            "buyer_idx_strong": c.buyeridx_strong,   # 매수우위지수 강세선
        },
    }


_SEOUL_AGG = {"강남11개구", "강북14개구"}


def _region_group(region: str, code: str | None) -> str:
    """시/도 그룹 (필터용)."""
    if region in _SEOUL_AGG or (code and code.startswith("11")):
        return "서울"
    if code and code.startswith("41"):
        return "경기"
    if code and code.startswith("28"):
        return "인천"
    return "지방·광역"


@app.get("/api/signals")
def signals(only: str | None = None):
    df = _signals_df()
    if only:
        keep = {s.strip().upper() for s in only.split(",")}
        df = df[df["signal"].isin(keep)]
    # pandas to_json 이 NaN → null 로 안전 변환 (float NaN 직렬화 오류 회피)
    recs = json.loads(df.to_json(orient="records", force_ascii=False))
    codes = _kb().codes
    for r in recs:
        r["group"] = _region_group(r["region"], codes.get(r["region"]))
    return recs


def _signal_map() -> dict:
    df = _signals_df()
    return dict(zip(df["region"], df["signal"]))


@app.get("/api/auction/buy-regions")
def buy_regions():
    """STRONG_BUY/BUY 시그널 지역 — 매물 탐색 가이드."""
    df = _signals_df()
    hot = df[df["signal"].isin(["STRONG_BUY", "BUY"])]
    return [{"region": r["region"], "signal": r["signal"]} for _, r in hot.iterrows()]


def _overrides(target_margin, loan_ratio, loan_rate, hold_months):
    return {"목표시세차익률": target_margin, "대출비율": loan_ratio,
            "대출금리": loan_rate, "보유개월": hold_months}


@app.get("/api/auction/listings")
def auction_listings(target_margin: float = auction.DEFAULTS["목표시세차익률"],
                     loan_ratio: float | None = None, loan_rate: float | None = None,
                     hold_months: int | None = None):
    """매물 + 권장입찰가/시세차익률 + 우선순위(높은순)."""
    ov = _overrides(target_margin, loan_ratio, loan_rate, hold_months)
    return {
        "params": {"target_margin": target_margin},
        "listings": auction.enrich(auction.load(), _signal_map(), ov),
    }


@app.get("/api/auction/calc/{listing_id}")
def auction_calc(listing_id: str, target_margin: float = auction.DEFAULTS["목표시세차익률"],
                 loan_ratio: float | None = None, loan_rate: float | None = None,
                 hold_months: int | None = None):
    """단일 매물의 비용분해 + 낙찰가율 민감도 표 + 권장 입찰가."""
    lst = next((x for x in auction.load() if x.id == listing_id), None)
    if lst is None:
        raise HTTPException(404, "listing not found")
    p = auction._p(_overrides(target_margin, loan_ratio, loan_rate, hold_months))
    return {
        "listing": asdict_listing(lst),
        "recommend": auction.recommend(lst, p),
        "table": auction.table(lst, p),
    }


@app.post("/api/auction/listings")
def auction_add(data: dict = Body(...)):
    return asdict_listing(auction.add(data))


@app.delete("/api/auction/listings/{listing_id}")
def auction_delete(listing_id: str):
    auction.remove(listing_id)
    return {"ok": True}


@app.post("/api/auction/import")
async def auction_import(request: Request):
    text = (await request.body()).decode("utf-8")
    return {"added": auction.import_csv(text)}


def asdict_listing(lst):
    from dataclasses import asdict
    return asdict(lst)


@app.get("/api/undervalued")
def undervalued():
    """수도권 시군구 저평가 랭킹 (입지 대비 가격). 시그널 등급 병합."""
    df = store.load_localities()
    if df.empty:
        return {"ready": False, "listings": []}
    sig = _signal_map()
    recs = json.loads(df.to_json(orient="records", force_ascii=False))
    for r in recs:
        r["시그널"] = sig.get(r["region"], "")
    return {"ready": True, "listings": recs}


@app.get("/api/series/{region}")
def series(region: str):
    kb = _kb()
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
    return out
