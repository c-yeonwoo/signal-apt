"""경매 매물 · 입찰가 산정. 쓰기 API는 admin only."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request

from realty_signal import auction, config, store
from realty_signal.routes import deps
from realty_signal.services import market_data as md

router = APIRouter(tags=["auction"])


def _overrides(target_margin, loan_ratio, loan_rate, hold_months):
    return {"목표시세차익률": target_margin, "대출비율": loan_ratio,
            "대출금리": loan_rate, "보유개월": hold_months}


def _asdict(lst):
    from dataclasses import asdict
    return asdict(lst)


@router.get("/api/auction/buy-regions")
def buy_regions():
    df = md.signals_df()
    hot = df[df["signal"].isin(["STRONG_BUY", "BUY"])]
    return [{"region": r["region"], "signal": r["signal"]} for _, r in hot.iterrows()]


@router.get("/api/auction/listings")
def auction_listings(target_margin: float = auction.DEFAULTS["목표시세차익률"],
                     loan_ratio: float | None = None, loan_rate: float | None = None,
                     hold_months: int | None = None):
    ov = _overrides(target_margin, loan_ratio, loan_rate, hold_months)
    return {
        "params": {"target_margin": target_margin},
        "listings": auction.enrich(auction.load(), md.signal_map(), ov),
    }


@router.get("/api/auction/calc/{listing_id}")
def auction_calc(listing_id: str, target_margin: float = auction.DEFAULTS["목표시세차익률"],
                 loan_ratio: float | None = None, loan_rate: float | None = None,
                 hold_months: int | None = None):
    lst = next((x for x in auction.load() if x.id == listing_id), None)
    if lst is None:
        raise HTTPException(404, "listing not found")
    p = auction._p(_overrides(target_margin, loan_ratio, loan_rate, hold_months))
    return {
        "listing": _asdict(lst),
        "recommend": auction.recommend(lst, p),
        "table": auction.table(lst, p),
    }


@router.post("/api/auction/listings")
def auction_add(request: Request, data: dict = Body(...)):
    if err := deps.require_admin(request):
        return err
    return _asdict(auction.add(data))


@router.post("/api/auction/parse")
def auction_parse(request: Request, data: dict = Body(...)):
    if err := deps.require_admin(request):
        return err
    from realty_signal import ai_report
    config.load_env()
    if not ai_report.available():
        return {"ok": False, "reason": "no_ai"}
    model = ai_report.OPUS if deps.is_opus_user(request) else ai_report.SONNET
    parsed = ai_report.parse_auction(data.get("text", ""), model=model)
    return {"ok": bool(parsed), "parsed": parsed or {}}


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


@router.post("/api/auction/import")
async def auction_import(request: Request):
    if err := deps.require_admin(request):
        return err
    text = (await request.body()).decode("utf-8")
    return {"added": auction.import_csv(text)}
