"""Ingest pipeline — 캐시 검증·상태·시장강도 파생 (Phase 3).

수집 로직은 기존 ingest/* 에 두고, 여기서는:
  1) 캐시 존재·스키마·신선도 검사
  2) 지역별 시장강도 프록시 산출·저장
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from realty_signal import store
from realty_signal.entities import Provenance, RegionEntity, SourceStatus
from realty_signal.signals.strength import market_strength

STRENGTH_FILE = store.CACHE_DIR / "market_strength.json"
QUICKSALE_FILE = store.CACHE_DIR / "quicksale.json"
_STALE_DAYS = {"long": 14, "volume": 45, "quicksale": 21, "supply": 21}


def _age_days(path: Path) -> float | None:
    if not path.exists():
        return None
    return (time.time() - path.stat().st_mtime) / 86400


def _status(path: Path, stale_days: float) -> SourceStatus:
    if not path.exists():
        return "missing"
    age = _age_days(path)
    if age is not None and age > stale_days:
        return "stale"
    return "ok"


def cache_health() -> dict[str, Any]:
    """소스별 ok/partial/stale/missing."""
    checks = {
        "kb_long": (store.CACHE_FILE, _STALE_DAYS["long"]),
        "supply": (store.SUPPLY_FILE, _STALE_DAYS["supply"]),
        "volume": (store.VOLUME_FILE, _STALE_DAYS["volume"]),
        "quicksale": (QUICKSALE_FILE, _STALE_DAYS["quicksale"]),
        "macro": (store.MACRO_FILE, _STALE_DAYS["long"]),
        "codes": (store.CODES_FILE, 9999),
    }
    sources = {}
    for key, (path, stale) in checks.items():
        st = _status(path, stale)
        sources[key] = {
            "status": st,
            "path": str(path),
            "age_days": round(_age_days(path), 1) if path.exists() else None,
            "exists": path.exists(),
        }
    worst = "ok"
    for s in sources.values():
        if s["status"] == "missing" and worst != "missing":
            worst = "missing"
        elif s["status"] == "stale" and worst == "ok":
            worst = "stale"
    # volume 없으면 partial (KB만으로도 시그널 가능)
    if sources.get("volume", {}).get("status") == "missing" and worst == "ok":
        worst = "partial"
    return {"status": worst, "sources": sources, "ts": int(time.time())}


def _quicksale_counts() -> dict[str, int]:
    if not QUICKSALE_FILE.exists():
        return {}
    try:
        listings = json.loads(QUICKSALE_FILE.read_text(encoding="utf-8")).get("listings", [])
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, int] = {}
    for m in listings:
        r = m.get("지역") or ""
        if r:
            out[r] = out.get(r, 0) + 1
    return out


def build_market_strength(
    signal_map: dict[str, str] | None = None,
    *,
    out: Path = STRENGTH_FILE,
) -> dict[str, Any]:
    """전 지역 시장강도 프록시 → market_strength.json."""
    vols = store.load_volumes()
    qs = _quicksale_counts()
    sig = signal_map or {}
    regions = set(vols) | set(qs) | set(sig)
    by_region: dict[str, Any] = {}
    for region in regions:
        vr = (vols.get(region) or {}).get("거래량비")
        qc = qs.get(region)
        if vr is None and qc is None and region not in sig:
            continue
        r = market_strength(volume_ratio=vr, quicksale_count=qc, signal=sig.get(region))
        by_region[region] = r.to_dict()
    payload = {
        "asof": time.strftime("%Y-%m-%d"),
        "source": "volume_quicksale_proxy",
        "count": len(by_region),
        "regions": by_region,
        "ts": int(time.time()),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def load_market_strength(cache: Path = STRENGTH_FILE) -> dict[str, Any]:
    if not cache.exists():
        return {}
    try:
        return json.loads(cache.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def region_entity(
    region: str,
    *,
    signal: str | None = None,
    timing_score: int | None = None,
) -> RegionEntity:
    """지역 Entity 조립 (캐시 기반)."""
    vols = store.load_volumes().get(region) or {}
    vr = vols.get("거래량비")
    qc = _quicksale_counts().get(region, 0)
    strength_cache = (load_market_strength().get("regions") or {}).get(region)
    if strength_cache:
        ms, msl = strength_cache.get("시장강도"), strength_cache.get("시장강도라벨")
        conf = strength_cache.get("confidence", 0.6)
    else:
        sr = market_strength(volume_ratio=vr, quicksale_count=qc, signal=signal)
        ms, msl, conf = sr.score, sr.label, sr.confidence
    sp = None
    try:
        sdf = store.load_supply()
        if not sdf.empty and "region" in sdf.columns:
            hit = sdf[sdf["region"] == region]
            if not hit.empty:
                sp = float(hit.iloc[0].get("supply_pressure"))
    except Exception:  # noqa: BLE001
        pass
    asof = None
    if store.CACHE_FILE.exists():
        try:
            asof = str(store.load().last_date.date())
        except Exception:  # noqa: BLE001
            pass
    return RegionEntity(
        region=region, signal=signal, timing_score=timing_score,
        market_strength=ms, market_strength_label=msl,
        volume_ratio=vr, quicksale_count=qc, supply_pressure=sp,
        provenance=Provenance(
            source="kb_weekly+volume+quicksale",
            asof=asof,
            status="ok" if vr is not None else "partial",
            confidence=float(conf),
        ),
    )
