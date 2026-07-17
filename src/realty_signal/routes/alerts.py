"""Alert Engine API."""

from __future__ import annotations

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from realty_signal import db
from realty_signal.routes import deps
from realty_signal.services import market_data as md

router = APIRouter(tags=["alerts"])


@router.get("/api/alerts/prefs")
def alerts_prefs_get(request: Request):
    uid = deps.uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    from realty_signal.brain.alerts import merge_prefs
    return {"ok": True, "prefs": merge_prefs(db.alert_prefs_get(uid))}


@router.put("/api/alerts/prefs")
def alerts_prefs_put(request: Request, data: dict = Body(...)):
    uid = deps.uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    prefs = data.get("prefs") if isinstance(data.get("prefs"), dict) else data
    return {"ok": True, "prefs": db.alert_prefs_set(uid, prefs)}


@router.get("/api/alerts")
def alerts(request: Request):
    """Alert Engine v1 — 시그널 변동·고타이밍 매물·동네 diff."""
    from realty_signal.brain import alerts as alert_engine
    from realty_signal import api as app_api

    uid = deps.uid(request)
    favs = {f["key"] for f in db.fav_list(uid) if f["kind"] == "region"} if uid else set()
    log_ = db.kv_get("signal_changes") or []
    seen = db.kv_get(f"alerts_seen:{uid}") or "" if uid else ""
    prefs = db.alert_prefs_get(uid) if uid else {}
    listings = app_api._build_listings({"경매", "급매"}) if favs and prefs.get("high_timing", True) else []
    nbhd_diffs = app_api._user_nbhd_diffs(uid, favs) if uid and favs else {}
    return alert_engine.evaluate(
        favs, prefs,
        signal_changes=log_,
        signal_map=md.signal_map(),
        listings=listings,
        nbhd_diffs=nbhd_diffs,
        seen_before=seen,
    )


@router.post("/api/alerts/seen")
def alerts_seen(request: Request):
    try:
        last = str(md.kb().last_date.date())
    except Exception:
        last = ""
    db.kv_set(f"alerts_seen:{deps.uid(request)}", last)
    return {"ok": True}


@router.get("/api/alerts/track-record")
def alerts_track_record(request: Request):
    """알림 성적표 요약(Phase 5) — 로그인 유저에게 hit-rate 만 노출, 상세는 최근 5건."""
    if not deps.uid(request):
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    tr = dict(md.alert_track_record())
    tr["recent"] = (tr.get("recent") or [])[:5]
    return {"ok": True, "track_record": tr}


@router.post("/api/alerts/feedback")
def alerts_feedback(request: Request, data: dict = Body(...)):
    """알림 유용성 👍/👎 (Phase 5, 선택 기능) — event_log(alert_feedback) 적재."""
    from realty_signal.brain.alert_track import FEEDBACK_KINDS

    uid = deps.uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    kind = (data.get("kind") or "").strip()
    if kind not in FEEDBACK_KINDS:
        return JSONResponse({"ok": False, "error": "invalid kind"}, status_code=400)
    props = {"kind": kind, "useful": bool(data.get("useful"))}
    region = (data.get("region") or "").strip()
    if region:
        props["region"] = region
    logged = db.event_log(uid, "alert_feedback", props)
    return {"ok": True, "logged": logged}
