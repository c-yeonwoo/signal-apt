"""지도·뉴스·규제·정책 · 사이클."""

from __future__ import annotations

from fastapi import APIRouter, Body, Request

router = APIRouter(tags=["geo"])


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


@router.post("/api/geocode")
def geocode_ep(data: dict = Body(...)):
    return _api().geocode_ep(data)


@router.get("/api/mapconfig")
def mapconfig():
    return _api().mapconfig()


@router.get("/api/transit")
def transit_ep(sx: float, sy: float, ex: float, ey: float):
    return _api().transit_ep(sx, sy, ex, ey)


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
