"""단지 · 저평가 · 비교 · 임장 · 중개."""

from __future__ import annotations

from fastapi import APIRouter, Body, Request

router = APIRouter(tags=["complex"])


def _api():
    from realty_signal import api as app_api
    return app_api


@router.get("/api/complex-grades/{region}")
def complex_grades(region: str):
    return _api().complex_grades(region)


@router.get("/api/undervalued")
def undervalued():
    return _api().undervalued()


@router.get("/api/complex-search")
def complex_search(q: str):
    return _api().complex_search(q)


@router.get("/api/addr-search")
def addr_search(q: str):
    return _api().addr_search(q)


@router.get("/api/complex/{region}/{name}")
def complex_detail(region: str, name: str):
    return _api().complex_detail(region, name)


@router.get("/api/complex/{region}/{name}/building")
def complex_building(region: str, name: str):
    return _api().complex_building(region, name)


@router.get("/api/complex-backtest")
def complex_backtest_api():
    return _api().complex_backtest_api()


@router.post("/api/complex-backtest/run")
def complex_backtest_run(request: Request):
    return _api().complex_backtest_run(request)


@router.post("/api/compare-recommend")
def compare_recommend_api(request: Request, data: dict = Body(...)):
    return _api().compare_recommend_api(request, data)


@router.post("/api/compare-insight")
def compare_insight_api(request: Request, data: dict = Body(...)):
    return _api().compare_insight_api(request, data)


@router.get("/api/imjang/{region}/{name}")
def imjang_report(region: str, name: str):
    return _api().imjang_report(region, name)


@router.get("/api/agents/{region}/{name}")
def agents_nearby(region: str, name: str):
    return _api().agents_nearby(region, name)
