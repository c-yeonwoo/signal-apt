"""홈 대시보드 전용 — 이번 주 변화 · 다음 할 일 · 이번 주 이슈.

셋 다 "이번 주"라는 한 가지 시점에 묶여 있어 한 파일에 둔다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Request
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
        out = weekly.for_user(favs)
        # 배너가 "왜 멈췄는지" 를 말하려면 수집 건강 상태가 같이 와야 한다
        try:
            from realty_signal import api as app_api
            out["kb_fetch"] = app_api.kb_fetch_health()
        except Exception:  # noqa: BLE001 — 부가 정보라 실패해도 본문은 낸다
            pass
        # 자리를 비운 동안 놓친 것 — '이번 주 변화' 는 직전 1주만 보므로 따로 계산한다
        if uid:
            out["comeback"] = _comeback_for(uid, favs, out.get("as_of"))
        return out
    except Exception as e:  # noqa: BLE001
        log.error("주간 변화 계산 실패: %s", e)
        # 실패를 '변화 없음'으로 보이게 하면 고장을 몇 주씩 못 본다
        return {"ready": False, "blocked_reason": f"주간 변화를 계산하지 못했습니다 ({e})",
                "signals": [], "movers": [], "mine": [], "rest": [], "my_movers": []}


def _comeback_for(uid: int, favs: set[str], as_of: str | None) -> dict:
    """복귀 브리핑을 계산한다. 기준점 이동은 브라우저의 열람 확인 뒤에 별도 처리한다."""
    from realty_signal.services import comeback, market_data as md

    if not as_of:
        return {"ready": False, "reason": "no_as_of"}
    try:
        kb_dates = sorted({str(d.date()) for d in md.kb().long["date"].unique()})
        out = comeback.compute(uid, favs, as_of, kb_dates)
    except Exception as e:  # noqa: BLE001
        log.error("복귀 브리핑 실패 uid=%s: %s", uid, e)
        return {"ready": False, "reason": "error", "detail": str(e)}
    return out


@router.post("/api/weekly-change/seen")
def weekly_change_seen(request: Request, data: dict | None = Body(default=None)):
    """브라우저가 주간 카드를 실제로 본 뒤에만 복귀 기준점을 전진한다."""
    from realty_signal.services import comeback, market_data as md

    uid = deps.uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    as_of = str((data or {}).get("as_of") or "")
    current = str(md.kb().last_date.date())
    if not as_of or as_of != current:
        # 화면이 그려진 뒤 데이터가 갱신됐으면, 새 주차까지 보았다고 처리하면 안 된다.
        return JSONResponse({"ok": False, "reason": "stale_response"}, status_code=409)
    comeback.mark_seen(uid, as_of)
    return {"ok": True, "as_of": as_of}


@router.get("/api/budget-watch")
def budget_watch(request: Request):
    """내 예산 안에 새로 들어온 매물 — 진짜 굿뉴스.

    '새로 들어옴' 을 신규 등장 / 가격 인하 / 매수력 상향으로 **구분해서** 낸다.
    뭉개면 거짓이 된다.
    """
    from realty_signal import api as app_api
    from realty_signal.services import budget_watch as bw

    uid = deps.uid(request)
    if not uid:
        return JSONResponse({"ready": False, "reason": "login_required"}, status_code=401)
    try:
        profile = db.profile_get(uid) or {}
        budget = (profile.get("매수력") or {}).get("최대매수가")
        if not budget:
            return {"ready": False, "reason": "no_budget",
                    "message": "매수력을 확정하면 예산 안에 들어온 매물을 알려드립니다."}
        rows = app_api._build_listings({"경매", "급매", "찐매물", "청약", "재건축"})
        return bw.compute(uid, rows, float(budget))
    except Exception as e:  # noqa: BLE001
        log.error("예산 변화 계산 실패 uid=%s: %s", uid, e)
        return {"ready": False, "reason": "error", "detail": str(e)}


@router.post("/api/budget-watch/seen")
def budget_watch_seen(request: Request):
    """예산 매물 카드가 보인 뒤에만 다음 비교 기준점을 저장한다."""
    from realty_signal import api as app_api
    from realty_signal.services import budget_watch as bw

    uid = deps.uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    profile = db.profile_get(uid) or {}
    budget = (profile.get("매수력") or {}).get("최대매수가")
    if not budget:
        return {"ok": False, "reason": "no_budget"}
    rows = app_api._build_listings({"경매", "급매", "찐매물", "청약", "재건축"})
    bw.mark_seen(uid, rows, float(budget))
    return {"ok": True}


@router.get("/api/complex-watch")
def complex_watch(request: Request):
    """관심단지가 움직였나 — 지역 등급이 아니라 **내가 찍어 둔 단지**.

    실거래 캐시만 읽는다(네트워크 호출 없음). 캐시가 비어 있으면 '데이터 없음' 으로
    솔직히 답하고, 채우는 건 주간 워밍(`warm_favorite_complexes`)과 단지 상세 조회가 한다 —
    홈 카드가 국토부 API 를 관심단지 수만큼 때리게 두면 홈이 느려진다.
    """
    from realty_signal.services import complex_watch as cw

    uid = deps.uid(request)
    if not uid:
        return JSONResponse({"ready": False, "reason": "login_required"}, status_code=401)

    favs = cw.favorites_of(uid)
    if not favs:
        return {"ready": False, "reason": "no_favorites",
                "message": "관심단지를 ★ 로 등록하면 그 단지의 실거래 변화를 알려드립니다."}
    try:
        budget = ((db.profile_get(uid) or {}).get("매수력") or {}).get("최대매수가")
        out = cw.compute(uid, favs, cw.cache_loader(),
                         budget=float(budget) if budget else None)
        out.pop("_snaps", None)       # 기준점은 열람 확인 API 에서만 전진한다
        return out
    except Exception as e:  # noqa: BLE001
        log.error("관심단지 변화 계산 실패 uid=%s: %s", uid, e)
        return {"ready": False, "reason": "error", "detail": str(e)}


@router.post("/api/complex-watch/seen")
def complex_watch_seen(request: Request):
    """관심단지 카드가 보인 뒤에만 실거래 비교 기준점을 저장한다."""
    from realty_signal.services import complex_watch as cw

    uid = deps.uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    favs = cw.favorites_of(uid)
    if not favs:
        return {"ok": False, "reason": "no_favorites"}
    budget = ((db.profile_get(uid) or {}).get("매수력") or {}).get("최대매수가")
    out = cw.compute(uid, favs, cw.cache_loader(),
                     budget=float(budget) if budget else None)
    cw.mark_seen(uid, out.get("_snaps", {}))
    return {"ok": True}


@router.get("/api/threshold-watch")
def threshold_watch(request: Request):
    """다음 주에 뒤집힐 수 있는 지역 — **예측이 아니라 임계까지의 거리.**

    등급이 계단 함수라 몇 달 정지했다가 한꺼번에 뒤집힌다. 그 '남은 거리'를 보여준다.
    as_of 단위로 캐시 — 117개 지역 × 지표를 매 조회마다 다시 재지 않는다.
    """
    from realty_signal.services import market_data as md, threshold_watch as tw

    uid = deps.uid(request)
    favs = {f["key"] for f in db.fav_list(uid) if f["kind"] == "region"} if uid else set()
    try:
        kb = md.kb()
        as_of = str(kb.last_date.date())
        # 스키마가 바뀌면 옛 캐시가 조용히 살아남아 ★ 가 안 붙는 식으로 어긋난다 → 버전을 키에 넣는다
        key = f"threshold_watch:v{tw.CACHE_VER}:{as_of}"
        cached = db.kv_get(key)
        if not (isinstance(cached, dict) and cached.get("as_of") == as_of):
            cached = tw.compute(kb, md.signal_config())
            db.kv_set(key, cached)
        # ★ 표시는 사용자마다 다르므로 캐시 뒤에 다시 입힌다
        items = []
        for x in cached.get("items", []):
            mine_regions = [r for r in (x.get("members") or [x.get("scope")]) if r in favs]
            items.append({**x, "mine": bool(mine_regions), "my_regions": mine_regions})
        items.sort(key=lambda x: (not x["mine"], x["weeks_away"]))
        return {**cached, "items": items, "mine": [x for x in items if x["mine"]],
                "count": len(items)}
    except Exception as e:  # noqa: BLE001
        log.error("임계 근접 계산 실패: %s", e)
        return {"as_of": None, "items": [], "mine": [], "count": 0,
                "blocked_reason": f"임계 근접을 계산하지 못했습니다 ({e})"}


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
