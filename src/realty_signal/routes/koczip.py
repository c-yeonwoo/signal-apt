"""콕집 급매·호가 프록시/DB — 개인 확인용 (admin 또는 로컬만)."""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from realty_signal import config, db
from realty_signal.ingest import koczip as kz
from realty_signal.routes import deps

router = APIRouter(tags=["koczip"])

_TTL = 12 * 3600
_CACHE_PREFIX = "koczip:qd:"
_LISTING_MAX_AGE = 86400  # 바로집 레이더와 동일(1일)


def _allow_personal(request: Request) -> JSONResponse | None:
    """prod 에서는 admin만, 로컬은 로그인 유저면 OK."""
    if not config.is_prod():
        return None
    return deps.require_admin(request)


def _cache_key(params: dict) -> str:
    blob = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return _CACHE_PREFIX + hashlib.sha1(blob.encode()).hexdigest()[:20]


def _match_signal(region_name: str | None, sig: dict) -> tuple[str | None, str | None]:
    return kz.match_signal_region(region_name, sig)


def _enrich(payload: dict) -> dict:
    from realty_signal import api as app_api

    sig = app_api._signal_map()
    items = []
    for raw in payload.get("items") or []:
        region, signal = _match_signal(raw.get("region_name"), sig)
        disc = kz.buyer_discount_pct(raw)
        items.append({
            "단지": raw.get("complex_name"),
            "complex_no": raw.get("complex_no"),
            "지역표시": raw.get("region_name"),
            "region": region,
            "시그널": signal,
            "면적타입": raw.get("area_name"),
            "전용㎡": raw.get("area1_m2") or raw.get("avg_excl"),
            "매물수": raw.get("n_listings"),
            "호가min": round((raw.get("asking_min") or 0) / 10_000) or None,
            "호가avg": round((raw.get("asking_avg") or 0) / 10_000) or None,
            "실거래평균": round((raw.get("avg_real") or 0) / 10_000) or None,
            "할인율": disc,
            "실거래건수": raw.get("n_real"),
            "세대수": raw.get("households"),
            "naver_url": raw.get("naver_complex_url"),
        })
    items.sort(key=lambda x: (x.get("할인율") is not None, x.get("할인율") or -999), reverse=True)
    return {
        "ok": True, "personal": True, "source": "koczip",
        "note": "개인 확인용 · 재배포 금지 · 호가 원천은 네이버(콕집 집계)",
        "meta": {k: payload.get(k) for k in (
            "days", "min_discount", "max_discount", "asset", "trade_type",
            "sido", "sigungu", "pyeong", "min_listings", "min_samples", "count",
        )},
        "sido_opts": [{"code": c, "label": l} for c, l in kz.SIDO_OPTS],
        "items": items, "cached": False,
    }


@router.get("/api/koczip/quick-deals")
def quick_deals(
    request: Request,
    sido: str | None = None,
    sigungu: str | None = None,
    days: int = 90,
    min_discount: float = 0.05,
    asset: str = "apt",
    trade_type: str = "A1",
    pyeong: int | None = None,
    limit: int = 60,
    refresh: int = 0,
):
    if err := _allow_personal(request):
        return err
    params = {
        "sido": sido or "", "sigungu": sigungu or "",
        "days": int(days), "min_discount": float(min_discount),
        "asset": asset or "apt", "trade_type": trade_type or "A1",
        "pyeong": pyeong, "limit": int(limit),
    }
    ck = _cache_key(params)
    if not refresh:
        hit = db.kv_get(ck, max_age=_TTL)
        if hit is not None:
            hit = dict(hit)
            hit["cached"] = True
            return hit
    try:
        raw = kz.fetch_quick_deals(
            sido=params["sido"] or None, sigungu=params["sigungu"] or None,
            days=params["days"], min_discount=params["min_discount"],
            asset=params["asset"], trade_type=params["trade_type"],
            pyeong=params["pyeong"], limit=params["limit"],
        )
    except kz.KoczipError as e:
        return JSONResponse({"ok": False, "error": str(e), "source": "koczip"}, status_code=502)
    out = _enrich(raw)
    db.kv_set(ck, out)
    return out


@router.get("/api/koczip/meta")
def koczip_meta(request: Request):
    allowed = _allow_personal(request) is None
    st = db.koczip_stats() if allowed else {}
    return {
        "ok": True, "allowed": allowed,
        "sido_opts": [{"code": c, "label": l} for c, l in kz.SIDO_OPTS],
        "ttl_hours": _TTL // 3600,
        "db": st,
        "note": "개인 확인용 · admin 또는 로컬 · DB 스캔은 BUY+/관심지역",
    }


@router.post("/api/koczip/scan")
def koczip_scan(request: Request, data: dict = Body(default={})):
    """BUY+∪관심지역(또는 body.regions) 스캔 → SQLite 적재. 주기=바로집과 동일(일 1회)."""
    if err := _allow_personal(request):
        return err
    from realty_signal import api as app_api

    try:
        out = app_api.koczip_refresh(data or {})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)
    if not out.get("ok"):
        return out
    return out

def _norm_article(a: dict) -> dict:
    disc = a.get("discount_pct")
    gap = -disc if disc is not None else None  # 급매갭 관례(음수=쌈)
    kind = a.get("kind") or "discount"
    typ = "특가" if kind == "special" else "할인"
    return {
        "단지명": a.get("name"), "complex_no": a.get("complex_no"),
        "지역": a.get("region"), "시그널": a.get("signal") or "",
        "호가": a.get("price"), "급매갭": gap, "할인율": disc,
        "평형": a.get("area"), "층": a.get("floor"), "방향": a.get("direction"),
        "lat": a.get("lat"), "lng": a.get("lng"),
        "출처": "콕집", "유형": typ, "kind": kind,
        "naver_url": a.get("naver_url"), "중개사": a.get("realtor"),
        "matched": a.get("matched"), "article_no": a.get("article_no"),
        "급매": True,
    }


def _norm_complex(c: dict) -> dict:
    # 대표 호가 = sale_min (핀 정렬·표시용)
    return {
        "단지명": c.get("name"), "complex_no": c.get("complex_no"),
        "지역": c.get("region"), "시그널": c.get("signal") or "",
        "호가": c.get("sale_min"), "호가max": c.get("sale_max"),
        "매물수": c.get("sale_count") or c.get("listing_total"),
        "급매갭": None, "할인율": None,
        "lat": c.get("lat"), "lng": c.get("lng"),
        "출처": "콕집", "유형": "호가요약", "kind": "complex",
        "naver_url": (
            f"https://new.land.naver.com/complexes/{c['complex_no']}"
            if c.get("complex_no") else None
        ),
        "급매": False,
    }


@router.get("/api/koczip/listings")
def koczip_listings(
    request: Request,
    kind: str = "all",
    region: str | None = None,
):
    """DB 정규화 목록 — 급매 지도 병합용."""
    if err := _allow_personal(request):
        return err
    kind = (kind or "all").lower()
    listings: list[dict] = []
    if kind in ("all", "discount", "special"):
        arts = db.koczip_article_list(
            region=region or None,
            kind=None if kind == "all" else kind,
            max_age=_LISTING_MAX_AGE,
        )
        if kind == "all":
            pass
        listings.extend(_norm_article(a) for a in arts)
    if kind in ("all", "complex"):
        cxs = db.koczip_complex_list(region=region or None, max_age=_LISTING_MAX_AGE)
        # 호실이 이미 있는 단지는 요약 핀 생략(중복 방지) — all 일 때
        if kind == "all":
            has_art = {a.get("complex_no") for a in listings if a.get("complex_no")}
            cxs = [c for c in cxs if c.get("complex_no") not in has_art]
        listings.extend(_norm_complex(c) for c in cxs)
    # 할인 깊은 순 · 호가 낮은 순
    listings.sort(key=lambda m: (
        0 if m.get("유형") in ("할인", "특가") else 1,
        m.get("급매갭") if m.get("급매갭") is not None else 0,
        m.get("호가") if m.get("호가") is not None else 10**12,
    ))
    st = db.koczip_stats()
    return {
        "ok": True, "ready": bool(st.get("complexes") or st.get("articles")),
        "source": "koczip", "personal": True,
        "count": len(listings), "listings": listings, "db": st,
        "note": "개인 확인용 · 재배포 금지",
    }
