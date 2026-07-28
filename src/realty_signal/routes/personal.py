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


@router.get("/api/buying-power")
def buying_power_get(request: Request, capital: float | None = None, income: float | None = None,
                     existing_debt_annual: float | None = None, homes: int | None = None,
                     first_time: bool | None = None, region: str | None = None,
                     regulated: bool | None = None, dispose: bool | None = None,
                     temp_two_home: bool | None = None, big_area: bool | None = None,
                     apply_bangongje: bool | None = None,
                     ltv: float | None = None, rate: float | None = None,
                     rate_type: str | None = None, years: int | None = None):
    from realty_signal import api as app_api
    return app_api.buying_power_statement(
        request, capital=capital, income=income, existing_debt_annual=existing_debt_annual,
        homes=homes, first_time=first_time, region=region, regulated=regulated,
        dispose=dispose, temp_two_home=temp_two_home, big_area=big_area,
        apply_bangongje=apply_bangongje,
        ltv=ltv, rate=rate, rate_type=rate_type, years=years)


@router.post("/api/buying-power/confirm")
def buying_power_confirm(request: Request, data: dict = Body(default={})):
    from realty_signal import api as app_api
    return app_api.buying_power_confirm(request, data)


@router.get("/api/shortlist")
def shortlist(request: Request, limit: int = 3, budget: float | None = None):
    from realty_signal import api as app_api
    return app_api.shortlist(request, limit, budget)


@router.get("/api/imjang/course")
def imjang_course(request: Request, on: str | None = None, start: str = "10:00",
                  stop_min: int = 50, limit: int = 3):
    from realty_signal import api as app_api
    return app_api.imjang_course(request, on=on, start=start, stop_min=stop_min, limit=limit)


@router.get("/api/imjang/visits")
def imjang_visits(request: Request, limit: int = 50):
    from realty_signal import api as app_api
    return app_api.imjang_visits(request, limit)


@router.post("/api/imjang/visit")
def imjang_visit_save(request: Request, data: dict = Body(default={})):
    from realty_signal import api as app_api
    return app_api.imjang_visit_save(request, data)


@router.delete("/api/imjang/visit/{visit_id}")
def imjang_visit_delete(request: Request, visit_id: int):
    from realty_signal import api as app_api
    return app_api.imjang_visit_delete(request, visit_id)


@router.get("/api/telegram/status")
def telegram_status(request: Request):
    from realty_signal import api as app_api
    return app_api.telegram_status(request)


@router.post("/api/telegram/link")
def telegram_link(request: Request):
    from realty_signal import api as app_api
    return app_api.telegram_link(request)


@router.post("/api/telegram/check")
def telegram_check(request: Request):
    from realty_signal import api as app_api
    return app_api.telegram_check(request)


@router.post("/api/telegram/unlink")
def telegram_unlink(request: Request):
    from realty_signal import api as app_api
    return app_api.telegram_unlink(request)


@router.post("/api/telegram/test")
def telegram_test(request: Request):
    from realty_signal import api as app_api
    return app_api.telegram_test(request)
