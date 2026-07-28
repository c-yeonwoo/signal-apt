"""매수 전략 — 결론 · 갈아타기 · 청약 · 급매."""

from __future__ import annotations

from fastapi import APIRouter, Body, Request

router = APIRouter(tags=["strategy"])


def _api():
    from realty_signal import api as app_api
    return app_api


@router.get("/api/presale")
def presale_list(request: Request):
    return _api().presale_list(request)


@router.get("/api/presale/{manage_no}/types")
def presale_types(manage_no: str):
    return _api().presale_types(manage_no)


@router.get("/api/conclusion")
def conclusion(request: Request, capital: float | None = None, ltv: float | None = None,
               pyeong: float | None = None, income: float | None = None,
               rate: float | None = None, years: int | None = None,
               prefer_strong: bool = True):
    """매수력 정본 + 통합 매물 추천. capital 없으면 프로필/확정 매수력 사용."""
    return _api().conclusion(
        request, capital=capital, ltv=ltv, pyeong=pyeong, income=income,
        rate=rate, years=years, prefer_strong=prefer_strong)


@router.get("/api/tradeup")
def tradeup(current_region: str, current_value: float, loan_balance: float = 0,
            extra_cash: float = 0, ltv: float = 0.7, income: float | None = None,
            rate: float = 0.04, years: int = 30, pyeong: float = 25.7):
    return _api().tradeup(current_region, current_value, loan_balance, extra_cash,
                          ltv, income, rate, years, pyeong)


@router.get("/api/quicksale")
def quicksale():
    return _api().quicksale()


@router.post("/api/quicksale/refresh")
def quicksale_refresh(request: Request, data: dict = Body(default={})):
    from realty_signal.routes import deps
    if err := deps.require_admin(request):
        return err
    return _api().quicksale_refresh(data)


@router.get("/api/certified")
def certified():
    return _api().certified()


@router.post("/api/certified/refresh")
def certified_refresh(request: Request, data: dict = Body(default={})):
    from realty_signal.routes import deps
    if err := deps.require_admin(request):
        return err
    return _api().certified_refresh(data)
