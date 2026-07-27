"""콕집 급매·호가 프록시 — 개인 확인용 (admin 또는 로컬만)."""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from realty_signal import config, db
from realty_signal.ingest import koczip as kz
from realty_signal.routes import deps

router = APIRouter(tags=["koczip"])

_TTL = 12 * 3600  # 12h — 저빈도
_CACHE_PREFIX = "koczip:qd:"


def _allow_personal(request: Request) -> JSONResponse | None:
    """prod 에서는 admin만, 로컬은 로그인 유저면 OK (auth_gate가 로그인 강제)."""
    if not config.is_prod():
        return None
    return deps.require_admin(request)


def _cache_key(params: dict) -> str:
    blob = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return _CACHE_PREFIX + hashlib.sha1(blob.encode()).hexdigest()[:20]


def _match_signal(region_name: str | None, sig: dict) -> tuple[str | None, str | None]:
    for cand in kz.region_candidates(region_name):
        if cand in sig:
            return cand, sig[cand]
        # 공백 없는 키도 시도
        compact = cand.replace(" ", "")
        for r, s in sig.items():
            if r.replace(" ", "") == compact:
                return r, s
    return None, None


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
            "호가min": round((raw.get("asking_min") or 0) / 10_000) or None,  # 만원
            "호가avg": round((raw.get("asking_avg") or 0) / 10_000) or None,
            "실거래평균": round((raw.get("avg_real") or 0) / 10_000) or None,
            "할인율": disc,
            "실거래건수": raw.get("n_real"),
            "세대수": raw.get("households"),
            "naver_url": raw.get("naver_complex_url"),
        })
    # 할인율 큰 순
    items.sort(key=lambda x: (x.get("할인율") is not None, x.get("할인율") or -999), reverse=True)
    return {
        "ok": True,
        "personal": True,
        "source": "koczip",
        "note": "개인 확인용 · 재배포 금지 · 호가 원천은 네이버(콕집 집계)",
        "meta": {k: payload.get(k) for k in (
            "days", "min_discount", "max_discount", "asset", "trade_type",
            "sido", "sigungu", "pyeong", "min_listings", "min_samples", "count",
        )},
        "sido_opts": [{"code": c, "label": l} for c, l in kz.SIDO_OPTS],
        "items": items,
        "cached": False,
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
        "sido": sido or "",
        "sigungu": sigungu or "",
        "days": int(days),
        "min_discount": float(min_discount),
        "asset": asset or "apt",
        "trade_type": trade_type or "A1",
        "pyeong": pyeong,
        "limit": int(limit),
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
            sido=params["sido"] or None,
            sigungu=params["sigungu"] or None,
            days=params["days"],
            min_discount=params["min_discount"],
            asset=params["asset"],
            trade_type=params["trade_type"],
            pyeong=params["pyeong"],
            limit=params["limit"],
        )
    except kz.KoczipError as e:
        return JSONResponse({"ok": False, "error": str(e), "source": "koczip"}, status_code=502)
    out = _enrich(raw)
    db.kv_set(ck, out)
    return out


@router.get("/api/koczip/meta")
def koczip_meta(request: Request):
    """셀렉트 옵션 + 접근 가능 여부(UI가 탭을 숨길지 판단)."""
    allowed = _allow_personal(request) is None
    return {
        "ok": True,
        "allowed": allowed,
        "sido_opts": [{"code": c, "label": l} for c, l in kz.SIDO_OPTS],
        "ttl_hours": _TTL // 3600,
        "note": "개인 확인용 · admin 또는 로컬",
    }
