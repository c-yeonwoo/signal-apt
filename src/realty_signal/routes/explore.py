"""탐색·부가 API — 단지·재건축·청약·급매·뉴스·규제·비교 등.

핸들러 본체는 아직 api.py 헬퍼와 결합돼 있어 thin-wrap 으로 등록만 이전.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Request

router = APIRouter(tags=["explore"])


def _api():
    from realty_signal import api as app_api
    return app_api


@router.get("/api/regulation")
def regulation_api():
    return _api().regulation_api()


@router.get("/api/admin/policy")
def admin_policy_list(request: Request):
    return _api().admin_policy_list(request)


@router.post("/api/admin/policy")
def admin_policy_add(request: Request, data: dict = Body(...)):
    return _api().admin_policy_add(request, data)


@router.delete("/api/admin/policy/{pid}")
def admin_policy_delete(request: Request, pid: int):
    return _api().admin_policy_delete(request, pid)


@router.get("/api/complex-grades/{region}")
def complex_grades(region: str):
    return _api().complex_grades(region)


@router.get("/api/undervalued")
def undervalued():
    return _api().undervalued()


@router.get("/api/presale")
def presale_list(request: Request):
    return _api().presale_list(request)


@router.get("/api/presale/{manage_no}/types")
def presale_types(manage_no: str):
    return _api().presale_types(manage_no)


@router.get("/api/conclusion")
def conclusion(capital: float, ltv: float = 0.7, pyeong: float = 25.7,
               income: float | None = None, rate: float = 0.04, years: int = 30):
    return _api().conclusion(capital, ltv, pyeong, income, rate, years)


@router.get("/api/tradeup")
def tradeup(current_region: str, current_value: float, loan_balance: float = 0,
            extra_cash: float = 0, ltv: float = 0.7, income: float | None = None,
            rate: float = 0.04, years: int = 30, pyeong: float = 25.7):
    return _api().tradeup(current_region, current_value, loan_balance, extra_cash,
                          ltv, income, rate, years, pyeong)


@router.get("/api/redev/zones")
def redev_zones(type: str | None = None, q: str | None = None):
    return _api().redev_zones(type, q)


@router.get("/api/redev/candidates/{region}")
def redev_candidates(region: str):
    return _api().redev_candidates(region)


@router.post("/api/redev/warm")
def redev_warm(data: dict = Body(default={})):
    return _api().redev_warm(data)


@router.get("/api/redev/stages")
def redev_stages(region: str | None = None):
    return _api().redev_stages(region)


@router.post("/api/geocode")
def geocode_ep(data: dict = Body(...)):
    return _api().geocode_ep(data)


@router.get("/api/mapconfig")
def mapconfig():
    return _api().mapconfig()


@router.get("/api/transit")
def transit_ep(sx: float, sy: float, ex: float, ey: float):
    return _api().transit_ep(sx, sy, ex, ey)


@router.get("/api/redev/value-calc")
def redev_value_calc(current_price: float, pyeong: float, presale_pyeong_price: float,
                     contribution: float, hold_months: int = 60):
    return _api().redev_value_calc(current_price, pyeong, presale_pyeong_price,
                                   contribution, hold_months)


@router.get("/api/news")
def news(topic: str | None = None):
    return _api().news(topic)


@router.get("/api/news/summary")
def news_summary(topic: str | None = None, days: int = 30):
    return _api().news_summary(topic, days)


@router.get("/api/cycle")
def cycle(region: str = "서울"):
    return _api().cycle(region)


@router.get("/api/cycle/history")
def cycle_history(region: str = "서울"):
    return _api().cycle_history(region)


@router.get("/api/complex-search")
def complex_search(q: str):
    return _api().complex_search(q)


@router.get("/api/addr-search")
def addr_search(q: str):
    return _api().addr_search(q)


@router.get("/api/complex/{region}/{name}")
def complex_detail(region: str, name: str):
    return _api().complex_detail(region, name)


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


@router.get("/api/complex/{region}/{name}/building")
def complex_building(region: str, name: str):
    return _api().complex_building(region, name)


@router.get("/api/quicksale")
def quicksale():
    return _api().quicksale()


@router.post("/api/quicksale/refresh")
def quicksale_refresh(data: dict = Body(default={})):
    return _api().quicksale_refresh(data)
