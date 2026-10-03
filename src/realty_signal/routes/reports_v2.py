"""Read-only, deterministic reports and opt-in listing discovery."""

from __future__ import annotations

from hashlib import sha256
import json

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from realty_signal.routes import deps
from realty_signal.services import market_data as md
from realty_signal.services import decision_notes_v2 as notes

router = APIRouter(tags=["reports-v2"])
PRIVATE = {"Cache-Control": "private, no-store"}


def _id(payload: dict) -> str:
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                             default=str).encode()).hexdigest()


@router.get("/api/v2/regions/{region}/report")
def region_report(region: str):
    from realty_signal.services import signal_assessment

    resolved = md.region_for_ref(region)
    if resolved is None:
        raise HTTPException(404, "지역을 찾지 못했습니다.")
    df = md.signals_df()
    hit = df[df["region"] == resolved]
    if hit.empty:
        raise HTTPException(404, "지역을 찾지 못했습니다.")
    row = json.loads(hit.iloc[0].to_json(force_ascii=False))
    assessment = signal_assessment.with_previous(
        signal_assessment.build(md.kb(), row, md.signal_config()))
    report = {"schema_version": "report-v2-1", "type": "region",
              "subject": {"region": resolved, "region_id": assessment["region_id"]},
              "asof": assessment["asof"], "status": assessment["assessment_status"],
              "assessment": assessment,
              "positive": [x for x in assessment["reasons"] if x["role"] == "driver" and x.get("passing")],
              "cautions": [x for x in assessment["reasons"]
                           if x["role"] in {"limitation", "counterevidence"}
                           or (x["role"] == "driver" and not x.get("passing"))],
              "unknowns": assessment["risk_flags"],
              "next_actions": ["이 지역 매물을 내 조건으로 비교하세요.",
                               "매물의 가격과 자금 조건을 별도로 확인하세요."]}
    report["report_id"] = _id(report)
    return JSONResponse(report, headers=PRIVATE)


@router.get("/api/v2/listings/report")
def listing_report(request: Request, key: str):
    from realty_signal.routes.market import listing_analysis

    legacy = listing_analysis(request, key, stage="full")
    base = json.loads(legacy.body)
    listing = base.get("listing") or {}
    if not listing.get("key"):
        raise HTTPException(404, "매물을 찾지 못했습니다.")
    report = {"schema_version": "report-v2-1", "type": "listing",
              "subject": listing, "asof": listing.get("collected_at"),
              "status": "stale" if listing.get("stale") else "ready",
              "price": base.get("price"), "buyer_fit": base.get("buyer_fit"),
              "decision": base.get("decision"), "lines": base.get("lines"),
              "positive": base.get("pros") or [], "cautions": base.get("cautions") or [],
              "unknowns": base.get("questions") or [], "evidence": base.get("evidence") or [],
              "building": base.get("building"), "development": base.get("development"),
              "next_actions": base.get("questions") or []}
    report["report_id"] = _id(report)
    return JSONResponse(report, headers=PRIVATE)


@router.post("/api/v2/comparisons")
def comparison_report(request: Request, data: dict = Body(...)):
    from realty_signal.routes.market import listing_compare

    keys = data.get("keys") if isinstance(data, dict) else None
    if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
        raise HTTPException(422, "비교할 매물 2~3개를 선택하세요.")
    legacy = listing_compare(request, keys)
    payload = json.loads(legacy.body)
    report = {"schema_version": "report-v2-1", "type": "comparison",
              "items": payload.get("items") or [], "cautions": payload.get("warnings") or [],
              "basis": payload.get("basis"), "unknowns": payload.get("not_compared") or []}
    report["report_id"] = _id(report)
    return JSONResponse(report, headers=PRIVATE)


@router.post("/api/v2/discovery")
def discovery(request: Request, data: dict = Body(...)):
    from realty_signal import api as app_api
    from realty_signal.services import discovery_v2

    try:
        spec = discovery_v2.validate(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    allowed = deps.personal_listings_allowed(request)
    rows = app_api._build_listings(discovery_v2.KINDS, include_private=allowed) if allowed else []
    result = discovery_v2.discover(rows, spec)
    result["private_access"] = allowed
    result["source_state"] = ("forbidden" if not allowed else "ready" if rows else
                              "empty" if any(path.exists() for path in (
                                  app_api.HANBANG_FILE, app_api.QUICKSALE_FILE,
                                  app_api.CERTIFIED_FILE)) else "unavailable")
    return JSONResponse(result, headers=PRIVATE)


def _note_subject(request: Request, kind: str, key: str) -> None:
    if kind == "region" and key in md.kb().regions:
        return
    if kind == "listing":
        from realty_signal.services import property_analysis
        try:
            property_analysis.resolve(key, private_allowed=deps.personal_listings_allowed(request))
            return
        except (ValueError, PermissionError, LookupError) as exc:
            raise HTTPException(404, "매물을 찾지 못했습니다.") from exc
    raise HTTPException(422, "관심 대상을 확인해 주세요.")


@router.get("/api/v2/decision-notes")
def decision_notes_list(request: Request, subject_type: str | None = None,
                        subject_key: str | None = None):
    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "로그인이 필요합니다.")
    if subject_type is not None and subject_type not in {"region", "listing"}:
        raise HTTPException(422, "대상 유형이 올바르지 않습니다.")
    if subject_key is not None and len(subject_key) > 180:
        raise HTTPException(422, "대상 식별자가 올바르지 않습니다.")
    return JSONResponse({"notes": notes.list_for(uid, subject_type, subject_key)}, headers=PRIVATE)


@router.post("/api/v2/decision-notes")
def decision_note_create(request: Request, data: dict = Body(...)):
    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "로그인이 필요합니다.")
    kind, key = data.get("subject_type"), data.get("subject_key")
    if not isinstance(kind, str) or not isinstance(key, str) or not 1 <= len(key) <= 180:
        raise HTTPException(422, "관심 대상을 확인해 주세요.")
    _note_subject(request, kind, key)
    try:
        result = notes.create(uid, kind, key, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return JSONResponse(result, status_code=201, headers=PRIVATE)


@router.patch("/api/v2/decision-notes/{note_id}")
def decision_note_update(request: Request, note_id: int, data: dict = Body(...)):
    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "로그인이 필요합니다.")
    revision = data.get("revision")
    if type(revision) is not int or revision < 1:
        raise HTTPException(422, "수정 버전을 확인해 주세요.")
    try:
        result = notes.update(uid, note_id, revision, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if result is None:
        raise HTTPException(409, "이 기록이 이미 수정됐거나 접근할 수 없습니다.")
    return JSONResponse(result, headers=PRIVATE)


@router.delete("/api/v2/decision-notes/{note_id}")
def decision_note_delete(request: Request, note_id: int):
    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "로그인이 필요합니다.")
    if not notes.delete(uid, note_id):
        raise HTTPException(404, "기록을 찾지 못했습니다.")
    return JSONResponse({"deleted": True}, headers=PRIVATE)
