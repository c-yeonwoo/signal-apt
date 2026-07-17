"""개인화 — 관심피드 · 동네 리포트 · 체크리스트 · 대출 시나리오."""

from __future__ import annotations

from fastapi import APIRouter, Body, Request

router = APIRouter(tags=["personal"])


@router.get("/api/myfeed")
def myfeed(request: Request):
    from realty_signal import api as app_api
    return app_api.myfeed(request)


@router.get("/api/neighborhood/{region}")
def neighborhood(request: Request, region: str):
    from realty_signal import api as app_api
    return app_api.neighborhood(request, region)


@router.get("/api/neighborhood-compare")
def neighborhood_compare(request: Request, a: str, b: str):
    from realty_signal import api as app_api
    return app_api.neighborhood_compare(request, a, b)


@router.post("/api/checklist/{region}")
def save_checklist(request: Request, region: str, data: dict = Body(...)):
    from realty_signal import api as app_api
    return app_api.save_checklist(request, region, data)


@router.get("/api/loan-scenarios")
def loan_scenarios(request: Request, capital: float | None = None, income: float | None = None,
                   rate: float = 0.04, years: int = 30, price: float | None = None):
    from realty_signal import api as app_api
    return app_api.loan_scenarios(request, capital, income, rate, years, price)
