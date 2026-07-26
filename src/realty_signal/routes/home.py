"""홈 대시보드 전용 — 이번 주 변화 · 다음 할 일 · 이번 주 이슈.

셋 다 "이번 주"라는 한 가지 시점에 묶여 있어 한 파일에 둔다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from realty_signal import db
from realty_signal.routes import deps

router = APIRouter(tags=["home"])
log = logging.getLogger("realty_signal.routes.home")

NEWS_WINDOW_DAYS = 7      # 홈은 아카이브가 아니다 — 이번 주치만 본다


@router.get("/api/weekly-change")
def weekly_change(request: Request):
    """지난주 KB 대비 이번 주에 바뀐 것. ★ 관심지역을 앞으로 뺀다."""
    from realty_signal import weekly

    uid = deps.uid(request)
    favs = {f["key"] for f in db.fav_list(uid) if f["kind"] == "region"} if uid else set()
    try:
        return weekly.for_user(favs)
    except Exception as e:  # noqa: BLE001
        log.error("주간 변화 계산 실패: %s", e)
        # 실패를 '변화 없음'으로 보이게 하면 고장을 몇 주씩 못 본다
        return {"ready": False, "blocked_reason": f"주간 변화를 계산하지 못했습니다 ({e})",
                "signals": [], "movers": [], "mine": [], "rest": [], "my_movers": []}


@router.get("/api/action-plan")
def action_plan(request: Request):
    """다음에 할 일 — 텔레그램 브리핑과 같은 판단(`briefing.actions`)."""
    from realty_signal import briefing

    uid = deps.uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    try:
        return {"ok": True, **briefing.plan(uid)}
    except Exception as e:  # noqa: BLE001
        log.error("액션플랜 실패 uid=%s: %s", uid, e)
        return {"ok": False, "reason": "error", "detail": str(e), "actions": []}


@router.get("/api/weekly-issues")
def weekly_issues(days: int = NEWS_WINDOW_DAYS):
    """이번 주 부동산 이슈 — 최근 N일 뉴스 + 요약.

    뉴스 탭을 없앤 이유가 그대로 규칙이 된다: 며칠 지난 기사는 맥락이 아니라 소음이라
    창(window)을 좁게 고정하고, 항목마다 **언제 기사인지**를 같이 낸다.
    """
    from realty_signal import api as app_api

    days = max(1, min(int(days or NEWS_WINDOW_DAYS), 30))
    try:
        app_api.news()                    # 1시간 초과 시 갱신 — 목록 조회 전에 먼저
    except Exception as e:  # noqa: BLE001
        log.warning("뉴스 갱신 실패(캐시로 진행): %s", e)
    items = db.news_since(None, days, 12)
    out = {"days": days, "items": items, "count": len(items)}
    if not items:
        out["blocked_reason"] = f"최근 {days}일 안에 수집된 기사가 없습니다"
        return out
    try:
        s = app_api.news_summary(None, days)
        if s.get("enough"):
            out["summary"] = s.get("summary")
            out["mock"] = bool(s.get("mock"))
    except Exception as e:  # noqa: BLE001
        log.warning("뉴스 요약 실패: %s", e)
    return out
