"""경매 매물 · 입찰가 산정. 쓰기 API는 admin only."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request

from realty_signal import auction, config, store
from realty_signal.routes import deps

router = APIRouter(tags=["auction"])


def _asdict(lst):
    from dataclasses import asdict
    return asdict(lst)


@router.get("/api/auction/buy-regions")
def buy_regions():
    from realty_signal import api as app_api

    safe = app_api._auction_signal_map()
    return [{"region": region, "signal": grade} for region, grade in safe.items()
            if grade in {"STRONG_BUY", "BUY"}]


@router.get("/api/auction/listings")
def auction_listings():
    from realty_signal import api as app_api

    from realty_signal.ingest import external

    signals = app_api._auction_signal_map()
    region_grades = app_api._regime().get("regions", {})
    external.schedule_hank_refresh()
    listings = auction.enrich(auction.load(), signals)
    for item in listings:
        region = item["region"]
        item["지역급지"] = (region_grades.get(region) or {}).get("급지") if region in signals else None
    known = {item.get("사건번호") for item in listings if item.get("사건번호")}
    for card in external.read_hank_cards():
        if card.get("사건번호") and card["사건번호"] in known:
            continue
        region = card.get("region") or ""
        safe = signals.get(region)
        listings.append({
            **card,
            "지역시그널": safe or "HELD",
            "지역급지": (region_grades.get(region) or {}).get("급지") if safe else None,
        })
    return {
        "params": {"mode": "purpose_specific"},
        "listings": listings,
    }


@router.get("/api/auction/calc/{listing_id}")
def auction_calc(listing_id: str):
    lst = next((x for x in auction.load() if x.id == listing_id), None)
    if lst is None:
        raise HTTPException(404, "listing not found")
    return {
        "listing": auction.public_fields(lst),
        "recommend": {"상태": "needs_review", "입찰가": None,
                      "사유": ["목적별 입찰 시뮬레이터에서 현금·세금·권리 근거를 입력하세요."]},
        "table": [],
    }


@router.post("/api/auction/scenario/{listing_id}")
def auction_scenario(listing_id: str, data: dict = Body(...)):
    """저장 없는 목적별 계산. 사용자 가정은 검증된 정책·대출로 승격하지 않는다."""
    from realty_signal.auction_bid_engine import evaluate

    lst = auction.get(listing_id)
    if lst is None:
        raise HTTPException(404, "listing not found")
    try:
        return evaluate(lst, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/auction/listings")
def auction_add(request: Request, data: dict = Body(...)):
    if err := deps.require_admin(request):
        return err
    return _asdict(auction.add(data))


@router.post("/api/auction/parse")
def auction_parse(request: Request, data: dict = Body(...)):
    """규칙 파서 우선 — AI 키가 없어도 임포트가 되게. 부족할 때만 AI 로 넘긴다."""
    if err := deps.require_admin(request):
        return err
    from realty_signal import ai_report
    config.load_env()
    text = data.get("text", "")
    parsed = auction.parse_text(text)
    conf = auction.parse_confidence(parsed)
    if conf == "high":
        return {"ok": True, "parsed": parsed, "source": "rule"}
    if not ai_report.available():
        return {"ok": bool(parsed), "parsed": parsed, "source": "rule",
                "reason": None if parsed else "no_ai"}
    model = ai_report.OPUS if deps.is_opus_user(request) else ai_report.SONNET
    ai = ai_report.parse_auction(text, model=model, uid=deps.uid(request)) or {}
    merged = {**parsed, **{k: v for k, v in ai.items() if v not in (None, "", 0)}}
    return {"ok": bool(merged), "parsed": merged, "source": "ai" if ai else "rule"}


@router.get("/api/auction/rights/{listing_id}")
def auction_rights_get(request: Request, listing_id: str):
    if err := deps.require_admin(request):
        return err
    lst = auction.get(listing_id)
    if lst is None:
        raise HTTPException(404, "listing not found")
    saved = lst.권리분석 or {}
    return {"ok": True, "입력": saved.get("입력") or {"권리": [], "임차인": []},
            "조사완료": saved.get("조사완료") is True,
            "분석": saved.get("분석"), "인수보증금": lst.인수보증금}


@router.post("/api/auction/rights/preview")
def auction_rights_preview(request: Request, data: dict = Body(default={})):
    """저장 없이 판정만 — 입력하면서 결과가 바로 보여야 끝까지 채운다."""
    if err := deps.require_admin(request):
        return err
    from realty_signal import auction_rights as ar
    return {"ok": True, "분석": ar.analyze(data.get("권리"), data.get("임차인"))}


@router.post("/api/auction/rights/{listing_id}")
def auction_rights_save(request: Request, listing_id: str, data: dict = Body(default={})):
    """미확정 인수금액은 null로 보존해 입찰 판단을 보류한다."""
    if err := deps.require_admin(request):
        return err
    from realty_signal import auction_rights as ar
    if auction.get(listing_id) is None:
        raise HTTPException(404, "listing not found")
    result = ar.analyze(data.get("권리"), data.get("임차인"))
    lst = auction.update(listing_id, {
        "인수보증금": result["인수합계"],
        "권리분석": {"입력": {"권리": data.get("권리") or [], "임차인": data.get("임차인") or []},
                 "조사완료": data.get("조사완료") is True, "분석": result},
    })
    return {"ok": True, "분석": result, "listing": _asdict(lst)}


@router.get("/api/auction/plan/{listing_id}")
def auction_plan(listing_id: str, bid: float | None = None):
    lst = auction.get(listing_id)
    if lst is None:
        raise HTTPException(404, "listing not found")
    return {"ok": True, "plan": auction.plan(lst, bid), "단지명": lst.단지명,
            "사건번호": lst.사건번호, "region": lst.region}


@router.post("/api/auction/won/{listing_id}")
def auction_won(request: Request, listing_id: str, data: dict = Body(default={})):
    """낙찰 기록 — 이후 플랜·알림이 낙찰일 기준으로 움직인다."""
    if err := deps.require_admin(request):
        return err
    if auction.get(listing_id) is None:
        raise HTTPException(404, "listing not found")
    from datetime import date
    lst = auction.update(listing_id, {
        "낙찰가": float(data.get("낙찰가") or 0) or None,
        "낙찰일": (data.get("낙찰일") or date.today().isoformat())[:10],
    })
    return {"ok": True, "listing": _asdict(lst), "plan": auction.plan(lst)}


@router.delete("/api/auction/listings/{listing_id}")
def auction_delete(request: Request, listing_id: str):
    if err := deps.require_admin(request):
        return err
    auction.remove(listing_id)
    return {"ok": True}


@router.post("/api/auction/refresh-market")
def auction_refresh_market(request: Request):
    if err := deps.require_admin(request):
        return err
    import json
    config.load_env()
    key = config.public_data_key()
    codes = json.loads(store.CODES_FILE.read_text(encoding="utf-8")) if store.CODES_FILE.exists() else {}
    return {"updated": auction.update_market(codes, key)}


@router.post("/api/auction/refresh-market/{listing_id}")
def auction_refresh_one_market(request: Request, listing_id: str):
    """입찰 시뮬레이터에서 현재 물건의 국토부 표본만 갱신한다."""
    if err := deps.require_admin(request):
        return err
    if auction.get(listing_id) is None:
        raise HTTPException(404, "listing not found")
    import json
    config.load_env()
    key = config.public_data_key()
    codes = json.loads(store.CODES_FILE.read_text(encoding="utf-8")) if store.CODES_FILE.exists() else {}
    updated = auction.update_market(codes, key, listing_id)
    current = auction.get(listing_id)
    return {"updated": updated, "sample_count": len(current.실거래표본),
            "refreshed_at": current.실거래표본갱신일 or None}


@router.post("/api/auction/import")
async def auction_import(request: Request):
    if err := deps.require_admin(request):
        return err
    text = (await request.body()).decode("utf-8")
    return {"added": auction.import_csv(text)}
