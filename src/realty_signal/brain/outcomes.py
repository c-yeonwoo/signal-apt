"""Outcome 추적 — 주간 feature 스냅샷 적재 (진화 루프 연료).

매 KB 갱신 시 지역 시그널·핵심 지표를 append-only 로 보관.
N주 후 실거래/KB 증감과 join 해 적중률·가중치 보정에 사용(배치, Phase 2).
"""

from __future__ import annotations

import time
from typing import Any

import pandas as pd

from realty_signal import db
from realty_signal.ingest.kb_weekly import KBWeekly
from realty_signal.signals.engine import price_index_from

KV_KEY = "outcome_snapshots"
LABELS_KEY = "outcome_labels"
MAX_WEEKS = 52
LABEL_HORIZONS = (4, 12, 26)


def append_region_snapshot(asof: str, rows: list[dict], *, source: str = "kb_weekly") -> dict:
    """지역별 feature 한 주차 분 저장. 중복 asof 는 덮어쓰기."""
    features: dict[str, Any] = {}
    for r in rows:
        region = r.get("region")
        if not region:
            continue
        features[region] = {
            "signal": r.get("signal"),
            "전세수급": r.get("전세수급"),
            "매수우위지수": r.get("매수우위지수"),
            "매수세우위": r.get("매수세우위"),
            "매매모멘텀": r.get("매매모멘텀"),
            "공급압력": r.get("공급압력"),
            "급지": r.get("급지"),
        }
    entry = {"asof": asof, "source": source, "ts": int(time.time()), "regions": features}
    log: list = db.kv_get(KV_KEY) or []
    log = [e for e in log if e.get("asof") != asof]
    log.insert(0, entry)
    db.kv_set(KV_KEY, log[:MAX_WEEKS])
    return {"asof": asof, "regions": len(features), "weeks_stored": len(log[:MAX_WEEKS])}


def list_snapshots(limit: int = 12) -> list[dict]:
    log: list = db.kv_get(KV_KEY) or []
    return [{"asof": e.get("asof"), "regions": len((e.get("regions") or {})), "source": e.get("source")}
            for e in log[:limit]]


def _price_label(pct: float) -> str:
    if pct >= 5.0:
        return "up_5pct"
    if pct <= -5.0:
        return "down_5pct"
    return "flat"


def label_from_kb(kb: KBWeekly, *, horizons: tuple[int, ...] = LABEL_HORIZONS) -> dict:
    """outcome 스냅샷 asof → N주 후 KB 매매가격지수 변화 라벨."""
    snaps: list = db.kv_get(KV_KEY) or []
    labeled: list[dict] = []
    for snap in snaps:
        asof_s = snap.get("asof")
        if not asof_s:
            continue
        try:
            asof = pd.Timestamp(asof_s)
        except Exception:  # noqa: BLE001
            continue
        for region, feat in (snap.get("regions") or {}).items():
            sale = kb.series(region, "sale_change")
            if sale.empty:
                continue
            idx = price_index_from(sale)
            i0 = idx.asof(asof)
            if pd.isna(i0) or not i0:
                continue
            row: dict[str, Any] = {
                "asof": asof_s,
                "region": region,
                "signal": feat.get("signal"),
                "horizons": {},
            }
            for w in horizons:
                target = asof + pd.Timedelta(weeks=w)
                if target > idx.index.max():
                    continue
                i1 = idx.asof(target)
                if pd.isna(i1) or not i1:
                    continue
                pct = round((i1 / i0 - 1) * 100, 2)
                row["horizons"][str(w)] = {"pct": pct, "label": _price_label(pct)}
            if row["horizons"]:
                labeled.append(row)
    db.kv_set(LABELS_KEY, labeled)
    return {"labeled": len(labeled), "horizons": list(horizons), "snapshots": len(snaps)}


def label_summary() -> dict:
    """시그널별·horizon별 라벨 분포."""
    rows: list = db.kv_get(LABELS_KEY) or []
    agg: dict[str, dict[str, dict[str, int]]] = {}
    for row in rows:
        sig = row.get("signal") or "UNKNOWN"
        for w, h in (row.get("horizons") or {}).items():
            lbl = h.get("label") or "flat"
            agg.setdefault(sig, {}).setdefault(w, {})
            agg[sig][w][lbl] = agg[sig][w].get(lbl, 0) + 1
    return {"total": len(rows), "by_signal": agg}


def list_labels(limit: int = 50) -> list[dict]:
    return (db.kv_get(LABELS_KEY) or [])[:limit]
