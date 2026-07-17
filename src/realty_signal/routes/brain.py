"""Brain layer API routes — outcomes · calibration · config."""

from __future__ import annotations

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from realty_signal import store
from realty_signal.brain import alert_track, calibrate, config_store, outcomes
from realty_signal.routes import deps
from realty_signal.services import market_data as md

router = APIRouter(prefix="/api/brain", tags=["brain"])


@router.get("/alerts/track-record")
def brain_alert_track_record(request: Request):
    """알림 성적표 전체 상세(Phase 5) — 관리자용, 피드백 집계 포함."""
    if not deps.is_admin(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    return {"ok": True, "track_record": md.alert_track_record(), "feedback": alert_track.feedback_summary()}


@router.get("/outcomes")
def brain_outcomes(request: Request, limit: int = 12):
    if not deps.is_admin(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    return {"ok": True, "snapshots": outcomes.list_snapshots(limit=min(limit, 52))}


@router.get("/outcomes/labels")
def brain_outcome_labels(request: Request, limit: int = 50):
    if not deps.is_admin(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    return {"ok": True, "summary": outcomes.label_summary(), "sample": outcomes.list_labels(min(limit, 100))}


@router.post("/outcomes/label")
def brain_outcome_label_run(request: Request):
    if not deps.is_admin(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    return {"ok": True, **outcomes.label_from_kb(store.load())}


@router.get("/config")
def brain_config_get(request: Request):
    if not deps.is_admin(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    meta = config_store.active_meta()
    return {"ok": True, "active": meta, "history": config_store.list_history(5)}


@router.get("/calibration")
def brain_calibration_get(request: Request):
    if not deps.is_admin(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    prop = calibrate.load_proposal()
    if not prop:
        prop = calibrate.build_proposal(store.load())
    return {"ok": True, "proposal": prop}


@router.post("/calibration/run")
def brain_calibration_run(request: Request):
    if not deps.is_admin(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    prop = calibrate.build_proposal(store.load())
    calibrate.save_proposal(prop)
    return {"ok": True, "proposal": prop}


@router.post("/calibration/apply")
def brain_calibration_apply(request: Request, data: dict = Body(...)):
    """제안 또는 body.params 를 새 config 버전으로 적용 (수동 승인)."""
    if not deps.is_admin(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    version = (data.get("version") or "").strip()
    params = data.get("params")
    note = (data.get("note") or "").strip()
    if not version:
        return JSONResponse({"ok": False, "error": "version required"}, status_code=400)
    if not isinstance(params, dict):
        sug_idx = data.get("suggestion_index")
        prop = calibrate.load_proposal() or calibrate.build_proposal(store.load())
        sugs = prop.get("suggestions") or []
        if sug_idx is None or sug_idx < 0 or sug_idx >= len(sugs):
            return JSONResponse({"ok": False, "error": "params or valid suggestion_index required"}, status_code=400)
        s = sugs[int(sug_idx)]
        base = dict(prop.get("baseline_params") or config_store.config_to_dict(config_store.active_config()))
        base[s["param"]] = s["to"]
        params = base
        note = note or s.get("reason", "")
    applied = config_store.apply_config(version, params, note=note)
    md.clear_caches()
    try:
        from realty_signal import api
        api._presale.cache_clear()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "active": applied}
