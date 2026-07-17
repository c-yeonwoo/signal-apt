"""FastAPI 백엔드 — 시그널 테이블 + 지역별 시계열을 제공하고 대시보드를 서빙."""

from __future__ import annotations

import hashlib
import json
import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from realty_signal import auction, auth, config, db, store
from realty_signal.signals.engine import SignalConfig, evaluate

log = logging.getLogger("realty_signal")

# 인증 게이트: /api/* 는 세션 필수(아래 경로 prefix 만 예외). 그 외(/, 정적)는 허용.
_OPEN_PREFIXES = ("/api/auth/",)


def _uid(request: Request):
    u = auth.current_user(request.cookies.get(auth.COOKIE))
    return u["id"] if u else None


def _is_opus_user(request: Request) -> bool:
    """이 계정이 Opus 4.8 프리미엄 티어인지(화이트리스트). 그 외는 저가 모델."""
    u = auth.current_user(request.cookies.get(auth.COOKIE))
    return bool(u) and (u.get("email") or "").lower() in config.opus_whitelist()


def _is_admin(request: Request) -> bool:
    """관리자(데이터 운영) 계정인지 — ADMIN_EMAILS 화이트리스트."""
    u = auth.current_user(request.cookies.get(auth.COOKIE))
    return bool(u) and (u.get("email") or "").lower() in config.admin_whitelist()


def _fav_context(uid: int) -> dict:
    fav = db.fav_list(uid)
    return {
        "관심지역": [f["key"] for f in fav if f["kind"] == "region"],
        "관심단지": [(f.get("label") or f["key"]).split("|")[-1] for f in fav if f["kind"] == "complex"],
    }


def _usage_status(uid: int, kind: str, *, unlimited: bool) -> dict:
    """kind=nick|report. unlimited면 한도 없음."""
    used = db.usage_get(uid, kind)
    if unlimited:
        return {"kind": kind, "used": used, "limit": None, "remaining": None, "unlimited": True}
    limit = config.nick_weekly_limit() if kind == "nick" else config.report_weekly_limit()
    return {
        "kind": kind, "used": used, "limit": limit,
        "remaining": max(0, limit - used), "unlimited": False,
    }


def _usage_allow(uid: int, kind: str, *, unlimited: bool) -> tuple[bool, dict]:
    st = _usage_status(uid, kind, unlimited=unlimited)
    if st["unlimited"]:
        return True, st
    return st["remaining"] > 0, st


_REFRESH_EVERY_DAYS = 7   # KB는 주간 발표 → 7일 주기로 신선도 점검


def _data_age_days() -> float | None:
    """현재 캐시 데이터의 경과일(없으면 None)."""
    try:
        last = _kb().last_date
        import datetime as _dt
        return (_dt.datetime.now() - last.to_pydatetime()).total_seconds() / 86400
    except Exception:
        return None


def _do_refresh() -> dict:
    """KB 재수집 + 파생 캐시 무효화. /api/refresh·스케줄러 공용."""
    import time
    kb = store.fetch()
    db.kv_set("last_kb_fetch", time.time())   # 마지막 수집 시각(스케줄러 기준)
    _kb.cache_clear()
    _signals_df.cache_clear()
    _regime.cache_clear()
    _presale.cache_clear()
    _backtest.cache_clear()
    changed = _snapshot_signals(str(kb.last_date.date()))
    return {"ok": True, "last_date": str(kb.last_date.date()), "regions": len(kb.regions),
            "signal_changes": len(changed)}


def _snapshot_signals(asof: str) -> list[dict]:
    """현재 시그널을 직전 스냅샷과 비교 → 변동을 로그에 적재하고 스냅샷 갱신.

    변동 로그(signal_changes)는 전역(비개인화). /api/alerts 에서 사용자 즐겨찾기로 필터.
    """
    from realty_signal.brain import snapshots as snap

    try:
        cur = _signal_map()
    except Exception:
        return []
    prev = snap.load().get("signals") or {}
    changes = []
    if prev:  # 최초 1회는 스냅샷만 저장(가짜 변동 방지)
        for region, sig in cur.items():
            old = prev.get(region)
            if old and old != sig:
                changes.append({"region": region, "from": old, "to": sig, "date": asof})
    if changes:
        log_ = db.kv_get("signal_changes") or []
        db.kv_set("signal_changes", (changes + log_)[:300])  # 최신순, 최대 300건
    snap.save(cur, asof)
    try:
        from realty_signal.brain import outcomes
        recs = json.loads(_signals_df().to_json(orient="records", force_ascii=False))
        outcomes.append_region_snapshot(asof, recs)
    except Exception as e:  # noqa: BLE001
        log.warning("outcome snapshot skip: %s", e)
    return changes


async def _auto_refresh_loop():
    """데이터 신선도 자동 유지 — 마지막 수집 후 7일 경과 시 재수집(매일 점검).

    KB 주간 데이터는 측정일이 발표보다 ~1주 지연 → '데이터 날짜'가 아닌 '마지막 수집 시각'
    기준으로 주 1회만 받아, 매 기동 재수집을 방지하면서 새 주차 발표를 빠르게 반영.
    """
    import asyncio
    import time
    while True:
        try:
            last = db.kv_get("last_kb_fetch")
            age_days = (time.time() - last) / 86400 if last else 999
            if age_days >= _REFRESH_EVERY_DAYS:
                log.warning("마지막 수집 %.1f일 경과 — 자동 갱신 시작", age_days)
                await asyncio.to_thread(_do_refresh)
                log.warning("자동 갱신 완료")
        except Exception as e:  # 갱신 실패해도 루프 유지(다음 점검에 재시도)
            log.error("자동 갱신 실패: %s", e)
        try:  # 회원·리포트 보존 — S3 백업(env 설정 시). 매일 1회.
            from realty_signal import backup
            if backup.enabled():
                await asyncio.to_thread(backup.run_backup)
        except Exception as e:
            log.error("백업 실패: %s", e)
        await asyncio.sleep(86400)  # 하루마다 점검


def _seed_if_missing():
    """빈 볼륨(첫 배포) 자동 시딩 — 파일 없을 때·키 있을 때만, 각 단계 best-effort.

    KB(시그널)만 부팅 자동수집되고 저평가·급매·재건축은 별도 생성이 필요해,
    신규 배포/볼륨 리셋 시 통합 매물·급지가 비어 보이는 것을 방지한다.
    """
    pk = config.public_data_key()
    if pk and not store.LOCALITY_FILE.exists():          # 저평가·급지
        try:
            log.warning("localities 없음 — 저평가·급지 빌드 중(수 분)…")
            store.build_localities()
        except Exception as e:  # noqa: BLE001
            log.error("localities 시딩 실패: %s", e)
    if pk and _quicksale_stale():                        # 급매 레이더 (없거나 옛 스캔버전이면 재스캔)
        try:
            log.warning("quicksale 없음/구버전 — 급매 레이더 스캔 중…")
            regions = _scan_regions()
            listings = _radar_scan(regions)
            QUICKSALE_FILE.write_text(json.dumps(
                {"ready": True, "listings": listings, "regions": regions,
                 "count": len(listings), "_scan_ver": _QUICKSALE_SCAN_VER},
                ensure_ascii=False), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.error("quicksale 시딩 실패: %s", e)
    if config.seoul_key():                               # 재건축 워밍(BUY+ 지역)
        try:
            log.warning("재건축 워밍 중(BUY+ 지역)…")
            _redev_zones()
            for r in (r for r, s in _signal_map().items() if s in ("STRONG_BUY", "BUY")):
                if db.kv_get(f"redev_cand:{r}", max_age=30 * 86400) is None:
                    try:
                        _redev_candidates(r)
                    except Exception as e:  # noqa: BLE001
                        log.warning("redev warm %s 실패: %s", r, e)
        except Exception as e:  # noqa: BLE001
            log.error("재건축 시딩 실패: %s", e)
    try:  # 시딩 전 빈 데이터로 캐시된 파생결과 무효화
        _regime.cache_clear()
        _signals_df.cache_clear()
    except Exception:  # noqa: BLE001
        pass


async def _startup_bg():
    """초기 수집·시딩·스냅샷을 백그라운드로 — 헬스체크(/)가 데이터를 기다리지 않도록.

    신규 배포 첫 부팅의 KB 수집은 수십 초 걸려, 동기 실행 시 lifespan이 막혀
    healthcheck 윈도우 내에 서버가 응답하지 못한다(배포 실패). '/'는 정적 SPA라
    데이터와 무관하므로, 수집은 별도 스레드에서 돌리고 서버는 즉시 뜬다.
    """
    import asyncio
    if not store.CACHE_FILE.exists():
        log.warning("캐시 없음 — KB 데이터허브에서 최초 수집 중(백그라운드)…")
        try:
            await asyncio.to_thread(store.fetch)
        except Exception as e:  # 수집 실패해도 서버는 기동(이후 갱신으로 재시도)
            log.error("초기 수집 실패: %s", e)
    try:
        await asyncio.to_thread(_seed_if_missing)   # 저평가·급매·재건축 자동 시딩(무거움)
    except Exception as e:  # noqa: BLE001
        log.error("자동 시딩 실패: %s", e)
    try:  # 시그널 스냅샷 초기화(최초 1회) — 이후 변동 감지 기준점
        if not db.kv_get("signal_snapshot"):
            _snapshot_signals(str(_kb().last_date.date()))
    except Exception as e:
        log.error("시그널 스냅샷 초기화 실패: %s", e)
    await _auto_refresh_loop()  # 백그라운드 자동 갱신(무한 루프)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    task = asyncio.create_task(_startup_bg())  # 수집·갱신은 백그라운드, 서버는 즉시 서빙
    yield
    task.cancel()


app = FastAPI(title="realty-signal-map", lifespan=lifespan)


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    """인증된 유저만 데이터 API 접근. /api/auth/* 와 비-API(/, 정적)는 허용."""
    p = request.url.path
    if p.startswith("/api/") and not p.startswith(_OPEN_PREFIXES):
        if not _uid(request):
            return JSONResponse({"error": "인증이 필요합니다.", "auth": False}, status_code=401)
    return await call_next(request)


# ---------- 인증 ----------
@app.post("/api/auth/signup")
def auth_signup(data: dict = Body(...)):
    token, err = auth.signup(data.get("email", ""), data.get("pw", ""))
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    r = JSONResponse({"ok": True})
    r.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return r


@app.post("/api/auth/login")
def auth_login(data: dict = Body(...)):
    token, err = auth.login(data.get("email", ""), data.get("pw", ""))
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=401)
    r = JSONResponse({"ok": True})
    r.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return r


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    auth.logout(request.cookies.get(auth.COOKIE))
    r = JSONResponse({"ok": True})
    r.delete_cookie(auth.COOKIE)
    return r


@app.get("/api/auth/me")
def auth_me(request: Request):
    u = auth.current_user(request.cookies.get(auth.COOKIE))
    if not u:
        return JSONResponse({"auth": False}, status_code=401)
    return {"auth": True, "email": u["email"], "profile": db.profile_get(u["id"]),
            "onboarded": bool(db.profile_get(u["id"])),
            "admin": (u["email"] or "").lower() in config.admin_whitelist()}


# ---------- 프로필 / 즐겨찾기 ----------
@app.get("/api/profile")
def profile_get(request: Request):
    return db.profile_get(_uid(request))


@app.put("/api/profile")
def profile_put(request: Request, data: dict = Body(...)):
    db.profile_set(_uid(request), data)
    return {"ok": True}


@app.post("/api/report/ai")
def report_ai(request: Request, data: dict = Body(...)):
    """프로필 + 결론 요약 → Claude 심층 리포트. 키 없으면 available:false (프론트 폴백)."""
    from realty_signal import ai_report
    config.load_env()
    if not ai_report.available():
        return {"available": False}
    uid = _uid(request)
    if not uid:
        return {"available": False}
    opus = _is_opus_user(request)
    unlimited = opus or _is_admin(request)
    ok, ust = _usage_allow(uid, "report", unlimited=unlimited)
    if not ok:
        return {"available": False, "reason": "limit", "usage": ust,
                "message": f"이번 주 AI 심층 리포트 한도({ust['limit']}회)에 도달했습니다. 규칙기반 리포트는 계속 이용할 수 있습니다."}
    model = ai_report.OPUS if opus else ai_report.SONNET   # 화이트리스트=Opus, 그 외=Sonnet
    tier = "opus" if opus else "sonnet"
    profile = db.profile_get(uid)
    summary = data.get("summary") or {}
    favorites = _fav_context(uid)
    # 캐시: 같은 데이터주차·프로필·관심·티어면 재사용(뉴스는 키에서 제외 — 주 단위 재생성으로 충분)
    try:
        wk = _kb().last_date.strftime("%G-W%V")
    except Exception:  # noqa: BLE001
        wk = "na"
    sig = hashlib.md5(json.dumps([profile, summary, favorites, tier], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    ckey = f"aireport:{uid}:{wk}:{sig}"
    cached = db.kv_get(ckey, max_age=14 * 86400)
    if cached is not None:
        return {**cached, "cached": True, "usage": ust}
    news = db.news_recent_for_ai(12)   # 최근 정책·시장 뉴스 맥락 주입
    report = ai_report.generate(profile, summary, news=news, favorites=favorites, model=model)
    if not report:
        return {"available": False}
    db.usage_inc(uid, "report")
    ust = _usage_status(uid, "report", unlimited=unlimited)
    out = {"available": True, "report": report, "news_used": len(news), "tier": tier, "usage": ust}
    db.kv_set(ckey, {k: v for k, v in out.items() if k != "usage"})
    return out


@app.get("/api/usage")
def usage_get(request: Request):
    """주간 Nick/리포트 사용량(소프트 한도 UI용)."""
    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    unlimited = _is_opus_user(request) or _is_admin(request)
    return {
        "ok": True,
        "nick": _usage_status(uid, "nick", unlimited=unlimited),
        "report": _usage_status(uid, "report", unlimited=unlimited),
    }


@app.get("/api/favorites")
def favorites_get(request: Request):
    return {"favorites": db.fav_list(_uid(request))}


@app.post("/api/favorites")
def favorites_add(request: Request, data: dict = Body(...)):
    db.fav_add(_uid(request), data.get("kind", "region"), data.get("key", ""), data.get("label", ""))
    return {"ok": True}


@app.delete("/api/favorites")
def favorites_del(request: Request, kind: str, key: str):
    db.fav_remove(_uid(request), kind, key)
    return {"ok": True}


# ---------- 알림 (Alert Engine v1) ----------
def _user_nbhd_diffs(uid: int, regions: set[str]) -> dict[str, list]:
    out: dict[str, list] = {}
    for region in regions:
        weeks = db.nbhd_snap_weeks(uid, region, 2)
        if len(weeks) < 2:
            continue
        curr = db.nbhd_snap_get(uid, region, weeks[0])
        prev = db.nbhd_snap_get(uid, region, weeks[1])
        if curr and prev:
            out[region] = _nbhd_diff(curr, prev)
    return out


@app.get("/api/alerts/prefs")
def alerts_prefs_get(request: Request):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    from realty_signal.brain.alerts import merge_prefs
    return {"ok": True, "prefs": merge_prefs(db.alert_prefs_get(uid))}


@app.put("/api/alerts/prefs")
def alerts_prefs_put(request: Request, data: dict = Body(...)):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    prefs = data.get("prefs") if isinstance(data.get("prefs"), dict) else data
    return {"ok": True, "prefs": db.alert_prefs_set(uid, prefs)}


@app.get("/api/alerts")
def alerts(request: Request):
    """Alert Engine v1 — 시그널 변동·고타이밍 매물·동네 diff."""
    from realty_signal.brain import alerts as alert_engine

    uid = _uid(request)
    favs = {f["key"] for f in db.fav_list(uid) if f["kind"] == "region"} if uid else set()
    log_ = db.kv_get("signal_changes") or []
    seen = db.kv_get(f"alerts_seen:{uid}") or "" if uid else ""
    prefs = db.alert_prefs_get(uid) if uid else {}
    listings = _build_listings({"경매", "급매"}) if favs and prefs.get("high_timing", True) else []
    nbhd_diffs = _user_nbhd_diffs(uid, favs) if uid and favs else {}
    payload = alert_engine.evaluate(
        favs, prefs,
        signal_changes=log_,
        signal_map=_signal_map(),
        listings=listings,
        nbhd_diffs=nbhd_diffs,
        seen_before=seen,
    )
    return payload


@app.get("/api/brain/outcomes")
def brain_outcomes(request: Request, limit: int = 12):
    """주간 outcome feature 스냅샷 목록(관리·디버그)."""
    if not _is_admin(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    from realty_signal.brain import outcomes
    return {"ok": True, "snapshots": outcomes.list_snapshots(limit=min(limit, 52))}


@app.post("/api/alerts/seen")
def alerts_seen(request: Request):
    """알림 확인 처리 — 현재 데이터 기준일을 '마지막 확인'으로 기록."""
    try:
        last = str(_kb().last_date.date())
    except Exception:
        last = ""
    db.kv_set(f"alerts_seen:{_uid(request)}", last)
    return {"ok": True}


@app.get("/api/myfeed")
def myfeed(request: Request):
    """내 관심 피드 — 즐겨찾기 지역·단지의 활동(급매·청약·최근 실거래) 개인화 집계. 로그인 필요."""
    uid = _uid(request)
    if not uid:
        return {"ok": False, "reason": "login_required"}
    favs = db.fav_list(uid)
    regions = [f["key"] for f in favs if f["kind"] == "region"]
    complexes = [f["key"] for f in favs if f["kind"] == "complex"]   # "region|name"
    if not regions and not complexes:
        return {"ok": True, "empty": True, "items": []}
    sig = _signal_map()
    # 급매(지역별)
    qs = []
    try:
        qs = json.loads(QUICKSALE_FILE.read_text(encoding="utf-8")).get("listings", []) if QUICKSALE_FILE.exists() else []
    except Exception:  # noqa: BLE001
        qs = []
    # 청약(지역별, 임박)
    ps = []
    try:
        ps = [d for d in _presale() if (d.get("Dday") is not None and d["Dday"] >= 0 and d["Dday"] <= 45)]
    except Exception:  # noqa: BLE001
        ps = []
    items = []
    for r in regions:
        rq = [m for m in qs if r in (m.get("지역") or "")]
        gap = min([m.get("급매갭") for m in rq if m.get("급매갭") is not None], default=None)
        rp = [d for d in ps if r in (d.get("지역") or "")]
        items.append({"type": "region", "region": r, "signal": sig.get(r, ""),
                      "급매": len(rq), "급매갭": gap, "청약임박": len(rp),
                      "청약단지": (rp[0].get("단지명") if rp else None)})
    for key in complexes:
        region, _, name = key.partition("|")
        code = _code_of(region)
        d = db.kv_get(f"complex:{code[:5]}:{name}", max_age=30 * 86400) if code[:5].isdigit() else None
        metrics = _main_flat_metrics(d or {})
        # 캐시에 단지시그널이 없어도 지역시그널+실거래로 즉시 산출(공시비율은 myfeed에서 생략 — 느림)
        cs = (d or {}).get("단지시그널") or {}
        if d and not cs.get("등급") and (d.get("총거래") or d.get("매매추이")):
            cs = _complex_signal(region, d, sig.get(region, ""), None)
        items.append({"type": "complex", "region": region, "name": name,
                      "최근평단가": (d or {}).get("최근평단가"), "추세pct": (d or {}).get("추세pct"),
                      "단지등급": cs.get("등급"), "단지점수": cs.get("점수"),
                      "전세가율": metrics.get("전세가율"), "갭": metrics.get("갭"),
                      "주력평형": metrics.get("주력평형"), "spark": metrics.get("spark") or [],
                      "근거부족": cs.get("근거부족") or [],
                      "데이터없음": d is None})
    return {"ok": True, "empty": False, "items": items, "기준일": str(_kb().last_date.date())}


@app.post("/api/events")
def track_event(request: Request, data: dict = Body(...)):
    """퍼널 이벤트 최소셋. name: signup|profile_complete|fav_add|report_open|nick_ask|nbhd_open|listing_detail_open|listing_click|timing_card_expand."""
    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    name = (data.get("name") or "").strip()
    props = data.get("props") if isinstance(data.get("props"), dict) else {}
    ok = db.event_log(uid, name, props)
    if not ok:
        return JSONResponse({"ok": False, "error": "invalid_event"}, status_code=400)
    return {"ok": True}


@app.get("/api/admin/events")
def admin_events(request: Request, days: int = 30):
    """최근 N일 이벤트 집계(관리자)."""
    if not _is_admin(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    return {"ok": True, "days": days, "counts": db.event_counts(days)}


WEB_DIR = Path(__file__).parent / "web"
_METRIC_LABEL = {
    "jeonse_supply": "전세수급지수",
    "buyer_demand": "매수세우위",
    "buyer_superiority": "매수우위지수",
    "sale_change": "매매증감%",
    "jeonse_change": "전세증감%",
}


@lru_cache(maxsize=1)
def _kb():
    return store.load()


@lru_cache(maxsize=1)
def _codes_nospace():
    """공백 제거 키 → 지역코드. 매물/급매 지역('성남시분당구')과 codes 키('성남시 분당구') 표기차 흡수."""
    return {k.replace(" ", ""): v for k, v in (_kb().codes or {}).items()}


def _code_of(region: str) -> str:
    """지역명 → 지역코드. 정확 매칭 실패 시 공백 무시로 재시도(경기 시-구 매물 실거래 매칭 보장)."""
    if not region:
        return ""
    codes = _kb().codes or {}
    return codes.get(region) or _codes_nospace().get(region.replace(" ", ""), "")


@lru_cache(maxsize=1)
def _regime():
    from realty_signal.signals.regime import compute_regime
    import json as _json
    codes = _json.loads(store.CODES_FILE.read_text(encoding="utf-8")) if store.CODES_FILE.exists() else {}
    return compute_regime(_kb(), store.load_localities(), codes)


@lru_cache(maxsize=1)
def _signals_df():
    return evaluate(_kb(), SignalConfig(), store.load_supply(), store.load_macro(),
                    store.load_volumes(), _regime())


@app.get("/", response_class=HTMLResponse)
def index():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/sudo_gu.geojson")
def sudo_gu_geojson():
    """수도권(서울·인천·경기) 시군구 경계 — 저평가 탭 급지 지도용(단순화 번들)."""
    from fastapi.responses import Response
    p = WEB_DIR / "sudo_gu.geojson"
    if not p.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return Response(p.read_text(encoding="utf-8"), media_type="application/geo+json",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.post("/api/refresh")
def refresh():
    """KB 데이터허브에서 최신 지표를 재수집하고 캐시/시그널을 갱신."""
    return _do_refresh()


@lru_cache(maxsize=1)
def _backtest():
    from realty_signal.signals.engine import backtest_summary
    return backtest_summary(_kb(), SignalConfig())


@app.get("/api/backtest")
def backtest():
    """시그널 적중률 — 전 지역 과거 구간의 실제 가격 결과로 산출(보유 데이터만)."""
    return {**_backtest(), "data_age_days": round(_data_age_days() or 0, 1)}


@app.get("/api/meta")
def meta():
    kb = _kb()
    c = SignalConfig()
    # 전세수급지수 구간(색·설명) — 차트 배경 밴드용
    jeonse_zones = [
        {"from": 0, "to": c.jeonse_oversupply, "label": "공급우위", "color": "#3b82f6",
         "desc": "전세 공급이 수요보다 많음. 전세가 안정·약세."},
        {"from": c.jeonse_oversupply, "to": c.jeonse_tight, "label": "보통", "color": "#64748b",
         "desc": "전세 수급 균형 구간."},
        {"from": c.jeonse_tight, "to": c.jeonse_crunch, "label": "타이트", "color": "#eab308",
         "desc": "전세 매물이 마르기 시작. 전세난 전환 관찰 구간."},
        {"from": c.jeonse_crunch, "to": c.jeonse_spillover, "label": "전세난", "color": "#f97316",
         "desc": "전세 구하기 어려움. 수요가 매매로 넘어올 압력."},
        {"from": c.jeonse_spillover, "to": 200, "label": "매매전이", "color": "#ef4444",
         "desc": "전세난 심화 → 매매가 상승 압력으로 전이되는 구간."},
    ]
    return {
        "regions": kb.regions,
        "metrics": [{"key": k, "label": _METRIC_LABEL.get(k, k)} for k in kb.metrics],
        "last_date": str(kb.last_date.date()),
        "zones": {
            "jeonse_supply": jeonse_zones,
            "buyer_demand_buy": c.demand_buy,        # 매수세우위 매수신호선
            "buyer_idx_strong": c.buyeridx_strong,   # 매수우위지수 강세선
        },
    }


def _file_mtime(p) -> int | None:
    try:
        return int(p.stat().st_mtime)
    except Exception:
        return None


@app.get("/api/freshness")
def freshness():
    """데이터 소스별 최종 갱신 시각·주기 — 유저가 분석 신선도를 확인. (읽기 전용, 부작용 없음)"""
    from realty_signal.auction import AUCTION_FILE
    last_date = str(_kb().last_date.date())
    sources = [
        {"key": "signal", "label": "시장 시그널 (KB 매매·전세·수급)", "asof": last_date,
         "ts": db.kv_ts("last_kb_fetch"), "cycle": "주 1회 자동",
         "note": "KB국민은행 주간 시계열로 전세수급·매수우위·매매모멘텀·국면을 산출. 기준일이 곧 분석 기준입니다."},
        {"key": "trade", "label": "국토부 실거래", "ts": db.kv_max_ts("complex:"),
         "cycle": "조회 시 · 14일 캐시", "note": "단지 조회 시 국토부 실거래를 수집(14일 캐시), 관심단지는 주 1회 자동 프리페치."},
        {"key": "quicksale", "label": "급매 스캔", "ts": _file_mtime(QUICKSALE_FILE),
         "cycle": "관리자 스캔 시", "note": "BUY+ 시그널 지역의 시세 이하 호가를 스캔해 적재."},
        {"key": "auction", "label": "경매 물건", "ts": _file_mtime(AUCTION_FILE),
         "cycle": "관리자 등록·갱신 시", "note": "법원경매 물건과 시세를 관리자가 등록·갱신."},
        {"key": "presale", "label": "청약", "ts": None, "cycle": "실시간",
         "note": "청약홈(applyhome) API를 조회 시점에 실시간 반영."},
        {"key": "redev", "label": "재건축·정비사업", "ts": db.kv_ts("redev_zones") or db.kv_max_ts("redev_cand:"),
         "cycle": "주 1회 자동", "note": "서울 정비사업 단계 + 국토부 실거래로 잠재력·가치를 산출."},
        {"key": "gongsi", "label": "공동주택 공시가격", "ts": db.kv_max_ts("gongsi:"),
         "cycle": "연 1회 · 90일 캐시", "note": "국토부 공시가격(VWorld). 실거래/공시 배수로 저평가·보유세 근거."},
        {"key": "news", "label": "부동산 뉴스", "ts": db.kv_ts("news_fetched"),
         "cycle": "조회 시 갱신", "note": "네이버 뉴스에서 부동산 관련 기사를 수집·요약."},
    ]
    return {"기준일": last_date, "now": int(__import__("time").time()), "sources": sources}


_ADV_RANK = {"STRONG_BUY": 0, "BUY": 1, "WATCH": 2, "NEUTRAL": 3, "SELL_RISK": 4}

# 규제지역(국토부 지정, 참고용·시군구 근사) — 프론트 지도 오버레이와 공용 정본. 지정/해제는 수시 변경.
_REGULATION_ASOF = "2025 기준(참고용·국토부 확인)"
_REGULATION = {
    "강남구": ["투기과열지구", "조정대상지역", "토지거래허가구역"],
    "서초구": ["투기과열지구", "조정대상지역", "토지거래허가구역"],
    "송파구": ["투기과열지구", "조정대상지역", "토지거래허가구역"],
    "용산구": ["투기과열지구", "조정대상지역", "토지거래허가구역"],
    "양천구": ["토지거래허가구역"], "영등포구": ["토지거래허가구역"], "성동구": ["토지거래허가구역"],
}


def _regulation_of(region: str) -> list[str]:
    r = (region or "").strip()
    return _REGULATION.get(r) or next((v for k, v in _REGULATION.items() if k in r or r in k), [])


@app.get("/api/regulation")
def regulation_api():
    """규제지역 지정 현황(참고용). 프론트 지도 오버레이·챗봇 공용 정본."""
    return {"asof": _REGULATION_ASOF, "map": _REGULATION}


def _adv_region_row(r: dict) -> dict:
    """자문 tool 용 지역 시그널 축약(+규제지역)."""
    out = {k: r.get(k) for k in ("region", "signal", "급지", "전세수급", "매수우위지수",
           "매매모멘텀", "공급압력", "저평가도", "수급출처", "근거", "해설") if r.get(k) is not None}
    reg = _regulation_of(r.get("region") or "")
    if reg:
        out["규제지역"] = reg
        out["규제기준"] = _REGULATION_ASOF
    return out


def _advisor_tool(name: str, args: dict) -> dict:
    """자문 에이전트 tool 실행 — 기존 데이터 함수로 위임(server-side)."""
    if name == "list_signal_regions":
        rows = signals()
        want = (args.get("signal") or "").upper()
        if want:
            rows = [r for r in rows if r.get("signal") == want]
        rows = sorted(rows, key=lambda r: (_ADV_RANK.get(r.get("signal"), 9), -(r.get("전세수급") or 0)))
        lim = min(int(args.get("limit") or 15), 40)
        return {"regions": [{"region": r.get("region"), "signal": r.get("signal"),
                             "급지": r.get("급지"), "전세수급": r.get("전세수급"),
                             "매수우위지수": r.get("매수우위지수")} for r in rows[:lim]]}
    if name == "get_region_signal":
        region = (args.get("region") or "").strip()
        rows = signals()
        hit = next((r for r in rows if r.get("region") == region), None) \
            or next((r for r in rows if region and region in (r.get("region") or "")), None)
        return _adv_region_row(hit) if hit else {"error": f"'{region}' 지역 데이터를 찾지 못했습니다."}
    if name == "get_complex":
        region, nm = (args.get("region") or "").strip(), (args.get("name") or "").strip()
        if not region or not nm:
            return {"error": "region 과 name 이 모두 필요합니다."}
        try:
            d = complex_detail(region, nm)
        except Exception:  # noqa: BLE001
            return {"error": "실거래 조회에 실패했습니다."}
        if not d or d.get("거래없음"):
            return {"error": f"{region} {nm} 의 최근 실거래를 찾지 못했습니다."}
        keep = {k: d.get(k) for k in ("단지명", "최근평단가", "추세pct", "총거래", "기간",
                "급지", "시그널", "단지시그널", "공시대비") if d.get(k) is not None}
        keep["평형별"] = (d.get("평형별") or [])[:6]
        m = _main_flat_metrics(d)
        keep["주력"] = {k: m[k] for k in ("주력평형", "전세가율", "갭", "최근매매", "최근전세") if m.get(k) is not None}
        missing = [k for k in ("전세가율", "갭") if m.get(k) is None]
        if missing:
            keep["데이터없음필드"] = missing
            keep["안내"] = "없는 수치(전세가율·갭 등)는 추정·지어내지 말 것. 지역 시그널(시그널)과 단지시그널은 별개."
        return keep
    if name == "get_backtest":
        bt = _backtest()
        return {"by_signal": bt.get("by_signal"), "설명": bt.get("설명"),
                "data_age_days": round(_data_age_days() or 0, 1)}
    if name == "get_timing":
        layer = (args.get("layer") or "region").strip()
        region = (args.get("region") or "").strip()
        if layer == "region":
            if not region:
                return {"error": "region 이 필요합니다."}
            return _region_timing_row(region)
        if layer == "listing":
            kind = args.get("kind") or "급매"
            items = _build_listings({kind})
            if region:
                items = [x for x in items if region in (x.get("지역") or "")]
            items = sorted(items, key=lambda x: x.get("타이밍점수") or 0, reverse=True)[:8]
            return {"layer": "listing", "asof": _timing_asof(),
                    "listings": [{"단지명": x.get("단지명"), "지역": x.get("지역"), "유형": x.get("유형"),
                                  "타이밍점수": x.get("타이밍점수"), "타이밍근거": x.get("타이밍근거"),
                                  "confidence": x.get("confidence")} for x in items]}
        return {"error": f"unknown layer '{layer}'"}
    if name == "get_regime":
        rg = _regime() or {}
        return {k: rg.get(k) for k in ("phase", "beta", "gap", "color", "desc") if k in rg}
    if name == "get_news":
        try:
            return news_summary(topic=args.get("topic"))
        except Exception:  # noqa: BLE001
            return {"error": "뉴스 조회 실패"}
    if name == "get_freshness":
        return freshness()
    if name == "get_regulation":
        region = (args.get("region") or "").strip()
        if region:
            reg = _regulation_of(region)
            return {"region": region, "규제지역": reg or "지정 없음(참고용)", "기준": _REGULATION_ASOF}
        return {"규제지역_전체": _REGULATION, "기준": _REGULATION_ASOF}
    if name == "get_presale":
        region = (args.get("region") or "").strip()
        try:
            items = _presale()
        except Exception:  # noqa: BLE001
            return {"error": "청약 조회 실패"}
        if region:
            items = [d for d in items if region in (d.get("지역") or "") or region in (d.get("주소") or "")]
        items = sorted(items, key=lambda d: (d.get("Dday") if d.get("Dday") is not None else 999))[:12]
        if not items:
            return {"result": "조건에 맞는 청약 단지가 없습니다."}
        return {"presales": [{"단지명": d.get("단지명"), "지역": d.get("지역"), "상태": d.get("상태"),
                "Dday": d.get("Dday"), "다음일정": d.get("다음일정"), "시그널": d.get("시그널"),
                "지역급지": d.get("지역급지"), "정비사업": d.get("정비사업")} for d in items]}
    if name == "get_redev":
        region = (args.get("region") or "").strip()
        if not region:
            return {"error": "region 이 필요합니다."}
        if not db_has_redev_cache(region):
            return {"result": f"{region} 재건축 데이터가 아직 준비되지 않았습니다(관리자 워밍 후 조회 가능)."}
        cands = (_redev_candidates(region) or [])[:10]
        return {"region": region, "시그널": _signal_map().get(region, ""), "candidates": cands}
    if name == "get_listings":
        region = (args.get("region") or "").strip()
        kind = args.get("kind") or "급매"
        out: dict = {}
        if kind in ("급매", "전체"):
            try:
                qs = json.loads(QUICKSALE_FILE.read_text(encoding="utf-8")).get("listings", []) if QUICKSALE_FILE.exists() else []
            except Exception:  # noqa: BLE001
                qs = []
            if region:
                qs = [m for m in qs if region in (m.get("지역") or "")]
            qs = sorted(qs, key=lambda m: (m.get("급매갭") if m.get("급매갭") is not None else 0))[:10]
            out["급매"] = [{"단지명": m.get("단지명"), "지역": m.get("지역"), "평형": m.get("평형"),
                          "호가": m.get("호가"), "급매갭": m.get("급매갭"), "시그널": m.get("시그널")} for m in qs]
        if kind in ("경매", "전체"):
            from realty_signal.auction import AUCTION_FILE
            try:
                au = json.loads(AUCTION_FILE.read_text(encoding="utf-8")) if AUCTION_FILE.exists() else []
                au = au if isinstance(au, list) else au.get("listings", [])
            except Exception:  # noqa: BLE001
                au = []
            if region:
                au = [m for m in au if region in (m.get("region") or "")]
            out["경매"] = [{"단지명": m.get("단지명"), "region": m.get("region"), "최저매각가": m.get("최저매각가"),
                          "감정가": m.get("감정가"), "유찰횟수": m.get("유찰횟수"), "입찰기일": m.get("입찰기일")} for m in au[:10]]
        if not out.get("급매") and not out.get("경매"):
            return {"result": "해당 조건의 매물이 없습니다(급매는 관리자 스캔 시점 기준)."}
        return out
    if name == "get_policy":
        _seed_policies()
        hits = db.policy_search(args.get("query") or "", args.get("region") or "", limit=5)
        if not hits:
            return {"result": "정책 지식베이스에 관련 항목이 없습니다."}
        return {"docs": [{"title": h["title"], "category": h["category"], "region": h["region"],
                          "source": h["source"], "eff_date": h["eff_date"],
                          "body": (h["body"] or "")[:1200]} for h in hits]}
    return {"error": f"알 수 없는 tool: {name}"}


# 정책 KB 초기 시드 — 뉴스로 안 잡히는 '제도·개발계획'의 구조적 개요(운영자가 admin 에서 보강).
# 세부 수치·시행일은 변동되므로 각 항목에 출처·기준·확인 안내를 포함.
_POLICY_SEED = [
    {"title": "스트레스 DSR (총부채원리금상환비율)", "category": "대출규제", "region": "전국",
     "tags": "DSR 대출 한도 금리 스트레스", "source": "금융위원회", "eff_date": "2024~단계 시행",
     "body": "대출 심사 시 미래 금리 상승 위험을 반영해 실제 금리에 '스트레스 금리'를 가산, 한도를 보수적으로 산정하는 제도. 단계적으로 적용 범위·가산폭이 확대돼 왔다. 차주의 연소득 대비 원리금 부담(DSR) 한도(대체로 40%)와 결합해 대출 가능액을 좌우한다. 세부 가산율·적용 시점은 시기별로 다르니 은행/금융위 최신 공고 확인 필요."},
    {"title": "생애최초 주택구입 LTV 우대", "category": "대출규제", "region": "전국",
     "tags": "생애최초 LTV 담보인정비율 첫집", "source": "국토부·금융위", "eff_date": "확인요망",
     "body": "생애최초 구입자는 일반 대비 완화된 LTV(담보인정비율)를 적용받아 자기자본 부담이 낮아진다(대체로 최대 80% 수준, 한도 상한 존재). DSR 규제는 별도로 적용되므로 소득에 따라 실제 한도가 제한될 수 있다. 정확한 비율·한도는 시기·규제지역 여부에 따라 다르니 확인 필요."},
    {"title": "3기 신도시", "category": "개발계획", "region": "수도권",
     "tags": "3기 신도시 공급 택지 남양주 하남 고양 부천 인천 광명시흥",
     "source": "국토부", "eff_date": "지구별 상이(조성 진행 중)",
     "body": "수도권 주택공급을 위한 대규모 공공택지. 대표 지구: 남양주 왕숙, 하남 교산, 고양 창릉, 부천 대장, 인천 계양, 광명·시흥 등. 광역교통(GTX·도로) 연계와 사전청약·본청약 일정이 지구별로 다르게 진행된다. 입주 시기·물량은 지구별 공고 확인."},
    {"title": "GTX (수도권 광역급행철도)", "category": "개발계획", "region": "수도권",
     "tags": "GTX A B C 광역교통 역세권 교통",
     "source": "국토부", "eff_date": "노선별 상이(개통 단계)",
     "body": "수도권 외곽과 서울 도심을 고속으로 연결하는 광역급행철도. A(파주운정~동탄), B(인천~남양주), C(양주~수원) 등 노선이 단계적으로 추진·개통된다. 역 신설 예정지 주변은 접근성 개선 기대가 가격에 선반영되는 경향이 있어, 개통 시점·확정 여부를 구분해 해석해야 한다."},
    {"title": "재건축 규제(안전진단·재건축초과이익환수)", "category": "정비사업", "region": "전국",
     "tags": "재건축 안전진단 재초환 정비사업 규제",
     "source": "국토부", "eff_date": "제도 변동 잦음(확인요망)",
     "body": "재건축은 안전진단 통과가 사업의 초기 관문이며, 기준 완화·강화가 시기별로 반복된다. 재건축초과이익환수제(재초환)는 조합원 초과이익의 일부를 부담금으로 환수하는 제도로, 면제·완화 논의가 이어져 왔다. 사업 단계별 규제는 변동이 크므로 개별 단지의 진행 단계와 최신 제도를 함께 확인해야 한다."},
]


def _seed_policies() -> None:
    """정책 KB가 비어 있으면 구조적 개요를 1회 시드(운영자가 admin 에서 보강)."""
    try:
        if db.policy_count() == 0:
            for p in _POLICY_SEED:
                db.policy_add(**p)
    except Exception:  # noqa: BLE001
        pass


@app.get("/api/admin/policy")
def admin_policy_list(request: Request):
    if not _is_admin(request):
        return JSONResponse({"error": "admin_only"}, status_code=403)
    _seed_policies()
    return {"policies": db.policy_all()}


@app.post("/api/admin/policy")
def admin_policy_add(request: Request, data: dict = Body(...)):
    if not _is_admin(request):
        return JSONResponse({"error": "admin_only"}, status_code=403)
    title = (data.get("title") or "").strip()
    body = (data.get("body") or "").strip()
    if not title or not body:
        return {"ok": False, "reason": "title_body_required"}
    pid = db.policy_add(title=title, body=body, category=(data.get("category") or "").strip(),
                        region=(data.get("region") or "").strip(), tags=(data.get("tags") or "").strip(),
                        source=(data.get("source") or "").strip(), eff_date=(data.get("eff_date") or "").strip())
    return {"ok": True, "id": pid}


@app.delete("/api/admin/policy/{pid}")
def admin_policy_delete(request: Request, pid: int):
    if not _is_admin(request):
        return JSONResponse({"error": "admin_only"}, status_code=403)
    db.policy_delete(pid)
    return {"ok": True}


@app.post("/api/advisor")
def advisor_api(request: Request, data: dict = Body(...)):
    """근거기반 부동산 자문 챗봇 — Claude tool-use 로 우리 데이터를 조회해 답변. 로그인 필요."""
    from realty_signal import advisor
    config.load_env()
    uid = _uid(request)
    if not uid:
        return {"ok": False, "reason": "login_required"}
    if not advisor.available():
        return {"ok": False, "reason": "no_ai",
                "answer": "AI 자문은 서버에 ANTHROPIC_API_KEY 가 설정되어야 이용할 수 있습니다."}
    unlimited = _is_opus_user(request) or _is_admin(request)
    ok, ust = _usage_allow(uid, "nick", unlimited=unlimited)
    if not ok:
        return {"ok": False, "reason": "limit", "usage": ust,
                "answer": f"이번 주 Nick 질문 한도({ust['limit']}회)에 도달했습니다. "
                          "관심지역 추적·동네 리포트·시그널은 계속 이용할 수 있습니다."}
    messages = data.get("messages") or []
    if not isinstance(messages, list) or not messages:
        return {"ok": False, "reason": "empty"}
    messages = messages[-12:]                              # 최근 12턴만(비용·컨텍스트 방어)
    model = advisor.OPUS if _is_opus_user(request) else advisor.SONNET
    system = advisor.build_system(db.profile_get(uid), _fav_context(uid))
    res = advisor.run_advisor(messages, _advisor_tool, model=model, system=system)
    if not res.get("answer"):
        return {"ok": False, "reason": "failed",
                "answer": "지금은 답변을 생성하지 못했습니다. 질문을 조금 더 구체적으로(지역·단지) 주시면 도움이 됩니다."}
    db.usage_inc(uid, "nick")
    ust = _usage_status(uid, "nick", unlimited=unlimited)
    return {"ok": True, "answer": res["answer"], "used": res.get("used", []),
            "기준일": str(_kb().last_date.date()), "usage": ust}


@app.post("/api/advisor/stream")
def advisor_stream_api(request: Request, data: dict = Body(...)):
    """자문 챗봇 스트리밍(SSE) — 답변 델타·tool 상태를 순차 전송. 로그인 필요."""
    from fastapi.responses import StreamingResponse
    from realty_signal import advisor
    config.load_env()
    uid = _uid(request)
    asof = str(_kb().last_date.date())
    opus = _is_opus_user(request)
    unlimited = opus or _is_admin(request)
    messages = (data.get("messages") or [])[-12:]
    profile = db.profile_get(uid) if uid else {}
    favorites = _fav_context(uid) if uid else {}
    system = advisor.build_system(profile, favorites)

    def _one(ev: dict) -> str:
        return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    def gen():
        if not uid:
            yield _one({"type": "error", "message": "login_required"}); return
        if not advisor.available():
            yield _one({"type": "error", "message": "no_ai"}); return
        if not messages:
            yield _one({"type": "error", "message": "empty"}); return
        ok, ust = _usage_allow(uid, "nick", unlimited=unlimited)
        if not ok:
            yield _one({"type": "error", "message": "limit", "usage": ust}); return
        model = advisor.OPUS if opus else advisor.SONNET
        try:
            for ev in advisor.run_advisor_stream(messages, _advisor_tool, model=model, system=system):
                if ev.get("type") == "done":
                    db.usage_inc(uid, "nick")
                    ev["기준일"] = asof
                    ev["usage"] = _usage_status(uid, "nick", unlimited=unlimited)
                yield _one(ev)
        except Exception:  # noqa: BLE001
            yield _one({"type": "error", "message": "failed"})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


_SEOUL_AGG = {"강남11개구", "강북14개구"}


def _region_group(region: str, code: str | None) -> str:
    """시/도 그룹 (필터용)."""
    if region in _SEOUL_AGG or (code and code.startswith("11")):
        return "서울"
    if code and code.startswith("41"):
        return "경기"
    if code and code.startswith("28"):
        return "인천"
    return "지방·광역"


@app.get("/api/signals")
def signals(only: str | None = None):
    df = _signals_df()
    if only:
        keep = {s.strip().upper() for s in only.split(",")}
        df = df[df["signal"].isin(keep)]
    # pandas to_json 이 NaN → null 로 안전 변환 (float NaN 직렬화 오류 회피)
    recs = json.loads(df.to_json(orient="records", force_ascii=False))
    codes = _kb().codes
    for r in recs:
        r["group"] = _region_group(r["region"], codes.get(r["region"]))
    return recs


def _signal_map() -> dict:
    df = _signals_df()
    return dict(zip(df["region"], df["signal"]))


@app.get("/api/auction/buy-regions")
def buy_regions():
    """STRONG_BUY/BUY 시그널 지역 — 매물 탐색 가이드."""
    df = _signals_df()
    hot = df[df["signal"].isin(["STRONG_BUY", "BUY"])]
    return [{"region": r["region"], "signal": r["signal"]} for _, r in hot.iterrows()]


def _overrides(target_margin, loan_ratio, loan_rate, hold_months):
    return {"목표시세차익률": target_margin, "대출비율": loan_ratio,
            "대출금리": loan_rate, "보유개월": hold_months}


@app.get("/api/auction/listings")
def auction_listings(target_margin: float = auction.DEFAULTS["목표시세차익률"],
                     loan_ratio: float | None = None, loan_rate: float | None = None,
                     hold_months: int | None = None):
    """매물 + 권장입찰가/시세차익률 + 우선순위(높은순)."""
    ov = _overrides(target_margin, loan_ratio, loan_rate, hold_months)
    return {
        "params": {"target_margin": target_margin},
        "listings": auction.enrich(auction.load(), _signal_map(), ov),
    }


@app.get("/api/auction/calc/{listing_id}")
def auction_calc(listing_id: str, target_margin: float = auction.DEFAULTS["목표시세차익률"],
                 loan_ratio: float | None = None, loan_rate: float | None = None,
                 hold_months: int | None = None):
    """단일 매물의 비용분해 + 낙찰가율 민감도 표 + 권장 입찰가."""
    lst = next((x for x in auction.load() if x.id == listing_id), None)
    if lst is None:
        raise HTTPException(404, "listing not found")
    p = auction._p(_overrides(target_margin, loan_ratio, loan_rate, hold_months))
    return {
        "listing": asdict_listing(lst),
        "recommend": auction.recommend(lst, p),
        "table": auction.table(lst, p),
    }


@app.post("/api/auction/listings")
def auction_add(data: dict = Body(...)):
    return asdict_listing(auction.add(data))


@app.post("/api/auction/parse")
def auction_parse(request: Request, data: dict = Body(...)):
    """법원경매 물건 텍스트 붙여넣기 → Claude 파싱 → 구조화 필드(프론트 폼 프리필). 크롤 없음."""
    from realty_signal import ai_report
    config.load_env()
    if not ai_report.available():
        return {"ok": False, "reason": "no_ai"}
    model = ai_report.OPUS if _is_opus_user(request) else ai_report.SONNET
    parsed = ai_report.parse_auction(data.get("text", ""), model=model)
    return {"ok": bool(parsed), "parsed": parsed or {}}


@app.delete("/api/auction/listings/{listing_id}")
def auction_delete(listing_id: str):
    auction.remove(listing_id)
    return {"ok": True}


@app.post("/api/auction/refresh-market")
def auction_refresh_market():
    """등록 매물의 최근 실거래가를 국토부에서 조회해 갱신."""
    config.load_env()
    key = config.public_data_key()
    codes = json.loads(store.CODES_FILE.read_text(encoding="utf-8")) if store.CODES_FILE.exists() else {}
    return {"updated": auction.update_market(codes, key)}


@app.post("/api/auction/import")
async def auction_import(request: Request):
    text = (await request.body()).decode("utf-8")
    return {"added": auction.import_csv(text)}


def asdict_listing(lst):
    from dataclasses import asdict
    return asdict(lst)


@lru_cache(maxsize=256)
def _region_grades(region: str):
    """시군구 단지별 급지 랭킹 (국토부 실거래 평단가 순위). 캐시."""
    from realty_signal.ingest.complex_grade import region_grades
    code = _code_of(region)
    if not (code and code.isdigit() and code[2:5] != "000"):
        return []  # 시군구 단위만 (광역/시도는 단지 랭킹 부적합)
    config.load_env()
    return region_grades(code[:5], config.public_data_key())


@app.get("/api/complex-grades/{region}")
def complex_grades(region: str):
    """지역 내 단지단위 급지 랭킹 — 저평가 탭 드릴다운용."""
    return {"region": region, "complexes": _region_grades(region)}


@app.get("/api/undervalued")
def undervalued():
    """수도권 시군구 저평가 랭킹 (입지 대비 가격). 시그널 등급 병합."""
    df = store.load_localities()
    if df.empty:
        return {"ready": False, "listings": []}
    sig = _signal_map()
    recs = json.loads(df.to_json(orient="records", force_ascii=False))
    for r in recs:
        r["시그널"] = sig.get(r["region"], "")
    return {"ready": True, "listings": recs}


_SIDO = {"11": "서울", "26": "부산", "27": "대구", "28": "인천", "29": "광주", "30": "대전",
         "31": "울산", "36": "세종", "41": "경기", "43": "충북", "44": "충남", "45": "전북",
         "46": "전남", "47": "경북", "48": "경남", "50": "제주", "51": "강원", "52": "전북"}


def _presale_since() -> str:
    """최근 ~4개월 공고부터 (진행중·예정 위주)."""
    from datetime import date
    y, m = date.today().year, date.today().month
    m -= 4
    if m <= 0:
        y, m = y - 1, m + 12
    return f"{y}-{m:02d}-01"


def _presale_status(d: dict, today: str) -> tuple[str, str | None]:
    """청약 진행상태 + 다음 일정일자. 날짜 문자열 비교(YYYY-MM-DD)."""
    def ok(s):
        return s if (s and len(str(s)) == 10 and str(s)[4] == "-") else None
    sp_s, sp_e = ok(d["특공접수시작"]), ok(d["특공접수마감"])
    r_s, r_e = ok(d["청약접수시작"]), ok(d["청약접수마감"])
    win, ct_e = ok(d["당첨발표"]), ok(d["계약종료"])
    starts = [x for x in (sp_s, r_s) if x]
    ends = [x for x in (sp_e, r_e) if x]
    first, last = (min(starts) if starts else None), (max(ends) if ends else None)
    if first and today < first:
        return "접수예정", first
    if first and last and first <= today <= last:
        return "접수중", last
    if win and today < win:
        return "발표대기", win
    if win and ct_e and today <= ct_e:
        return "계약중", ct_e
    if ct_e and today > ct_e:
        return "완료", None
    return "공고", win or last


@lru_cache(maxsize=1)
def _presale():
    from realty_signal.ingest import applyhome
    from datetime import date
    config.load_env()
    applyhome.set_key(config.public_data_key())
    sig = _signal_map()
    regions = _regime().get("regions", {})
    codes = _kb().codes or {}
    region_sido = {r: _SIDO.get((c or "")[:2]) for r, c in codes.items()}  # 시군구→시도 (동명이군 구분)
    today = date.today().isoformat()
    out = []
    for d in applyhome.fetch_pblanc(_presale_since()):
        sgg = d["시군구"]
        # 시군구명 + 시도 둘 다 일치할 때만 결합(부산 강서구↔서울 강서구 오매칭 방지)
        region = sgg if (sgg in sig and region_sido.get(sgg) == d["시도"]) else None
        d["지역"] = sgg or d["시도"] or ""
        d["시그널"] = sig.get(region, "")
        rg = regions.get(region) if region else None
        d["지역급지"] = rg.get("급지") if rg else None
        d["지역평단가"] = rg.get("평단가") if rg else None
        nm = d["단지명"] or ""
        d["정비사업"] = any(k in nm for k in ("재건축", "재개발", "정비사업", "구역"))
        st, nxt = _presale_status(d, today)
        d["상태"] = st
        d["다음일정"] = nxt
        d["Dday"] = (date.fromisoformat(nxt) - date.today()).days if nxt else None
        out.append(d)
    return out


@app.get("/api/presale")
def presale_list(request: Request):
    """청약 단지(청약홈) — 지역 시그널 결합 + 거주지 당해 판정. 당해→임박→BUY+ 정렬."""
    sig_rank = {"STRONG_BUY": 0, "BUY": 1, "WATCH": 2, "NEUTRAL": 3, "SELL_RISK": 4, "": 5}
    st_rank = {"접수중": 0, "접수예정": 1, "발표대기": 2, "계약중": 3, "공고": 4, "완료": 5}
    home = (db.profile_get(_uid(request)).get("거주지") or "").strip()  # 거주 시군구

    items = []
    for d in _presale():
        d = dict(d)
        # 당해(해당지역 우선공급): 거주 시군구가 단지 지역/주소에 일치
        d["당해"] = bool(home and (home == d.get("지역") or home in (d.get("주소") or "")))
        items.append(d)

    def key(d):
        return (0 if d["당해"] else 1, st_rank.get(d["상태"], 6),
                d["Dday"] if d["Dday"] is not None else 999, sig_rank.get(d["시그널"], 5))
    return sorted(items, key=key)


@app.get("/api/presale/{manage_no}/types")
def presale_types(manage_no: str):
    """단지 평형별 분양가·특별공급 + 주변시세(지역평단가) 대비 메리트."""
    from realty_signal.ingest import applyhome
    config.load_env()
    applyhome.set_key(config.public_data_key())
    d = next((x for x in _presale() if str(x["관리번호"]) == str(manage_no)), None)
    지역평단가 = d.get("지역평단가") if d else None
    types = applyhome.fetch_types(manage_no)
    for t in types:
        # 메리트 = (분양평단가 − 지역평단가)/지역평단가. 음수 = 주변시세보다 싸다(안전마진)
        if t["분양평단가"] and 지역평단가:
            t["메리트"] = round((t["분양평단가"] / 지역평단가 - 1) * 100)
        else:
            t["메리트"] = None
    return {"관리번호": manage_no, "지역평단가": 지역평단가, "types": types}


_BROKER_RATE = 0.005   # 중개보수(근사)


def _acq_tax_rate(price_manwon: float) -> float:
    """주택 취득세율(+지방교육세·농특세 근사). 만원 기준. 6억↓ 1.1% / 6~9억 선형 / 9억↑ 3.3%."""
    억 = price_manwon / 10000
    base = 0.01 if 억 <= 6 else (억 * 2 / 3 - 3) / 100 if 억 <= 9 else 0.03
    return base + 0.002


def _max_purchase(capital: float, ltv: float, income: float | None, rate: float, years: int = 30):
    """자기자본으로 살 수 있는 최대 매수가(만원) + 그 가격의 비용분해.

    LTV 한도와 DSR(소득) 한도 중 작은 쪽으로 대출이 제한되고,
    자기자본 = (매수가 − 대출) + 취득세 + 중개비 를 만족하는 최대가를 이분탐색.
    """
    dsr_cap = float("inf")
    if income and income > 0:
        n, mr = years * 12, rate / 12
        ann_per_principal = (mr / (1 - (1 + mr) ** -n)) * 12 if mr else 1 / years
        dsr_cap = income * 0.40 / ann_per_principal  # DSR 40% 대출한도(만원)

    def need(P):  # 그 가격을 사는 데 필요한 자기자본
        loan = min(ltv * P, dsr_cap)
        return P - loan + (_acq_tax_rate(P) + _BROKER_RATE) * P

    lo, hi = 0.0, 5_000_000.0  # 0~500억
    for _ in range(48):
        mid = (lo + hi) / 2
        if need(mid) <= capital:
            lo = mid
        else:
            hi = mid
    P = round(lo)
    loan = round(min(ltv * P, dsr_cap))
    return P, {"대출": loan, "취득세": round(_acq_tax_rate(P) * P),
               "중개비": round(_BROKER_RATE * P), "자기자본": round(capital),
               "DSR제약": bool(income and loan < round(ltv * P))}


@app.get("/api/conclusion")
def conclusion(capital: float, ltv: float = 0.7, pyeong: float = 25.7,
               income: float | None = None, rate: float = 0.04, years: int = 30):
    """가용자본 → (LTV+DSR+취득세 반영) 매수가능가 → BUY+ × 저평가 × 단지급지 종합 추천.

    capital: 자기자본(만원), income: 연소득(만원, DSR용·선택), rate: 대출금리, years: 만기.
    경매·급매·청약은 랭킹에 섞지 않고 '그 지역 N건'으로 카드에 첨부.
    """
    from collections import defaultdict

    budget, budget_detail = _max_purchase(capital, ltv, income, rate, years)
    sig = _signal_map()
    loc = store.load_localities()
    locmap = {}
    if not loc.empty:
        for r in json.loads(loc.to_json(orient="records", force_ascii=False)):
            locmap[r["region"]] = r
    # 지역별 경매 매물(권장입찰가·급지) + 청약 단지(분양가·일정) 상세
    auc_by = defaultdict(list)
    for e in auction.enrich(auction.load(), sig):
        auc_by[e["region"]].append({
            "단지명": e["단지명"], "단지급지": e.get("단지급지"),
            "권장입찰가": e.get("권장입찰가"), "시세차익률": e.get("시세차익률"),
            "전용면적": e.get("전용면적"),
        })
    ps_by = defaultdict(list)
    for d in _presale():
        if d.get("시그널") in ("STRONG_BUY", "BUY") and d.get("상태") in ("접수중", "접수예정"):
            ps_by[d["지역"]].append({
                "단지명": d["단지명"], "관리번호": d.get("관리번호"),
                "주택구분": d.get("주택구분"), "상태": d.get("상태"), "Dday": d.get("Dday"),
                "총세대": d.get("총세대"), "정비사업": d.get("정비사업"),
            })
    # 지역별 급매(baroezip 레이더 캐시) — 급매갭 깊은 순
    quick_by = defaultdict(list)
    if QUICKSALE_FILE.exists():
        for m in json.loads(QUICKSALE_FILE.read_text(encoding="utf-8")).get("listings", []):
            quick_by[m.get("지역")].append({
                "단지명": m.get("단지명"), "평형": m.get("평형"), "층": m.get("층"),
                "호가": m.get("호가"), "급매갭": m.get("급매갭"),
            })
    for v in quick_by.values():
        v.sort(key=lambda m: m["급매갭"] if m["급매갭"] is not None else 0)

    regions = _regime().get("regions", {})
    rank = {"STRONG_BUY": 2, "BUY": 1}

    cards = []
    for region, s in sig.items():
        if s not in rank:
            continue
        lr = locmap.get(region)
        if not lr:
            continue
        price = lr.get("price")
        est = round(price * pyeong) if price else None       # 84㎡ 예상 매수가(만원)
        affordable = bool(est and est <= budget)
        uv = lr.get("저평가도") or 0
        score = rank[s] * 1000 + uv * 10 + (lr.get("입지점수") or 0)
        rg = regions.get(region, {})
        cards.append({
            "region": region, "시그널": s, "평단가": price, "예상매수가": est,
            "예산내": affordable, "저평가도": uv, "입지점수": lr.get("입지점수"),
            "지역급지": rg.get("급지"), "해설": lr.get("해설"),
            "경매단지": auc_by.get(region, []), "청약단지": ps_by.get(region, []),
            "급매단지": quick_by.get(region, []),
            "경매건수": len(auc_by.get(region, [])), "청약건수": len(ps_by.get(region, [])),
            "급매건수": len(quick_by.get(region, [])),
            "_score": round(score, 1),
        })
    # 예산 내 우선 → 점수순
    cards.sort(key=lambda c: (c["예산내"], c["_score"]), reverse=True)
    return {"budget": budget, "pyeong": pyeong, "ltv": ltv, "capital": capital,
            "income": income, "detail": budget_detail, "cards": cards}


_GRADE_ORDER = {"A": 4, "B": 3, "C": 2, "D": 1}


@app.get("/api/tradeup")
def tradeup(current_region: str, current_value: float, loan_balance: float = 0,
            extra_cash: float = 0, ltv: float = 0.7, income: float | None = None,
            rate: float = 0.04, years: int = 30, pyeong: float = 25.7):
    """갈아타기 전략 — 현 자산 매도 → 상급지/저평가 착지 후보.

    current_value: 현재 집 시세(만원), loan_balance: 대출잔액(만원),
    extra_cash: 추가 투입 현금(만원). 순자산(=시세−잔액)+추가현금 → 새 매수 예산 산출.
    현재 거주 급지 대비 상향/동급/하향으로 후보를 분류해 반환(1주택 비과세 가정·참고용).
    """
    from collections import defaultdict

    net_equity = max(0.0, current_value - loan_balance)   # 매도 시 손에 쥐는 순자산
    capital = net_equity + max(0.0, extra_cash)           # 갈아타기 가용 자기자본
    budget, budget_detail = _max_purchase(capital, ltv, income, rate, years)

    sig = _signal_map()
    df = store.load_localities()
    locmap = {}
    if not df.empty:
        for r in json.loads(df.to_json(orient="records", force_ascii=False)):
            locmap[r["region"]] = r
    regions = _regime().get("regions", {})

    cur_grade = (regions.get(current_region) or {}).get("급지")
    cur_g = _GRADE_ORDER.get(cur_grade, 0)
    rank = {"STRONG_BUY": 2, "BUY": 1, "WATCH": 0}

    cards = []
    for region, r in locmap.items():
        if region == current_region:
            continue
        price = r.get("price")
        est = round(price * pyeong) if price else None      # 84㎡ 예상 매수가(만원)
        if not est:
            continue
        rg = regions.get(region, {})
        g = _GRADE_ORDER.get(rg.get("급지"), 0)
        delta = g - cur_g                                    # +상급지 / 0 동급 / −하급지
        move = "상급지" if delta > 0 else ("동급지" if delta == 0 else "하급지")
        s = sig.get(region, "")
        uv = r.get("저평가도") or 0
        affordable = est <= budget
        # 갈아타기 매력 = 급지상향 우선 → 시그널 → 저평가 → 입지
        score = delta * 10000 + rank.get(s, -1) * 1000 + uv * 10 + (r.get("입지점수") or 0)
        cards.append({
            "region": region, "이동": move, "급지상향": delta,
            "지역급지": rg.get("급지"), "시그널": s, "평단가": price,
            "예상매수가": est, "예산내": affordable,
            "추가필요": None if affordable else round(est - budget),
            "저평가도": uv, "입지점수": r.get("입지점수"), "해설": r.get("해설"),
            "_score": round(score, 1),
        })
    # 예산 내 & 상급지 우선 → 점수순
    cards.sort(key=lambda c: (c["예산내"], c["_score"]), reverse=True)

    # 단지-레벨: 예산 이하 실제 매물(경매·급매) — 급지 하향 아닌 지역만, 급지상향·기회도 순
    grade_of = {c["region"]: c["급지상향"] for c in cards}
    floor = current_value * 0.6                           # 현 시세 60% 미만은 갈아타기 아님(오피스텔·소형 노이즈 제거)
    listings = []
    for L in _build_listings({"경매", "급매"}):
        tot = L.get("총액")
        if not tot or tot > budget or tot < floor:
            continue
        delta = grade_of.get(L["지역"])
        if delta is None or delta < 0:                    # 하급지·현지역 제외
            continue
        listings.append({
            "유형": L["유형"], "단지명": L["단지명"], "지역": L["지역"],
            "지역급지": L["지역급지"], "급지상향": delta, "시그널": L["시그널"],
            "총액": round(tot), "기회도": L["기회도"], "기회도근거": L["기회도근거"],
            "여유": round(budget - tot), "ref": L.get("ref"),
        })
    listings.sort(key=lambda x: (x["급지상향"], x["기회도"] or 0), reverse=True)

    return {
        "current": {"region": current_region, "급지": cur_grade,
                    "시세": round(current_value), "대출잔액": round(loan_balance),
                    "순자산": round(net_equity)},
        "extra_cash": round(extra_cash), "capital": round(capital),
        "budget": budget, "detail": budget_detail, "pyeong": pyeong,
        "cards": cards, "listings": listings[:40],
    }


QUICKSALE_FILE = store.CACHE_DIR / "quicksale.json"
_QUICKSALE_SCAN_VER = 3   # 스캔 로직 버전 — 올리면 배포 후 부팅 시 자동 재스캔(3=BUY+∪관심지역 커버리지 확대)


def _quicksale_stale() -> bool:
    """급매 캐시가 없거나 옛 스캔버전이면 True → 재스캔 필요."""
    if not QUICKSALE_FILE.exists():
        return True
    try:
        return json.loads(QUICKSALE_FILE.read_text(encoding="utf-8")).get("_scan_ver", 0) < _QUICKSALE_SCAN_VER
    except Exception:  # noqa: BLE001
        return True
REGION_GEO_FILE = store.CACHE_DIR / "region_geo.json"


@lru_cache(maxsize=1)
def _redev_zones():
    """서울 정비구역(재건축/재개발) — upisRebuild. DB 영구 캐시(90일) → 인메모리."""
    from realty_signal import db
    cached = db.kv_get("redev_zones", max_age=90 * 86400)
    if cached is not None:
        return cached
    from realty_signal.ingest import redev as rd
    config.load_env()
    key = config.seoul_key()
    zones = rd.fetch_zones(key) if key else []
    if zones:
        db.kv_set("redev_zones", zones)
    return zones


@app.get("/api/redev/zones")
def redev_zones(type: str | None = None, q: str | None = None):
    """정비구역 목록. type=재건축/재개발/..., q=위치·구역명 검색."""
    zones = _redev_zones()
    if type:
        zones = [z for z in zones if z["구분"] == type]
    if q:
        zones = [z for z in zones if q in z["위치"] or q in z["구역명"]]
    from collections import Counter
    return {"total": len(zones), "by_type": dict(Counter(z["구분"] for z in _redev_zones())),
            "zones": zones[:500]}


@lru_cache(maxsize=64)
def _redev_candidates(region: str):
    """지역 재건축 잠재력 단지 — DB 영구 캐시(30일) → 인메모리. 미스만 라이브 계산."""
    from realty_signal import db
    cached = db.kv_get(f"redev_cand:{region}", max_age=30 * 86400)
    if cached is not None:
        return cached
    from realty_signal.ingest import redev as rd
    code = _code_of(region)
    if not (code and code.isdigit() and code[2:5] != "000"):
        return []
    config.load_env()
    cands = rd.rebuild_candidates(code[:5], config.public_data_key())
    db.kv_set(f"redev_cand:{region}", cands)
    return cands


@app.get("/api/redev/candidates/{region}")
def redev_candidates(region: str):
    """지역 내 재건축 잠재력 단지 랭킹 (구축, 연식·용적률·세대수·시세 기반)."""
    sig = _signal_map()
    cands = _redev_candidates(region)
    return {"region": region, "시그널": sig.get(region, ""),
            "cached": db_has_redev_cache(region), "candidates": cands}


def db_has_redev_cache(region: str) -> bool:
    from realty_signal import db
    return db.kv_get(f"redev_cand:{region}", max_age=30 * 86400) is not None


@app.post("/api/redev/warm")
def redev_warm(data: dict = Body(default={})):
    """비개인화 재건축 데이터 사전 계산 — 지정 지역(없으면 BUY+ 지역)을 DB에 적재.

    한 번 호출해두면 이후 재건축 탭은 DB 캐시에서 즉시 응답. (수십초~수분 소요)
    """
    from realty_signal import db
    regions = data.get("regions")
    if not regions:  # 기본: 매수 시그널(BUY+) 지역만
        sig = _signal_map()
        regions = [r for r, s in sig.items() if s in ("STRONG_BUY", "BUY")]
    _redev_zones()
    done, skipped = [], []
    for r in regions:
        if db.kv_get(f"redev_cand:{r}", max_age=30 * 86400) is not None:
            skipped.append(r)
            continue
        try:
            _redev_candidates(r)
            done.append(r)
        except Exception as e:
            log.warning("warm %s 실패: %s", r, e)
    return {"warmed": done, "already_cached": skipped, "total": len(regions)}


@lru_cache(maxsize=1)
def _redev_progress():
    """정비사업 추진경과(≈3만행) — SQLite(db.redev_progress) 우선, 없으면 수집·적재."""
    from realty_signal import db
    from realty_signal.ingest import redev as rd
    if db.redev_count() > 0:
        return db.redev_rows()
    config.load_env()
    key = config.seoul_key()
    rows = rd.fetch_progress(key) if key else []
    if rows:
        db.redev_replace(rows)
    return rows


@app.get("/api/redev/stages")
def redev_stages(region: str | None = None):
    """정비사업 단계 현황 — 시군구별 현 단계 분포 + 단계 평균 소요기간."""
    from realty_signal.ingest import redev as rd
    sgg5 = None
    if region:
        code = _code_of(region)
        sgg5 = code[:5] if (code and code.isdigit()) else None
    return {"region": region or "서울 전체", **rd.stage_summary(_redev_progress(), sgg5)}


@app.post("/api/geocode")
def geocode_ep(data: dict = Body(...)):
    """단지명/주소 목록 → 좌표 (SQLite 캐시 우선, 미스 일부만 OSM 조회).

    body: {"queries": ["서울 강남구 은마아파트", ...], "max_miss": 20}
    """
    from realty_signal.ingest import geocode
    queries = data.get("queries", [])
    max_miss = int(data.get("max_miss", 20))
    return geocode.geocode_batch(queries, max_miss=max_miss)


@app.get("/api/mapconfig")
def mapconfig():
    """지도 타일 설정 — VWorld 키 있으면 한글 타일 URL, 없으면 null(프론트 CartoDB 폴백)."""
    k = config.vworld_key()
    return {"vworld": k or None}


@app.get("/api/transit")
def transit_ep(sx: float, sy: float, ex: float, ey: float):
    """두 좌표 간 대중교통 최단경로(분·환승·요금). 좌표 라운딩 키로 kv 캐시(30일)."""
    from realty_signal import db
    from realty_signal.ingest import locality
    ck = f"transit:{sx:.3f},{sy:.3f}->{ex:.3f},{ey:.3f}"
    cached = db.kv_get(ck, max_age=30 * 86400)
    if cached is not None:
        return {**cached, "cached": True}
    r = locality.transit_between(sx, sy, ex, ey)
    out = {"available": r is not None, "route": r}
    if r:
        db.kv_set(ck, out)
    return out


@app.get("/api/redev/value-calc")
def redev_value_calc(current_price: float, pyeong: float, presale_pyeong_price: float,
                     contribution: float, hold_months: int = 60):
    """재건축 가치 계산 — 현재가·평형·예상분양평단가·분담금 → ROI."""
    from realty_signal.ingest import redev as rd
    return rd.value_calc(current_price, pyeong, presale_pyeong_price, contribution, hold_months)


@lru_cache(maxsize=1)
def _bundled_centroids() -> dict:
    """레포에 번들된 수도권 시군구 중심좌표 — 라이브 지오코딩(Nominatim 1req/s·DC IP 차단) 회피."""
    p = Path(__file__).parent / "region_centroids.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _region_centroid(region: str, code: str) -> tuple[float, float] | None:
    """시군구 중심좌표 — 번들 → DB 캐시 → (최후) 라이브 지오코딩."""
    b = _bundled_centroids().get(region)
    if b:
        return tuple(b)
    from realty_signal import db
    cached = db.region_get(region)
    if cached:
        return tuple(cached)
    from realty_signal.ingest.locality import geocode
    c = geocode(region, code)
    db.region_set(region, list(c) if c else None)
    return c


@app.get("/api/news")
def news(topic: str | None = None):
    """부동산 뉴스 KB — 최신순. 1시간 초과 시 네이버 뉴스에서 갱신·누적."""
    from realty_signal import db
    from realty_signal.ingest import news as nw
    import time as _t
    last = db.kv_get("news_fetched") or 0
    if _t.time() - last > 3600:
        config.load_env()
        cid, csec = config.naver_search()
        if cid and csec:
            added = db.news_upsert(nw.fetch_news(cid, csec))
            db.kv_set("news_fetched", _t.time())
            log.info("뉴스 갱신 — 신규 %d건", added)
    items = db.news_list(topic)
    return {"topics": ["전체", *nw._TOPICS.keys()], "items": items,
            "available": bool(items) or bool(config.naver_search()[0])}


@app.get("/api/news/summary")
def news_summary(topic: str | None = None, days: int = 30):
    """테마별 뉴스 요약. 로컬 dev → 목업(LLM 미호출), prod → Claude + 캐시 TTL 1일."""
    import os as _os
    from realty_signal import db
    from realty_signal.ingest import news as nw
    items = db.news_since(topic, days, 40)
    if len(items) < 5:
        return {"available": True, "enough": False, "count": len(items)}
    detail = bool(topic and topic != "전체")   # 특정 테마 → 심층 요약
    # 로컬 개발: LLM 비용 없이 헤드라인 기반 목업 (매번 새로 생성해도 저렴)
    if not config.is_prod():
        return {"available": True, "enough": True, "detail": detail, "n": len(items),
                "days": days, "mock": True, "summary": nw.mock_summary(topic, items, detail)}
    # 배포: Claude 요약 + 하루 캐시 (매 요청마다 호출 금지)
    if not _os.environ.get("ANTHROPIC_API_KEY"):
        return {"available": False}
    ckey = f"newsum:{topic or '전체'}:{days}"
    cached = db.kv_get(ckey, max_age=86400)   # TTL 1일
    if cached is not None:
        return {**cached, "cached": True}
    summary = nw.summarize(topic, items, detail=detail)
    out = {"available": True, "enough": True, "summary": summary, "n": len(items),
           "days": days, "detail": detail}
    if summary:
        db.kv_set(ckey, out)
    return out


@app.get("/api/cycle")
def cycle(region: str = "서울"):
    """부동산 경기 사이클 국면(벌집순환 4국면) + 근거. 광역(기본 서울) 주간 시리즈 기반."""
    from realty_signal.signals import cycle as cyc
    return cyc.current_phase(_kb(), region) or {"phase": None}


@app.get("/api/cycle/history")
def cycle_history(region: str = "서울"):
    """지역 시기별 경기 국면 타임라인 — 시그널 차트 오버레이용 밴드."""
    from realty_signal.signals import cycle as cyc
    kb = _kb()
    r = region if not kb.series(region, "sale_change").dropna().empty else "서울"
    return {"region": r, "bands": cyc.cycle_history(kb, r)}


@app.get("/api/complex-search")
def complex_search(q: str):
    """단지명 통합검색 — 카카오 로컬로 위치 해석 → {단지명, region} 후보. deep-dive 진입용."""
    import json as _json
    import urllib.parse
    import urllib.request
    key = config.kakao_key()
    if not key:
        config.load_env()
        key = config.kakao_key()
    if not key or not q.strip():
        return {"results": []}
    codes = _kb().codes
    url = "https://dapi.kakao.com/v2/local/search/keyword.json?" + urllib.parse.urlencode(
        {"query": q if "아파트" in q else q + " 아파트", "size": 12})
    try:
        data = _json.loads(urllib.request.urlopen(  # noqa: S310
            urllib.request.Request(url, headers={"Authorization": f"KakaoAK {key}"}), timeout=8).read())
    except Exception as e:
        log.warning("단지검색 실패: %s", e)
        return {"results": []}
    seen, out = set(), []
    for d in data.get("documents", []):
        addr = d.get("road_address_name") or d.get("address_name") or ""
        # 주소에 포함된 코드키 중 가장 구체적인 것(시군구 > 시도) 선택 — '서울'보다 '강남구' 우선
        region = max((k for k in codes if k in addr), key=len, default=None)
        nm = d.get("place_name", "")
        if not region or (nm, region) in seen:
            continue
        seen.add((nm, region))
        out.append({"name": nm, "region": region, "address": addr})
        if len(out) >= 6:
            break
    return {"results": out}


@app.get("/api/addr-search")
def addr_search(q: str):
    """거주지 검색 — 도로명/지번/단지 키워드 → {name, address, sigungu}. 카카오 로컬 키워드."""
    import json as _json
    import urllib.parse
    import urllib.request
    key = config.kakao_key()
    if not key:
        config.load_env(); key = config.kakao_key()
    if not key or not q.strip():
        return {"results": []}
    codes = _kb().codes
    url = "https://dapi.kakao.com/v2/local/search/keyword.json?" + urllib.parse.urlencode({"query": q, "size": 12})
    try:
        data = _json.loads(urllib.request.urlopen(  # noqa: S310
            urllib.request.Request(url, headers={"Authorization": f"KakaoAK {key}"}), timeout=8).read())
    except Exception as e:
        log.warning("주소검색 실패: %s", e)
        return {"results": []}
    seen, out = set(), []
    for d in data.get("documents", []):
        addr = d.get("road_address_name") or d.get("address_name") or ""
        sgg = max((k for k in codes if k in addr), key=len, default=None)
        nm = d.get("place_name", "")
        key2 = (nm, addr)
        if not sgg or key2 in seen:
            continue
        seen.add(key2)
        out.append({"name": nm, "address": addr, "sigungu": sgg})
        if len(out) >= 8:
            break
    return {"results": out}


_COMPLEX_TTL = 14 * 86400   # 실거래 신고지연(~1개월) 감안, 2주면 신선도 충분


@lru_cache(maxsize=1)
def _uv_map():
    """시군구 → 저평가도 (localities 캐시). 단지 시그널의 지역 가점용."""
    df = store.load_localities()
    if df.empty:
        return {}
    return {r["region"]: r.get("저평가도")
            for r in json.loads(df.to_json(orient="records", force_ascii=False))}


_CX_SIG_GRADE = [(75, "STRONG_BUY"), (62, "BUY"), (50, "WATCH"), (40, "NEUTRAL"), (0, "SELL_RISK")]


def _main_flat_metrics(data: dict) -> dict:
    """주력 평형(매매건수 max)의 전세가율·갭 + 실거래 spark. 캐시/피드 공통 — 없는 필드는 None."""
    plist = data.get("평형별") or []
    main = max(plist, key=lambda p: p.get("매매건수", 0) or 0) if plist else {}
    spark = [x.get("평단가") for x in (data.get("매매추이") or []) if x.get("평단가") is not None][-12:]
    return {
        "주력평형": main.get("평형"),
        "전세가율": main.get("전세가율"),
        "갭": main.get("갭"),
        "최근매매": main.get("최근매매"),
        "최근전세": main.get("최근전세"),
        "spark": spark,
    }


def _complex_signal(region: str, data: dict, signal: str | None, gongsi_ratio: float | None = None) -> dict:
    """단지 시그널 — 사이클(권역)·지역시그널·단지지표·가격을 정규화 가중합(참고용, 백테스트 전).

    가중치(전세가율 있음): 사이클 15% · 지역 40% · 단지 25% · 가격 20%.
    전세가율 없음: 단지 가중 축소(17%)·지역↑(48%) — 검증된 KB 지역시그널에 더 의존.
    """
    def clamp(v, lo=0.0, hi=100.0):
        return max(lo, min(hi, v))

    regime = _regime()
    rg = (regime.get("regions") or {}).get(region) or {}
    if rg.get("막차"):
        cyc = 15.0
    elif regime.get("endgame"):
        cyc = 25.0
    else:
        cyc = 68.0
    reg = {"STRONG_BUY": 90, "BUY": 72, "WATCH": 52, "NEUTRAL": 42, "SELL_RISK": 18}.get(signal or "", 45.0)
    uv = _uv_map().get(region)
    if uv is not None:
        reg = clamp(reg + uv * 0.4)
    # 단지 지표 — 백테스트 결과 단지 단기 가격추세는 예측력 약함 → 추세추종 영향 축소, 구조지표(전세가율·거래량) 위주
    comp = 50.0
    tr = data.get("추세pct")
    if tr is not None:
        comp += clamp(tr * 1.2, -14, 14)          # 추세 영향 축소(백테스트 매수 56%)
    plist = data.get("평형별") or []
    main = max(plist, key=lambda p: p.get("매매건수", 0) or 0) if plist else {}
    has_jeonse = main.get("전세가율") is not None
    if has_jeonse:
        comp += (main["전세가율"] - 60) * 0.55     # 전세가율(하방지지)에 더 의존
    if data.get("총거래"):
        comp += min(15, data["총거래"] / 12)
    comp = clamp(comp)
    # 가격 — 구조적 저평가 우선: 실거래/공시 배수(있으면). 낮을수록(공시 대비 쌈) 가점, 높으면(거품·세부담) 감점.
    price = 50.0
    if gongsi_ratio:
        price = clamp(50 + (1.5 - gongsi_ratio) * 90)   # 1.2→77, 1.5→50, 1.8→23
    else:   # 공시가 없으면 폴백: 최근 평단가 vs 2년평균(눌림목)
        ts = [x.get("평단가") for x in (data.get("매매추이") or []) if x.get("평단가")]
        if len(ts) >= 3 and sum(ts):
            avg = sum(ts) / len(ts)
            price = clamp(50 + (avg - ts[-1]) / avg * 100 * 1.5)
    # 전세 없으면 미검증 단지축 비중↓ — 지역 KB 시그널을 더 신뢰
    w_reg, w_comp = (0.40, 0.25) if has_jeonse else (0.48, 0.17)
    total = round(cyc * 0.15 + reg * w_reg + comp * w_comp + price * 0.20)
    grade = next(g for th, g in _CX_SIG_GRADE if total >= th)
    out = {"등급": grade, "점수": total,
           "분해": {"사이클": round(cyc), "지역": round(reg), "단지": round(comp), "가격": round(price)}}
    if not has_jeonse:
        out["근거부족"] = ["전세가율"]
        out["주의"] = "전세가율 없음 — 단지 가중 축소, 지역시그널 비중↑"
    return out


def _gongsi_for(region: str, name: str) -> dict | None:
    """단지 공동주택 공시가격(VWorld WFS) — 좌표 bbox 조회 → 이름/최근접 매칭. 캐시(90일, 공시가 연1회)."""
    from realty_signal import db
    ck = f"gongsi:{region}:{name}"
    cached = db.kv_get(ck, max_age=90 * 86400)
    if cached is not None:
        return cached or None
    config.load_env()
    key = config.vworld_data_key()
    if not key:
        return None
    from realty_signal.ingest import gongsi, geocode
    from realty_signal.ingest.complex import _canon
    q = f"{region} {name}"
    coords = geocode.geocode_batch([q], max_miss=1).get("coords", {}).get(q)
    if not coords:
        db.kv_set(ck, {}); return None
    feats = gongsi.fetch_bbox(coords[0], coords[1], key, config.vworld_domain())
    if not feats:
        db.kv_set(ck, {}); return None
    tgt = _canon(name)
    match = next((f for f in feats if f.get("단지명") and _canon(f["단지명"]) == tgt), None)
    if not match:   # 이름 매칭 실패 → 최근접
        match = min(feats, key=lambda f: ((f.get("lat") or 0) - coords[0]) ** 2 + ((f.get("lng") or 0) - coords[1]) ** 2)
    db.kv_set(ck, match)
    return match


@app.get("/api/complex/{region}/{name}")
def complex_detail(region: str, name: str):
    """단지 deep-dive — 실거래 매매·전세 추이 + 평형별 + 전세가율·갭 + 단지 시그널 + 공시가격. DB 캐시(14일)."""
    from realty_signal import db
    grade = (_regime().get("regions", {}).get(region) or {}).get("급지")
    signal = _signal_map().get(region)

    def deco(d):   # 급지·시그널·공시가격·단지시그널은 응답 시점에 부착(각자 캐시)
        out = {**d, "급지": grade, "시그널": signal}
        ratio = None
        try:                                                     # 공시가격 먼저 → 단지시그널 가격 성분에 사용
            g = _gongsi_for(region, name)
            if g and g.get("㎡단가"):
                out["공시가격"] = g
                last, gpy = out.get("최근평단가"), g["㎡단가"] * 3.3058 / 10000
                if last and gpy:
                    ratio = round(last / gpy, 2)                 # 실거래/공시 배수(>1=실거래 우위)
                    out["공시대비"] = ratio
        except Exception as e:  # noqa: BLE001
            log.warning("공시가격 조회 실패 %s/%s: %s", region, name, e)
        if not d.get("지원안함") and (d.get("총거래") or d.get("매매추이")):
            out["단지시그널"] = _complex_signal(region, out, signal, ratio)
        return out

    code = _code_of(region)
    if not (code and code.isdigit() and len(code) >= 5):
        return deco({"단지명": name, "지원안함": True, "평형별": [], "매매추이": []})
    lawd5 = code[:5]
    ckey = f"complex:{lawd5}:{name}"
    cached = db.kv_get(ckey, max_age=_COMPLEX_TTL)
    if cached is not None:
        return deco({**cached, "cached": True})
    from realty_signal.ingest import complex as cx
    config.load_env()
    pk = config.public_data_key()
    if not pk:
        return deco({"단지명": name, "지원안함": True, "평형별": [], "매매추이": []})
    data = cx.fetch_complex(lawd5, name, pk)
    data["region"] = region
    db.kv_set(ckey, data)
    return deco(data)


def _complex_backtest(sample: int = 30, months: int = 36) -> dict:
    """단지 가격추세 백테스트 — 표본 단지의 월별 평단가로 '3개월 추세 전환 → 6개월 방향 유지' 적중률.

    단지 시그널의 단지-고유 성분(가격추세)을 검증(지역·사이클 성분은 지역 성적표가 검증).
    표본 단지별 국토부 실거래를 길게(months) 받아야 해 비용이 커서 결과는 캐시(30일).
    """
    from realty_signal import db
    from realty_signal.ingest import complex as cx
    config.load_env()
    pk = config.public_data_key()
    if not pk:
        return {"ready": False}
    codes = _kb().codes
    pool, seen = [], set()
    if QUICKSALE_FILE.exists():                       # 급매 단지 풀에서 표본 추출
        for m in json.loads(QUICKSALE_FILE.read_text(encoding="utf-8")).get("listings", []):
            r, n = m.get("지역"), m.get("단지명")
            code = _code_of(r)
            if r and n and (r, n) not in seen and code[:5].isdigit() and len(code) >= 5:
                seen.add((r, n)); pool.append((code[:5], r, n))
    pool = pool[:sample]
    buy = {"hit": 0, "n": 0, "fwd": []}
    sell = {"hit": 0, "n": 0, "fwd": []}
    n_cx, L = 0, 6
    for lawd5, r, n in pool:
        try:
            d = cx.fetch_complex(lawd5, n, pk, trade_months=months, rent_months=1)
        except Exception:  # noqa: BLE001
            continue
        ps = [x["평단가"] for x in (d.get("매매추이") or []) if x.get("평단가")]
        if len(ps) < 12:
            continue
        n_cx += 1
        for t in range(3, len(ps) - L):
            mom, fwd = ps[t] / ps[t - 3] - 1, ps[t + L] / ps[t] - 1
            if mom >= 0.02:
                buy["n"] += 1; buy["fwd"].append(fwd * 100); buy["hit"] += fwd > 0
            elif mom <= -0.02:
                sell["n"] += 1; sell["fwd"].append(fwd * 100); sell["hit"] += fwd < 0

    def _avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else None

    def _blk(a):
        return {"평가수": a["n"], "적중률": round(a["hit"] / a["n"] * 100) if a["n"] else None,
                "이후6개월평균": _avg(a["fwd"])}

    out = {"ready": True, "표본단지수": n_cx, "months": months,
           "매수": _blk(buy), "매도": _blk(sell),
           "설명": "표본 단지 월별 평단가로 '3개월 추세 전환 이후 6개월 방향 유지'를 검증. "
                   "단지-고유 가격추세 성분(지역·사이클은 지역 성적표가 별도 검증)."}
    db.kv_set("complex_backtest", out)
    return out


@app.get("/api/complex-backtest")
def complex_backtest_api():
    """단지 시그널(가격추세 성분) 검증 성적표. 캐시(30일) — 없으면 미계산 안내."""
    from realty_signal import db
    cached = db.kv_get("complex_backtest", max_age=30 * 86400)
    return {**cached, "cached": True} if cached is not None else {"ready": False}


@app.post("/api/complex-backtest/run")
def complex_backtest_run(request: Request):
    """단지 백테스트 계산·캐시(수 분 소요, 관리자용). 로그인 필요."""
    if not _uid(request):
        return {"ready": False, "error": "로그인 필요"}
    return _complex_backtest()


def warm_favorite_complexes() -> dict:
    """전체 관심단지 실거래 캐시 워밍(주간 스케줄러용). 14일내 신선한 건 건너뜀."""
    from realty_signal import db
    from realty_signal.ingest import complex as cx
    config.load_env()
    pk = config.public_data_key()
    if not pk:
        return {"warmed": 0, "skipped": 0, "no_key": True}
    codes = _kb().codes
    warmed = skipped = 0
    for region, name in db.all_fav_complexes():
        code = _code_of(region)
        if not (code and code.isdigit() and len(code) >= 5):
            continue
        ckey = f"complex:{code[:5]}:{name}"
        if db.kv_get(ckey, max_age=_COMPLEX_TTL) is not None:
            skipped += 1
            continue
        try:
            data = cx.fetch_complex(code[:5], name, pk)
            data["region"] = region
            db.kv_set(ckey, data)
            warmed += 1
        except Exception as e:  # noqa: BLE001
            log.warning("관심단지 워밍 실패 %s/%s: %s", region, name, e)
    return {"warmed": warmed, "skipped": skipped}


# 가치기준 → (필드, 작을수록 유리?, 표시라벨, 값포맷)
_CMP_CRIT = {
    "가격":     ("최근평단가", True,  "평단가",   lambda v: f"{round(v):,}만"),
    "상승여력": ("추세pct",   False, "2년 추세", lambda v: f"{v:+g}%"),
    "전세안정성": ("전세가율", False, "전세가율", lambda v: f"{round(v)}%"),
    "실투자금": ("갭",       True,  "갭",      lambda v: f"{v/10000:.1f}억"),
    "유동성":   ("총거래",    False, "거래량",   lambda v: f"{round(v)}건"),
}


def _compare_rule(criterion: str, complexes: list) -> str:
    """규칙기반 비교 해설 — 선택 가치기준에서 1등 단지 + 반대관점 주의."""
    spec = _CMP_CRIT.get(criterion)
    if not spec or not complexes:
        return "비교할 데이터가 부족합니다."
    field, lower_better, label, fmt = spec
    have = [c for c in complexes if c.get(field) is not None]
    if not have:
        return f"{label} 데이터가 있는 단지가 없어 비교가 어렵습니다."
    best = (min if lower_better else max)(have, key=lambda c: c[field])
    msg = f"‘{criterion}’ 기준으로는 {best.get('단지명','–')}가 {label} {fmt(best[field])}로 가장 유리합니다."
    # 반대 관점: 전세안정성이 낮으면 하방 주의 등 간단 힌트
    if criterion == "실투자금":
        risky = [c for c in have if (c.get("전세가율") or 0) < 60]
        if any(c is best for c in risky):
            msg += " 다만 전세가율이 낮아 하방 안전마진은 상대적으로 약할 수 있습니다."
    elif criterion == "상승여력" and (best.get("추세pct") or 0) < 0:
        msg += " 단, 최근 2년 추세가 하락이라 반등 신호는 별도 확인이 필요합니다."
    return msg


def _compare_score(criteria: list, complexes: list) -> tuple[list, int]:
    """선택 가치기준들로 각 단지를 랭크정규화(0~1, 유리할수록 1) 후 합산 → (점수 내림차순 목록, 사용된 기준수)."""
    valid = [c for c in complexes if c.get("단지명")]
    total: dict = {c["단지명"]: 0.0 for c in valid}
    detail: dict = {c["단지명"]: {} for c in valid}
    used = 0
    for crit in criteria:
        spec = _CMP_CRIT.get(crit)
        if not spec:
            continue
        field, lower, label, fmt = spec
        have = [(c, c[field]) for c in valid if c.get(field) is not None]
        if len(have) < 2:                       # 값 가진 단지 2개 미만이면 변별 불가 → 스킵
            for c, v in have:
                detail[c["단지명"]][crit] = fmt(v)
            continue
        used += 1
        vals = [v for _, v in have]
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1
        for c, v in have:
            s = (hi - v) / span if lower else (v - lo) / span   # 유리할수록 1
            total[c["단지명"]] += s
            detail[c["단지명"]][crit] = fmt(v)
    ranked = sorted(valid, key=lambda c: total[c["단지명"]], reverse=True)
    return [{"단지명": c["단지명"], "급지": c.get("급지"), "시그널": c.get("시그널"),
             "점수": round(total[c["단지명"]], 2), "지표": detail[c["단지명"]]} for c in ranked], used


def _recommend_rule(criteria: list, ranked: list) -> str:
    """규칙기반 추천 해설 — 종합점수 1위 + 차순위 + 근거 수치."""
    if not ranked:
        return "비교할 데이터가 부족합니다."
    w = ranked[0]
    parts = ", ".join(f"{k} {v}" for k, v in (w.get("지표") or {}).items())
    msg = f"선택하신 {'·'.join(criteria)} 기준을 종합하면 {w['단지명']}가 가장 부합합니다"
    msg += f" ({parts})." if parts else "."
    if len(ranked) > 1:
        msg += f" 차순위는 {ranked[1]['단지명']}입니다."
    return msg


@app.post("/api/compare-recommend")
def compare_recommend_api(request: Request, data: dict = Body(...)):
    """비교 단지(2+) + 중시가치(복수) → 종합점수 순위 + 추천 단지·해설(Claude, 없으면 규칙기반)."""
    from realty_signal import ai_report
    config.load_env()
    criteria = [c for c in (data.get("criteria") or []) if c in _CMP_CRIT]
    complexes = data.get("complexes") or []
    if not criteria or len(complexes) < 2:
        return {"ok": False, "reason": "need_criteria_and_2_complexes"}
    ranked, used = _compare_score(criteria, complexes)
    if not ranked or not used:
        return {"ok": False, "reason": "no_comparable_data"}
    opus = _is_opus_user(request)
    model = ai_report.OPUS if opus else ai_report.SONNET   # 추천은 다인자 종합 → Sonnet 이상
    tier = "opus" if opus else "sonnet"
    sig_src = sorted(f"{c.get('단지명')}|{c.get('최근평단가')}|{c.get('전세가율')}|{c.get('갭')}|{c.get('추세pct')}|{c.get('총거래')}" for c in complexes)
    sig = hashlib.md5(json.dumps([sorted(criteria), sig_src, tier], ensure_ascii=False).encode()).hexdigest()[:16]
    ckey = f"cmpreco:{sig}"
    cached = db.kv_get(ckey, max_age=14 * 86400)
    if cached is not None:
        return {**cached, "cached": True}
    text = ai_report.compare_recommend(criteria, complexes, ranked, model=model)
    out = {"ok": True, "추천": ranked[0]["단지명"], "순위": ranked,
           "해설": text or _recommend_rule(criteria, ranked), "ai": bool(text)}
    if text:
        db.kv_set(ckey, out)
    return out


@app.post("/api/compare-insight")
def compare_insight_api(request: Request, data: dict = Body(...)):
    """비교 단지 + 가치기준 → 한줄 해설(Claude, 없으면 규칙기반)."""
    from realty_signal import ai_report
    config.load_env()
    criterion = data.get("criterion") or "가격"
    complexes = data.get("complexes") or []
    opus = _is_opus_user(request)
    model = ai_report.OPUS if opus else ai_report.HAIKU   # 화이트리스트=Opus, 그 외=Haiku(단순 태스크)
    tier = "opus" if opus else "haiku"
    # 캐시: 기준 + 단지 시그니처(이름·핵심수치) + 티어 → 반복 클릭 재과금 방지
    sig_src = sorted(f"{c.get('단지명')}|{c.get('최근평단가')}|{c.get('전세가율')}|{c.get('갭')}|{c.get('추세pct')}|{c.get('총거래')}" for c in complexes)
    sig = hashlib.md5(json.dumps([criterion, sig_src, tier], ensure_ascii=False).encode()).hexdigest()[:16]
    ckey = f"cmpins:{sig}"
    cached = db.kv_get(ckey, max_age=14 * 86400)
    if cached is not None:
        return {**cached, "cached": True}
    text = ai_report.compare_insight(criterion, complexes, model=model)
    out = {"해설": text or _compare_rule(criterion, complexes), "ai": bool(text)}
    if text:   # 규칙기반 폴백은 무료·즉시 → 캐시 불필요
        db.kv_set(ckey, out)
    return out


@app.get("/api/imjang/{region}/{name}")
def imjang_report(region: str, name: str):
    """단지 임장 리포트 — 유튜브·블로그 수집 → (키 있으면) Claude 종합. 링크 폴백. 캐시 30일."""
    from realty_signal import db
    from realty_signal.ingest import imjang
    config.load_env()
    yt = config.youtube_key()
    nv_id, nv_sec = config.naver_search()
    anth = bool(__import__("os").environ.get("ANTHROPIC_API_KEY"))
    # 큐레이션/리포트 tier만 캐시(순수 링크는 캐시 의미 없음)
    ckey = f"imjang:{region}:{name}"
    cached = db.kv_get(ckey, max_age=30 * 86400)
    if cached is not None:
        return {**cached, "cached": True}
    data = imjang.build_report(name, yt_key=yt, nv_id=nv_id, nv_sec=nv_sec, anthropic_on=anth)
    if data.get("tier") != "links":
        db.kv_set(ckey, data)
    return data


@app.get("/api/agents/{region}/{name}")
def agents_nearby(region: str, name: str):
    """단지 근처 공인중개사 — 카카오 로컬. 단지 좌표(지오코딩→지역중심 폴백) 반경 검색. 캐시 7일."""
    from realty_signal import db
    key = config.kakao_key()
    if not key:
        config.load_env()
        key = config.kakao_key()
    if not key:
        return {"available": False, "agents": []}
    ckey = f"agents:{region}:{name}"
    cached = db.kv_get(ckey, max_age=7 * 86400)
    if cached is not None:
        return {**cached, "cached": True}
    # 단지 좌표: 지오코딩 캐시 → 실패 시 지역 중심
    from realty_signal.ingest import agents as ag, geocode
    q = f"{region} {name}"
    coords = geocode.geocode_batch([q], max_miss=1).get("coords", {}).get(q)
    if not coords:
        c = _region_centroid(region, _code_of(region))
        coords = list(c) if c else None
    if not coords:
        return {"available": True, "agents": [], "no_coord": True}
    lst = ag.search_agents(coords[0], coords[1], key)
    out = {"available": True, "agents": lst, "coord": coords}
    db.kv_set(ckey, out)
    return out


_NBHD_CATS = [("SW8", "지하철역"), ("SC4", "학교"), ("MT1", "대형마트"), ("HP8", "병원")]
# 주요 업무지구 (lat, lng) — 통근시간 기준점
_JOB_HUBS = [("강남", 37.4979, 127.0276), ("여의도", 37.5219, 126.9245), ("광화문", 37.5716, 126.9769)]


def _nbhd_commute(lat: float, lng: float) -> dict:
    """지역 중심 → 주요 업무지구 대중교통 소요(분). ODsay 키 없거나 실패 항목은 생략."""
    from realty_signal.ingest import locality
    out = {}
    for name, jlat, jlng in _JOB_HUBS:
        try:
            r = locality.transit_between(lng, lat, jlng, jlat)   # sx=경도, sy=위도
            if r and r.get("min"):
                out[name] = {"min": r["min"], "transfer": r.get("transfer")}
        except Exception:  # noqa: BLE001
            continue
    return out


def _nbhd_infra(lat: float, lng: float, key: str, radius: int = 1500) -> dict:
    """카카오 로컬 카테고리 검색으로 생활인프라 개수(구 중심 반경). 실패 시 해당 항목 생략."""
    import urllib.parse
    import urllib.request
    out = {}
    for code, label in _NBHD_CATS:
        try:
            url = "https://dapi.kakao.com/v2/local/search/category.json?" + urllib.parse.urlencode(
                {"category_group_code": code, "x": lng, "y": lat, "radius": radius, "size": 1})
            req = urllib.request.Request(url, headers={"Authorization": f"KakaoAK {key}"})
            data = json.loads(urllib.request.urlopen(req, timeout=6).read())  # noqa: S310
            out[label] = (data.get("meta") or {}).get("total_count")
        except Exception:  # noqa: BLE001
            continue
    return out


_NBHD_METRIC_KEYS = (
    "시그널", "전세수급", "매수우위지수", "매매모멘텀", "평단가", "저평가도",
    "공급압력", "급매", "청약", "국면", "거래량비",
)
_SIG_RANK = {"SELL_RISK": 0, "NEUTRAL": 1, "WATCH": 2, "BUY": 3, "STRONG_BUY": 4}


def _nbhd_metrics(payload: dict) -> dict:
    """주간 diff용 핵심 지표만 추출."""
    m = payload.get("매물") or {}
    vol = payload.get("거래량") or {}
    return {
        "시그널": payload.get("시그널"),
        "전세수급": payload.get("전세수급"),
        "매수우위지수": payload.get("매수우위지수"),
        "매매모멘텀": payload.get("매매모멘텀"),
        "평단가": payload.get("평단가"),
        "저평가도": payload.get("저평가도"),
        "공급압력": payload.get("공급압력"),
        "급매": m.get("급매"),
        "청약": m.get("청약"),
        "국면": (payload.get("국면") or {}).get("phase"),
        "급지": payload.get("급지"),
        "거래량비": vol.get("거래량비"),
    }


def _nbhd_diff(curr: dict, prev: dict) -> list[dict]:
    """curr vs prev 지표 변화 목록."""
    out = []
    for k in _NBHD_METRIC_KEYS:
        a, b = curr.get(k), prev.get(k)
        if a is None and b is None:
            continue
        if a == b:
            continue
        item = {"key": k, "from": b, "to": a}
        if k == "시그널":
            item["delta"] = _SIG_RANK.get(a, 1) - _SIG_RANK.get(b, 1)
        elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
            item["delta"] = round(a - b, 2)
        out.append(item)
    return out


def _nbhd_week() -> str:
    try:
        return _kb().last_date.strftime("%G-W%V")
    except Exception:  # noqa: BLE001
        import datetime as _dt
        return _dt.date.today().strftime("%G-W%V")


@app.get("/api/neighborhood/{region}")
def neighborhood(request: Request, region: str):
    """동네 딥다이브 — 보유 데이터 재조립 + 생활인프라. 로그인 시 주간 스냅샷 저장·지난 대비 diff."""
    sigrow = next((r for r in signals() if r.get("region") == region), None) \
        or next((r for r in signals() if region and region in (r.get("region") or "")), None)
    if not sigrow:
        return {"ok": False, "reason": "no_region"}
    region = sigrow["region"]
    rg = (_regime().get("regions", {}) or {}).get(region, {})
    # 가격·저평가(localities)
    loc = store.load_localities()
    lr = {}
    if not loc.empty:
        for r in json.loads(loc.to_json(orient="records", force_ascii=False)):
            if r.get("region") == region:
                lr = r
                break
    # 매물 건수(이 동네)
    def _qs_count():
        try:
            qs = json.loads(QUICKSALE_FILE.read_text(encoding="utf-8")).get("listings", []) if QUICKSALE_FILE.exists() else []
            return sum(1 for m in qs if region in (m.get("지역") or ""))
        except Exception:  # noqa: BLE001
            return 0
    ps_cnt = 0
    try:
        ps_cnt = sum(1 for d in _presale() if region in (d.get("지역") or ""))
    except Exception:  # noqa: BLE001
        pass
    # 미래가치
    redev_n = len(_redev_candidates(region) or []) if db_has_redev_cache(region) else None
    dev = db.policy_search(region, region=region, limit=3)
    dev = [{"title": d["title"], "eff_date": d["eff_date"]} for d in dev
           if d.get("category") in ("개발계획", "정비사업")]
    c = _region_centroid(region, _code_of(region))
    # 생활인프라(카카오, 30일 캐시)
    ck = f"nbhd_infra:{region}"
    infra = db.kv_get(ck, max_age=30 * 86400)
    if infra is None:
        config.load_env()
        kkey = config.kakao_key()
        if kkey and c:
            infra = _nbhd_infra(c[0], c[1], kkey)
            if infra:
                db.kv_set(ck, infra)
    # 업무지구 통근(ODsay, 30일 캐시)
    cmk = f"nbhd_commute:{region}"
    commute = db.kv_get(cmk, max_age=30 * 86400)
    if commute is None and c:
        config.load_env()
        commute = _nbhd_commute(c[0], c[1])
        if commute:
            db.kv_set(cmk, commute)
    asof = str(_kb().last_date.date())
    week = _nbhd_week()
    from realty_signal import personal_layer as pl
    vol = pl.volume_summary(region)
    out = {
        "ok": True, "region": region, "시그널": sigrow.get("signal"),
        "급지": sigrow.get("급지"), "해설": sigrow.get("해설"), "근거": sigrow.get("근거"),
        "전세수급": sigrow.get("전세수급"), "매수우위지수": sigrow.get("매수우위지수"),
        "매매모멘텀": sigrow.get("매매모멘텀"), "공급압력": sigrow.get("공급압력"),
        "수급출처": sigrow.get("수급출처"),
        "국면": {"phase": rg.get("phase") or _regime().get("phase"),
                "color": _regime().get("color")},
        "평단가": lr.get("price"), "저평가도": lr.get("저평가도"), "입지점수": lr.get("입지점수"),
        "규제지역": _regulation_of(region), "규제기준": _REGULATION_ASOF,
        "미래가치": {"재건축후보수": redev_n, "개발계획": dev},
        "매물": {"급매": _qs_count(), "청약": ps_cnt},
        "거래량": vol,
        "거시": pl.macro_latest(),
        "입지상세": pl.locality_bits(lr),
        "외부확인": pl.ext_links(region),
        "임장체크항목": pl.IMJANG_CHECKS,
        "생활인프라": infra or {}, "인프라기준": "구 중심 반경 1.5km · 카카오 로컬(참고용)",
        "통근": commute or {}, "통근기준": "구 중심 → 업무지구 대중교통(ODsay·참고용)",
        "기준일": asof, "week": week,
    }
    # 로그인 시: 이번 주 스냅샷 저장 + 지난 스냅샷 diff + 관심단지 공시 샘플·체크리스트
    uid = _uid(request)
    if uid:
        out["공시샘플"] = pl.fav_gongsi_samples(uid, region)
        ck = db.kv_get(f"checklist:{uid}:{region}", max_age=365 * 86400) or {}
        out["임장체크"] = ck if isinstance(ck, dict) else {}
        metrics = _nbhd_metrics(out)
        metrics["기준일"] = asof
        db.nbhd_snap_save(uid, region, week, metrics)
        prev = db.nbhd_snap_prev(uid, region, week)
        if prev and prev.get("data"):
            out["prev"] = {"week": prev["week"], "data": prev["data"]}
            out["diff"] = _nbhd_diff(metrics, prev["data"])
        out["snap_weeks"] = db.nbhd_snap_weeks(uid, region)
    return out


@app.get("/api/neighborhood-compare")
def neighborhood_compare(request: Request, a: str, b: str):
    """두 동네 핵심 지표 나란히 비교(관심 후보 좁힐 때)."""
    if not a or not b or a == b:
        return {"ok": False, "reason": "need_two_regions"}
    da = neighborhood(request, a)
    db_ = neighborhood(request, b)
    if not da.get("ok") or not db_.get("ok"):
        return {"ok": False, "reason": "no_region", "a": da, "b": db_}
    keys = ["시그널", "급지", "평단가", "저평가도", "입지점수", "전세수급", "매수우위지수",
            "매매모멘텀", "공급압력", "급매", "청약", "국면", "거래량비"]
    def _val(d, k):
        if k in ("급매", "청약"):
            return (d.get("매물") or {}).get(k)
        if k == "국면":
            return (d.get("국면") or {}).get("phase")
        if k == "거래량비":
            return (d.get("거래량") or {}).get("거래량비")
        return d.get(k)
    rows = [{"key": k, "a": _val(da, k), "b": _val(db_, k)} for k in keys]
    return {"ok": True, "a": {"region": da["region"], "시그널": da.get("시그널")},
            "b": {"region": db_["region"], "시그널": db_.get("시그널")},
            "rows": rows, "기준일": da.get("기준일")}


@app.post("/api/checklist/{region}")
def save_checklist(request: Request, region: str, data: dict = Body(...)):
    """임장 체크리스트 저장(지역 단위). {checks: {id: bool}}."""
    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    checks = data.get("checks") if isinstance(data.get("checks"), dict) else {}
    from realty_signal import personal_layer as pl
    allowed = {c["id"] for c in pl.IMJANG_CHECKS}
    clean = {k: bool(v) for k, v in checks.items() if k in allowed}
    db.kv_set(f"checklist:{uid}:{region}", clean)
    return {"ok": True, "checks": clean}


@app.get("/api/loan-scenarios")
def loan_scenarios(request: Request, capital: float | None = None, income: float | None = None,
                   rate: float = 0.04, years: int = 30, price: float | None = None):
    """LTV 60/70/80 시나리오. capital 생략 시 프로필 가용자본."""
    from realty_signal import personal_layer as pl
    uid = _uid(request)
    if capital is None and uid:
        p = db.profile_get(uid) or {}
        capital = float(p.get("가용자본") or 0) or None
        if income is None and p.get("연소득"):
            income = float(p["연소득"])
    if not capital or capital <= 0:
        return {"ok": False, "reason": "need_capital"}
    # macro 금리 있으면 기본값으로
    if rate == 0.04:
        m = pl.macro_latest()
        if m.get("대출금리"):
            try:
                rate = float(m["대출금리"]) / 100.0
            except (TypeError, ValueError):
                pass
    rows = pl.loan_scenarios(capital, income, rate, years, _max_purchase)
    out = {"ok": True, "capital": capital, "income": income, "rate": rate, "years": years, "scenarios": rows}
    if price:
        out["목표가"] = price
        out["목표대비"] = [{"ltv": r["ltv"], "여유": round(r["매수가능가"] - price),
                         "가능": r["매수가능가"] >= price} for r in rows]
    return out


@app.get("/api/complex/{region}/{name}/building")
def complex_building(region: str, name: str):
    """단지 건축물대장(용적률·세대·연식) — 펼칠 때 온디맨드."""
    from realty_signal import personal_layer as pl
    config.load_env()
    pk = config.public_data_key()
    code = _code_of(region)
    b = pl.building_for_complex(region, name, code, pk or "")
    if not b:
        return {"ok": False, "reason": "not_found"}
    return {"ok": True, "building": b}
    """시군구 중심좌표 배치 — 콤마구분 지역명 → {지역:[lat,lng]}. DB 캐시 우선(즉시).

    핀 폴백용: 단지 지오코딩 실패 단지를 지역 중심에 표시해 항상 클릭/포커스 가능.
    """
    codes = _kb().codes
    out = {}
    for region in [r for r in regions.split(",") if r][:60]:
        c = _region_centroid(region, _code_of(region))
        if c:
            out[region] = [c[0], c[1]]
    return {"centroids": out}


@lru_cache(maxsize=1)
def _sigungu_polys():
    """수도권 시군구 경계 폴리곤 (sudo_gu.geojson) → [(name, outer_rings, bbox)]. 좌표→구 역판정용."""
    try:
        g = json.loads((WEB_DIR / "sudo_gu.geojson").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for f in g.get("features", []):
        nm = (f.get("properties") or {}).get("name")
        geom = f.get("geometry") or {}
        polys = geom.get("coordinates") or []
        if geom.get("type") == "Polygon":
            polys = [polys]
        rings = [poly[0] for poly in polys if poly]          # 외곽 링만(홀 무시 — 시군구엔 사실상 없음)
        pts = [pt for r in rings for pt in r]
        if not pts:
            continue
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        out.append((nm, rings, (min(xs), min(ys), max(xs), max(ys))))
    return out


def _pip(lng: float, lat: float, ring: list) -> bool:
    """ray-casting point-in-polygon. ring=[[lng,lat],...]."""
    inside, n, j = False, len(ring), len(ring) - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _sigungu_at(lat, lng) -> str | None:
    """좌표 → 실제 시군구명(수도권). baroezip 급매의 bbox 스캔이 인접 구를 잘못 태깅하는 것 교정."""
    if not lat or not lng:
        return None
    for nm, rings, (x0, y0, x1, y1) in _sigungu_polys():
        if x0 <= lng <= x1 and y0 <= lat <= y1 and any(_pip(lng, lat, r) for r in rings):
            return nm
    return None


def _radar_scan(regions: list[str]) -> list[dict]:
    """급매 레이더 — 지역별 baroezip 공개 spatialmarket 조회 → 급매 매물 + 지역 시그널.

    bbox 스캔은 인접 구를 덮으므로, 매물의 실제 좌표로 시군구를 역판정해 지역·급지 오분류를 막는다.
    """
    from realty_signal.ingest.baroezip import bbox_around, fetch_market

    codes = _kb().codes
    sig = _signal_map()
    seen, out = set(), []
    for region in regions:
        code = _code_of(region)
        c = _region_centroid(region, code)
        if not c:
            continue
        for m in fetch_market(*bbox_around(c[0], c[1])):
            if not m["급매"]:
                continue
            key = (m["complex_no"], m["평형"], m["층"])
            if key in seen:
                continue
            seen.add(key)
            actual = _sigungu_at(m.get("lat"), m.get("lng")) or region   # 좌표 우선, 실패 시 스캔 지역
            m["지역"] = actual
            m["시그널"] = sig.get(actual, "")
            out.append(m)
    out.sort(key=lambda m: m["급매갭"] if m["급매갭"] is not None else 0)
    return out


_SIG_BONUS = {"STRONG_BUY": 25, "BUY": 15, "WATCH": 5, "NEUTRAL": 0, "SELL_RISK": -20}
_GRADE_BONUS = {"A": 8, "B": 4, "C": 0, "D": -4}   # region_timing·listing_timing 내부와 동기


def _timing_asof() -> str:
    return str(_kb().last_date.date())


def _region_timing_row(region: str) -> dict:
    """지역 타이밍 — signals row + (선택) 백테스트 상승 확률."""
    from realty_signal.signals.timing import region_timing

    rows = signals()
    hit = next((r for r in rows if r.get("region") == region), None) \
        or next((r for r in rows if region and region in (r.get("region") or "")), None)
    if not hit:
        return {"error": f"'{region}' 지역 데이터를 찾지 못했습니다."}
    bt_up = None
    try:
        by_sig = {r["signal"]: r for r in (_backtest().get("by_signal") or []) if r.get("signal")}
        sig = hit.get("signal")
        if sig and sig in by_sig:
            bt_up = by_sig[sig].get("적중률")
    except Exception:  # noqa: BLE001
        pass
    tr = region_timing(
        hit.get("signal"),
        asof=_timing_asof(),
        jeonse_supply=hit.get("전세수급"),
        sale_momentum=hit.get("매매모멘텀"),
        backtest_up_pct=float(bt_up) if bt_up is not None else None,
    )
    return {**tr.to_dict(), "region": region, "signal": hit.get("signal")}


def _opportunity(kind: str, m: dict, signal: str | None, grade: str | None):
    """기회도 0~100 — listing_timing 래퍼 (하위 호환)."""
    from realty_signal.signals.timing import listing_timing

    tr = listing_timing(kind, m, signal, grade, asof=_timing_asof())
    return tr.score, tr.reasons_text


def _build_listings(want: set[str]) -> list[dict]:
    """통합 매물 정규화(공통 스키마 + 기회도 + 총액). 유형 필터(want)만 수집."""
    grade = {r: (v or {}).get("급지") for r, v in _regime().get("regions", {}).items()}
    out = []

    def add(kind, name, region, signal, mlabel, mval, munit, raw, lat, lng, ref, total=None):
        from realty_signal.signals.timing import listing_timing

        tr = listing_timing(kind, raw, signal, grade.get(region), asof=_timing_asof())
        row = {"유형": kind, "단지명": name, "지역": region, "시그널": signal or "",
               "지역급지": grade.get(region), "지표라벨": mlabel, "지표값": mval, "지표단위": munit,
               "총액": total, "lat": lat, "lng": lng, "ref": ref}
        row.update(tr.to_dict())
        out.append(row)

    if "경매" in want:
        for r in auction.enrich(auction.load(), _signal_map(), {}):
            add("경매", r.get("단지명"), r.get("region"), r.get("지역시그널"),
                "시세차익", r.get("시세차익률"), "%", r, r.get("lat"), r.get("lng"),
                {"id": r.get("id")}, total=r.get("최저매각가") or r.get("권장입찰가"))
    if "급매" in want and QUICKSALE_FILE.exists():
        for m in json.loads(QUICKSALE_FILE.read_text(encoding="utf-8")).get("listings", []):
            add("급매", m.get("단지명"), m.get("지역"), m.get("시그널"),
                "급매갭", m.get("급매갭"), "%", m, m.get("lat"), m.get("lng"),
                {"평형": m.get("평형"), "호가": m.get("호가"), "complex_no": m.get("complex_no")},
                total=m.get("호가"))
    if "청약" in want:
        for d in _presale():
            add("청약", d.get("단지명"), d.get("지역"), d.get("시그널"),
                "청약상태", d.get("상태"), "", d, None, None,
                {"관리번호": d.get("관리번호"), "Dday": d.get("Dday"), "주소": d.get("주소")})
    if "재건축" in want:
        sig = _signal_map()
        for region, s in sig.items():                     # 캐시된 지역만 — 라이브 재계산 없이(opt-in)
            if not db_has_redev_cache(region):
                continue
            for c in _redev_candidates(region):
                add("재건축", c.get("단지명"), region, s,
                    "재건축잠재력", c.get("잠재력"), "점", c, None, None,
                    {"연식년": c.get("연식년"), "평단가": c.get("평단가")})
    return out


@app.get("/api/timing")
def timing_api(region: str | None = None):
    """지역 타이밍 점수(0~100) — 근거·신뢰도·기준일."""
    if not region or not region.strip():
        return JSONResponse({"error": "region required"}, status_code=400)
    return _region_timing_row(region.strip())


@app.get("/api/listings/all")
def listings_all(request: Request, types: str = "경매,급매,청약"):
    """통합 매물 — 경매·급매·청약을 공통 스키마로 정규화 + 타이밍점수(기회도 호환). 유형 교차 정렬."""
    from realty_signal.signals.timing import VERSION as TIMING_VERSION

    out = _build_listings(set(t for t in types.split(",") if t))
    out.sort(key=lambda x: (x["기회도"] if x["기회도"] is not None else -1), reverse=True)
    asof = _timing_asof()
    return {
        "listings": out,
        "asof": asof,
        "meta": {
            "source": "kb_weekly",
            "timing_version": TIMING_VERSION,
            "data_age_days": round(_data_age_days() or 0, 1),
        },
        "counts": {k: sum(1 for x in out if x["유형"] == k) for k in ("경매", "급매", "청약", "재건축")},
    }


@app.get("/api/quicksale")
def quicksale():
    """급매 레이더 결과 (캐시). 개인용 — baroezip 공개 API 기반."""
    if QUICKSALE_FILE.exists():
        return json.loads(QUICKSALE_FILE.read_text(encoding="utf-8"))
    return {"ready": False, "listings": [], "regions": []}


def _scan_regions() -> list[str]:
    """급매 스캔 대상 = BUY+ 시그널 지역 ∪ 전체 사용자 관심 지역(중복 제거).

    관심 지역엔 시그널과 무관하게 급매 데이터가 항상 있도록 보장 → 관심 피드·동네 리포트 밀도↑.
    """
    df = _signals_df()
    buy = list(df[df["signal"].isin(["STRONG_BUY", "BUY"])]["region"])
    valid = set(df["region"])
    favs = [r for r in db.all_fav_regions() if r in valid]   # 유효 지역만
    seen, out = set(), []
    for r in buy + favs:
        if r not in seen:
            seen.add(r); out.append(r)
    return out


@app.post("/api/quicksale/refresh")
def quicksale_refresh(data: dict = Body(default={})):
    """급매 레이더 갱신. body {regions:[...]} 없으면 BUY+ ∪ 관심 지역 스캔."""
    regions = data.get("regions") or _scan_regions()
    listings = _radar_scan(regions)
    result = {"ready": True, "listings": listings, "regions": regions,
              "count": len(listings), "_scan_ver": _QUICKSALE_SCAN_VER}
    QUICKSALE_FILE.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "count": len(listings), "regions": len(regions)}


@app.get("/api/regime")
def regime():
    """수도권 급지역전 국면(유동성 신호등). regions 상세는 제외하고 요약만."""
    r = dict(_regime())
    r.pop("regions", None)
    return r


@app.get("/api/macro")
def macro():
    """거시지표: 전국 아파트 주택구매력지수 + 주택담보대출금리 (월간)."""
    return store.load_macro()


@app.get("/api/signal-history/{region}")
def signal_hist(region: str):
    """지역의 과거 STRONG_BUY/BUY 구간 (백테스트)."""
    from realty_signal.signals.engine import signal_history
    return {"intervals": signal_history(_kb(), region, SignalConfig())}


@app.get("/api/series/{region}")
def series(region: str):
    kb = _kb()
    if region not in kb.regions:
        raise HTTPException(404, f"unknown region: {region}")
    out = {"region": region, "metrics": {}}
    for m in kb.metrics:
        s = kb.series(region, m)
        out["metrics"][m] = {
            "label": _METRIC_LABEL.get(m, m),
            "dates": [str(d.date()) for d in s.index],
            "values": [round(float(v), 3) for v in s.values],
        }
    # 누적 매매가격지수(KB 증감률 누적, 시작=100) — 시그널 검증용
    from realty_signal.signals.engine import price_index_from
    pidx = price_index_from(kb.series(region, "sale_change"))
    if not pidx.empty:
        out["metrics"]["price_index"] = {
            "label": "매매가격지수",
            "dates": [str(d.date()) for d in pidx.index],
            "values": [round(float(v), 1) for v in pidx.values],
        }
    vol = store.load_volumes().get(region)  # 월별 거래량(시군구)
    if vol:
        out["volume"] = vol
    return out
