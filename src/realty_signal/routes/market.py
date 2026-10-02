"""시장 시그널 · 타이밍 · 강도 · 메타 · 시계열."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from realty_signal import db, store
from realty_signal.routes import deps
from realty_signal.services import market_data as md

router = APIRouter(tags=["market"])


@router.get("/live")
def live():
    return {"live": True}


@router.get("/ready")
def ready():
    try:
        c = db.conn()
        c.execute("SELECT 1")
        c.close()
        fresh = md.data_age_days()
        ok = store.CACHE_FILE.exists() and fresh is not None and fresh <= 14
        return JSONResponse({"ready": ok}, status_code=200 if ok else 503)
    except Exception:
        return JSONResponse({"ready": False}, status_code=503)


@router.get("/api/operations")
def operations(request: Request):
    if err := deps.require_admin(request):
        return err
    from realty_signal import jobs, llm
    from realty_signal.ingest.pipeline import cache_health
    return {"jobs": jobs.status(), "llm": llm.usage_summary(),
            "sources": cache_health(include_private=deps.personal_listings_allowed(request))}

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
            "momentum_up": c.momentum_up,
        },
    }


@router.get("/api/freshness")
def freshness(request: Request):
    from realty_signal.auction import AUCTION_FILE
    from realty_signal import api as app_api
    last_date = str(md.kb().last_date.date())
    qs = getattr(app_api, "QUICKSALE_FILE", store.CACHE_DIR / "quicksale.json")
    cert = getattr(app_api, "CERTIFIED_FILE", store.CACHE_DIR / "certified.json")
    hanbang = getattr(app_api, "HANBANG_FILE", store.CACHE_DIR / "hanbang_general.json")
    sources = [
        {"key": "signal", "label": "시장 시그널 (KB 매매·전세·수급)", "asof": last_date,
         "ts": db.kv_ts("last_kb_fetch"), "cycle": "주 1회 자동",
         "note": "KB국민은행 주간 시계열로 전세수급·매수우위·매매모멘텀·국면을 산출. 기준일이 곧 분석 기준입니다."},
        {"key": "trade", "label": "국토부 실거래", "ts": db.kv_max_ts("complex:"),
         "cycle": "조회 시 · 14일 캐시", "note": "단지 조회 시 국토부 실거래를 수집(14일 캐시), 관심단지는 주 1회 자동 프리페치."},
        {"key": "quicksale", "label": "급매 스캔", "ts": _file_mtime(qs),
         "cycle": "하루 1회 자동", "note": "BUY+·관심지역 시세 이하 호가(baroezip). 캐시 1일."},
        {"key": "certified", "label": "찐매물 스캔", "ts": _file_mtime(cert),
         "cycle": "하루 1회 자동", "note": "바로이집 내집등록·인증 매물(scope=all). 캐시 1일."},
        {"key": "hanbang", "label": "일반 아파트 매매", "ts": _file_mtime(hanbang),
         "cycle": "하루 1회 자동 · 최대 3개 지역/지역당 3페이지",
         "note": "한방 아파트 매매 목록의 개인용 표본. 페이지 제한으로 전체 시장 매물이 아닙니다."},
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
         "cycle": "KB 갱신 시 자동", "note": "공공 거래량비+시장 시그널 프록시. 개인 외부 매물 제외."},
    ]
    from realty_signal.ingest import pipeline
    private = deps.personal_listings_allowed(request)
    if not private:
        sources = [source for source in sources if source["key"] not in {"quicksale", "certified", "hanbang"}]
    return {"기준일": last_date, "now": int(__import__("time").time()),
            "sources": sources, "pipeline": pipeline.cache_health(include_private=private),
            # 수집이 멈췄을 때 '왜' 를 화면이 말할 수 있어야 한다. 로그를 볼 수 없는 사용자도 본다.
            "kb_fetch": app_api.kb_fetch_health()}


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


@router.get("/api/listing-analysis")
def listing_analysis(request: Request, key: str, stage: str = "full"):
    """선택 매물을 서버 스냅샷에서 재조회하고 개인 매물 권한을 검사한다."""
    from realty_signal import api as app_api
    from realty_signal.services import property_analysis as analysis

    if stage not in {"base", "full"}:
        raise HTTPException(422, "매물 식별자 또는 조회 단계가 올바르지 않습니다.")
    allowed = deps.personal_listings_allowed(request)
    try:
        row = analysis.resolve(key, private_allowed=allowed)
    except ValueError as exc:
        raise HTTPException(422, "매물 식별자가 올바르지 않습니다.") from exc
    except PermissionError as exc:
        raise HTTPException(403, "개인용 외부 매물은 소유 계정에서만 분석할 수 있습니다.") from exc
    except LookupError as exc:
        raise HTTPException(404, "현재 수집 범위에서 매물을 찾지 못했습니다.") from exc
    profile = db.profile_get(deps.uid(request)) or {}
    if stage == "base":
        listing = analysis.snapshot(row)
        out = {"listing": listing, "buyer_fit": analysis.buyer_fit(listing, profile), "status": "base"}
    else:
        detail = None
        if row.get("지역") and row.get("단지명"):
            try:
                detail = app_api.complex_detail(row["지역"], row["단지명"])
            except Exception:  # noqa: BLE001
                detail = {"status": "failed", "degraded": True}
        zones = []
        try:
            cached = db.kv_get("redev_zones", max_age=90 * 86400)
            if isinstance(cached, list):
                zones = cached
        except Exception:  # noqa: BLE001
            zones = []
        schedule = analysis.official_schedule(row.get("단지명"), zones)
        out = {"status": "ready", **analysis.build(row, detail, profile=profile, schedule=schedule)}
        try:
            from realty_signal.services import buyer_decision
            params = app_api._buyer_params(profile)
        except Exception:  # noqa: BLE001
            params = None
            buyer_decision = None
        if buyer_decision is not None:
            try:
                packet = buyer_decision.annotate(
                    dict(row), params, uid=deps.uid(request), sido_of=app_api._sido_of)
                out["lines"] = packet["lines"]
                out["decision"] = packet["decision"]
            except Exception:  # noqa: BLE001
                pass
    return JSONResponse(out, headers={"Cache-Control": "private, no-store"})


@router.get("/api/listing-location")
def listing_location(request: Request, key: str):
    """같은 개인 권한을 검사한 뒤 표시 좌표 기준 입지 근거를 조회한다."""
    from realty_signal.services import listing_location as location
    from realty_signal.services import property_analysis as analysis

    try:
        row = analysis.resolve(key, private_allowed=deps.personal_listings_allowed(request))
    except ValueError as exc:
        raise HTTPException(422, "매물 식별자가 올바르지 않습니다.") from exc
    except PermissionError as exc:
        raise HTTPException(403, "개인용 외부 매물은 소유 계정에서만 분석할 수 있습니다.") from exc
    except LookupError as exc:
        raise HTTPException(404, "현재 수집 범위에서 매물을 찾지 못했습니다.") from exc
    profile = db.profile_get(deps.uid(request)) or {}
    uid = deps.uid(request)
    entrance = db.entrance_get(uid, key) if uid else None
    return JSONResponse(location.build(row, profile, entrance), headers={"Cache-Control": "private, no-store"})


@router.get("/api/listing-kapt")
def listing_kapt(request: Request, key: str):
    """단지명·시군구가 유일하게 일치할 때만 공식 K-APT 정보를 제공한다."""
    from realty_signal import api as app_api
    from realty_signal.ingest import kapt
    from realty_signal.services import property_analysis as analysis

    try:
        row = analysis.resolve(key, private_allowed=deps.personal_listings_allowed(request))
    except ValueError as exc:
        raise HTTPException(422, "매물 식별자가 올바르지 않습니다.") from exc
    except PermissionError as exc:
        raise HTTPException(403, "개인용 외부 매물은 소유 계정에서만 조회할 수 있습니다.") from exc
    except LookupError as exc:
        raise HTTPException(404, "현재 수집 범위에서 매물을 찾지 못했습니다.") from exc
    sigungu = app_api._code_of(row.get("지역") or "")[:5]
    return JSONResponse(kapt.lookup(row.get("단지명") or "", sigungu),
                        headers={"Cache-Control": "private, no-store"})


@router.put("/api/listing-entrance")
def listing_entrance_set(request: Request, data: dict = Body(...)):
    from realty_signal.services import listing_entrance as entrance
    from realty_signal.services import property_analysis as analysis

    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "로그인이 필요합니다.")
    key = data.get("key")
    try:
        row = analysis.resolve(key, private_allowed=deps.personal_listings_allowed(request))
    except ValueError as exc:
        raise HTTPException(422, "매물 식별자가 올바르지 않습니다.") from exc
    except PermissionError as exc:
        raise HTTPException(403, "개인용 외부 매물은 소유 계정에서만 지정할 수 있습니다.") from exc
    except LookupError as exc:
        raise HTTPException(404, "현재 수집 범위에서 매물을 찾지 못했습니다.") from exc
    try:
        lat, lng = entrance.validate(row, data.get("lat"), data.get("lng"))
    except ValueError as exc:
        raise HTTPException(422, "출입구 후보는 매물 표시 위치에서 800m 이내의 유효한 좌표로 지정해 주세요.") from exc
    db.entrance_set(uid, key, lat, lng)
    return JSONResponse({"ok": True, "status": "user_marked_candidate"},
                        headers={"Cache-Control": "private, no-store"})


@router.delete("/api/listing-entrance")
def listing_entrance_delete(request: Request, key: str):
    from realty_signal.services import property_analysis as analysis

    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "로그인이 필요합니다.")
    try:
        analysis.resolve(key, private_allowed=deps.personal_listings_allowed(request))
    except ValueError as exc:
        raise HTTPException(422, "매물 식별자가 올바르지 않습니다.") from exc
    except PermissionError as exc:
        raise HTTPException(403, "개인용 외부 매물은 소유 계정에서만 삭제할 수 있습니다.") from exc
    except LookupError:
        # 더 이상 수집되지 않는 매물이라도 사용자의 저장 좌표는 지울 수 있어야 한다.
        pass
    db.entrance_delete(uid, key)
    return JSONResponse({"ok": True}, headers={"Cache-Control": "private, no-store"})


@router.post("/api/listing-compare")
def listing_compare(request: Request, keys: list[str] = Body(embed=True)):
    """매물 ID만 받아 현재 수집분을 다시 찾고, 개인 권한을 항목별로 검증한다."""
    from realty_signal import api as app_api
    from realty_signal.services import listing_compare as compare
    from realty_signal.services import property_analysis as analysis

    if len(keys) not in (2, 3) or len(set(keys)) != len(keys):
        raise HTTPException(422, "서로 다른 매물 2~3개를 선택해 주세요.")
    rows = []
    for key in keys:
        try:
            rows.append(analysis.resolve(key, private_allowed=deps.personal_listings_allowed(request)))
        except ValueError as exc:
            raise HTTPException(422, "매물 식별자가 올바르지 않습니다.") from exc
        except PermissionError as exc:
            raise HTTPException(403, "개인용 외부 매물은 소유 계정에서만 비교할 수 있습니다.") from exc
        except LookupError as exc:
            raise HTTPException(404, "현재 수집 범위에서 매물을 찾지 못했습니다.") from exc
    profile = db.profile_get(deps.uid(request)) or {}
    return JSONResponse(compare.build(rows, app_api.complex_detail, profile),
                        headers={"Cache-Control": "private, no-store"})


@router.get("/api/listing-discovery")
def listing_discovery(request: Request, key: str):
    """현재 수집 매물의 대안과 기존 뉴스 KB 문자열 일치 후보를 온디맨드로 보여준다."""
    from realty_signal import api as app_api
    from realty_signal.services import listing_discovery as discovery
    from realty_signal.services import property_analysis as analysis

    allowed = deps.personal_listings_allowed(request)
    try:
        row = analysis.resolve(key, private_allowed=allowed)
    except ValueError as exc:
        raise HTTPException(422, "매물 식별자가 올바르지 않습니다.") from exc
    except PermissionError as exc:
        raise HTTPException(403, "개인용 외부 매물은 소유 계정에서만 탐색할 수 있습니다.") from exc
    except LookupError as exc:
        raise HTTPException(404, "현재 수집 범위에서 매물을 찾지 못했습니다.") from exc
    profile = db.profile_get(deps.uid(request)) or {}
    budget = discovery._positive((profile.get("매수력") or {}).get("최대매수가"))
    candidates = app_api._build_listings({"일반매물", "급매", "찐매물"}, include_private=allowed) if allowed else []
    out = {"alternatives": discovery.alternatives(row, candidates, budget),
           "headlines": discovery.news(row, db.news_list(None, limit=300)),
           "news_note": "헤드라인 문자열 일치는 단지 관련성·개발사업 단계·가격 영향을 입증하지 않습니다.",
           "source_note": "대안은 현재 수집분의 가격·면적 유사성 기준이며 매물 상태를 보증하지 않습니다."}
    return JSONResponse(out, headers={"Cache-Control": "private, no-store"})


@router.get("/api/listing-discovery/commute")
def listing_discovery_commute(request: Request, key: str):
    """현재 수집 대안에 한정해 사용자 요청 시에만 통근 안내시간을 조회한다."""
    from realty_signal import api as app_api, config
    from realty_signal.services import listing_discovery as discovery
    from realty_signal.services import property_analysis as analysis

    allowed = deps.personal_listings_allowed(request)
    try:
        row = analysis.resolve(key, private_allowed=allowed)
    except ValueError as exc:
        raise HTTPException(422, "매물 식별자가 올바르지 않습니다.") from exc
    except PermissionError as exc:
        raise HTTPException(403, "개인용 외부 매물은 소유 계정에서만 탐색할 수 있습니다.") from exc
    except LookupError as exc:
        raise HTTPException(404, "현재 수집 범위에서 매물을 찾지 못했습니다.") from exc
    uid = deps.uid(request)
    profile = db.profile_get(uid) or {}
    candidates = app_api._build_listings({"일반매물", "급매", "찐매물"}, include_private=True) if allowed else []
    budget = discovery._positive((profile.get("매수력") or {}).get("최대매수가"))
    selected = discovery.alternatives(row, candidates, budget)
    by_key = {candidate.get("key"): candidate for candidate in candidates}
    rows = [by_key[x["listing"]["key"]] for x in selected if x["listing"]["key"] in by_key]
    entrance_get = (lambda item_key: db.entrance_get(uid, item_key)) if uid else None
    result = discovery.commute_candidates(row, rows, profile, config.kakao_key(), entrance_get)
    return JSONResponse(result, headers={"Cache-Control": "private, no-store"})


@router.get("/api/listing-watch")
def listing_watch_get(request: Request):
    from realty_signal import api as app_api
    from realty_signal.services import listing_watch as watch

    uid = deps.uid(request)
    saved = db.listing_watch_list(uid)
    if not deps.personal_listings_allowed(request):
        saved = [row for row in saved if row["kind"] not in watch.PRIVATE]
    kinds = {row["kind"] for row in saved}
    # 대안은 저장한 유형뿐 아니라 다른 급매·찐매물도 비교한다. 비소유자에게는 절대 읽지 않는다.
    if kinds & watch.PRIVATE and deps.personal_listings_allowed(request):
        kinds |= watch.PRIVATE
    current = app_api._build_listings(kinds, include_private=deps.personal_listings_allowed(request)) if kinds else []
    return {"items": watch.build(saved, current)}


@router.post("/api/listing-watch")
def listing_watch_add(request: Request, data: dict = Body(...)):
    from realty_signal import api as app_api
    from realty_signal.services import listing_watch as watch

    key = data.get("key")
    if not isinstance(key, str) or len(key) > 180 or ":" not in key:
        raise HTTPException(422, "매물 식별자를 확인해 주세요.")
    kind = key.split(":", 1)[0]
    if kind not in watch.WATCHABLE:
        raise HTTPException(422, "이 유형은 매물 찜을 지원하지 않습니다.")
    if kind in watch.PRIVATE and not deps.personal_listings_allowed(request):
        raise HTTPException(403, "개인용 외부 매물은 소유 계정에서만 저장할 수 있습니다.")
    current = app_api._build_listings({kind}, include_private=deps.personal_listings_allowed(request))
    row = next((r for r in current if r["key"] == key), None)
    if row is None:
        raise HTTPException(404, "현재 수집 범위에서 매물을 찾지 못했습니다. 새로고침 후 다시 시도해 주세요.")
    if not row.get("단지명"):
        raise HTTPException(422, "단지명이 없는 매물은 찜할 수 없습니다.")
    db.listing_watch_add(deps.uid(request), row)
    return {"ok": True}


@router.delete("/api/listing-watch")
def listing_watch_remove(request: Request, key: str):
    if not key or len(key) > 180:
        raise HTTPException(422, "매물 식별자를 확인해 주세요.")
    db.listing_watch_remove(deps.uid(request), key)
    return {"ok": True}


@router.get("/api/listing-costs")
def listing_costs(
    request: Request,
    price: float,
    region: str | None = None,
    area: float | None = None,
    pyeong: float | None = None,
    homes: int | None = None,
    first_time: bool | None = None,
    moving: float | None = None,
    interior: float | None = None,
):
    """매물 호가·실거래가 기준 매수 부대비용(취득세·중개·법무·이사·인테리어).

    쿼리 생략 시 로그인 프로필의 주택수·생애최초를 기본값으로 쓴다.
    interior 미지정=평당 기본, interior=0=인테리어 제외.
    """
    from realty_signal import transaction_costs as tc

    if price is None or price <= 0:
        return JSONResponse({"error": "price required"}, status_code=400)

    uid_ = deps.uid(request)
    profile = db.profile_get(uid_) if uid_ else {}
    conf = (profile.get("매수력") or {}).get("가정") or {}

    def _pick_homes() -> int:
        if homes is not None:
            return int(homes)
        v = profile.get("주택수")
        if v is None or v == "":
            v = conf.get("주택수")
        return int(v or 0)

    def _pick_ft() -> bool:
        if first_time is not None:
            return bool(first_time)
        v = profile.get("생애최초")
        if v is None or v == "":
            v = conf.get("생애최초")
        return bool(v)

    return tc.estimate(
        price,
        region=(region or "").strip() or None,
        exclusive_m2=area,
        pyeong=pyeong,
        homes=_pick_homes(),
        first_time=_pick_ft(),
        moving=moving,
        interior=interior,
    )
