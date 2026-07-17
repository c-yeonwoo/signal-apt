"""Outcome 추적 — 주간 feature 스냅샷 적재 (진화 루프 연료).

매 KB 갱신 시 지역 시그널·핵심 지표를 append-only 로 보관.
N주 후 실거래/KB 증감과 join 해 적중률·가중치 보정에 사용(배치, Phase 2).
"""

from __future__ import annotations

import time
from typing import Any

from realty_signal import db

KV_KEY = "outcome_snapshots"
MAX_WEEKS = 52


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
