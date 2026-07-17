"""재건축 · 정비사업."""

from __future__ import annotations

from fastapi import APIRouter, Body

router = APIRouter(tags=["redev"])


def _api():
    from realty_signal import api as app_api
    return app_api


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


@router.get("/api/redev/value-calc")
def redev_value_calc(current_price: float, pyeong: float, presale_pyeong_price: float,
                     contribution: float, hold_months: int = 60):
    return _api().redev_value_calc(current_price, pyeong, presale_pyeong_price,
                                   contribution, hold_months)
