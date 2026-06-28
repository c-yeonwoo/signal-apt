"""FastAPI 백엔드 — 시그널 테이블 + 지역별 시계열을 제공하고 대시보드를 서빙."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from realty_signal import store
from realty_signal.signals.engine import SignalConfig, evaluate

app = FastAPI(title="realty-signal-map")

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
    return evaluate(_kb(), SignalConfig())


@app.get("/", response_class=HTMLResponse)
def index():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/meta")
def meta():
    kb = _kb()
    return {
        "regions": kb.regions,
        "metrics": [{"key": k, "label": _METRIC_LABEL.get(k, k)} for k in kb.metrics],
        "last_date": str(kb.last_date.date()),
    }


@app.get("/api/signals")
def signals(only: str | None = None):
    df = _signals_df()
    if only:
        keep = {s.strip().upper() for s in only.split(",")}
        df = df[df["signal"].isin(keep)]
    # pandas to_json 이 NaN → null 로 안전 변환 (float NaN 직렬화 오류 회피)
    return json.loads(df.to_json(orient="records", force_ascii=False))


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
