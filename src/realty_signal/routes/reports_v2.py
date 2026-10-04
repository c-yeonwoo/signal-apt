"""Read-only, deterministic reports and opt-in listing discovery."""

from __future__ import annotations

from hashlib import sha256
import json

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from realty_signal import config
from realty_signal.routes import deps
from realty_signal.services import market_data as md
from realty_signal.services import decision_notes_v2 as notes
from realty_signal.services import report_snapshots_v2 as snapshots

router = APIRouter(tags=["reports-v2"])
PRIVATE = {"Cache-Control": "private, no-store"}

_DISCOVERY_INPUT_ERRORS = {
    "invalid_conditions": "검색 조건을 다시 입력해 주세요.",
    "unsupported_condition": "지원하지 않는 검색 조건이 있습니다. 조건을 다시 선택해 주세요.",
    "invalid_condition_value": "호가·면적·방 개수·통근시간 등 숫자 조건을 다시 확인해 주세요.",
    "invalid_move_in_date": "입주 필요일을 다시 선택해 주세요.",
    "invalid_region": "지역을 목록에서 다시 선택해 주세요.",
    "invalid_region_code": "지역을 목록에서 다시 선택해 주세요.",
    "conflicting_region_conditions": "지역은 필수 또는 선호 중 하나만 선택해 주세요.",
    "invalid_priority": "중요한 선호 조건을 다시 선택해 주세요.",
    "invalid_limit": "표시할 후보 수를 다시 확인해 주세요.",
    "invalid_cursor": "검색 조건이 바뀌었습니다. 처음부터 다시 검색해 주세요.",
}
_NOTE_INPUT_ERRORS = {
    "invalid_note": "판단 기록을 다시 입력해 주세요.",
    "invalid_thesis": "관심 이유를 1~500자로 입력해 주세요.",
    "invalid_counter_condition": "다시 생각할 조건을 1~500자로 입력해 주세요.",
    "invalid_horizon": "복기 시점을 다시 선택해 주세요.",
    "invalid_report_id": "연결된 리포트를 확인할 수 없습니다. 리포트를 다시 열어 주세요.",
}


def _input_error(exc: ValueError, messages: dict[str, str]) -> str:
    """Only expose reviewed validation messages, never an arbitrary exception body."""
    return messages.get(str(exc), "입력값을 확인하고 다시 시도해 주세요.")


def _id(payload: dict) -> str:
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                             default=str).encode()).hexdigest()


def _require_feature(name: str) -> None:
    if not config.rollout_flags()[name]:
        raise HTTPException(503, "이 기능은 잠시 중지됐습니다. 기존 탐색 화면을 이용해 주세요.")


@router.get("/api/v2/regions/{region}/report")
def region_report(region: str):
    _require_feature("report_v2_enabled")
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


def _listing_report(request: Request, key: str, *, cache_only: bool) -> JSONResponse:
    _require_feature("report_v2_enabled")
    from realty_signal.routes.market import listing_analysis

    legacy = listing_analysis(request, key, stage="full", cache_only=cache_only)
    base = json.loads(legacy.body)
    listing = base.get("listing") or {}
    if not listing.get("key"):
        raise HTTPException(404, "매물을 찾지 못했습니다.")
    report = {"schema_version": "report-v2-1", "type": "listing",
              "subject": listing, "asof": listing.get("collected_at"),
              "status": ("stale" if listing.get("stale") else
                         "partial" if base.get("partial_failures") else "ready"),
              "price": base.get("price"), "buyer_fit": base.get("buyer_fit"),
              "decision": base.get("decision"), "lines": base.get("lines"),
              "positive": base.get("pros") or [], "cautions": base.get("cautions") or [],
              "unknowns": base.get("questions") or [], "evidence": base.get("evidence") or [],
              "building": base.get("building"), "development": base.get("development"),
              "partial_failures": base.get("partial_failures") or [],
              "next_actions": base.get("questions") or []}
    report["report_id"] = _id(report)
    return JSONResponse(report, headers=PRIVATE)


@router.get("/api/v2/listings/report")
def listing_report(request: Request, key: str):
    """Fast, cache-only read; missing trade evidence remains explicit."""
    return _listing_report(request, key, cache_only=True)


@router.post("/api/v2/listings/report-enrich")
def listing_report_enrich(request: Request, data: dict = Body(...)):
    """User-requested trade refresh; the normal report GET never starts source I/O."""
    key = data.get("key") if isinstance(data, dict) else None
    if not isinstance(key, str):
        raise HTTPException(422, "매물 식별자가 올바르지 않습니다.")
    return _listing_report(request, key, cache_only=False)


@router.post("/api/v2/report-snapshots")
def report_snapshot_save(request: Request, data: dict = Body(...)):
    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "로그인이 필요합니다.")
    kind = data.get("type", "listing")
    key = data.get("region") if kind == "region" else data.get("key")
    keys = data.get("keys") if kind == "comparison" else None
    expected = data.get("report_id")
    valid_keys = (isinstance(keys, list) and len(keys) in (2, 3)
                  and all(isinstance(item, str) and 1 <= len(item) <= 180 and ":" in item for item in keys)
                  and len(set(keys)) == len(keys))
    if (not isinstance(kind, str) or kind not in {"listing", "region", "comparison"}
            or (kind != "comparison" and (not isinstance(key, str) or not 1 <= len(key) <= 180
                                              or (kind == "listing" and ":" not in key)))
            or (kind == "comparison" and not valid_keys)
            or not isinstance(expected, str) or len(expected) != 64
            or any(ch not in "0123456789abcdef" for ch in expected)):
        raise HTTPException(422, "저장할 리포트를 확인해 주세요.")
    if kind == "listing" and key.split(":", 1)[0] in snapshots.PRIVATE_KINDS and not deps.personal_listings_allowed(request):
        raise HTTPException(403, "개인 매물 접근권이 필요합니다.")
    current = json.loads((region_report(key) if kind == "region" else
                          comparison_report(request, {"keys": keys}) if kind == "comparison" else
                          listing_report(request, key)).body)
    if current["report_id"] != expected:
        raise HTTPException(409, "매물 자료나 내 조건이 바뀌었습니다. 리포트를 다시 열어 주세요.")
    try:
        saved = snapshots.save(uid, current)
    except ValueError as exc:
        raise HTTPException(413, "리포트가 너무 커서 저장하지 못했습니다.") from exc
    return JSONResponse(saved, status_code=201, headers=PRIVATE)


@router.get("/api/v2/report-snapshots")
def report_snapshots_list(request: Request, key: str | None = None, cursor: str | None = None):
    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "로그인이 필요합니다.")
    if key is not None and (not 1 <= len(key) <= 180 or ":" not in key):
        raise HTTPException(422, "매물 식별자가 올바르지 않습니다.")
    try:
        page = snapshots.list_page(uid, key=key,
                                   private_allowed=deps.personal_listings_allowed(request), cursor=cursor)
    except ValueError as exc:
        raise HTTPException(422, "저장본 목록 위치가 올바르지 않습니다. 목록을 처음부터 다시 열어 주세요.") from exc
    return JSONResponse(page, headers=PRIVATE)


@router.get("/api/v2/report-snapshots/{report_id}")
def report_snapshot_get(request: Request, report_id: str):
    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "로그인이 필요합니다.")
    if len(report_id) != 64 or any(ch not in "0123456789abcdef" for ch in report_id):
        raise HTTPException(404, "저장본을 찾지 못했습니다.")
    result = snapshots.get(uid, report_id, private_allowed=deps.personal_listings_allowed(request))
    if result is None:
        raise HTTPException(404, "저장본을 찾지 못했습니다.")
    return JSONResponse(result, headers=PRIVATE)


@router.delete("/api/v2/report-snapshots/{report_id}")
def report_snapshot_delete(request: Request, report_id: str):
    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "로그인이 필요합니다.")
    if len(report_id) != 64 or any(ch not in "0123456789abcdef" for ch in report_id):
        raise HTTPException(404, "저장본을 찾지 못했습니다.")
    if not snapshots.delete(uid, report_id):
        raise HTTPException(404, "저장본을 찾지 못했습니다.")
    return JSONResponse({"deleted": True}, headers=PRIVATE)


@router.post("/api/v2/comparisons")
def comparison_report(request: Request, data: dict = Body(...)):
    _require_feature("report_v2_enabled")
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


@router.post("/api/v2/reports/{report_id}/explanations")
def explanation_request(request: Request, report_id: str, data: dict = Body(...)):
    """Explicit paid explanation request against the current authorized report."""
    _require_feature("contextual_explanations_enabled")
    from realty_signal.services import report_narrative as narrative

    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "로그인이 필요합니다.")
    if (len(report_id) != 64 or any(ch not in "0123456789abcdef" for ch in report_id)
            or not isinstance(data, dict)):
        raise HTTPException(422, "리포트 요청을 확인해 주세요.")
    kind = data.get("type")
    key = data.get("key")
    keys = data.get("keys")
    if kind == "region" and isinstance(key, str) and 1 <= len(key) <= 180:
        current = json.loads(region_report(key).body)
        private_source = False
    elif kind == "listing" and isinstance(key, str) and 1 <= len(key) <= 180 and ":" in key:
        private_source = key.split(":", 1)[0] in snapshots.PRIVATE_LISTING_KINDS
        if private_source and not deps.personal_listings_allowed(request):
            raise HTTPException(403, "개인 매물 접근권이 필요합니다.")
        current = json.loads(listing_report(request, key).body)
    elif (kind == "comparison" and isinstance(keys, list) and len(keys) in (2, 3)
          and len(set(str(item) for item in keys)) == len(keys)
          and all(isinstance(item, str) and 1 <= len(item) <= 180 and ":" in item for item in keys)):
        private_source = any(item.split(":", 1)[0] in snapshots.PRIVATE_LISTING_KINDS for item in keys)
        if private_source and not deps.personal_listings_allowed(request):
            raise HTTPException(403, "개인 매물 접근권이 필요합니다.")
        current = json.loads(comparison_report(request, {"keys": keys}).body)
    else:
        raise HTTPException(422, "리포트 대상을 확인해 주세요.")
    if current["report_id"] != report_id:
        raise HTTPException(409, "근거가 바뀌었습니다. 리포트를 다시 열어 주세요.")
    try:
        result, status = narrative.enqueue(uid, current, data.get("mode"), data.get("question"),
                                           private_source=private_source)
    except ValueError as exc:
        raise HTTPException(422, "설명 요청을 확인해 주세요.") from exc
    except RuntimeError as exc:
        raise HTTPException(429, "오늘 요청 가능 횟수에 도달했습니다.") from exc
    return JSONResponse(result, status_code=status, headers=PRIVATE)


@router.get("/api/v2/report-jobs/{job_id}")
def explanation_job(request: Request, job_id: str):
    from realty_signal.services import report_narrative as narrative

    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "로그인이 필요합니다.")
    result = narrative.get(uid, job_id, private_allowed=deps.personal_listings_allowed(request))
    if result is None:
        raise HTTPException(404, "설명 작업을 찾지 못했습니다.")
    return JSONResponse(result, headers=PRIVATE)


def _discovery_regions() -> list[dict]:
    """Only offer current, uniquely identified KB districts for listing filters."""
    from realty_signal.services.signal_assessment import INCHEON_RETIRED_CODES

    source = md.kb()
    if not source.identity_verified:
        return []
    grouped = {}
    for name, raw_code in (source.codes or {}).items():
        code = str(raw_code)[:5]
        if (name not in source.regions or len(code) != 5 or not code.isdigit()
                or code[2:] == "000" or code in INCHEON_RETIRED_CODES):
            continue
        grouped.setdefault(code, set()).add(name)
    options = []
    for code, names in grouped.items():
        if len(names) != 1:
            continue
        name = next(iter(names))
        source_sido = md.SIDO_LABELS.get(code[:2])
        if source_sido:
            options.append({"code": code, "name": name, "sido": source_sido,
                            "label": f"{source_sido} · {name}"})
    options.sort(key=lambda item: (item["label"], item["code"]))
    return options


@router.get("/api/v2/discovery/regions")
def discovery_regions():
    _require_feature("discovery_v2_enabled")
    options = _discovery_regions()
    return JSONResponse({"status": "ready" if options else "unverified", "regions": options}, headers=PRIVATE)


def _canonical_discovery_region(spec: dict) -> dict:
    if not any(spec.get(key) for key in ("region", "prefer_region", "region_code", "prefer_region_code")):
        return spec
    options = _discovery_regions()
    by_code = {option["code"]: option for option in options}
    for key in ("region_code", "prefer_region_code"):
        if key in spec and spec[key] not in by_code:
            raise HTTPException(422, "현재 지역 코드를 확인할 수 없습니다. 지역을 다시 선택해 주세요.")
    for old, new in (("region", "region_code"), ("prefer_region", "prefer_region_code")):
        if not spec.get(old):
            continue
        matching = [option for option in options if option["name"] == spec[old]]
        same_tail = [option for option in options if option["name"].split()[-1] == spec[old]]
        if len(matching) != 1 or len(same_tail) > 1:
            raise HTTPException(422, "지역 이름만으로는 확인할 수 없습니다. 목록에서 지역을 다시 선택해 주세요.")
        spec[new] = matching[0]["code"]
        del spec[old]
    return spec


@router.post("/api/v2/discovery")
def discovery(request: Request, data: dict = Body(...)):
    _require_feature("discovery_v2_enabled")
    from realty_signal import api as app_api
    from realty_signal.services import discovery_v2
    from realty_signal.services import discovery_finance
    from realty_signal.services import discovery_occupancy
    from realty_signal.services import discovery_commute

    try:
        spec = _canonical_discovery_region(discovery_v2.validate(data))
    except ValueError as exc:
        raise HTTPException(422, _input_error(exc, _DISCOVERY_INPUT_ERRORS)) from exc
    allowed = deps.personal_listings_allowed(request)
    rows, sources = [], []
    if allowed:
        for kind, read_cache in (("일반매물", app_api.hanbang),
                                 ("급매", app_api.quicksale),
                                 ("찐매물", app_api.certified)):
            cache = read_cache()
            state = cache.get("state") or "never_scanned"
            sources.append({"kind": kind, "state": state, "count": 0,
                            "regions": cache.get("regions") or [],
                            "expected_count": len(cache.get("listings") or []),
                            "last_success_at": cache.get("last_success_at"),
                            "failed_requests": (cache.get("refresh") or {}).get("failed_requests") or 0,
                            "limited_regions": (cache.get("refresh") or {}).get("limited_regions") or []})
        readable = {source["kind"] for source in sources
                    if source["state"] not in {"failed", "never_scanned", "identity_unverified"}}
        by_kind = {kind: [] for kind in readable}
        if readable:
            try:
                combined = app_api._build_listings(readable, include_private=True)
                for row in combined:
                    if row.get("유형") in by_kind:
                        by_kind[row["유형"]].append(row)
            except Exception:  # noqa: BLE001 — 한 원천의 손상 시 나머지 원천을 독립적으로 재시도한다.
                for kind in readable:
                    try:
                        by_kind[kind] = app_api._build_listings({kind}, include_private=True)
                    except Exception:  # noqa: BLE001
                        next(source for source in sources if source["kind"] == kind)["state"] = "failed"
        for source in sources:
            kind, state = source["kind"], source["state"]
            source_rows = by_kind.get(kind, []) if state != "failed" else []
            if source["expected_count"] and not source_rows:
                state = source["state"] = "failed"
            if state in {"stale", "stale_failed", "unverified"}:
                source_rows = [{**row, "stale": True} for row in source_rows]
            rows.extend(source_rows)
            source["count"] = len(source_rows)
            del source["expected_count"]
            if not source["regions"]:
                source["regions"] = sorted({row.get("지역") for row in source_rows if row.get("지역")})
    from realty_signal.services.listing_inventory import collapse
    from realty_signal.services.listing_prices import attach as attach_prices
    rows = attach_prices(collapse(rows))
    scenario = None
    profile = None
    profile_failed = False
    if allowed and ({"max_monthly_manwon", "max_commute_minutes"} & spec.keys()):
        from realty_signal import db
        uid = deps.uid(request)
        try:
            profile = db.profile_get(uid) if uid else None
        except Exception:  # noqa: BLE001 — 자금 프로필 장애가 매물 검색 전체를 막지 않게 한다.
            profile = None
            profile_failed = True
    if allowed and "max_monthly_manwon" in spec:
        scenario = discovery_finance.FinanceScenario(profile, sido_of=app_api._sido_of)
        if profile_failed:
            scenario.status = "profile_unavailable"
    commute_context = discovery_commute.work_context(profile) if allowed and "max_commute_minutes" in spec else None
    if commute_context and profile_failed:
        commute_context = {"status": "profile_unavailable"}
    fingerprint = {"sources": sources}
    if scenario:
        fingerprint.update(finance=scenario.fingerprint, finance_status=scenario.status)
    if commute_context:
        fingerprint.update(commute=commute_context.get("identity"), commute_status=commute_context["status"])
    region_hint = None
    if selected_code := spec.get("region_code"):
        region_hint = next(({"name": option["name"], "sido": option["sido"]}
                            for option in _discovery_regions() if option["code"] == selected_code), None)
    occupancy = {}
    if allowed and "move_in_by" in spec and (uid := deps.uid(request)):
        occupancy = discovery_occupancy.cached(uid, {row["key"]: row.get("fetched_at") or 0
            for row in rows if row.get("유형") == "일반매물" and row.get("key")})
    commute = {}
    if allowed and "max_commute_minutes" in spec and (uid := deps.uid(request)) and commute_context["status"] == "ready":
        commute = discovery_commute.cached(uid, rows, profile)
    try:
        result = discovery_v2.discover(rows, spec, source_fingerprint=fingerprint,
                                       finance_of=scenario.for_row if scenario else None,
                                       region_hint=region_hint, occupancy_by_key=occupancy,
                                       commute_by_key=commute)
    except ValueError as exc:
        if str(exc) == "stale_cursor":
            raise HTTPException(409, "수집 결과가 바뀌었습니다. 처음부터 다시 검색하세요.") from exc
        raise HTTPException(422, _input_error(exc, _DISCOVERY_INPUT_ERRORS)) from exc
    result["private_access"] = allowed
    states = [source["state"] for source in sources]
    degraded = any(state not in {"ready", "empty"} for state in states)
    result["source_state"] = ("forbidden" if not allowed else
                              "unavailable" if all(state == "never_scanned" for state in states) else
                              "partial" if rows and degraded else "ready" if rows else
                              "partial_empty" if degraded else "empty")
    result["sources"] = sources
    if scenario:
        result["finance_context"] = {"status": scenario.status,
                                     "policy_status": scenario.policy["status"],
                                     "policy_declared_asof": scenario.policy["declared_asof"]}
    if commute_context:
        result["commute_context"] = {"status": commute_context["status"]}
    result["coverage"] = {"scope": "collected_sample", "regions": sorted({region for source in sources
                                                                            for region in source["regions"]}),
                          "source_count": len(sources)}
    return JSONResponse(result, headers=PRIVATE)


@router.post("/api/v2/discovery/occupancy")
def discovery_occupancy_lookup(request: Request, data: dict = Body(...)):
    """사용자가 고른 한방 매물 하나의 입주 가능일만 상세 조회한다."""
    _require_feature("discovery_v2_enabled")
    from realty_signal.services import discovery_occupancy, property_analysis

    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "로그인이 필요합니다.")
    if not deps.personal_listings_allowed(request):
        raise HTTPException(403, "개인용 매물 접근 권한이 없습니다.")
    try:
        row = property_analysis.resolve(data.get("key"), private_allowed=True)
    except ValueError as exc:
        raise HTTPException(422, "매물 키를 확인해 주세요.") from exc
    except LookupError as exc:
        raise HTTPException(404, "현재 수집분에서 매물을 찾지 못했습니다.") from exc
    if row.get("유형") != "일반매물" or not (source_id := (row.get("ref") or {}).get("hanbang_id")):
        raise HTTPException(422, "한방 일반 매물만 입주일 상세 조회가 가능합니다.")
    try:
        value = discovery_occupancy.lookup(uid, row["key"], str(source_id),
                                            fresh_after=row.get("fetched_at") or 0)
    except Exception as exc:  # noqa: BLE001 — 외부 상세 장애를 입주 가능일 부재로 단정하지 않는다.
        raise HTTPException(502, "입주 가능일 상세 원천을 확인하지 못했습니다.") from exc
    return JSONResponse(value, headers=PRIVATE)


@router.post("/api/v2/discovery/commute")
def discovery_commute_lookup(request: Request, data: dict = Body(...)):
    """현재 매물 한 건과 저장 직장의 참고 경로만 명시 요청으로 조회한다."""
    _require_feature("discovery_v2_enabled")
    from realty_signal import db
    from realty_signal.services import discovery_commute, discovery_v2, property_analysis

    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "로그인이 필요합니다.")
    if not deps.personal_listings_allowed(request):
        raise HTTPException(403, "개인용 매물 접근 권한이 없습니다.")
    try:
        row = property_analysis.resolve(data.get("key"), private_allowed=True)
    except ValueError as exc:
        raise HTTPException(422, "매물 키를 확인해 주세요.") from exc
    except LookupError as exc:
        raise HTTPException(404, "현재 수집분에서 매물을 찾지 못했습니다.") from exc
    if row.get("유형") not in discovery_v2.KINDS:
        raise HTTPException(422, "일반·급매·찐매물만 통근 경로를 확인할 수 있습니다.")
    profile = db.profile_get(uid)
    try:
        value = discovery_commute.lookup(uid, row, profile)
    except ValueError as exc:
        raise HTTPException(422, "저장된 직장 위치 또는 경로 API 설정을 확인해 주세요.") from exc
    return JSONResponse(discovery_commute.public(value), headers=PRIVATE)


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
                        subject_key: str | None = None, cursor: str | None = None):
    uid = deps.uid(request)
    if not uid:
        raise HTTPException(401, "로그인이 필요합니다.")
    if subject_type is not None and subject_type not in {"region", "listing"}:
        raise HTTPException(422, "대상 유형이 올바르지 않습니다.")
    if subject_key is not None and len(subject_key) > 180:
        raise HTTPException(422, "대상 식별자가 올바르지 않습니다.")
    try:
        page = notes.list_page(uid, subject_type, subject_key, cursor)
    except ValueError as exc:
        raise HTTPException(422, "목록 위치를 확인해 주세요.") from exc
    return JSONResponse(page, headers=PRIVATE)


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
        raise HTTPException(422, _input_error(exc, _NOTE_INPUT_ERRORS)) from exc
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
        raise HTTPException(422, _input_error(exc, _NOTE_INPUT_ERRORS)) from exc
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
