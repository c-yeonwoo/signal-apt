"""FastAPI 백엔드 — 시그널 테이블 + 지역별 시계열을 제공하고 대시보드를 서빙."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import date
from functools import lru_cache
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from realty_signal import jsonx
from realty_signal import auction, auth, buying_power, config, db, store
from realty_signal.signals.engine import SignalConfig
from realty_signal.time_kst import today_kst

log = logging.getLogger("realty_signal")

# 인증 게이트: /api/* 는 세션 필수(아래 prefix·정확경로만 예외). 그 외(/, 정적)는 허용.
_OPEN_PREFIXES = ("/api/auth/",)
# 가입 전 공개 경로는 연구용 집계와 UI 버전뿐이다. 지역별·개인별 데이터는 절대 여기 넣지 않는다.
_OPEN_PATHS = ("/api/backtest", "/api/client-version")


from realty_signal.routes import deps
from realty_signal.services import market_data as md
from realty_signal.services.quote_check import MAX_SOURCE_AGE_DAYS

_uid = deps.uid
_is_opus_user = deps.is_opus_user
_is_admin = deps.is_admin
_fav_context = deps.fav_context
_usage_status = deps.usage_status
_usage_allow = deps.usage_allow


def _personal_listings_allowed(*, request: Request | None = None, uid: int | None = None) -> bool:
    """바로이집 원문·파생 매물은 한 계정의 응답/자문에만 포함한다."""
    if request is not None:
        return deps.personal_listings_allowed(request)
    return config.personal_listing_allowed(db.user_email(uid))


_REFRESH_EVERY_DAYS = 7   # 관측이 신선할 때의 KB 점검 간격
_STALE_REFRESH_EVERY_DAYS = 0.25  # 관측이 지연되면 6시간마다 새 공표분 확인
_REFRESH_RETRY_HOURS = 6  # 수집 실패 시 하루를 기다리지 않고 이만큼 뒤 재시도

# 수집 상태 kv 키 — **실패를 화면에서 볼 수 있게 하는 것이 목적이다.**
# 예전엔 실패가 log.error 로만 남아, prod 컨테이너 로그를 못 보면 2주 내내 실패해도
# 아무도 몰랐다(2026-09-06 조사: prod 기준일이 8/24 에 13.5일 멈춰 있었다).
_KB_LAST_ERROR = "kb_fetch_error"      # {"ts","message","consecutive"}
_KB_LAST_ATTEMPT = "kb_fetch_attempt"  # 마지막 시도 시각(성공·실패 무관)


def kb_fetch_health() -> dict:
    """KB 수집 건강 상태. 로그 없이도 '언제 마지막으로 성공했고 왜 멈췄는지' 가 보여야 한다."""
    import time
    last_ok = db.kv_get("last_kb_fetch")
    attempt = db.kv_get(_KB_LAST_ATTEMPT)
    err = db.kv_get(_KB_LAST_ERROR) or None
    observation = db.kv_get("kb_observation_check") or {}
    now = time.time()
    return {
        "last_success_ts": last_ok,
        "last_success_days": round((now - last_ok) / 86400, 1) if last_ok else None,
        "last_attempt_ts": attempt,
        "last_attempt_days": round((now - attempt) / 86400, 1) if attempt else None,
        "error": err,
        "failing": bool(err and err.get("consecutive", 0) >= 1),
        "observation_check": observation,
    }


def _data_age_days() -> float | None:
    """현재 캐시 데이터의 경과일(없으면 None)."""
    return md.data_age_days()


def _kb_refresh_due(last_fetch: float, data_age_days: float | None, now: float) -> bool:
    """관측이 8일 넘게 지연되면, 새 공표분이 나왔는지 6시간마다 확인한다."""
    interval = (_STALE_REFRESH_EVERY_DAYS if data_age_days is None or data_age_days > 8
                else _REFRESH_EVERY_DAYS)
    return now - last_fetch >= interval * 86400


def _kb_due_now() -> bool:
    last = db.kv_get("last_kb_fetch") or 0
    return not store.CACHE_FILE.exists() or _kb_refresh_due(last, _data_age_days(), time.time())


def _do_refresh() -> dict:
    """KB 재수집 + 파생 캐시 무효화. /api/refresh·스케줄러 공용."""
    import time
    previous = db.kv_get("kb_observation_check") or {}
    kb = store.fetch()
    checked_at = time.time()
    asof = str(kb.last_date.date())
    previous_asof = previous.get("asof")
    db.kv_set("kb_observation_check", {
        "asof": asof, "previous_asof": previous_asof,
        "changed": None if previous_asof is None else asof != previous_asof,
        "checked_at": checked_at,
    })
    db.kv_set("last_kb_fetch", checked_at)   # 마지막 수집 시각(스케줄러 기준)
    md.clear_caches()
    from realty_signal.services.complex_signal import clear_uv_cache
    clear_uv_cache()
    _presale.cache_clear()
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
    changes = snap.advance(cur, asof)
    try:
        from realty_signal.brain import outcomes
        recs = json.loads(_signals_df().to_json(orient="records", force_ascii=False))
        outcomes.append_region_snapshot(asof, recs)
    except Exception as e:  # noqa: BLE001
        log.warning("outcome snapshot skip: %s", e)
    try:
        recs = json.loads(_signals_df().to_json(orient="records", force_ascii=False))
        from realty_signal.services import signal_assessment
        assessments = [signal_assessment.build(md.kb(), row, md.signal_config(),
                                               asof=md.kb().last_date.date()) for row in recs]
        signal_assessment.issue_many(assessments)
    except Exception as e:  # noqa: BLE001
        log.warning("signal assessment issuance skip: %s", e)
    try:
        from realty_signal.ingest import pipeline
        pipeline.build_market_strength()
    except Exception as e:  # noqa: BLE001
        log.warning("market strength rebuild skip: %s", e)
    return changes


_BRIEFING_TICK = 900    # 텔레그램 폴링·발송 점검 주기(초)


def _briefing_hour() -> int:
    """데일리 브리핑 발송 시각(KST). 기본 08시."""
    try:
        return max(0, min(23, int(os.environ.get("BRIEFING_HOUR", "8"))))
    except ValueError:
        return 8


async def _briefing_loop():
    """텔레그램 연결 폴링(항상) + 하루 1회 브리핑 발송(KST 지정 시각 이후).

    웹훅을 쓰지 않으므로 연결 대기 중인 /start 를 이 루프가 걷어 간다.
    발송 여부는 날짜 키로 관리해 재배포로 루프가 재시작돼도 중복 발송하지 않는다.
    """
    import asyncio

    from realty_signal import briefing as br
    from realty_signal import jobs
    from realty_signal import telegram as tg
    while True:
        try:
            if tg.available():
                await asyncio.to_thread(tg.poll_updates)
                today = br.today_kst().isoformat()
                if br.now_kst().hour >= _briefing_hour():
                    def send_today():
                        stats = br.run(send=True, quiet=True)
                        db.kv_set(f"briefing_run:{today}", stats)
                        log.warning("데일리 브리핑: %s", stats)
                        return {"ok": stats["errors"] == 0}
                    await asyncio.to_thread(lambda: jobs.run(
                        f"telegram_briefing:{today}", send_today, interval=86400, retry=900))
        except Exception as e:  # noqa: BLE001
            log.error("브리핑 루프 실패: %s", e)
        await asyncio.sleep(_BRIEFING_TICK)


def _scheduled_backup():
    from realty_signal import backup

    if not backup.enabled():
        raise backup.BackupNotConfigured()
    if backup.run_backup() is None:
        raise backup.BackupUploadFailed()


def _refresh_kb_if_due():
    """Shared by scheduled and read-triggered refreshes under the KB job lease."""
    if not _kb_due_now():
        return
    db.kv_set(_KB_LAST_ATTEMPT, time.time())
    try:
        result = _do_refresh()
        db.kv_set(_KB_LAST_ERROR, None)
        return result
    except Exception as exc:
        prev = db.kv_get(_KB_LAST_ERROR) or {}
        db.kv_set(_KB_LAST_ERROR, {"ts": time.time(), "message": type(exc).__name__,
                                  "consecutive": int(prev.get("consecutive") or 0)+1})
        raise


async def _auto_refresh_loop():
    """Independent due times: a failing source never delays other source jobs."""
    from realty_signal import jobs
    import asyncio
    import time

    def locality_job():
        if (config.public_data_key() and config.odsay_analysis_approved()
                and config.odsay_cache_approved() and store.load_localities().empty):
            store.build_localities()

    def school_zone_job():
        from realty_signal.ingest import school_zone
        return school_zone.refresh()

    def watch_alert_job():
        from realty_signal.services import watch_alerts_v2
        return watch_alerts_v2.scan_cached_sources()

    def region_alert_job():
        from realty_signal.services import region_alerts_v2
        return region_alerts_v2.scan_issued()

    def presale_alert_job():
        from realty_signal.services import presale_alerts_v2
        return presale_alerts_v2.scan_fresh_announcements()

    def telegram_update_job():
        from realty_signal.services import telegram_updates
        return telegram_updates.run()

    def hank_job():
        from realty_signal.ingest import external
        return external.refresh_hank_cache()

    def hank_due() -> bool:
        from realty_signal.ingest import external
        return external.hank_due()

    specs = [
        ("kb", _refresh_kb_if_due, 6*3600),
        ("quicksale", lambda: quicksale_refresh({}) if _quicksale_stale() else None, 3600),
        ("certified", lambda: certified_refresh({}) if _certified_stale() else None, 3600),
        ("hanbang", lambda: hanbang_refresh({})
         if config.personal_listing_email() and _hanbang_stale() else None, 3600),
        ("localities", locality_job, 86400),
        ("school_zones", school_zone_job, 7*86400),
        ("backup", _scheduled_backup, 86400),
        ("watch_alerts", watch_alert_job, 900),
        ("region_alerts", region_alert_job, 900),
        ("presale_alerts", presale_alert_job, 6*3600),
        ("telegram_updates", telegram_update_job, 900),
        ("hank", hank_job, 12 * 3600),
    ]
    # Each loop owns a renewable lease and its own retry/due clock.
    async def serve(name, fn, interval):
        while True:
            try:
                await asyncio.to_thread(jobs.run, name, fn, interval=interval, retry=3600,
                                        expedite=(name == "kb" and _kb_due_now()) or
                                                 (name == "hanbang" and bool(config.personal_listing_email())
                                                  and _hanbang_stale()) or
                                                 (name == "quicksale" and _quicksale_stale()) or
                                                 (name == "certified" and _certified_stale()) or
                                                 (name == "hank" and hank_due()))
            except Exception:
                log.error("job state failure: %s", name)
            await asyncio.sleep(60)
    tasks = [asyncio.create_task(serve(*spec)) for spec in specs]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _seed_if_missing():
    """빈 볼륨(첫 배포) 자동 시딩 — 파일 없을 때·키 있을 때만, 각 단계 best-effort.

    KB(시그널)만 부팅 자동수집되고 저평가·급매·재건축은 별도 생성이 필요해,
    신규 배포/볼륨 리셋 시 통합 매물·급지가 비어 보이는 것을 방지한다.
    """
    pk = config.public_data_key()
    if (pk and config.odsay_analysis_approved() and config.odsay_cache_approved()
            and store.load_localities().empty):          # 저평가·급지
        try:
            log.warning("localities 없음 — 저평가·급지 빌드 중(수 분)…")
            store.build_localities()
        except Exception as e:  # noqa: BLE001
            log.error("localities 시딩 실패: %s", e)
    # 바로집 원천은 공공데이터 키를 쓰지 않는다. 키 유무로 막으면 빈 볼륨 배포에서
    # 급매 캐시가 영구히 생성되지 않는다.
    if _quicksale_stale():                               # 급매 레이더 (없거나 1일 경과·옛 버전)
        try:
            log.warning("quicksale 없음/만료 — 급매 레이더 스캔 중…")
            quicksale_refresh({})
        except Exception as e:  # noqa: BLE001
            log.error("quicksale 시딩 실패: %s", e)
    if _certified_stale():                               # 찐매물(내집등록) — 급매와 동일 주기
        try:
            log.warning("certified 없음/만료 — 찐매물 스캔 중…")
            certified_refresh({})
        except Exception as e:  # noqa: BLE001
            log.error("certified 시딩 실패: %s", e)
    if config.seoul_key():                               # 재건축 워밍(BUY+ 지역)
        try:
            log.warning("재건축 워밍 중(BUY+ 지역)…")
            _redev_zones()
            for r in (r for r, s in _display_signal_map().items() if s in ("STRONG_BUY", "BUY")):
                if db.kv_get(f"redev_cand:{r}", max_age=30 * 86400) is None:
                    try:
                        _redev_candidates(r)
                    except Exception as e:  # noqa: BLE001
                        log.warning("redev warm %s 실패: %s", r, e)
        except Exception as e:  # noqa: BLE001
            log.error("재건축 시딩 실패: %s", e)
    try:  # 시딩 전 빈 데이터로 캐시된 파생결과 무효화
        md.clear_caches()
    except Exception:  # noqa: BLE001
        pass


async def _startup_bg():
    """Serve immediately; each source starts independently under a durable lease."""
    await _auto_refresh_loop()


def _narrative_private_allowed(uid: int) -> bool:
    c = db.conn()
    try:
        row = c.execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()
        return bool(row) and config.personal_listing_allowed(row[0])
    finally:
        c.close()


async def _explanation_loop():
    """Resume explicit explanation jobs after restarts without repeating paid attempts."""
    import asyncio

    from realty_signal.services import report_narrative

    while True:
        try:
            processed = await asyncio.to_thread(
                report_narrative.run_once, private_user_allowed=_narrative_private_allowed)
        except Exception as exc:  # noqa: BLE001
            log.warning("report explanation worker failure: %s", exc)
            processed = False
        await asyncio.sleep(0.25 if processed else 3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    config.load_env()
    task = asyncio.create_task(_startup_bg())  # 수집·갱신은 백그라운드, 서버는 즉시 서빙
    brief = asyncio.create_task(_briefing_loop())  # 텔레그램 폴링·데일리 브리핑
    explanation = asyncio.create_task(_explanation_loop())
    yield
    task.cancel()
    brief.cancel()
    explanation.cancel()
    await asyncio.gather(task, brief, explanation, return_exceptions=True)


class SafeJSONResponse(JSONResponse):
    """NaN·Infinity 를 `null` 로 직렬화한다.

    기본 `JSONResponse` 는 `allow_nan=False` 라 NaN 이 하나만 섞여도
    **엔드포인트 전체가 500** 이 된다(2026-09-06 prod 실측:
    `ValueError: Out of range float values are not JSON compliant: nan`).

    값 하나가 비었다고 화면 전체를 죽이는 건 어떤 경우에도 잘못된 트레이드오프다.
    누락은 `null` 로 내보내고 화면이 '–' 로 처리하게 둔다.
    **이건 안전망이지 면죄부가 아니다** — NaN 이 나오는 계산부는 따로 고친다.
    """

    def render(self, content) -> bytes:
        from realty_signal import jsonx

        return jsonx.dumps(content, separators=(",", ":")).encode("utf-8")


app = FastAPI(title="realty-signal-map", lifespan=lifespan,
              default_response_class=SafeJSONResponse)

from realty_signal.routes.auth import router as auth_router  # noqa: E402
from realty_signal.routes.alerts import router as alerts_router  # noqa: E402
from realty_signal.routes.advisor import router as advisor_router  # noqa: E402
from realty_signal.routes.market import router as market_router  # noqa: E402
from realty_signal.routes.auction import router as auction_router  # noqa: E402
from realty_signal.routes.personal import router as personal_router  # noqa: E402
from realty_signal.routes.complex import router as complex_router  # noqa: E402
from realty_signal.routes.redev import router as redev_router  # noqa: E402
from realty_signal.routes.strategy import router as strategy_router  # noqa: E402
from realty_signal.routes.geo import router as geo_router  # noqa: E402
from realty_signal.routes.brain import router as brain_router  # noqa: E402
from realty_signal.routes.home import router as home_router  # noqa: E402
from realty_signal.routes.reports_v2 import router as reports_v2_router  # noqa: E402

app.include_router(auth_router, default_response_class=SafeJSONResponse)
app.include_router(alerts_router, default_response_class=SafeJSONResponse)
app.include_router(advisor_router, default_response_class=SafeJSONResponse)
app.include_router(market_router, default_response_class=SafeJSONResponse)
app.include_router(auction_router, default_response_class=SafeJSONResponse)
app.include_router(personal_router, default_response_class=SafeJSONResponse)
app.include_router(complex_router, default_response_class=SafeJSONResponse)
app.include_router(redev_router, default_response_class=SafeJSONResponse)
app.include_router(strategy_router, default_response_class=SafeJSONResponse)
app.include_router(geo_router, default_response_class=SafeJSONResponse)
app.include_router(brain_router, default_response_class=SafeJSONResponse)
app.include_router(home_router, default_response_class=SafeJSONResponse)
app.include_router(reports_v2_router, default_response_class=SafeJSONResponse)


def _signal_config() -> SignalConfig:
    return md.signal_config()


def _clear_signal_caches() -> None:
    md.clear_caches()


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    """인증된 유저만 데이터 API 접근. /api/auth/*·집계 공개 경로·비-API(/, 정적)는 허용."""
    p = request.url.path
    if p.startswith("/api/") and not p.startswith(_OPEN_PREFIXES) and p not in _OPEN_PATHS:
        if not _uid(request):
            return JSONResponse({"error": "인증이 필요합니다.", "auth": False}, status_code=401)
    response = await call_next(request)
    if p.startswith("/api/") and p not in _OPEN_PATHS:
        response.headers["Cache-Control"] = "private, no-store"
    return response


def _user_nbhd_diffs(uid: int, regions: set[str]) -> dict[str, list]:
    out: dict[str, list] = {}
    for region in regions:
        weeks = db.nbhd_snap_weeks(uid, region, 2)
        if len(weeks) < 2:
            continue
        curr = db.nbhd_snap_get(uid, region, weeks[0])
        prev = db.nbhd_snap_get(uid, region, weeks[1])
        if curr and prev:
            if not _personal_listings_allowed(uid=uid):
                curr = {**curr, "급매": None}
                prev = {**prev, "급매": None}
            out[region] = _nbhd_diff(curr, prev)
    return out


def myfeed(request: Request):
    """내 관심 피드 — 즐겨찾기 지역·단지의 활동(급매·청약·최근 실거래) 개인화 집계. 로그인 필요."""
    uid = _uid(request)
    if not uid:
        return {"ok": False, "reason": "login_required"}
    favs = db.fav_list(uid)
    regions = db.actionable_region_favs(uid)
    complexes = [f["key"] for f in favs if f["kind"] == "complex"]   # "region|name"
    if not regions and not complexes:
        return {"ok": True, "empty": True, "items": []}
    sig = _display_signal_map()
    # 급매(지역별)
    qs = []
    try:
        if _personal_listings_allowed(request=request) and QUICKSALE_FILE.exists():
            qs = _radar_verified_rows(QUICKSALE_FILE, _QUICKSALE_SCAN_VER)
    except Exception:  # noqa: BLE001
        qs = []
    # 청약(지역별, 임박)
    ps = []
    try:
        ps = [_presale_current(raw) for raw in _presale()]
        ps = [d for d in ps if d.get("Dday") is not None and 0 <= d["Dday"] <= 45]
    except Exception:  # noqa: BLE001
        ps = []
    items = []
    qs = [m for m in qs if _listing_region_matches_kb(m.get("지역"), m.get("시도"), m.get("지역코드"))]
    for r in regions:
        rq = [m for m in qs if m.get("지역") == r]
        rp = [d for d in ps if d.get("_signal_region") == r]
        items.append({"type": "region", "region": r, "signal": sig.get(r, ""),
                      "급매": len(rq) if _personal_listings_allowed(request=request) else None,
                      "급매갭": None, "personal_only": not _personal_listings_allowed(request=request),
                      "청약임박": len(rp),
                      "청약단지": (rp[0].get("단지명") if rp else None)})
    for key in complexes:
        ref, _, name = key.partition("|")
        identity = db.complex_favorite_region(ref)
        if identity["status"] != "ready":
            items.append({"type": "complex", "key": key, "region": ref, "name": name,
                          "지역확인필요": True, "데이터없음": True})
            continue
        region, code = identity["name"], identity["code"]
        d = db.kv_get(f"complex:{code[:5]}:{name}", max_age=30 * 86400) if code[:5].isdigit() else None
        metrics = _main_flat_metrics(d or {})
        # 캐시에 단지시그널이 없어도 지역시그널+실거래로 즉시 산출(공시비율은 myfeed에서 생략 — 느림)
        cs = {}
        if d and (d.get("총거래") or d.get("매매추이")):
            cs = _complex_signal(region, d, sig.get(region, ""), None)
        items.append({"type": "complex", "key": key, "region": region, "name": name,
                      "최근평단가": (d or {}).get("최근평단가"), "추세pct": (d or {}).get("추세pct"),
                      "단지등급": cs.get("등급"), "단지점수": cs.get("점수"),
                      "전세가율": metrics.get("전세가율"), "갭": metrics.get("갭"),
                      "주력평형": metrics.get("주력평형"), "spark": metrics.get("spark") or [],
                      "근거부족": cs.get("근거부족") or [],
                      "데이터없음": d is None})
    return {"ok": True, "empty": False, "items": items, "기준일": str(_kb().last_date.date())}



WEB_DIR = Path(__file__).parent / "web"
_CLIENT_VERSION_MARKER = "__SIGNAL_APT_CLIENT_VERSION__"


def _client_version_for(web_dir: Path) -> str:
    digest = hashlib.sha256()
    for name in ("index.html", "signal-v2.js"):
        data = (web_dir / name).read_bytes()
        digest.update(name.encode("ascii"))
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()[:20]


@lru_cache(maxsize=1)
def _client_version() -> str:
    """Immutable UI revision, computed once per deployment process."""
    return _client_version_for(WEB_DIR)


def _kb():
    return md.kb()


def _codes_nospace():
    """공백 제거 키 → 지역코드. 매물/급매 지역('성남시분당구')과 codes 키('성남시 분당구') 표기차 흡수."""
    return md.codes_nospace()


def _code_of(region: str) -> str:
    """지역명 → 지역코드. 정확 매칭 실패 시 공백 무시로 재시도(경기 시-구 매물 실거래 매칭 보장)."""
    return md.code_of(region)


def _regime():
    return md.regime()


def _signals_df():
    return md.signals_df()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Revalidate the HTML by content and expose its matching JS revision."""
    from fastapi.responses import Response

    version = _client_version()
    etag = f'W/"{version}"'
    headers = {"ETag": etag, "Cache-Control": "no-cache, must-revalidate"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    body = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(body.replace(_CLIENT_VERSION_MARKER, version), headers=headers)


@app.get("/api/client-version")
def client_version():
    """Lightweight public check for tabs that were open across a deployment."""
    return JSONResponse({"client_version": _client_version()},
                        headers={"Cache-Control": "no-store"})


@app.get("/assets/signal-v2.js")
def signal_v2_script():
    """Small isolated interface module; deployed with the HTML revision."""
    from fastapi.responses import Response

    return Response((WEB_DIR / "signal-v2.js").read_text(encoding="utf-8"),
                    media_type="text/javascript; charset=utf-8",
                    headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/assets/gtx-evidence.json")
def gtx_evidence():
    """Versioned, source-linked GTX map evidence; only fetched when the layer is opened."""
    from fastapi.responses import Response

    return Response((WEB_DIR / "gtx-evidence.json").read_text(encoding="utf-8"),
                    media_type="application/json; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/legal/terms", response_class=HTMLResponse)
def legal_terms():
    from realty_signal.legal import TERMS_HTML
    return TERMS_HTML


@app.get("/legal/privacy", response_class=HTMLResponse)
def legal_privacy():
    from realty_signal.legal import PRIVACY_HTML
    return PRIVACY_HTML


@app.get("/sudo_gu.geojson")
def sudo_gu_geojson():
    """수도권(서울·인천·경기) 시군구 경계 — 저평가 탭 급지 지도용(단순화 번들)."""
    from fastapi.responses import Response
    p = WEB_DIR / "sudo_gu.geojson"
    if not p.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return Response(p.read_text(encoding="utf-8"), media_type="application/geo+json",
                    headers={"Cache-Control": "public, max-age=86400"})


def _backtest():
    return md.backtest()


_ADV_RANK = {"STRONG_BUY": 0, "BUY": 1, "WATCH": 2, "NEUTRAL": 3, "SELL_RISK": 4}

# 저장된 규제 가정은 최신 고시가 검증되기 전까지 현재 지정 현황이 아니다.
from realty_signal import regulation as _reg  # noqa: E402

_REGULATION_ASOF = f"{_reg.AS_OF} 저장 규칙 · 현재 지정 미검증"
_REGULATION_NOTICE = "현재 규제 지정과 적용 요건을 검증하지 못했습니다. 계약·대출 전 공식 고시와 금융기관에 확인하세요."


def _regulation_verified() -> bool:
    manifest = _reg.policy_manifest()
    regions = next((rule for rule in manifest.get("rules", []) if rule.get("id") == "regions"), {})
    try:
        today = today_kst()
        effective = date.fromisoformat(regions["effective_from"])
        reviewed = date.fromisoformat(str(regions["verified_at"])[:10])
        review_due = date.fromisoformat(regions["review_due"])
        until = date.fromisoformat(regions["effective_until"]) if regions.get("effective_until") else None
    except (KeyError, TypeError, ValueError):
        return False
    return (manifest.get("status") == "verified" and regions.get("status") == "verified"
            and bool(regions.get("sources")) and bool(regions.get("verified_at"))
            and effective <= today <= review_due and reviewed <= today
            and (until is None or today <= until))


def _regulation_asof() -> str:
    return (f"{_reg.AS_OF} 기준 (검증된 목록 · 시군구 근사)" if _regulation_verified()
            else _REGULATION_ASOF)


def _regulation_of(region: str, sido: str | None = None) -> list[str]:
    return _reg.designations(region, sido or _sido_of(region)) if _regulation_verified() else []


def regulation_api():
    """Expose current designations only after source and review-date verification."""
    verified = _regulation_verified()
    return {
        "status": "verified" if verified else "unverified",
        "asof": _regulation_asof(),
        "map": ({r: list(_reg.DESIGNATION_TAGS) for r in _reg.regulated_regions()} if verified else {}),
        "notes": _reg.PARTIAL_NOTE if verified else {},
        "notice": None if verified else _REGULATION_NOTICE,
    }



def _adv_region_row(r: dict) -> dict:
    """자문 tool 용 지역 시그널 축약(+규제지역)."""
    out = {k: r.get(k) for k in ("region", "급지", "전세수급", "매수우위지수",
           "매매모멘텀", "공급압력", "저평가도", "수급출처", "근거", "해설") if r.get(k) is not None}
    out["signal"] = _adv_safe_grade(r)
    out["assessment_status"] = r.get("assessment_status") or "held"
    tags = _regulation_of(r.get("region") or "")
    if tags:
        out["규제지역"] = tags
        out["규제기준"] = _regulation_asof()
    elif not _regulation_verified():
        out["규제검증상태"] = "unverified"
    return out


def _adv_safe_grade(row: dict) -> str:
    grade = row.get("display_signal")
    return grade if row.get("assessment_status") == "ready" and grade in {
        "STRONG_BUY", "BUY", "WATCH", "NEUTRAL", "SELL_RISK"} else "HELD"


def advisor_tools(uid: int, listing_key: str | None = None,
                  comparison_keys: list[str] | None = None):
    """Bind identity once per request; never read process-global user state."""
    from functools import partial
    return partial(_advisor_tool, uid=uid, listing_key=listing_key,
                   comparison_keys=comparison_keys)


def _advisor_tool(name: str, args: dict, *, uid: int | None = None,
                  listing_key: str | None = None,
                  comparison_keys: list[str] | None = None) -> dict:
    """자문 에이전트 tool 실행 — 기존 데이터 함수로 위임(server-side)."""
    if name == "get_selected_listing_report":
        from realty_signal.services import property_analysis as analysis
        if not listing_key:
            return {"error": "선택한 매물이 없습니다."}
        try:
            row = analysis.resolve(listing_key, private_allowed=_personal_listings_allowed(uid=uid))
        except (ValueError, PermissionError, LookupError):
            return {"error": "선택 매물을 현재 수집분에서 확인할 수 없습니다."}
        try:
            d = complex_detail(row["지역"], row["단지명"]) if row.get("지역") and row.get("단지명") else None
        except Exception:  # noqa: BLE001
            d = {"status": "failed", "degraded": True}
        report = analysis.build(row, d, profile=db.profile_get(uid) if uid else None)
        report.pop("trades", None)
        return report
    if name == "get_selected_listing_location":
        from realty_signal.services import listing_location as location
        from realty_signal.services import property_analysis as analysis
        if not listing_key:
            return {"error": "선택한 매물이 없습니다."}
        try:
            row = analysis.resolve(listing_key, private_allowed=_personal_listings_allowed(uid=uid))
        except (ValueError, PermissionError, LookupError):
            return {"error": "선택 매물을 현재 수집분에서 확인할 수 없습니다."}
        result = location.build(row, db.profile_get(uid) if uid else {},
                                db.entrance_get(uid, listing_key) if uid else None)
        result.pop("listing", None)
        for route in (result.get("mobility") or {}).values():
            if isinstance(route, dict):
                route.pop("path", None)
        return result
    if name == "get_selected_listing_kapt":
        from realty_signal.ingest import kapt
        from realty_signal.services import property_analysis as analysis
        if not listing_key:
            return {"error": "선택한 매물이 없습니다."}
        try:
            row = analysis.resolve(listing_key, private_allowed=_personal_listings_allowed(uid=uid))
        except (ValueError, PermissionError, LookupError):
            return {"error": "선택 매물을 현재 수집분에서 확인할 수 없습니다."}
        return kapt.lookup(row.get("단지명") or "", _code_of(row.get("지역") or "")[:5])
    if name == "get_selected_listing_comparison":
        from realty_signal.services import listing_compare as compare
        from realty_signal.services import property_analysis as analysis
        if not comparison_keys or len(comparison_keys) not in (2, 3):
            return {"error": "비교할 매물 2~3개가 선택되지 않았습니다."}
        try:
            rows = [analysis.resolve(key, private_allowed=_personal_listings_allowed(uid=uid))
                    for key in comparison_keys]
        except (ValueError, PermissionError, LookupError):
            return {"error": "비교 매물을 현재 수집분에서 확인할 수 없습니다."}
        return compare.build(rows, complex_detail, db.profile_get(uid) if uid else {})
    if name == "get_user_context":
        from realty_signal.brain import memory as nick_mem
        if not uid:
            return {"error": "login_required"}
        fav = _fav_context(uid)
        return {
            "favorites": fav,
            "memory": nick_mem.to_public(nick_mem.load(uid)),
            "profile_keys": list((db.profile_get(uid) or {}).keys()),
        }
    if name == "list_signal_regions":
        rows = signals()
        want = (args.get("signal") or "").upper()
        if want:
            rows = [r for r in rows if _adv_safe_grade(r) == want]
        rows = sorted(rows, key=lambda r: (_ADV_RANK.get(_adv_safe_grade(r), 9), -(r.get("전세수급") or 0)))
        lim = min(int(args.get("limit") or 15), 40)
        return {"regions": [{"region": r.get("region"), "signal": _adv_safe_grade(r),
                             "assessment_status": r.get("assessment_status") or "held",
                             "급지": r.get("급지"), "전세수급": r.get("전세수급"),
                             "매수우위지수": r.get("매수우위지수")} for r in rows[:lim]]}
    if name == "get_region_signal":
        region = (args.get("region") or "").strip()
        rows = signals()
        hit = next((r for r in rows if r.get("region") == region), None)
        return _adv_region_row(hit) if hit else {"error": f"'{region}' 지역 데이터를 찾지 못했습니다. 정확한 시군구명을 선택해 주세요."}
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
                "급지", "시그널", "단지시그널", "공시대비", "status", "message", "degraded", "identity_status") if d.get(k) is not None}
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
                "research_only": True, "주의": bt.get("주의"), "protocol": bt.get("protocol"),
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
            items = _build_listings({kind}, include_private=_personal_listings_allowed(uid=uid))
            if region:
                items = [x for x in items if x.get("지역") == region]
            items = sorted(items, key=lambda x: x.get("타이밍점수") or 0, reverse=True)[:8]
            return {"layer": "listing", "asof": _timing_asof(),
                    "listings": [{"단지명": x.get("단지명"), "지역": x.get("지역"), "유형": x.get("유형"),
                                  "타이밍점수": x.get("타이밍점수"), "타이밍근거": x.get("타이밍근거"),
                                  "confidence": x.get("confidence")} for x in items]}
        return {"error": f"unknown layer '{layer}'"}
    if name == "get_strength":
        return strength_api(region=(args.get("region") or "").strip() or None)
    if name == "get_regime":
        rg = _regime() or {}
        keys = ("phase", "beta", "gap", "color", "desc", "endgame",
                "ascents", "descents", "ladder_corr", "tier_avgs",
                "n_regions", "e_median_price", "quality", "quality_note")
        out = {k: rg.get(k) for k in keys if k in rg}
        if rg.get("evidence"):
            out["evidence"] = rg["evidence"]
        # Nick이 BUY와 끝물을 같은 축으로 섞지 않게
        out["how_to_read"] = (
            "지역 BUY/매도 시그널은 그 동네 KB 수급·모멘텀(정본). "
            "국면(끝물)은 수도권 유동성 경고. 둘 다 매수+끝물이면 막차 가능성으로 설명."
        )
        return out
    if name == "get_news":
        try:
            return news_summary(topic=args.get("topic"))
        except Exception:  # noqa: BLE001
            return {"error": "뉴스 조회 실패"}
    if name == "get_freshness":
        return freshness()
    if name == "get_regulation":
        region = (args.get("region") or "").strip()
        if not _regulation_verified():
            return {**regulation_api(), "region": region or None,
                    "current_designations": None, "LTV": None}
        if region:
            tags = _regulation_of(region)
            c = _reg.classify(region, _sido_of(region))
            return {"region": region, "status": "verified", "규제지역": tags or "확인된 목록에 지정 없음",
                    "수도권": c["수도권"], "기준": _regulation_asof()}
        return {**regulation_api(), "기준": _regulation_asof()}
    if name == "get_presale":
        region = (args.get("region") or "").strip()
        try:
            items = _presale_visible_items()
        except Exception:  # noqa: BLE001
            return {"error": "청약 조회 실패"}
        if region:
            items = [d for d in items if d.get("지역") == region
                     and _listing_region_matches_kb(region, d.get("시도"))]
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
        return {"region": region, "시그널": _display_signal_map().get(region, "HELD"), "candidates": cands}
    if name == "get_listings":
        region = (args.get("region") or "").strip()
        kind = args.get("kind") or "급매"
        private_allowed = _personal_listings_allowed(uid=uid)
        if kind in ("일반매물", "급매", "찐매물") and not private_allowed:
            return {"reason": "personal_only", "result": "외부 매물은 개인 계정에서만 확인할 수 있습니다."}
        out: dict = {}
        if private_allowed and kind in ("급매", "찐매물", "전체"):
            out["가격근거주의"] = "급매는 공급사 표시입니다. 동일 전용면적·거래조건이 확인되지 않은 중위가격과 갭은 제공하지 않습니다."
        safe_signals = _display_signal_map() if private_allowed and kind in ("급매", "찐매물", "전체") else {}
        if kind in ("일반매물", "전체") and private_allowed:
            hb = _hanbang_verified_rows()
            if region:
                hb = [m for m in hb if m.get("지역") == region]
            out["일반매물"] = [{"매물키": _listing_key("일반매물", m, {"hanbang_id": m.get("hanbang_id")},
                                            m.get("단지명"), m.get("지역")),
                               "단지명": m.get("단지명"), "지역": m.get("지역"),
                               "호가": m.get("호가"), "전용면적": m.get("전용면적"),
                               "층": m.get("층"), "등록일": m.get("등록일")}
                              for m in hb[:10]]
        if kind in ("급매", "전체") and private_allowed:
            try:
                qs = _radar_verified_rows(QUICKSALE_FILE, _QUICKSALE_SCAN_VER)
            except Exception:  # noqa: BLE001
                qs = []
            if region:
                qs = [m for m in qs if m.get("지역") == region]
            qs = qs[:10]
            out["급매"] = [{"단지명": m.get("단지명"), "지역": m.get("지역"), "평형": m.get("평형"),
                          "호가": m.get("호가"), "급매갭": None,
                          "시그널": safe_signals.get(m.get("지역"), "HELD")
                          if _listing_region_matches_kb(m.get("지역"), m.get("시도"), m.get("지역코드")) else "HELD"} for m in qs]
        if kind in ("찐매물", "전체") and private_allowed:
            try:
                cs = _radar_verified_rows(CERTIFIED_FILE, _CERTIFIED_SCAN_VER)
            except Exception:  # noqa: BLE001
                cs = []
            if region:
                cs = [m for m in cs if m.get("지역") == region]
            cs = cs[:10]
            out["찐매물"] = [{"단지명": m.get("단지명"), "지역": m.get("지역"), "평형": m.get("평형"),
                            "호가": m.get("호가"), "시세갭": None,
                          "시그널": safe_signals.get(m.get("지역"), "HELD")
                          if _listing_region_matches_kb(m.get("지역"), m.get("시도"), m.get("지역코드")) else "HELD"} for m in cs]
        if kind in ("경매", "전체"):
            from realty_signal.auction import AUCTION_FILE
            try:
                au = json.loads(AUCTION_FILE.read_text(encoding="utf-8")) if AUCTION_FILE.exists() else []
                au = au if isinstance(au, list) else au.get("listings", [])
            except Exception:  # noqa: BLE001
                au = []
            if region:
                au = [m for m in au if m.get("region") == region
                      and region not in db.AMBIGUOUS_LEGACY_REGION_KEYS]
            out["경매"] = [{"단지명": m.get("단지명"), "region": m.get("region"), "최저매각가": m.get("최저매각가"),
                          "감정가": m.get("감정가"), "유찰횟수": m.get("유찰횟수"), "입찰기일": m.get("입찰기일")} for m in au[:10]]
        if not any(out.get(k) for k in ("일반매물", "급매", "찐매물", "경매")):
            return {"result": "해당 조건의 매물이 없습니다(외부 매물은 수집 범위와 기준일을 확인하세요)."}
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


def admin_policy_list(request: Request):
    if not _is_admin(request):
        return JSONResponse({"error": "admin_only"}, status_code=403)
    _seed_policies()
    return {"policies": db.policy_all()}



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



def admin_policy_delete(request: Request, pid: int):
    if not _is_admin(request):
        return JSONResponse({"error": "admin_only"}, status_code=403)
    db.policy_delete(pid)
    return {"ok": True}



def _nick_system(uid: int | None) -> str:
    from realty_signal import advisor
    from realty_signal.brain import memory as nick_mem
    profile = db.profile_get(uid) if uid else {}
    fav = _fav_context(uid) if uid else {}
    mem = nick_mem.load(uid) if uid else None
    return advisor.build_system(profile, fav, memory=mem)


def _nick_remember(uid: int, messages: list, answer: str | None = None) -> None:
    from realty_signal.brain import memory as nick_mem
    try:
        known = list(_kb().regions) if store.CACHE_FILE.exists() else []
    except Exception:  # noqa: BLE001
        known = []
    try:
        nick_mem.update_from_messages(uid, messages, known_regions=known, answer=answer)
    except Exception as e:  # noqa: BLE001
        log.warning("nick memory skip: %s", e)


def _signal_map() -> dict:
    return md.signal_map()


def _display_signal_map() -> dict:
    """User-facing grades fail closed while preserving candidate-region coverage."""
    try:
        raw = _signal_map()
    except Exception as exc:  # noqa: BLE001 — 원천 장애는 원시 BUY 재노출 사유가 아니다.
        log.warning("raw signal map unavailable: %s", exc)
        return {}
    try:
        labels = md.assessed_signal_labels(today_kst().isoformat())
    except Exception as exc:  # noqa: BLE001 — 판정 실패는 원시 BUY 노출 사유가 아니다.
        log.warning("display signal assessment unavailable: %s", exc)
        labels = {}
    allowed = {"STRONG_BUY", "BUY", "WATCH", "NEUTRAL", "SELL_RISK"}
    visible = {}
    for region in raw:
        label = labels.get(region) or {}
        grade = label.get("display_signal")
        visible[region] = grade if label.get("assessment_status") == "ready" and grade in allowed else "HELD"
    return visible


def _auction_signal_map() -> dict:
    """소재 시·도/코드가 없는 경매의 동명이 지역은 지역 시그널을 붙이지 않는다."""
    return {region: grade for region, grade in _display_signal_map().items()
            if region not in db.AMBIGUOUS_LEGACY_REGION_KEYS}


def signals(only: str | None = None):
    """시그널 레코드 리스트 — Nick·동네 리포트·advisor tool 공용 (라우트는 market router)."""
    from realty_signal.routes.market import signals as _market_signals
    return _market_signals(only)


_GRADES_TTL = 7 * 86400   # 국토부 실거래 6개월치 — 주 1회면 충분


def _region_grades(region: str):
    """시군구 단지별 급지 랭킹 (국토부 실거래 평단가 순위).

    프로세스 lru + DB 7일 캐시. 콜드 상태에서 지역당 6회 API 호출이라
    재시작마다 다시 긁으면 대시보드가 수십 초 멈춘다.
    """
    from realty_signal.ingest.complex_grade import region_grades
    code = _code_of(region)
    if not (code and code.isdigit() and code[2:5] != "000"):
        return []  # 시군구 단위만 (광역/시도는 단지 랭킹 부적합)
    ckey = f"region_grades:{code[:5]}"
    cached = db.kv_get(ckey, max_age=_GRADES_TTL)
    if cached is not None:
        return cached
    config.load_env()
    from realty_signal.ingest.complex import SourceUnavailable
    try:
        out = region_grades(code[:5], config.public_data_key())
    except SourceUnavailable:
        return [{**row, "데이터상태": "stale"} for row in (db.kv_get(ckey) or [])]
    if out:
        db.kv_set(ckey, out)
    return out


def complex_grades(region: str):
    """지역 내 단지단위 급지 랭킹 — 저평가 탭 드릴다운용."""
    return {"region": region, "complexes": _region_grades(region)}


def region_trade_prices(region_ref: str, area: int = 84):
    """Comparable-area district sale sample; no licensed locality model involved."""
    from realty_signal.ingest.complex import SourceUnavailable
    from realty_signal.ingest.complex_grade import region_trade_prices as collect_prices

    if area not in (59, 84):
        return {"status": "unsupported_area"}
    identity = md.current_region_identity(region_ref)
    if identity is None or identity["code"][2:5] == "000":
        return {"status": "region_unverified"}
    code = identity["code"][:5]
    from realty_signal.auction import _recent_yms
    cache_key = f"region_trade_prices:v1:{code}:{area}:{_recent_yms(1)[0]}"
    cached = db.kv_get(cache_key, max_age=6 * 3600)
    if cached is not None:
        return cached
    config.load_env()
    try:
        sample = collect_prices(code, config.public_data_key(), area)
    except SourceUnavailable:
        old = db.kv_get(cache_key)
        return {**old, "status": "stale"} if old is not None else {
            "status": "source_unavailable", "region": identity["name"], "area": area}
    from realty_signal.time_kst import now_kst
    result = {**sample, "region": identity["name"], "region_id": region_ref,
              "area": area, "computed_at": now_kst().isoformat(),
              "source": "국토교통부 아파트 매매 실거래 공개자료"}
    db.kv_set(cache_key, result)
    return result



def undervalued():
    """수도권 시군구 저평가 랭킹 (입지 대비 가격). 시그널 등급 병합."""
    df = store.load_localities()
    if df.empty:
        permitted = config.odsay_analysis_approved() and config.odsay_cache_approved()
        return {"ready": False, "listings": [],
                "reason": "insufficient_verified_data" if permitted else "source_permission_required"}
    sig = _display_signal_map()
    recs = json.loads(df.to_json(orient="records", force_ascii=False))
    for r in recs:
        r["시그널"] = sig.get(r["region"], "")
    return {"ready": True, "listings": recs}



_SIDO = {"11": "서울", "26": "부산", "27": "대구", "28": "인천", "29": "광주", "30": "대전",
         "31": "울산", "36": "세종", "41": "경기", "43": "충북", "44": "충남", "45": "전북",
         "46": "전남", "47": "경북", "48": "경남", "50": "제주", "51": "강원", "52": "전북"}


def _presale_since() -> str:
    """최근 ~4개월 공고부터 (진행중·예정 위주)."""
    today = today_kst()
    y, m = today.year, today.month
    m -= 4
    if m <= 0:
        y, m = y - 1, m + 12
    return f"{y}-{m:02d}-01"


def _presale_status(d: dict, today: str) -> tuple[str, str | None]:
    """청약 진행상태 + 다음 일정일자. 날짜 문자열 비교(YYYY-MM-DD)."""
    def ok(s):
        try:
            value = str(s)
            return value if date.fromisoformat(value).isoformat() == value else None
        except (TypeError, ValueError):
            return None
    sp_s, sp_e = ok(d.get("특공접수시작")), ok(d.get("특공접수마감"))
    r_s, r_e = ok(d.get("청약접수시작")), ok(d.get("청약접수마감"))
    win, ct_e = ok(d.get("당첨발표")), ok(d.get("계약종료"))
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
    key = config.public_data_key()
    if not key:
        log.warning("PUBLIC_DATA_KEY 없음 — 청약 목록 비움")
        db.kv_set("presale_fetch_status", {"ok": False, "reason": "key_missing", "ts": time.time()})
        return []
    applyhome.set_key(key)
    try:
        raw = applyhome.fetch_pblanc(_presale_since())
    except Exception as e:  # noqa: BLE001
        log.error("청약홈 fetch 실패: %s", e)
        db.kv_set("presale_fetch_status", {"ok": False, "reason": type(e).__name__, "ts": time.time()})
        return []
    db.kv_set("presale_fetch_status", {"ok": True, "count": len(raw), "ts": time.time()})
    sig = _signal_map()
    regions = _regime().get("regions", {})
    codes = _kb().codes or {}
    region_sido = {r: _SIDO.get((c or "")[:2]) for r, c in codes.items()}  # 시군구→시도 (동명이군 구분)
    today_date = today_kst()
    today = today_date.isoformat()
    out = []
    for d in raw:
        sgg = d["시군구"]
        # 시군구명 + 시도 둘 다 일치할 때만 결합(부산 강서구↔서울 강서구 오매칭 방지)
        region = sgg if (sgg in sig and region_sido.get(sgg) == d["시도"]) else None
        d["_signal_region"] = region
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
        d["Dday"] = (date.fromisoformat(nxt) - today_date).days if nxt else None
        out.append(d)
    return out


def _presale_visible_items() -> list[dict]:
    """Cached announcement facts stay reusable; current signal safety is overlaid at read time."""
    safe = _display_signal_map()
    items = []
    for row in _presale():
        item = _presale_current(row)
        item["시그널"] = safe.get(item.pop("_signal_region", None), "HELD")
        items.append(item)
    return items


def _presale_current(row: dict) -> dict:
    """Recompute a cached announcement's relative date on each read in Korea time."""
    item = dict(row)
    today = today_kst()
    if not any(item.get(key) for key in ("특공접수시작", "특공접수마감", "청약접수시작",
                                         "청약접수마감", "당첨발표", "계약종료")):
        if item.get("다음일정"):
            try:
                item["Dday"] = (date.fromisoformat(item["다음일정"]) - today).days
            except (TypeError, ValueError):
                item["Dday"] = None
        return item
    status, next_date = _presale_status(item, today.isoformat())
    item["상태"] = status
    item["다음일정"] = next_date
    try:
        item["Dday"] = (date.fromisoformat(next_date) - today).days if next_date else None
    except ValueError:
        item["Dday"] = None
    return item


def _verified_home_region(profile: dict) -> dict | None:
    """Only a current, unambiguous KB identity may personalize a saved residence."""
    name = str(profile.get("거주지") or "").strip()
    ref = profile.get("거주지코드")
    if not name:
        return None
    try:
        if ref:
            identity = md.current_region_identity(ref)
        elif name not in buying_power.AMBIGUOUS_PROFILE_REGIONS:
            code = md.code_of(name)
            identity = md.current_region_identity(f"kb:{code}") if code else None
        else:
            identity = None
    except Exception:  # noqa: BLE001 — identity source failure must not imply a residence match.
        return None
    return identity if identity and identity["name"] == name else None


def _same_presale_district(identity: dict | None, row: dict) -> bool:
    """Geographic coincidence only; announcement-specific priority eligibility is not inferred."""
    if not identity or row.get("시도") != identity["sido"]:
        return False
    district = identity["name"].removeprefix(identity["sido"] + " ")
    return bool(district and row.get("지역") == district)


def presale_list(request: Request):
    """청약 단지 — 진행상태→확인된 동일 시군구→임박→안전한 시그널 순서."""
    sig_rank = {"STRONG_BUY": 0, "BUY": 1, "WATCH": 2, "NEUTRAL": 3, "SELL_RISK": 4, "HELD": 5}
    st_rank = {"접수중": 0, "접수예정": 1, "발표대기": 2, "계약중": 3, "완료": 4, "공고": 5}
    home = _verified_home_region(db.profile_get(_uid(request)))

    items = []
    for d in _presale_visible_items():
        d["거주지일치"] = _same_presale_district(home, d)
        items.append(d)

    def key(d):
        return (st_rank.get(d["상태"], 6), 0 if d["거주지일치"] else 1,
                d["Dday"] if d["Dday"] is not None else 999, sig_rank.get(d["시그널"], 5))
    return sorted(items, key=key)



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



def _max_purchase(capital: float, ltv: float, income: float | None, rate: float,
                  years: int = 30, **profile_kw):
    """자기자본으로 살 수 있는 최대 매수가(만원) + 비용분해.

    실제 계산은 `buying_power`(주택수·생애최초 취득세, 기대출 차감 DSR, 스트레스 가산,
    중개보수 구간요율, 이사·수리 현금)에 위임한다. 결론·갈아타기·확정서가 같은 엔진을 쓴다.
    """
    p = buying_power.Params(capital=capital, income=income, rate=rate, years=years,
                            ltv=ltv, **profile_kw)
    return buying_power.max_purchase(p)


def _sido_of(region: str | None) -> str | None:
    """지역명 → 시도. 동명이구(중구·강서구)의 규제지역 오판을 막는다."""
    if not region:
        return None
    from realty_signal import regulation as reg
    return reg.sido_hint(region) or _SIDO.get((_code_of(region) or "")[:2])


def _listing_region_matches_kb(region: str | None, source_sido: str | None,
                               source_code: str | None = None) -> bool:
    """외부 매물의 경계 코드(없으면 시도)를 현재 KB 지역과 대조한다."""
    if not region:
        return False
    try:
        if source_code:
            code = str(source_code)
            if len(code) != 5 or not code.isdigit():
                return False
            if source_sido and _SIDO.get(code[:2]) != source_sido:
                return False
            return str(_code_of(region))[:5] == code
        if not source_sido:
            return False
        expected = _sido_of(region)
    except Exception:  # noqa: BLE001 - KB 코드 장애는 이름만으로 통과시키지 않는다
        return False
    return bool(expected and source_sido == expected)


def _verified_listing_region_code(region: str | None, source_sido: str | None) -> str | None:
    """Map a previously source-verified sido/district pair to a current KB district code."""
    from realty_signal.services.signal_assessment import INCHEON_RETIRED_CODES

    if not region or not source_sido:
        return None
    try:
        source = _kb()
        if not source.identity_verified or region not in source.regions:
            return None
        code = str(_code_of(region))[:5]
        if (len(code) == 5 and code.isdigit() and code not in INCHEON_RETIRED_CODES
                and _SIDO.get(code[:2]) == source_sido):
            return code
    except Exception:  # noqa: BLE001 — KB 식별 장애는 코드 미확인으로 남긴다.
        pass
    return None


def _finance_region_choice(region: str | None, region_code: str | None) -> tuple:
    """An explicit buyer region may be code-proven or name-only, never inferred from a collision."""
    if region_code:
        try:
            identity = md.current_region_identity(region_code)
        except Exception as exc:  # noqa: BLE001 — 임시 원천 장애는 지역 미확인이며 금융 계산으로 승격하지 않는다.
            raise HTTPException(503, "지역 코드 자료를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.") from exc
        if not identity or (region and region != identity["name"]):
            raise HTTPException(422, "현재 지역 코드와 이름을 함께 확인할 수 없습니다. 지역을 다시 선택해 주세요.")
        return identity["name"], identity["sido"], identity["region_id"], "verified"
    if region in buying_power.AMBIGUOUS_PROFILE_REGIONS:
        raise HTTPException(422, "이름이 같은 시군구가 있습니다. 시·도가 확인된 지역을 다시 선택해 주세요.")
    if not region:
        return None, None, None, "assumed"
    try:
        code = md.code_of(region)
        identity = md.current_region_identity(f"kb:{code}") if code else None
        if identity and identity["name"] == region:
            return region, identity["sido"], identity["region_id"], "verified"
    except Exception:  # noqa: BLE001 — 원천 장애 시 이름을 코드로 승격하지 않는다.
        pass
    return None, None, None, "unverified_name"


def _default_finance_region(request: Request, profile: dict) -> tuple:
    """Prior verified code > safe legacy name > actionable favorite > conservative assumption."""
    conf = ((profile.get("매수력") or {}).get("가정") or {})
    saved_code = profile.get("매수지역코드") or conf.get("지역코드")
    if saved_code:
        try:
            identity = md.current_region_identity(saved_code)
        except Exception:  # noqa: BLE001 — 저장 코드가 확인되지 않으면 과거 이름을 대입하지 않는다.
            identity = None
        return ((identity["name"], identity["sido"], identity["region_id"], "verified")
                if identity else (None, None, None, "reselection_required"))
    saved = conf.get("지역") or profile.get("매수지역")
    if saved in buying_power.AMBIGUOUS_PROFILE_REGIONS:
        return None, None, None, "reselection_required"
    if saved:
        return _finance_region_choice(saved, None)
    uid = _uid(request)
    for name in db.actionable_region_favs(uid) if uid else []:
        if name not in buying_power.AMBIGUOUS_PROFILE_REGIONS:
            return _finance_region_choice(name, None)
    return None, None, None, "assumed"


def _buyer_params(profile, **override):
    from fastapi import HTTPException
    try:
        return buying_power.params_from_profile(profile, **override)
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(422, "자금·소득·금리·만기 입력 범위를 확인해 주세요") from None


def buying_power_statement(request: Request, **override):
    """매수력 확정서 — 프로필 기본값 + 화면 입력 override."""
    profile = db.profile_get(_uid(request)) or {}
    region_code = override.pop("region_code", None)
    region, sido, selected_code, identity_status = (
        _finance_region_choice(override.get("region"), region_code)
        if override.get("region") or region_code else _default_finance_region(request, profile))
    override["region"], override["sido"] = region, sido
    clear_monthly = override.pop("clear_monthly_budget", False)
    p = _buyer_params(profile, **override)
    if clear_monthly:
        p.monthly_budget = None
    if p.capital <= 0:
        return {"ready": False, "reason": "no_capital",
                "message": "가용자본을 입력하면 매수력을 확정할 수 있어요."}
    out = buying_power.statement(p)
    out["가정"]["지역코드"] = selected_code
    out["지역식별"] = identity_status
    out["ready"] = True
    out["확정"] = (profile.get("매수력") or {}).get("최대매수가")
    out["재확인필요"] = bool(out["확정"] and (
        buying_power.validated_confirmed_power(profile) is None
        or (profile.get("매수력") or {}).get("가정버전") != out["가정버전"]
        or identity_status in {"reselection_required", "unverified_name"}))
    return out


def buying_power_confirm(request: Request, data: dict):
    """확정서를 프로필에 저장 — 이후 추천·숏리스트·브리핑이 이 숫자를 기준으로 움직인다."""
    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    profile = db.profile_get(uid) or {}
    kw = {k: data.get(k) for k in ("capital", "income", "existing_debt_annual", "homes",
                                   "first_time", "temp_two_home", "big_area",
                                   "apply_bangongje", "region", "regulated", "dispose",
                                   "ltv", "rate", "rate_type", "years", "reserve_cash",
                                   "monthly_budget", "moving_cost", "repair_cost") if k in data}
    # Explicit null clears the optional cap; omitted values preserve saved inputs.
    if "monthly_budget" in data and data["monthly_budget"] is None:
        profile["월상환한도"] = None
        if profile.get("매수력"):
            profile["매수력"].get("가정", {}).pop("월상환한도", None)
    region_code = data.get("region_code")
    explicit_region = bool(kw.get("region") or region_code)
    region, sido, selected_code, identity_status = (
        _finance_region_choice(kw.get("region"), region_code) if explicit_region
        else _default_finance_region(request, profile))
    if explicit_region and identity_status == "unverified_name":
        return JSONResponse({"ok": False, "error": "지역 코드를 확인할 수 없습니다. 목록에서 지역을 선택하거나 지역을 비워 보수 계산해 주세요."},
                            status_code=422)
    kw["region"], kw["sido"] = region, sido
    p = _buyer_params(profile, **kw)
    if p.capital <= 0:
        return JSONResponse({"ok": False, "error": "가용자본을 입력해 주세요."}, status_code=400)
    st = buying_power.statement(p)
    st["가정"]["지역코드"] = selected_code
    st["지역식별"] = identity_status
    st["확정일"] = today_kst().isoformat()
    profile["매수력"] = st
    if p.capital:
        profile["가용자본"] = round(p.capital)
    profile["연소득"] = round(p.income or 0)
    profile["비상자금"] = p.reserve_cash
    profile["월상환한도"] = p.monthly_budget
    profile["기대출연원리금"] = round(p.existing_debt_annual or 0)
    profile["주택수"] = int(p.homes or 0)
    profile["생애최초"] = 1 if p.first_time else 0
    if p.region:
        profile["매수지역"] = p.region
    if explicit_region:
        if selected_code:
            profile["매수지역코드"] = selected_code
        else:
            profile.pop("매수지역코드", None)
    db.profile_set(uid, profile)
    return {"ok": True, "매수력": st}


def imjang_course(request: Request, on: str | None = None, start: str = "10:00",
                  stop_min: int = 50, limit: int = 3):
    """후보 3곳 → 시각이 박힌 반나절 코스. 이미 다녀온 단지는 순서를 뒤로 민다."""
    from realty_signal.services import imjang as ij

    uid = _uid(request)
    sl = shortlist(request, limit=limit)
    if not sl.get("ready"):
        return {"ready": False, "reason": sl.get("reason") or "no_candidates",
                "message": sl.get("message") or "후보가 있어야 코스를 짤 수 있어요."}
    profile = (db.profile_get(uid) or {}) if uid else {}
    home_identity = _verified_home_region(profile)
    home = (_region_centroid(home_identity["name"], home_identity["code"])
            if home_identity else None)
    visited = db.imjang_latest(uid) if uid else {}
    out = ij.build_course(sl["candidates"], start=start, stop_min=max(20, min(180, stop_min)),
                          home=home, visited=visited, on=on)
    out["집기준"] = bool(home)
    out["거주지확인필요"] = bool(profile.get("거주지") and not home_identity)
    return out


def imjang_visits(request: Request, limit: int = 50):
    from realty_signal.services import imjang as ij

    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    rows = db.imjang_list(uid, limit=max(1, min(200, limit)))
    for r in rows:
        r["약점"] = ij.weak_points(r["checks"])
    return {"ok": True, "visits": rows, "checks": ij.CHECKS}


def imjang_visit_save(request: Request, data: dict):
    """방문 1건 저장 — 같은 단지·같은 날짜는 덮어쓴다."""
    from realty_signal.services import imjang as ij

    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    region = (data.get("region") or "").strip()
    cx = (data.get("단지") or data.get("complex") or "").strip()
    if not region or not cx:
        return JSONResponse({"ok": False, "error": "지역과 단지가 필요합니다."}, status_code=400)
    visited = (data.get("방문일") or "").strip() or today_kst().isoformat()
    checks = ij.clean_checks(data.get("checks"))
    verdict = (data.get("verdict") or "").strip()
    if verdict and verdict not in ij.VERDICTS:
        verdict = ""
    sc = ij.score(checks)
    vid = db.imjang_save(uid, region, cx, visited, checks=checks,
                         memo=(data.get("memo") or "")[:2000], verdict=verdict,
                         score=(sc or {}).get("점수"))
    return {"ok": True, "id": vid, "점수": sc, "약점": ij.weak_points(checks)}


def imjang_visit_delete(request: Request, visit_id: int):
    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    return {"ok": db.imjang_delete(uid, visit_id)}


def telegram_status(request: Request):
    """봇 사용 가능 여부 + 내 연결 상태."""
    from realty_signal import telegram as tg

    uid = _uid(request)
    profile = db.profile_get(uid) or {} if uid else {}
    link = (profile.get("telegram") or {})
    return {"available": tg.available(), "linked": bool(tg.chat_id_of(profile)),
            "username": link.get("username"),
            "hour": _briefing_hour()}


def telegram_link(request: Request):
    """일회용 딥링크 발급. 유저가 봇에 /start <code> 를 보내면 폴링이 연결한다."""
    from realty_signal import telegram as tg

    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    if not tg.available():
        return JSONResponse({"ok": False, "error": "봇이 아직 설정되지 않았습니다."}, status_code=503)
    return {"ok": True, **tg.issue_link_code(uid)}


def telegram_check(request: Request):
    """연결 확인 — 대기 중인 /start 를 즉시 처리(스케줄러 폴링을 기다리지 않도록)."""
    from realty_signal import telegram as tg

    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    tg.poll_updates()
    link = (db.profile_get(uid) or {}).get("telegram") or {}
    return {"ok": True, "linked": bool(tg.chat_id_of(db.profile_get(uid))), "username": link.get("username")}


def telegram_unlink(request: Request):
    from realty_signal import telegram as tg

    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    return {"ok": True, "unlinked": tg.unlink(uid)}


def telegram_test(request: Request):
    """지금 브리핑 한 통 — 변화가 없어도 강제 발송해 연결·내용 확인."""
    from realty_signal import briefing as br
    from realty_signal import telegram as tg

    uid = _uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    chat_id = tg.chat_id_of(db.profile_get(uid))
    if not chat_id:
        return JSONResponse({"ok": False, "error": "텔레그램을 먼저 연결해 주세요."}, status_code=400)
    b = br.build(uid, force=True)
    if not b.get("send"):
        return {"ok": False, "reason": b.get("reason"),
                "error": "매수력을 확정하면 브리핑을 만들 수 있어요."}
    ok = tg.send_message(chat_id, b["text"])
    return {"ok": ok, "preview": b["text"]}


def _remember_decisions(uid: int | None, rows: list[dict]) -> None:
    if not uid or not rows:
        return
    try:
        from realty_signal.services import decision_log
        decision_log.remember(uid, rows)
    except Exception as exc:  # noqa: BLE001 — 기록 실패가 후보 응답을 막지 않는다
        log.warning("보류 기록 실패: %s", exc)


def _attach_card_lines(rows: list[dict], uid: int | None) -> list[dict]:
    from realty_signal.services import buyer_decision
    profile = db.profile_get(uid) or {} if uid else {}
    try:
        params = _buyer_params(profile) if profile else None
    except Exception:  # noqa: BLE001
        params = None
    return [buyer_decision.annotate(row, params, uid=uid, sido_of=_sido_of) for row in rows]


def shortlist(request: Request, limit: int = 3, budget: float | None = None):
    """이번 주 볼 단지 3곳 — 확정 매수력 × 라이프스타일 적합도."""
    from realty_signal.services import shortlist as sl

    explicit_budget = budget is not None
    uid = _uid(request)
    profile = dict(db.profile_get(uid) or {})
    if uid:
        profile["_favs"] = db.actionable_region_favs(uid)
    if budget is None:
        p = _buyer_params(profile)
        if p.capital <= 0:
            return {"ready": False, "reason": "no_budget",
                    "message": "가용자본을 입력하면 후보를 좁혀 드려요."}
        budget = float(buying_power.max_purchase(p)[0])
    if not budget:
        return {"ready": False, "reason": "no_budget",
                "message": "매수력을 먼저 확정해 주세요."}
    out = sl.build(profile, budget, limit=max(1, min(10, limit)), budget_is_ceiling=explicit_budget)
    out["확정예산"] = buying_power.validated_confirmed_power(profile) is not None
    _remember_decisions(uid, out.get("candidates") or [])
    return out


def conclusion(request: Request | None = None, capital: float | None = None,
               ltv: float | None = None, pyeong: float | None = None,
               income: float | None = None, rate: float | None = None,
               years: int | None = None, prefer_strong: bool = True):
    """매수력 정본 예산 + 통합 매물(`_build_listings`) 기반 추천.

    지역 평단×평형이 아니라 급매·경매·찐·청약·재건축 정규 뷰를 후보로 쓴다.
    기본 풀 STRONG_BUY(부족 시 BUY 보충). UI·AI 리포트·대시보드가 동일 응답을 소비한다.
    """
    from realty_signal.services import recommend as rec

    profile = {}
    if request is not None:
        profile = db.profile_get(_uid(request)) or {}
    override = {}
    if capital is not None:
        override["capital"] = capital
    if income is not None:
        override["income"] = income
    if ltv is not None:
        override["ltv"] = ltv
    if rate is not None:
        override["rate"] = rate
    if years is not None:
        override["years"] = years
    p = _buyer_params(profile, **override)
    py = float(pyeong) if pyeong is not None else buying_power.pyeong_of(profile.get("관심평수"))

    if p.capital <= 0:
        return {"ready": False, "reason": "no_capital", "budget": 0, "cards": [],
                "listings": [], "message": "가용자본을 입력하면 매수가능가·추천 매물을 계산합니다."}

    st = buying_power.statement(p)
    budget = float(st["최대매수가"])
    costs = st.get("비용") or {}
    equity = max(0.0, budget - float(st.get("대출") or 0))
    detail = {
        "대출": st["대출"], "승인액": st.get("승인액"), "방공제": st.get("방공제"),
        "자기자본투입": round(equity),
        "취득세": costs.get("취득세"), "중개비": costs.get("중개비"),
        "법무비": costs.get("법무비"), "인지세": costs.get("인지세"),
        "등록면허세": costs.get("등록면허세"), "국민주택채권": costs.get("국민주택채권"),
        "이사비": costs.get("이사비"), "수리비": costs.get("수리비"),
        "월상환": st.get("월상환"), "실효LTV": st.get("실효LTV"),
        "LTV상한": st.get("LTV상한"), "제약": st.get("제약"),
        "DSR제약": st.get("제약") == "DSR",
        "필요현금": st.get("필요현금"),
    }
    자금 = {
        "매수가": budget, "대출": st["대출"], "자기자본투입": round(equity),
        "실효LTV": st.get("실효LTV"), "LTV상한": st.get("LTV상한"),
        "희망LTV": p.ltv, "필요현금": st.get("필요현금"),
        "방공제": st.get("방공제"), "제약": st.get("제약"),
        "규제": st.get("규제"),
    }

    loc = store.load_localities()
    locmap = {}
    if not loc.empty:
        for r in json.loads(loc.to_json(orient="records", force_ascii=False)):
            locmap[r["region"]] = r

    raw = _build_listings({"경매", "급매", "찐매물", "청약", "재건축"},
                          include_private=_personal_listings_allowed(request=request))
    def candidate_finance(row, price):
        from dataclasses import replace
        rp = buying_power.params_for_region(p, row.get("지역"), _sido_of(row.get("지역")))
        area = row.get("전용면적") or row.get("area")
        if area is not None:
            try:
                rp = replace(rp, big_area=float(area) > 85)
            except (TypeError, ValueError):
                pass
        return buying_power.for_price(price, rp)

    listings = rec.rank_listings(
        raw, budget=budget, pyeong=py,
        loc_price_of=lambda region: (locmap.get(region) or {}).get("price"),
        prefer_strong=prefer_strong, limit=40,
        finance_of=candidate_finance,
    )
    from realty_signal.services import buyer_decision
    # 응답 경량화 — 프론트·AI에 필요한 필드만
    slim = []
    for L in listings:
        slim.append({
            "유형": L.get("유형"), "단지명": L.get("단지명"), "지역": L.get("지역"),
            "시그널": L.get("시그널"), "지역급지": L.get("지역급지"),
            "총액": L.get("총액"), "추정가": L.get("추정가"), "평형": L.get("평형"),
            "지표라벨": L.get("지표라벨"), "지표값": L.get("지표값"),
            "지표단위": L.get("지표단위"), "기회도": L.get("기회도"),
            "예산내": L.get("예산내"), "예산비율": L.get("예산비율"),
            "가격출처": L.get("가격출처"), "예산확인필요": L.get("예산확인필요"),
            "판단": L.get("판단"), "자금": L.get("자금"), "판단버전": L.get("판단버전"),
            "입찰상태": L.get("입찰상태"), "검토용상한": L.get("검토용상한"),
            "ref": L.get("ref"), "_score": L.get("_score"),
            **buyer_decision.build(L, p, uid=_uid(request) if request else None),
        })
    cards = rec.aggregate_regions(listings, locmap=locmap, budget=budget, pyeong=py)
    _remember_decisions(_uid(request) if request else None, slim)
    return {
        "ready": True,
        "budget": budget, "pyeong": py, "ltv": p.ltv, "capital": round(p.capital),
        "income": round(p.income) if p.income else None,
        "detail": detail, "자금": 자금, "안내": st.get("안내") or [],
        "listings": slim, "cards": cards,
        "prefer_strong": prefer_strong,
        "counts": {
            "listings": len(slim),
            "strong": sum(1 for x in slim if x.get("시그널") == "STRONG_BUY"),
            "in_budget": sum(1 for x in slim if x.get("예산내")),
        },
    }



_GRADE_ORDER = {"A": 4, "B": 3, "C": 2, "D": 1}


def tradeup(current_region: str, current_value: float, loan_balance: float = 0,
            extra_cash: float = 0, ltv: float = 0.7, income: float | None = None,
            rate: float = 0.04, years: int = 30, pyeong: float = 25.7,
            request: Request | None = None):
    """갈아타기 전략 — 현 자산 매도 → 상급지/저평가 착지 후보.

    current_value: 현재 집 시세(만원), loan_balance: 대출잔액(만원),
    extra_cash: 추가 투입 현금(만원). 순자산(=시세−잔액)+추가현금 → 새 매수 예산 산출.
    현재 거주 급지 대비 상향/동급/하향으로 후보를 분류해 반환(1주택 비과세 가정·참고용).
    """
    from collections import defaultdict

    net_equity = max(0.0, current_value - loan_balance)   # 매도 시 손에 쥐는 순자산
    capital = net_equity + max(0.0, extra_cash)           # 갈아타기 가용 자기자본
    budget, budget_detail = _max_purchase(capital, ltv, income, rate, years)

    sig = _display_signal_map()
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
    for L in _build_listings({"경매", "급매"}, include_private=_personal_listings_allowed(request=request)):
        if L.get("유형") == "경매" and L.get("입찰상태") != "conditional_bid":
            continue
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
CERTIFIED_FILE = store.CACHE_DIR / "certified.json"
HANBANG_FILE = store.CACHE_DIR / "hanbang_general.json"
_QUICKSALE_SCAN_VER = 6   # 6=2026-07 행정구역 경계·시군구 코드
_CERTIFIED_SCAN_VER = 4   # 4=2026-07 행정구역 경계·시군구 코드
_HANBANG_SCAN_VER = 2   # 2=원천 시도·시군구와 목록 행의 지역을 함께 검증
_RADAR_MAX_AGE = 86400     # 재수집 주기: 하루
_LISTING_PRICE_MAX_AGE = 7 * 86400  # 정상 수집 호가의 비교 유효기간: 7일


def _radar_cache_stale(path, min_ver: int) -> bool:
    """캐시 없음 · 스캔버전 낮음 · mtime 1일 경과 → 재스캔."""
    import time
    if not path.exists():
        return True
    try:
        if time.time() - path.stat().st_mtime >= _RADAR_MAX_AGE:
            return True
        return json.loads(path.read_text(encoding="utf-8")).get("_scan_ver", 0) < min_ver
    except Exception:  # noqa: BLE001
        return True


def _quicksale_stale() -> bool:
    """급매 캐시가 없거나 옛 버전·1일 경과면 True."""
    return _radar_cache_stale(QUICKSALE_FILE, _QUICKSALE_SCAN_VER)


def _certified_stale() -> bool:
    """찐매물 캐시가 없거나 옛 버전·1일 경과면 True."""
    return _radar_cache_stale(CERTIFIED_FILE, _CERTIFIED_SCAN_VER)


def _hanbang_stale() -> bool:
    return _radar_cache_stale(HANBANG_FILE, _HANBANG_SCAN_VER)


def _radar_refresh_file(path):
    """마지막 레이더 갱신 결과. 본 캐시와 분리해 실패해도 직전 결과를 보존한다."""
    return path.with_name(f"{path.stem}_refresh.json")


def _radar_refresh_status(path) -> dict:
    try:
        return json.loads(_radar_refresh_file(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _record_radar_refresh(path, status: dict) -> None:
    from realty_signal.storage import atomic_json
    atomic_json(_radar_refresh_file(path), status)


def _radar_cached_response(path, min_ver: int) -> dict:
    """0건·필터·원천 실패·지난 결과를 화면에서 구분할 상태 계약."""
    refresh = _radar_refresh_status(path)
    if path.exists():
        try:
            out = json.loads(path.read_text(encoding="utf-8"))
            if out.get("_scan_ver", 0) < min_ver and path in {QUICKSALE_FILE, CERTIFIED_FILE}:
                return {"ready": False, "state": "identity_unverified", "listings": [],
                        "regions": out.get("regions") or [], "last_success_at": path.stat().st_mtime,
                        "refresh": refresh}
            refresh_due = _radar_cache_stale(path, min_ver)
            stale = (time.time() - path.stat().st_mtime > _LISTING_PRICE_MAX_AGE
                     or out.get("_scan_ver", 0) < min_ver)
            count = len(out.get("listings") or [])
            if refresh.get("ok") is False:
                state = "stale_failed"
            elif stale:
                state = "stale"
            elif refresh.get("ok") is not True:
                state = "unverified"
            elif refresh.get("failed_requests") or refresh.get("limited_regions"):
                state = "partial" if count else "partial_empty"
            else:
                state = "ready" if count else "empty"
            out["stale"] = stale
            out["refresh_due"] = refresh_due
            out["state"] = state
            out["last_success_at"] = path.stat().st_mtime
            out["refresh"] = refresh
            if path in {QUICKSALE_FILE, CERTIFIED_FILE}:
                from realty_signal.ingest.baroezip import safe_market_rows
                out["listings"] = safe_market_rows(out.get("listings"))
            return out
        except Exception:  # noqa: BLE001
            pass
    return {"ready": False, "state": "failed" if refresh.get("ok") is False else "never_scanned",
            "listings": [], "regions": [], "last_success_at": None, "refresh": refresh}


def _radar_verified_rows(path, min_ver: int) -> list[dict]:
    """출처 시도를 저장하지 않은 구 캐시는 추천·Nick·관심 피드에 쓰지 않는다."""
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cached, dict) or cached.get("_scan_ver", 0) < min_ver:
            return []
        rows = cached.get("listings")
        from realty_signal.ingest.baroezip import safe_market_rows
        return safe_market_rows(rows)
    except (FileNotFoundError, ValueError, TypeError):
        return []




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


def redev_candidates(region: str):
    """지역 내 재건축 잠재력 단지 랭킹 (구축, 연식·용적률·세대수·시세 기반)."""
    sig = _display_signal_map()
    cands = _redev_candidates(region)
    return {"region": region, "시그널": sig.get(region, "HELD"),
            "cached": db_has_redev_cache(region), "candidates": cands}



def db_has_redev_cache(region: str) -> bool:
    from realty_signal import db
    return db.kv_get(f"redev_cand:{region}", max_age=30 * 86400) is not None


def redev_warm(data: dict = Body(default={})):
    """비개인화 재건축 데이터 사전 계산 — 지정 지역(없으면 BUY+ 지역)을 DB에 적재.

    한 번 호출해두면 이후 재건축 탭은 DB 캐시에서 즉시 응답. (수십초~수분 소요)
    """
    from realty_signal import db
    regions = data.get("regions")
    if not regions:  # 기본: 매수 시그널(BUY+) 지역만
        sig = _display_signal_map()
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


def redev_stages(region: str | None = None):
    """정비사업 단계 현황 — 시군구별 현 단계 분포 + 단계 평균 소요기간."""
    from realty_signal.ingest import redev as rd
    sgg5 = None
    if region:
        code = _code_of(region)
        sgg5 = code[:5] if (code and code.isdigit()) else None
    return {"region": region or "서울 전체", **rd.stage_summary(_redev_progress(), sgg5)}



def geocode_ep(data: dict = Body(...)):
    """단지명/주소 목록 → 좌표 (SQLite 캐시 우선, 미스 일부만 OSM 조회).

    body: {"queries": ["서울 강남구 은마아파트", ...], "max_miss": 20}
    """
    from realty_signal.ingest import geocode
    queries = data.get("queries", [])
    max_miss = int(data.get("max_miss", 20))
    return geocode.geocode_batch(queries, max_miss=max_miss)



def mapconfig():
    """지도 타일 설정 — VWorld 키 있으면 한글 타일 URL, 없으면 null(프론트 CartoDB 폴백)."""
    k = config.vworld_key()
    return {"vworld": k or None}



def transit_ep(sx: float, sy: float, ex: float, ey: float):
    """두 좌표 간 직접 경로조회. 저장 허가가 없으면 응답을 캐시하지 않는다."""
    from realty_signal import db
    from realty_signal.ingest import locality
    ck = f"transit:{sx:.3f},{sy:.3f}->{ex:.3f},{ey:.3f}"
    if config.odsay_cache_approved():
        cached = db.kv_get(ck, max_age=30 * 86400)
        if cached is not None:
            return {**cached, "cached": True}
    r = locality.transit_between(sx, sy, ex, ey)
    out = {"available": r is not None, "route": r}
    if r and config.odsay_cache_approved():
        db.kv_set(ck, out)
    return out



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



def cycle(region: str = "서울"):
    """부동산 경기 사이클 국면(벌집순환 4국면) + 근거. 광역(기본 서울) 주간 시리즈 기반."""
    from realty_signal.signals import cycle as cyc
    return cyc.current_phase(_kb(), region) or {"phase": None}



def cycle_history(region: str = "서울"):
    """지역 시기별 경기 국면 타임라인 — 시그널 차트 오버레이용 밴드."""
    from realty_signal.signals import cycle as cyc
    kb = _kb()
    return {"region": region, "bands": cyc.cycle_history(kb, region)}



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



def _address_region_identity(address: str) -> dict | None:
    """Resolve a Kakao address only when both province and district match current KB."""
    matches = []
    address_parts = address.split()
    if len(address_parts) < 2:
        return None
    for name, code in (_kb().codes or {}).items():
        code = str(code)
        sido = md.SIDO_LABELS.get(code[:2])
        if not sido or not address_parts[0].startswith(sido):
            continue
        district = name.removeprefix(sido + " ")
        district_parts = district.split()
        if address_parts[1:1 + len(district_parts)] != district_parts:
            continue
        try:
            identity = md.current_region_identity(f"kb:{code}")
        except Exception:  # noqa: BLE001 — unverified source stays unselected.
            return None
        if identity and identity["name"] == name and identity["sido"] == sido:
            matches.append(identity)
    # The source can contain province-level and district-level rows; select the
    # most specific district only, and never guess across an equally specific tie.
    matches = [m for m in matches if len(m["code"]) == 10 and m["code"][2:5] != "000"]
    matches.sort(key=lambda m: len(m["name"].removeprefix(m["sido"] + " ")), reverse=True)
    if not matches:
        return None
    best = len(matches[0]["name"].removeprefix(matches[0]["sido"] + " "))
    return matches[0] if sum(len(m["name"].removeprefix(m["sido"] + " ")) == best for m in matches) == 1 else None


def addr_search(q: str):
    """거주지 검색 — 현재 시·도와 시군구가 확인된 결과에만 지역 코드를 제공."""
    import json as _json
    import urllib.parse
    import urllib.request
    key = config.kakao_key()
    if not key:
        config.load_env(); key = config.kakao_key()
    if not key or not q.strip():
        return {"results": []}
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
        identity = _address_region_identity(addr)
        nm = d.get("place_name", "")
        key2 = (nm, addr)
        if not addr or key2 in seen:
            continue
        seen.add(key2)
        out.append({"name": nm, "address": addr,
                    "sigungu": identity["name"] if identity else "",
                    "sido": identity["sido"] if identity else "",
                    "region_id": identity["region_id"] if identity else None})
        if len(out) >= 8:
            break
    return {"results": out}



_COMPLEX_TTL = MAX_SOURCE_AGE_DAYS * 86400   # 목록·리포트가 같은 캐시 유효기간을 사용한다.


def _uv_map():
    from realty_signal.services.complex_signal import uv_map
    return uv_map()


def _main_flat_metrics(data: dict) -> dict:
    from realty_signal.services.complex_signal import main_flat_metrics
    return main_flat_metrics(data)


def _complex_signal(region: str, data: dict, signal: str | None, gongsi_ratio: float | None = None) -> dict:
    from realty_signal.services.complex_signal import complex_signal
    return complex_signal(region, data, signal, gongsi_ratio)



def _gongsi_for(region: str, name: str, *, cache_only: bool = False) -> dict | None:
    """단지 공동주택 공시가격(VWorld WFS) — 좌표 bbox 조회 → 이름/최근접 매칭. 캐시(90일, 공시가 연1회)."""
    from realty_signal import db
    ck = f"gongsi:{region}:{name}"
    cached = db.kv_get(ck, max_age=90 * 86400)
    if cached is not None:
        return cached or None
    if cache_only:
        return None
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


def complex_detail(region: str, name: str, *, cache_only: bool = False):
    """단지 deep-dive — 실거래 매매·전세 추이 + 평형별 + 전세가율·갭 + 단지 시그널 + 공시가격. DB 캐시(7일)."""
    from realty_signal import db
    from realty_signal.services.complex_signal import region_price_context
    grade = (_regime().get("regions", {}).get(region) or {}).get("급지")
    signal = _display_signal_map().get(region, "HELD")

    def deco(d):   # 급지·시그널·공시가격·단지시그널·지역대비는 응답 시점에 부착(각자 캐시)
        out = {**d, "region": region, "급지": grade, "시그널": signal}
        if d.get("status") in {"ambiguous", "failed", "stale", "unavailable"}:
            return out
        ratio = None
        try:                                                     # 공시가격 먼저 → 단지시그널 가격 성분에 사용
            g = _gongsi_for(region, name, cache_only=cache_only)
            if g and g.get("㎡단가"):
                out["공시가격"] = g
                last, gpy = out.get("최근평단가"), g["㎡단가"] * 3.3058 / 10000
                if last and gpy:
                    ratio = round(last / gpy, 2)                 # 실거래/공시 배수(>1=실거래 우위)
                    out["공시대비"] = ratio
        except Exception as e:  # noqa: BLE001
            log.warning("공시가격 조회 실패 %s/%s: %s", region, name, e)
        out.update(region_price_context(region, out.get("최근평단가")))
        if not d.get("지원안함") and (d.get("총거래") or d.get("매매추이")):
            out["단지시그널"] = _complex_signal(region, out, signal, ratio)
        return out

    code = _code_of(region)
    if not (code and code.isdigit() and len(code) >= 5):
        return deco({"단지명": name, "지원안함": True, "평형별": [], "매매추이": []})
    lawd5 = code[:5]
    ckey = f"complex:{lawd5}:{name}"
    cached = db.kv_get(ckey, max_age=_COMPLEX_TTL)
    if cached is not None and cached.get("schema_version") == 4:
        return deco({**cached, "cached": True})
    if cache_only:
        previous = db.kv_get(ckey)
        return deco({**previous, "status": "stale", "degraded": True}
                    if isinstance(previous, dict) and previous.get("schema_version") == 4 else
                    {"단지명": name, "status": "unavailable", "평형별": [], "매매추이": []})
    from realty_signal.ingest import complex as cx
    config.load_env()
    pk = config.public_data_key()
    if not pk:
        return deco({"단지명": name, "지원안함": True, "평형별": [], "매매추이": []})
    try:
        data = cx.fetch_complex(lawd5, name, pk)
    except cx.SourceUnavailable:
        previous = db.kv_get(ckey)
        return deco({**(previous or {"단지명": name, "평형별": [], "매매추이": []}),
                     "status": "stale" if previous else "failed", "degraded": True,
                     "message": "원천 조회 실패 — 거래 0건을 의미하지 않습니다"})
    data["region"] = region
    db.kv_set(ckey, data)
    return deco(data)


def complex_quote_check(region: str, name: str, data: dict):
    """사용자 입력 호가를 국토부 동일면적 거래분포와 대조하되 저장하지 않는다."""
    from fastapi import HTTPException
    from realty_signal.services import quote_check

    if not isinstance(data, dict):
        raise HTTPException(422, "호가와 전용면적을 입력해 주세요")
    try:
        asking, area = quote_check.validate_input(data.get("asking"), data.get("exclusive_m2"))
        floor = quote_check.validate_floor(data.get("floor"))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return quote_check.assess(complex_detail(region, name), asking=asking, exclusive_m2=area, floor=floor)



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
        for m in _radar_verified_rows(QUICKSALE_FILE, _QUICKSALE_SCAN_VER):
            r, n = m.get("지역"), m.get("단지명")
            if not _listing_region_matches_kb(r, m.get("시도"), m.get("지역코드")):
                continue
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


def complex_backtest_api():
    """단지 시그널(가격추세 성분) 검증 성적표. 캐시(30일) — 없으면 미계산 안내."""
    from realty_signal import db
    cached = db.kv_get("complex_backtest", max_age=30 * 86400)
    return {**cached, "cached": True} if cached is not None else {"ready": False}



def complex_backtest_run(request: Request):
    """단지 백테스트 계산·캐시(수 분 소요, 관리자용). 로그인 필요."""
    if not _uid(request):
        return {"ready": False, "error": "로그인 필요"}
    return _complex_backtest()



def warm_favorite_complex(region: str, name: str) -> dict:
    """관심단지 하나의 실거래 캐시를 준비한다. 등록 직후와 주간 워밍이 함께 쓴다."""
    from realty_signal import db
    from realty_signal.ingest import complex as cx
    identity = db.complex_favorite_region(region)
    if identity["status"] != "ready":
        return {"status": "unavailable", "reason": "ambiguous_region" if identity["status"] == "needs_reselection" else "unverified_region"}
    config.load_env()
    pk = config.public_data_key()
    if not pk:
        return {"status": "unavailable", "reason": "no_key"}
    code = identity["code"]
    if not (code and code.isdigit() and len(code) >= 5):
        return {"status": "unavailable", "reason": "invalid_region"}
    if code[2:5] == "000":
        # 국토부 API는 '서울' 같은 시·도 코드로 단지를 특정할 수 없다.
        return {"status": "unavailable", "reason": "sido_level"}
    ckey = f"complex:{code[:5]}:{name}"
    if db.kv_get(ckey, max_age=_COMPLEX_TTL) is not None:
        return {"status": "skipped"}
    try:
        data = cx.fetch_complex(code[:5], name, pk)
        data["region"] = identity["name"]
        db.kv_set(ckey, data)
        return {"status": "warmed"}
    except Exception as e:  # noqa: BLE001
        log.warning("관심단지 워밍 실패 %s/%s: %s", region, name, e)
        return {"status": "error"}


def warm_favorite_complexes() -> dict:
    """전체 관심단지 실거래 캐시 워밍(주간 스케줄러용). 14일내 신선한 건 건너뜀."""
    from realty_signal import db

    warmed = skipped = unavailable = errors = 0
    for region, name in db.all_fav_complexes():
        status = warm_favorite_complex(region, name).get("status")
        if status == "warmed":
            warmed += 1
        elif status == "skipped":
            skipped += 1
        elif status == "unavailable":
            unavailable += 1
        else:
            errors += 1
    return {"warmed": warmed, "skipped": skipped, "unavailable": unavailable, "errors": errors}


# 가치기준 → (필드, 작을수록 유리?, 표시라벨, 값포맷)
_CMP_CRIT = {
    "가격":     ("최근평단가", True,  "최근 거래월 평균 평단", lambda v: f"{round(v):,}만"),
    "가격변화": ("추세pct",   False, "2년 관측 변화", lambda v: f"{v:+g}%"),
    "전세차액": ("갭",       True,  "매매-전세 차액", lambda v: f"{v/10000:.1f}억"),
    "거래활동": ("총거래",    False, "조회기간 신고거래", lambda v: f"{round(v)}건"),
}
_CMP_ALIASES = {"상승여력": "가격변화", "실투자금": "전세차액", "유동성": "거래활동"}


def _comp_area_comparable(complexes: list) -> bool:
    areas = [c.get("전용㎡") for c in complexes]
    return bool(areas and all(isinstance(a, (int, float)) and a > 0 for a in areas)
                and max(areas) - min(areas) <= 1)


def _compare_rule(criterion: str, complexes: list) -> str:
    """규칙기반 비교 해설 — 선택 가치기준에서 1등 단지 + 반대관점 주의."""
    criterion = _CMP_ALIASES.get(criterion, criterion)
    spec = _CMP_CRIT.get(criterion)
    if not spec or not complexes:
        return "비교할 데이터가 부족합니다."
    field, lower_better, label, fmt = spec
    if criterion == "전세차액" and not _comp_area_comparable(complexes):
        return "주력 전용면적이 서로 달라 매매-전세 차액을 같은 기준으로 비교할 수 없습니다."
    have = [c for c in complexes if c.get(field) is not None]
    if not have:
        return f"{label} 데이터가 있는 단지가 없어 비교가 어렵습니다."
    best = (min if lower_better else max)(have, key=lambda c: c[field])
    msg = f"선택한 {label} 수치만 보면 {best.get('단지명','–')}가 {fmt(best[field])}입니다."
    if criterion == "전세차액":
        msg += " 취득세·수리비·대출·보증금 반환 부담을 포함한 실제 필요현금은 아닙니다."
    elif criterion == "가격변화":
        msg += " 과거 변화는 향후 상승 여력을 뜻하지 않습니다."
    elif criterion == "거래활동":
        msg += " 단지 규모를 보정하지 않은 건수라 매도 소요기간을 뜻하지 않습니다."
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
        if crit == "전세차액" and (len(have) != len(valid) or not _comp_area_comparable(valid)):
            continue
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
    msg = f"선택한 관측 지표({'·'.join(criteria)}) 순서에서는 {w['단지명']}가 앞섭니다"
    msg += f" ({parts})." if parts else "."
    if len(ranked) > 1:
        msg += f" 차순위는 {ranked[1]['단지명']}입니다."
    return msg + " 이 순위는 현재 매수가·실투자금·미래 수익률을 검증한 매수 추천이 아닙니다."


def _verified_comparison(data):
    from fastapi import HTTPException
    raw = data.get("complexes") or []
    if not isinstance(raw, list) or not 1 <= len(raw) <= 5:
        raise HTTPException(422, "비교 단지는 1~5곳만 선택해 주세요")
    out = []
    for row in raw:
        if not isinstance(row, dict):
            raise HTTPException(422, "단지 식별자가 필요합니다")
        region, name = row.get("region") or row.get("지역"), row.get("단지명")
        if not isinstance(region, str) or not isinstance(name, str) or not region or not name or len(region) > 60 or len(name) > 100:
            raise HTTPException(422, "지역과 단지명을 확인해 주세요")
        detail = complex_detail(region, name)
        main = max(detail.get("평형별") or [{}], key=lambda x: x.get("매매건수") or 0)
        comp = main.get("비교거래") or {}
        gap = main.get("갭") if comp.get("상태") in ("관측", "표본적음") else None
        out.append({**detail, "전세가율": main.get("전세가율"), "갭": gap,
                    "전용㎡": main.get("전용㎡")})
    return out


def compare_recommend_api(request: Request, data: dict = Body(...)):
    """비교 단지(2+) + 중시가치(복수) → 종합점수 순위 + 추천 단지·해설(Claude, 없으면 규칙기반)."""
    from realty_signal import ai_report
    config.load_env()
    criteria = [_CMP_ALIASES.get(c, c) for c in (data.get("criteria") or [])]
    criteria = list(dict.fromkeys(c for c in criteria if c in _CMP_CRIT))
    complexes = _verified_comparison(data)
    excluded = []
    if "전세차액" in criteria and (not _comp_area_comparable(complexes)
                                or any(c.get("갭") is None for c in complexes)):
        criteria.remove("전세차액")
        excluded.append("매매-전세 차액: 주력 전용면적 또는 최근 매매·전세 거래월이 비교 불가")
    if not criteria or len(complexes) < 2:
        return {"ok": False, "reason": "no_comparable_data" if excluded else "need_criteria_and_2_complexes",
                "제외기준": excluded}
    ranked, used = _compare_score(criteria, complexes)
    if not ranked or not used:
        return {"ok": False, "reason": "no_comparable_data", "제외기준": excluded}
    opus = _is_opus_user(request)
    model = ai_report.OPUS if opus else ai_report.SONNET   # 추천은 다인자 종합 → Sonnet 이상
    tier = "opus" if opus else "sonnet"
    from realty_signal.services.buyer_decision import cache_payload, fingerprint
    sig = fingerprint(cache_payload([sorted(criteria), complexes, model, "buyer-explanation-v4"]))
    ckey = f"cmpreco:v4:{_uid(request)}:{sig}"
    cached = db.kv_get(ckey, max_age=14 * 86400)
    if cached is not None:
        return {**cached, "cached": True}
    text = ai_report.compare_recommend(criteria, complexes, ranked, model=model, uid=_uid(request))
    out = {"ok": True, "추천": ranked[0]["단지명"], "순위": ranked,
           "해설": text or _recommend_rule(criteria, ranked), "ai": bool(text), "제외기준": excluded}
    if text:
        db.kv_set(ckey, out)
    return out



def compare_insight_api(request: Request, data: dict = Body(...)):
    """비교 단지 + 가치기준 → 한줄 해설(Claude, 없으면 규칙기반)."""
    from realty_signal import ai_report
    config.load_env()
    criterion = _CMP_ALIASES.get(data.get("criterion") or "가격", data.get("criterion") or "가격")
    if criterion not in _CMP_CRIT:
        return {"해설": "선택한 지표는 안전한 단지 비교 기준으로 제공하지 않습니다.", "ai": False}
    complexes = _verified_comparison(data)
    if criterion == "전세차액" and (not _comp_area_comparable(complexes)
                               or any(c.get("갭") is None for c in complexes)):
        return {"해설": "주력 전용면적 또는 최근 매매·전세 거래월이 달라 차액 비교를 보류합니다.", "ai": False}
    opus = _is_opus_user(request)
    model = ai_report.OPUS if opus else ai_report.HAIKU   # 화이트리스트=Opus, 그 외=Haiku(단순 태스크)
    tier = "opus" if opus else "haiku"
    # 캐시: 기준 + 단지 시그니처(이름·핵심수치) + 티어 → 반복 클릭 재과금 방지
    from realty_signal.services.buyer_decision import cache_payload, fingerprint
    sig = fingerprint(cache_payload([criterion, complexes, model, "buyer-explanation-v4"]))
    ckey = f"cmpins:v4:{_uid(request)}:{sig}"
    cached = db.kv_get(ckey, max_age=14 * 86400)
    if cached is not None:
        return {**cached, "cached": True}
    text = ai_report.compare_insight(criterion, complexes, model=model, uid=_uid(request))
    out = {"해설": text or _compare_rule(criterion, complexes), "ai": bool(text)}
    if text:   # 규칙기반 폴백은 무료·즉시 → 캐시 불필요
        db.kv_set(ckey, out)
    return out



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
            r = locality.transit_between(lng, lat, jlng, jlat, purpose="analysis")
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
        return today_kst().strftime("%G-W%V")


def _local_development_docs(region: str) -> list[dict]:
    """Only documents explicitly scoped to this district may appear as local plans."""
    return [{"title": row["title"], "source": row["source"], "eff_date": row["eff_date"]}
            for row in db.policy_local_development(region)]


def neighborhood(request: Request, region: str):
    """동네 딥다이브 — 보유 데이터 재조립 + 생활인프라. 로그인 시 주간 스냅샷 저장·지난 대비 diff."""
    sigrow = next((r for r in signals() if r.get("region") == region), None)
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
            qs = (_radar_verified_rows(QUICKSALE_FILE, _QUICKSALE_SCAN_VER)
                  if _personal_listings_allowed(request=request) else [])
            return sum(1 for m in qs if m.get("지역") == region
                       and _listing_region_matches_kb(m.get("지역"), m.get("시도"), m.get("지역코드")))
        except Exception:  # noqa: BLE001
            return 0
    ps_cnt = 0
    try:
        ps_cnt = sum(1 for d in _presale() if d.get("_signal_region") == region)
    except Exception:  # noqa: BLE001
        pass
    # 미래가치
    redev_n = len(_redev_candidates(region) or []) if db_has_redev_cache(region) else None
    dev = _local_development_docs(region)
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
    # 업무지구 경로는 분석 허가 전 조회하지 않고, 저장 허가 전 캐시하지 않는다.
    cmk = f"nbhd_commute:{region}"
    config.load_env()
    analysis_ok = config.odsay_analysis_approved()
    cache_ok = analysis_ok and config.odsay_cache_approved()
    commute = db.kv_get(cmk, max_age=30 * 86400) if cache_ok else None
    if commute is None and c and analysis_ok:
        commute = _nbhd_commute(c[0], c[1])
        if commute and cache_ok:
            db.kv_set(cmk, commute)
    asof = str(_kb().last_date.date())
    week = _nbhd_week()
    from realty_signal import personal_layer as pl
    vol = pl.volume_summary(region)
    out = {
        "ok": True, "region": region, "시그널": sigrow.get("display_signal", "HELD"),
        "판정상태": sigrow.get("assessment_status", "held"),
        "급지": sigrow.get("급지"),
        "해설": sigrow.get("해설") if sigrow.get("assessment_status") == "ready" else "현재 지역 판정 보류 · 최신 근거를 다시 확인해 주세요.",
        "근거": sigrow.get("근거") if sigrow.get("assessment_status") == "ready" else None,
        "전세수급": sigrow.get("전세수급"), "매수우위지수": sigrow.get("매수우위지수"),
        "매매모멘텀": sigrow.get("매매모멘텀"), "공급압력": sigrow.get("공급압력"),
        "수급출처": sigrow.get("수급출처"),
        "국면": {"phase": rg.get("phase") or _regime().get("phase"),
                "color": _regime().get("color")},
        "평단가": lr.get("price"), "저평가도": lr.get("저평가도"), "입지점수": lr.get("입지점수"),
        "규제지역": _regulation_of(region), "규제기준": _regulation_asof(),
        "규제검증상태": "verified" if _regulation_verified() else "unverified",
        "미래가치": {"재건축후보수": redev_n, "개발계획": dev},
        "매물": {"급매": _qs_count() if _personal_listings_allowed(request=request) else None,
                 "청약": ps_cnt},
        "거래량": vol,
        "거시": pl.macro_latest(),
        "입지상세": pl.locality_bits(lr),
        "외부확인": pl.ext_links(region),
        "임장체크항목": pl.IMJANG_CHECKS,
        "생활인프라": infra or {}, "인프라기준": "구 중심 반경 1.5km · 카카오 로컬(참고용)",
        "통근": commute or {}, "통근기준": ("구 중심 → 업무지구 대중교통(ODsay·참고용)"
                                            if analysis_ok else "교통 데이터 분석 이용범위 확인 전"),
        "기준일": asof, "week": week,
    }
    try:
        from realty_signal.ingest import pipeline
        ent = pipeline.region_entity(region, signal=sigrow.get("display_signal", "HELD"))
        out["시장강도"] = ent.market_strength
        out["시장강도라벨"] = ent.market_strength_label
        out["급매건수"] = ent.quicksale_count
        if ent.provenance:
            out["provenance"] = ent.provenance.to_dict()
    except Exception:  # noqa: BLE001
        pass
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
            prev_data = dict(prev["data"])
            if not _personal_listings_allowed(request=request):
                prev_data["급매"] = None
            out["prev"] = {"week": prev["week"], "data": prev_data}
            out["diff"] = _nbhd_diff(metrics, prev_data)
        out["snap_weeks"] = db.nbhd_snap_weeks(uid, region)
    return out



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


def region_centroids(regions: str):
    """시군구 중심좌표 배치 — 이름 또는 검증된 kb:코드 → 좌표·식별 증명.

    핀 폴백용: 청약처럼 lat/lng 없는 매물은 확인 가능한 지역 중심에 표시한다.
    동명이 시군구의 이름만 있으면 다른 지역 중심으로 추측하지 않는다.
    """
    out, identities = {}, {}
    for ref in [r for r in regions.split(",") if r][:60]:
        try:
            identity = (md.current_region_identity(ref) if ref.startswith("kb:")
                        else _verified_home_region({"거주지": ref}))
        except Exception:  # noqa: BLE001 — identity failure must not be promoted to a verified pin.
            identity = None
        if not identity and (ref.startswith("kb:") or ref in buying_power.AMBIGUOUS_PROFILE_REGIONS):
            continue
        region = identity["name"] if identity else ref
        code = identity["code"] if identity else _code_of(region)
        c = _region_centroid(region, code)
        if c:
            out[ref] = [c[0], c[1]]
            if identity:
                identities[ref] = {"name": identity["name"], "sido": identity["sido"],
                                   "region_id": identity["region_id"]}
    return {"centroids": out, "identities": identities}



@lru_cache(maxsize=1)
def _sigungu_polys():
    """2026-07 수도권 시군구 경계 → [(name, sido, code, outer_rings, bbox)]."""
    try:
        g = json.loads((WEB_DIR / "sudo_gu.geojson").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for f in g.get("features", []):
        props = f.get("properties") or {}
        nm = props.get("name")
        sido = {"11": "서울", "28": "인천", "41": "경기"}.get(str(props.get("sido") or ""))
        code = str(props.get("code") or "")
        geom = f.get("geometry") or {}
        polys = geom.get("coordinates") or []
        if geom.get("type") == "Polygon":
            polys = [polys]
        rings = [poly[0] for poly in polys if poly]          # 외곽 링만(홀 무시 — 시군구엔 사실상 없음)
        pts = [pt for r in rings for pt in r]
        if not pts:
            continue
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        out.append((nm, sido, code, rings, (min(xs), min(ys), max(xs), max(ys))))
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


def _sigungu_identity_at(lat, lng) -> tuple[str, str, str] | None:
    """좌표 → 현재 시군구명·시도·코드. 동명이 구는 이름만으로 매칭하지 않는다."""
    if not lat or not lng:
        return None
    for nm, sido, code, rings, (x0, y0, x1, y1) in _sigungu_polys():
        if nm and sido and len(code) == 5 and x0 <= lng <= x1 and y0 <= lat <= y1 and any(_pip(lng, lat, r) for r in rings):
            return nm, sido, code
    return None


def _radar_scan_with_status(regions: list[str], *, kind: str = "급매") -> tuple[list[dict], dict]:
    """baroezip spatialmarket 스캔 → 급매 또는 찐매물 + 지역 시그널.

    kind='급매': 기본 피드(is_urgent). kind='찐매물': scope=all + has_certified.
    bbox 스캔은 인접 구를 덮으므로, 매물 좌표로 시군구를 역판정해 지역·급지 오분류를 막는다.
    원천 요청이 전부 실패했을 때 ``빈 결과``와 구분할 수 있도록 요청 커버리지를 남긴다.
    """
    from realty_signal.ingest.baroezip import bbox_around, fetch_market_with_status

    scope = "all" if kind == "찐매물" else None
    try:
        sig = _signal_map()
        signal_context = "available"
    except Exception as exc:  # noqa: BLE001 - 매물 수집은 KB 시그널 장애와 독립적으로 계속한다
        log.warning("급매 지역 시그널 미확인: %s", type(exc).__name__)
        sig, signal_context = {}, "unavailable"
    seen, out = set(), []
    queryable, succeeded, failures, skipped = 0, 0, [], 0
    successful_regions = []
    for region in regions:
        code = ""
        if region not in _bundled_centroids():
            try:
                code = _code_of(region)
            except Exception as exc:  # noqa: BLE001 - DB 좌표가 있으면 코드 없이도 수집한다
                log.warning("급매 지역코드 미확인 %s: %s", region, type(exc).__name__)
        c = _region_centroid(region, code)
        if not c:
            skipped += 1
            continue
        queryable += 1
        rows, error = fetch_market_with_status(*bbox_around(c[0], c[1]), scope=scope)
        if error:
            failures.append({"region": region, "error": error})
            continue
        succeeded += 1
        successful_regions.append(region)
        for m in rows:
            if kind == "급매" and not m.get("급매"):
                continue
            if kind == "찐매물" and not m.get("찐매물"):
                continue
            location = _sigungu_identity_at(m.get("lat"), m.get("lng"))
            actual, source_sido, source_code = location if location else (region, None, None)
            key = (source_code, actual, m.get("complex_no"), m.get("평형"), m.get("층"), m.get("호가"))
            if key in seen:
                continue
            seen.add(key)
            m["지역"] = actual
            m["시도"] = source_sido
            m["지역코드"] = source_code
            import time
            m["fetched_at"] = time.time()
            m["source"] = "baroezip"
            # KB 판정은 이름 키이므로, 출처 시도가 다르거나 불명이면 동명이 구에 결합하지 않는다.
            m["시그널"] = sig.get(actual, "") if _listing_region_matches_kb(actual, source_sido, source_code) else ""
            out.append(m)
    # 공급사 갭은 면적·조건이 검증되지 않았으므로 순위에 사용하지 않는다.
    out.sort(key=lambda m: (str(m.get("단지명") or ""), str(m.get("naver_id") or "")))
    # 단일 지역 수동 갱신은 한 번의 성공으로 충분하고, 전체 갱신은 절반 이상 원천 응답을
    # 받아야 기존의 유효 캐시를 불완전한 결과로 바꾸지 않는다.
    required = max(1, (queryable + 1) // 2)
    status = {
        "requested_regions": len(regions),
        "queryable_regions": queryable,
        "successful_requests": succeeded,
        "successful_regions": successful_regions,
        "failed_requests": len(failures),
        "skipped_regions": skipped,
        "required_successes": required,
        "usable": succeeded >= required,
        "signal_context": signal_context,
        "failures": failures[:10],
    }
    return out, status


def _radar_scan(regions: list[str], *, kind: str = "급매") -> list[dict]:
    """급매/찐매물 목록만 필요한 기존 호출자용 호환 래퍼."""
    listings, _ = _radar_scan_with_status(regions, kind=kind)
    return listings


_SIG_BONUS = {"STRONG_BUY": 25, "BUY": 15, "WATCH": 5, "NEUTRAL": 0, "SELL_RISK": -20}
_GRADE_BONUS = {"A": 8, "B": 4, "C": 0, "D": -4}   # region_timing·listing_timing 내부와 동기


def _timing_asof() -> str:
    return str(_kb().last_date.date())


def _region_timing_row(region: str) -> dict:
    """지역 타이밍 — signals row + (선택) 백테스트·시장강도."""
    from realty_signal.ingest import pipeline
    from realty_signal.signals.timing import region_timing

    rows = signals()
    hit = next((r for r in rows if r.get("region") == region), None)
    if not hit:
        return {"error": f"'{region}' 지역 데이터를 찾지 못했습니다."}
    signal = hit.get("display_signal", "HELD")
    if hit.get("assessment_status") != "ready":
        return {"region": hit.get("region") or region, "signal": "HELD",
                "assessment_status": "held", "status": "held", "타이밍점수": None,
                "기회도": None, "타이밍근거": "현재 지역 판정 보류 · 타이밍 점수 미산출",
                "기회도근거": "현재 지역 판정 보류 · 타이밍 점수 미산출",
                "asof": _timing_asof()}
    bt_up = None
    try:
        by_sig = {r["signal"]: r for r in (_backtest().get("by_signal") or []) if r.get("signal")}
        sig = signal
        if sig and sig in by_sig:
            bt_up = by_sig[sig].get("적중률")
    except Exception:  # noqa: BLE001
        pass
    strength = (pipeline.load_market_strength().get("regions") or {}).get(hit.get("region") or region)
    if not strength:
        ent = pipeline.region_entity(hit.get("region") or region, signal=signal)
        strength = {
            "시장강도": ent.market_strength, "시장강도라벨": ent.market_strength_label,
            "거래량비": ent.volume_ratio, "급매건수": ent.quicksale_count,
            "거래량기준일": ent.provenance.asof if ent.provenance else None,
        }
    tr = region_timing(
        signal,
        asof=_timing_asof(),
        jeonse_supply=hit.get("전세수급"),
        sale_momentum=hit.get("매매모멘텀"),
        backtest_up_pct=float(bt_up) if bt_up is not None else None,
        # 월별 거래량은 시점이 다른 독립 관측값이다. 미검증 타이밍 점수에 합산하지 않는다.
        market_strength=None,
    )
    out = {**tr.to_dict(), "region": hit.get("region") or region, "signal": signal,
           "assessment_status": "ready", "status": "ready"}
    if strength:
        out["시장강도"] = strength.get("시장강도")
        out["시장강도라벨"] = strength.get("시장강도라벨")
        out["거래량비"] = strength.get("거래량비")
        out["시장강도기준일"] = strength.get("거래량기준일")
        out["시장강도설명"] = "별도 월별 거래량 참고값 · 타이밍 점수에 미합산"
        out["급매건수"] = strength.get("급매건수")
    return out


def _opportunity(kind: str, m: dict, signal: str | None, grade: str | None):
    """기회도 0~100 — listing_timing 래퍼 (하위 호환)."""
    from realty_signal.signals.timing import listing_timing

    tr = listing_timing(kind, m, signal, grade, asof=_timing_asof())
    return tr.score, tr.reasons_text


def _listing_pyeong(kind: str, raw: dict | None, ref: dict | None) -> float | None:
    """유형별 평형(평) — 급매는 평, 경매는 전용㎡→평. 청약·재건축은 보통 없음."""
    raw, ref = raw or {}, ref or {}
    py = raw.get("평형") if raw.get("평형") is not None else ref.get("평형")
    if py is not None:
        try:
            return round(float(py), 1)
        except (TypeError, ValueError):
            pass
    if kind == "경매":
        try:
            m2 = float(raw.get("전용면적") or 0)
        except (TypeError, ValueError):
            m2 = 0
        if m2 > 0:
            return round(m2 / 3.3058, 1)
    return None


def _listing_key(kind: str, raw: dict, ref: dict, name: str | None, region: str | None) -> str:
    """매물의 **호가와 무관한** 안정 식별자.

    호가를 키에 넣으면 가격이 바뀔 때마다 다른 매물로 보여서 "새로 들어왔다" 가 거짓이 된다.
    """
    raw = raw or {}
    ref = ref or {}
    for cand in (raw.get("naver_id"), ref.get("naver_id"), raw.get("hanbang_id"),
                 ref.get("hanbang_id"), ref.get("id"), ref.get("관리번호")):
        if cand:
            return f"{kind}:{cand}"
    parts = [kind, region or "", name or "", str(ref.get("평형") or raw.get("평형") or ""),
             str(raw.get("층") or "")]
    return ":".join(parts)


def _build_listings(want: set[str], *, include_private: bool = False) -> list[dict]:
    """통합 매물 정규화. 외부 제휴 매물은 명시적 허용 없으면 읽지 않는다."""
    if not include_private:
        want = want - {"급매", "찐매물", "일반매물"}
    grade = {r: (v or {}).get("급지") for r, v in _regime().get("regions", {}).items()}
    try:
        assessed = md.assessed_signal_labels(today_kst().isoformat())
    except Exception as exc:  # noqa: BLE001 — 판정 불가 시 원시 등급으로 되돌아가지 않는다.
        log.warning("listing signal assessment unavailable: %s", exc)
        assessed = {}
    safe_auction_signals = {region: label.get("display_signal")
                            for region, label in assessed.items()
                            if label.get("assessment_status") == "ready"
                            and label.get("display_signal") in {"STRONG_BUY", "BUY", "WATCH", "NEUTRAL", "SELL_RISK"}
                            and region not in db.AMBIGUOUS_LEGACY_REGION_KEYS}
    out = []

    def add(kind, name, region, signal, mlabel, mval, munit, raw, lat, lng, ref, total=None):
        from realty_signal.signals.timing import listing_timing

        assessment = assessed.get(region) or {}
        source_code = raw.get("지역코드")
        # 한방 v2 캐시는 원천 시·도/시군구를 검증했지만 5자리 코드는 저장하지 않았다.
        # 검증된 현재 KB 식별과 시·도가 일치할 때만 읽기 모델에서 코드를 보강한다.
        if kind == "일반매물" and not source_code:
            source_code = _verified_listing_region_code(region, raw.get("시도"))
        if kind in {"일반매물", "급매", "찐매물"}:
            identity_ok = _listing_region_matches_kb(region, raw.get("시도"), source_code)
        elif kind == "청약":
            identity_ok = (bool(region and raw.get("_signal_region") == region)
                           and _listing_region_matches_kb(region, raw.get("시도")))
        elif kind == "경매":
            identity_ok = bool(region and region not in db.AMBIGUOUS_LEGACY_REGION_KEYS)
        else:
            identity_ok = True
        safe_signal = assessment.get("display_signal") if assessment.get("assessment_status") == "ready" else "HELD"
        if not identity_ok:
            safe_signal = "HELD"
        if safe_signal not in {"STRONG_BUY", "BUY", "WATCH", "NEUTRAL", "SELL_RISK", "HELD"}:
            safe_signal = "HELD"
        hold_reason = None
        if safe_signal == "HELD":
            flags = assessment.get("risk_flags") or []
            hold_reason = ("region_identity_unverified" if not identity_ok else
                           next((flag for flag in (
                               "region_boundary_obsolete", "source_stale",
                               "region_identity_ambiguous", "market_inputs_missing",
                               "market_inputs_stale", "sale_weeks_incomplete",
                               "price_direction_conflict") if flag in flags),
                                "assessment_unavailable"))
        safe_grade = grade.get(region) if identity_ok else None
        identity_status = ("held" if not identity_ok else
                           "not_applicable" if kind not in {"일반매물", "급매", "찐매물"} else
                           "matched" if raw.get("시도") else "name_only")
        tr = listing_timing(kind, raw, safe_signal, safe_grade, asof=_timing_asof())
        row = {"유형": kind, "단지명": name, "지역": region, "시도": raw.get("시도"),
               "동": raw.get("동") if kind == "경매" or (identity_ok and kind == "일반매물") else None,
               "지역코드": source_code, "시그널": safe_signal,
               "원시시그널": signal or "", "판정상태": (assessment.get("assessment_status") or "held") if identity_ok else "held",
               "지역신호보류사유": hold_reason,
               "지역식별상태": identity_status,
               "지역급지": safe_grade, "지표라벨": mlabel, "지표값": mval, "지표단위": munit,
               "총액": total, "평형": _listing_pyeong(kind, raw, ref),
               "lat": lat, "lng": lng, "ref": ref,
               # 주간 비교("예산 안에 새로 들어온 매물")를 하려면 **호가와 무관한** 식별자가 필요하다.
               # 급매·찐매물은 naver_id 가 2,127건 전부 고유해서 그대로 쓴다.
               "key": _listing_key(kind, raw, ref, name, region)}
        if kind == "경매":
            row.update(입찰상태=raw.get("입찰상태"), 확인할것=raw.get("확인할것") or [],
                       검토용상한=raw.get("권장입찰가"), 사건번호=raw.get("사건번호") or "")
        row.update(tr.to_dict())
        if kind == "경매" and raw.get("source"):
            row["source"] = raw.get("source")
        if kind in {"일반매물", "급매", "찐매물"}:
            row["supplier_flags"] = (["urgent"] if raw.get("급매") or raw.get("급매표시") else []) + (
                ["certified"] if raw.get("찐매물") or raw.get("검증표시") else [])
        if kind in ("급매", "찐매물"):
            import time
            path = QUICKSALE_FILE if kind == "급매" else CERTIFIED_FILE
            fetched = raw.get("fetched_at") or path.stat().st_mtime
            # A preserved-row marker describes renewal, not quote age. Keep the
            # original observation timestamp; never rewrite history as a new quote.
            row.update(source="baroezip", fetched_at=fetched,
                       stale=not 0 <= time.time()-fetched <= _LISTING_PRICE_MAX_AGE,
                       refresh_due=bool(raw.get("stale") or radar_failed.get(kind)
                                        or time.time()-fetched >= _RADAR_MAX_AGE),
                       refresh_failed=bool(radar_failed.get(kind)),
                       degraded=bool(raw.get("degraded")), price_kind="asking",
                       published_at=None, spatial_grain="listing")
        if kind == "일반매물":
            import time
            fetched = raw.get("fetched_at") or HANBANG_FILE.stat().st_mtime
            row.update(source="hanbang", fetched_at=fetched,
                       stale=not 0 <= time.time()-fetched <= _LISTING_PRICE_MAX_AGE,
                       refresh_due=bool(raw.get("stale") or source_failed
                                        or time.time()-fetched >= _RADAR_MAX_AGE),
                       refresh_failed=source_failed,
                       price_kind="asking", published_at=raw.get("등록일"),
                       spatial_grain="listing")
        out.append(row)

    radar_failed = {kind: _radar_refresh_status(path).get("ok") is False
                    for kind, path in (("급매", QUICKSALE_FILE), ("찐매물", CERTIFIED_FILE)) if kind in want}
    if "경매" in want:
        try:
            raw_auction_signals = _signal_map()
        except Exception:  # noqa: BLE001 — 원시 등급 장애가 경매 매물 조회를 막지 않는다.
            raw_auction_signals = {}
        ranked = auction.enrich(auction.load(), safe_auction_signals, {})
        known_cases = {r.get("사건번호") for r in ranked if r.get("사건번호")}
        for r in ranked:
            add("경매", r.get("단지명"), r.get("region"), raw_auction_signals.get(r.get("region")),
                "총비용우위", r.get("총비용우위율"), "%", r, r.get("lat"), r.get("lng"),
                {"id": r.get("id")}, total=r.get("최저매각가") or r.get("권장입찰가"))
        from realty_signal.ingest import external
        for card in external.read_hank_cards():
            if card.get("사건번호") and card["사건번호"] in known_cases:
                continue
            add("경매", card.get("단지명"), card.get("region"),
                raw_auction_signals.get(card.get("region")),
                "최저가", None, "", card, card.get("lat"), card.get("lng"),
                {"id": card.get("id"), "건물면적": card.get("건물면적"), "주소": card.get("주소")},
                total=card.get("최저매각가"))
    if "급매" in want:
        for m in _radar_verified_rows(QUICKSALE_FILE, _QUICKSALE_SCAN_VER):
            add("급매", m.get("단지명"), m.get("지역"), m.get("시그널"),
                "가격 비교", None, "", m, m.get("lat"), m.get("lng"),
                {"평형": m.get("평형"), "호가": m.get("호가"), "complex_no": m.get("complex_no"),
                 "전용면적": m.get("전용면적"), "층": m.get("층"), "naver_id": m.get("naver_id")},
                total=m.get("호가"))
    if "찐매물" in want:
        for m in _radar_verified_rows(CERTIFIED_FILE, _CERTIFIED_SCAN_VER):
            add("찐매물", m.get("단지명"), m.get("지역"), m.get("시그널"),
                "가격 비교", None, "", m, m.get("lat"), m.get("lng"),
                {"평형": m.get("평형"), "호가": m.get("호가"), "complex_no": m.get("complex_no"),
                 "전용면적": m.get("전용면적"), "층": m.get("층"), "naver_id": m.get("naver_id"), "찐매물": True},
                total=m.get("호가"))
    if "일반매물" in want and HANBANG_FILE.exists():
        source_failed = _radar_refresh_status(HANBANG_FILE).get("ok") is False
        for m in _hanbang_verified_rows():
            add("일반매물", m.get("단지명"), m.get("지역"), m.get("시그널"),
                "등록일", m.get("등록일"), "", m, m.get("lat"), m.get("lng"),
                {"hanbang_id": m.get("hanbang_id"), "hanbang_complex_id": m.get("hanbang_complex_id"),
                 "전용면적": m.get("전용면적"), "층": m.get("층"), "방수": m.get("방수"), "호가": m.get("호가"),
                 "등록일": m.get("등록일"), "검증표시": m.get("검증표시")},
                total=m.get("호가"))
    if "청약" in want:
        for raw in _presale():
            d = _presale_current(raw)
            add("청약", d.get("단지명"), d.get("지역"), d.get("시그널"),
                "청약상태", d.get("상태"), "", d, None, None,
                {"관리번호": d.get("관리번호"), "Dday": d.get("Dday"),
                 "다음일정": d.get("다음일정"), "주소": d.get("주소")})
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


_LISTING_CACHEABLE = frozenset({"일반매물", "급매", "찐매물", "경매"})
_listing_assembly_cache: dict[tuple, tuple[list[dict], int]] = {}
_listing_assembly_lock = threading.Lock()


def _listing_file_stamp(path) -> tuple:
    try:
        stat = path.stat()
    except OSError:
        return (str(path), None)
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _listing_assembly_key(types: set[str], include_private: bool):
    """디스크 원본이 그대로면 같은 키. 청약·재건축과 테스트는 캐시하지 않는다."""
    if os.environ.get("PYTEST_CURRENT_TEST") or not types or types - _LISTING_CACHEABLE:
        return None
    from realty_signal.signals.timing import VERSION as TIMING_VERSION

    paths = [store.CACHE_FILE, store.CODES_FILE, store.IDENTITY_FILE]
    if "경매" in types:
        from realty_signal.auction import AUCTION_FILE
        from realty_signal.ingest.external import cache_path
        paths.extend([AUCTION_FILE, cache_path()])
    if include_private and types & {"일반매물", "급매", "찐매물"}:
        paths.extend([
            QUICKSALE_FILE, CERTIFIED_FILE, HANBANG_FILE,
            _radar_refresh_file(QUICKSALE_FILE),
            _radar_refresh_file(CERTIFIED_FILE),
            _radar_refresh_file(HANBANG_FILE),
        ])
    return (
        include_private, tuple(sorted(types)), today_kst().isoformat(), TIMING_VERSION,
        tuple(_listing_file_stamp(path) for path in paths),
    )


def _assemble_listings(types: set[str], include_private: bool) -> tuple[list[dict], int]:
    from realty_signal.services.listing_inventory import collapse
    from realty_signal.services.listing_prices import attach as attach_prices

    out = _build_listings(types, include_private=include_private)
    source_record_count = len(out)
    collapsed = collapse(out)
    cohort_rows = collapsed
    sale_kinds = {"일반매물", "급매", "찐매물"}
    if include_private and types.intersection(sale_kinds) and not sale_kinds.issubset(types):
        try:
            cohort_rows = collapse(_build_listings(sale_kinds, include_private=True))
        except Exception:  # 호가 표본 보강 실패가 기존 매물 조회를 막지 않도록 한다.
            cohort_rows = collapsed
    out = attach_prices(collapsed, cohort_rows=cohort_rows)
    out.sort(key=lambda x: (x["기회도"] if x["기회도"] is not None else -1), reverse=True)
    return out, source_record_count


def _assembled_listings(types: set[str], include_private: bool) -> tuple[list[dict], int]:
    """합쳐 둔 공통 목록. 계정별 예산·관심은 호출한 쪽에서 얹는다."""
    key = _listing_assembly_key(types, include_private)
    if key is not None:
        with _listing_assembly_lock:
            hit = _listing_assembly_cache.get(key)
        if hit is not None:
            rows, count = hit
            return [dict(row) for row in rows], count
    rows, count = _assemble_listings(types, include_private)
    if key is not None:
        stored = [dict(row) for row in rows]
        with _listing_assembly_lock:
            if len(_listing_assembly_cache) >= 8:
                _listing_assembly_cache.clear()
            _listing_assembly_cache[key] = (stored, count)
        return [dict(row) for row in stored], count
    return rows, count


def listings_all(request: Request, types: str = "경매,급매,청약", view: str = "full"):
    """통합 매물 — 경매·급매·찐매물·청약을 공통 스키마로 정규화 + 타이밍점수(기회도 호환)."""
    from realty_signal.brain import ranking as eng_rank
    from realty_signal.signals.timing import VERSION as TIMING_VERSION

    private_access = _personal_listings_allowed(request=request)
    requested_types = set(t for t in types.split(",") if t)
    from realty_signal.services import listing_refresh
    listing_refresh.schedule(requested_types, private_allowed=private_access)
    if "경매" in requested_types:
        from realty_signal.ingest import external
        external.schedule_hank_refresh()
    out, source_record_count = _assembled_listings(requested_types, private_access)
    uid = _uid(request)
    if uid and private_access:
        locality = db.listing_localities(uid, [key for row in out if row.get("유형") in {"일반매물", "급매", "찐매물"}
                                           for key in row.get("listing_aliases", [row.get("key")]) if key])
        for row in out:
            if row.get("유형") in {"일반매물", "급매", "찐매물"} and not row.get("동") and row.get("지역식별상태") == "matched":
                value = next((locality[key] for key in row.get("listing_aliases", [row.get("key")])
                              if key in locality), None)
                if value:
                    row["동"] = value
                    row["동출처"] = "직접 입력"
    scores = eng_rank.engagement_scores(uid=uid)
    if scores:
        out = eng_rank.apply_engagement_bonus(out, scores)
    out.sort(key=lambda x: (x["기회도"] if x["기회도"] is not None else -1), reverse=True)
    # 통합 목록은 가격·예산·지도 필드만 사용한다. 상세 판단/근거 문서는 리포트에서
    # 계산하므로 목록마다 생성·전송하지 않아도 된다. 기존 API 기본 응답은 유지한다.
    if view != "card":
        out = _attach_card_lines(out, uid)
    from realty_signal.services import property_analysis
    profile = db.profile_get(uid) or {} if uid else {}
    confirmed = buying_power.validated_confirmed_power(profile)
    has_confirmed_budget = bool(confirmed and (profile.get("매수지역코드")
        or ((profile.get("매수력") or {}).get("가정") or {}).get("지역코드")))
    for row in out:
        if row.get("유형") in {"일반매물", "급매", "찐매물"}:
            row["budget_fit"] = property_analysis.buyer_fit(
                property_analysis.snapshot(row), profile, confirmed_power=confirmed)
    # 원시 등급은 내부 감사용이다. 현재 보류 판정과 함께 브라우저로 내보내면
    # 다른 화면이 원시 BUY를 현재 추천으로 재사용할 수 있다.
    public_rows = [{key: value for key, value in row.items() if key != "원시시그널"}
                   for row in out]
    hold_reasons = {}
    for row in public_rows:
        if row.get("시그널") == "HELD":
            reason = row.get("지역신호보류사유") or "assessment_unavailable"
            hold_reasons[reason] = hold_reasons.get(reason, 0) + 1
    asof = _timing_asof()
    kinds = ("경매", "급매", "찐매물", "일반매물", "청약", "재건축")
    general_refresh = (_radar_refresh_status(HANBANG_FILE)
                       if private_access and "일반매물" in requested_types else {})
    general_scope = None
    if general_refresh.get("ok") is True:
        failed = general_refresh.get("failed_requests")
        failed = max(0, min(_HANBANG_REGION_CAP, failed)) if type(failed) is int else 0
        limited = general_refresh.get("limited_regions")
        limited = min(_HANBANG_REGION_CAP, len(limited)) if isinstance(limited, list) else 0
        if general_refresh.get("empty_reason") == "no_favorite_complexes":
            general_scope = {"failed_regions": 0, "page_limited_regions": 0,
                             "no_favorite_complexes": True}
        elif failed or limited:
            general_scope = {"failed_regions": failed, "page_limited_regions": limited}
    age = _data_age_days()
    return {
        "listings": public_rows,
        "asof": asof,
        "meta": {
            "source": "kb_weekly",
            "timing_version": TIMING_VERSION,
            "data_age_days": round(age, 1) if age is not None else None,
            "engagement_boost": bool(scores),
            "private_access": private_access,
            "general_refresh_failed": general_refresh.get("ok") is False,
            "general_scope": general_scope,
            "source_record_count": source_record_count,
            "duplicate_records_collapsed": source_record_count - len(out),
            "signal_hold_reasons": hold_reasons,
            "confirmed_budget": has_confirmed_budget,
            "budget_manwon": confirmed[0] if has_confirmed_budget else None,
        },
        "counts": {k: sum(1 for x in out if x["유형"] == k) for k in kinds},
    }



def quicksale():
    """급매 레이더 결과 (캐시). 개인용 — baroezip 공개 API 기반."""
    return _radar_cached_response(QUICKSALE_FILE, _QUICKSALE_SCAN_VER)


def certified():
    """찐매물(내집등록·인증) 레이더 결과 (캐시). scope=all + has_certified."""
    return _radar_cached_response(CERTIFIED_FILE, _CERTIFIED_SCAN_VER)


def hanbang():
    """한방 일반 아파트 매매 — 개인 계정 관심단지의 공유 미러. 지역당 3페이지."""
    out = _radar_cached_response(HANBANG_FILE, _HANBANG_SCAN_VER)
    if out.get("last_success_at") is not None and out.get("_scan_ver", 0) < _HANBANG_SCAN_VER:
        out["listings"] = []
        out["regions"] = []
        out["count"] = 0
        out["state"] = "identity_unverified"
    return out


def _hanbang_verified_rows() -> list[dict]:
    """구버전 지역 출처가 증명되지 않은 매물은 어떤 소비자에도 내보내지 않는다."""
    try:
        cached = json.loads(HANBANG_FILE.read_text(encoding="utf-8"))
        if not isinstance(cached, dict) or cached.get("_scan_ver", 0) < _HANBANG_SCAN_VER:
            return []
        return cached.get("listings") or []
    except (FileNotFoundError, ValueError, TypeError):
        return []



def _scan_regions() -> list[str]:
    """급매·찐매물 스캔 대상 = BUY+ 시그널 지역 ∪ 개인 계정 관심 지역.

    KB 장애 중에도 소유자 관심지역은 유지한다. 과거 캐시의 지역은 수집 당시의
    소유자를 확인할 수 없으므로 재사용하지 않는다.
    """
    known = set(_bundled_centroids())
    try:
        df = _signals_df()
        valid = set(df["region"]) | known
    except Exception as exc:  # noqa: BLE001 - KB 실패가 외부 매물 수집까지 막지 않게 한다
        log.warning("급매 대상 지역 시그널 미확인: %s", type(exc).__name__)
        df, valid = None, known
    try:
        labels = md.assessed_signal_labels(today_kst().isoformat()) if df is not None else {}
    except Exception as exc:  # noqa: BLE001 - 오래된 원시 BUY로 스캔 대상을 고르지 않는다
        log.warning("급매 대상 지역 안전 판정 미확인: %s", type(exc).__name__)
        labels = {}
    buy = ([r for r in df["region"] if (labels.get(r) or {}).get("assessment_status") == "ready"
            and (labels.get(r) or {}).get("display_signal") in {"STRONG_BUY", "BUY"}]
           if df is not None else [])
    owner_email = config.personal_listing_email()
    owner = db.user_by_email(owner_email) if owner_email else None
    favs = ([region for region in db.actionable_region_favs(owner["id"])
             if region in valid] if owner else [])
    seen, out = set(), []
    for r in buy + favs:
        if r not in seen and _scan_region_current(r):
            seen.add(r); out.append(r)
    return out


def _scan_region_current(region: str) -> bool:
    """동명/개편 전 지역의 중심점을 다른 구 스캔에 재사용하지 않는다."""
    if region not in {"중구", "서구", "동구", "남구", "강서구", "북구", "인천 중구"}:
        return True
    try:
        code = _code_of(region)
    except Exception:  # noqa: BLE001 - 이름만으로 동명이 구를 짐작하지 않는다
        return False
    point = _bundled_centroids().get(region)
    identity = _sigungu_identity_at(*point) if point else None
    return bool(code and identity and identity[0] == region and identity[2] == code[:5])


_HANBANG_REGION_CAP = 8


def _complex_name_key(name: str) -> str:
    """단지명 비교용. 공백을 없애고 끝의 '아파트'만 뺀다."""
    text = "".join(str(name or "").split())
    suffix = "아파트"
    if text.endswith(suffix) and len(text) > len(suffix):
        text = text[: -len(suffix)]
    return text


def _complex_name_allowed(name: str, allowed: set[str]) -> bool:
    key = _complex_name_key(name)
    return bool(key) and any(_complex_name_key(item) == key for item in allowed)


def _hanbang_complex_targets() -> dict[str, set[str]]:
    """개인 계정이 찍어 둔 관심단지. 지역 → 단지명.

    매수력·관심지역·매수권 구 목록은 수집 범위에 쓰지 않는다. 조회는 이 집합을
    하루 한 번 받아 두는 공유 미러다.
    """
    owner_email = config.personal_listing_email()
    owner = db.user_by_email(owner_email) if owner_email else None
    if not owner:
        return {}
    known = set(_bundled_centroids())
    grouped: dict[str, set[str]] = {}
    for fav in db.fav_list(owner["id"]):
        if fav.get("kind") != "complex" or "|" not in (fav.get("key") or ""):
            continue
        region, name = fav["key"].split("|", 1)
        name = name.strip()
        if not name or region not in known or not _scan_region_current(region):
            continue
        if region not in grouped and len(grouped) >= _HANBANG_REGION_CAP:
            continue
        grouped.setdefault(region, set()).add(name)
    return grouped


def _hanbang_regions() -> list[str]:
    """관심단지가 있는 시군구. 매수권 구 전체는 받지 않는다."""
    return list(_hanbang_complex_targets())


def _hanbang_scan_with_status(regions: list[str], names: dict[str, set[str]] | None = None
                              ) -> tuple[list[dict], dict]:
    from realty_signal.ingest.hanbang import fetch_region_with_status, fetch_named_region_with_status

    try:
        signals = _signal_map()
        signal_context = "available"
    except Exception:  # noqa: BLE001 - KB 장애가 외부 매물 수집을 막지 않는다
        signals, signal_context = {}, "unavailable"
    listings, seen, succeeded, complete, limited, failures, named = [], set(), [], [], [], [], []
    queryable = 0
    for region in regions[:_HANBANG_REGION_CAP]:
        point = _bundled_centroids().get(region)
        if not point:
            failures.append({"region": region, "error": "지원하는 시군구 중심좌표 없음"})
            continue
        queryable += 1
        expected_sido = _sido_of(region)
        rows, status = fetch_region_with_status(*point)
        used_named = False
        if not status.get("ok") and status.get("phase") == "location" and expected_sido:
            rows, named_status = fetch_named_region_with_status(expected_sido, region)
            if named_status.get("ok"):
                status = named_status
                used_named = True
            else:
                status = {"ok": False, "error": f"좌표 조회 실패; 이름 조회 실패: {named_status.get('error', '원천 오류')}"}
        source_region = (status.get("source_sgg") or "").replace(" ", "")
        source_sido = (status.get("source_ctpv") or "").strip()
        if (not status.get("ok") or not source_region or not region.replace(" ", "").endswith(source_region)
                or not expected_sido or not source_sido.startswith(expected_sido)):
            failures.append({"region": region,
                             "error": status.get("error") or "원천 시도·시군구와 요청 지역이 다름"})
            continue
        if any((item.get("지역") or "").replace(" ", "") != source_region for item in rows):
            failures.append({"region": region, "error": "원천 목록에 다른 시군구 매물이 섞여 있음"})
            continue
        if used_named:
            named.append(region)
        succeeded.append(region)
        (complete if status["complete"] else limited).append(region)
        allowed = (names.get(region) or set()) if names is not None else None
        for item in rows:
            if allowed is not None and not _complex_name_allowed(item.get("단지명") or "", allowed):
                continue
            if item["hanbang_id"] in seen:
                continue
            seen.add(item["hanbang_id"])
            locality_sido = item.pop("_동표기시도", None)
            if locality_sido and not source_sido.startswith(locality_sido):
                item["동"] = None
            item["지역"] = region
            item["시도"] = expected_sido
            item["시그널"] = signals.get(region, "")
            item["fetched_at"] = __import__("time").time()
            listings.append(item)
    required = max(1, (queryable + 1) // 2)
    return listings, {
        "requested_regions": len(regions[:_HANBANG_REGION_CAP]), "queryable_regions": queryable,
        "successful_requests": len(succeeded), "successful_regions": succeeded,
        "complete_regions": complete, "limited_regions": limited,
        "named_lookup_regions": named,
        "failed_requests": len(failures), "required_successes": required,
        "usable": len(succeeded) >= required, "signal_context": signal_context,
        "failures": failures[:3],
    }


def _hanbang_preserve_unscanned(listings: list[dict], scan: dict,
                                names: dict[str, set[str]] | None = None) -> list[dict]:
    """오류·페이지 제한 지역의 이전 매물을 지난 결과로 보존한다."""
    import time
    if not HANBANG_FILE.exists():
        return listings
    previous = _hanbang_verified_rows()
    ids = {row["hanbang_id"] for row in listings}
    complete = set(scan["complete_regions"])
    requested = set(scan["regions"])
    kept = [
        {**row, "stale": True} for row in previous
        if row.get("hanbang_id") not in ids and row.get("지역") in requested
        and row.get("지역") not in complete
        and time.time() - (row.get("fetched_at") or HANBANG_FILE.stat().st_mtime) <= 7 * 86400
        and (names is None or _complex_name_allowed(
            row.get("단지명") or "", names.get(row.get("지역")) or set()))
    ]
    return listings + kept


def _preserve_unscanned(path, listings, scan):
    if not path.exists() or "successful_regions" not in scan:
        return listings
    try:
        cached = jsonx.loads(path.read_text(encoding="utf-8"))
        min_ver = _QUICKSALE_SCAN_VER if path == QUICKSALE_FILE else _CERTIFIED_SCAN_VER if path == CERTIFIED_FILE else 0
        if cached.get("_scan_ver", 0) < min_ver:
            return listings
        previous = cached.get("listings", [])
    except Exception:
        return listings
    successful = set(scan["successful_regions"])
    def identity(row):
        return row.get("naver_id") or (row.get("complex_no"), row.get("지역"), row.get("평형"), row.get("층"))
    current = {identity(row) for row in listings}
    old = [{**row, "stale": True, "fetched_at": row.get("fetched_at") or path.stat().st_mtime}
           for row in previous if row.get("지역") not in successful and identity(row) not in current]
    return listings + old


def _record_listing_price_history(source: str, listings: list[dict]) -> None:
    """History writes follow a successful source-cache publish; failure is non-fatal."""
    try:
        from realty_signal.services import listing_price_history
        listing_price_history.record_source_rows(source, listings)
    except Exception as exc:  # noqa: BLE001 — current feed remains usable if history storage is unavailable.
        log.warning("listing price history write failed for %s: %s", source, exc)


def quicksale_refresh(data: dict = Body(default={})):
    """급매 레이더 갱신. body {regions:[...]} 없으면 BUY+ ∪ 관심 지역 스캔."""
    import time
    regions = data.get("regions") or _scan_regions()
    listings, scan = _radar_scan_with_status(regions, kind="급매")
    status = {"attempted_at": time.time(), "ok": bool(scan["usable"]), **scan}
    if not scan["usable"]:
        status["error"] = ("스캔 가능한 지역이 없습니다." if not scan["queryable_regions"]
                           else "원천 응답 부족으로 기존 급매 결과를 유지했습니다.")
        _record_radar_refresh(QUICKSALE_FILE, status)
        return {"ok": False, "count": 0, "regions": len(regions), "scan": status}
    listings = _preserve_unscanned(QUICKSALE_FILE, listings, scan)
    result = {"ready": True, "listings": listings, "regions": regions,
              "count": len(listings), "_scan_ver": _QUICKSALE_SCAN_VER}
    from realty_signal.storage import atomic_json
    atomic_json(QUICKSALE_FILE, jsonx.loads(jsonx.dumps(result)))
    _record_listing_price_history("baroezip", listings)
    _record_radar_refresh(QUICKSALE_FILE, status)
    return {"ok": True, "count": len(listings), "regions": len(regions), "scan": status}


def certified_refresh(data: dict = Body(default={})):
    """찐매물 레이더 갱신. body {regions:[...]} 없으면 BUY+ ∪ 관심 지역 스캔."""
    import time
    regions = data.get("regions") or _scan_regions()
    listings, scan = _radar_scan_with_status(regions, kind="찐매물")
    status = {"attempted_at": time.time(), "ok": bool(scan["usable"]), **scan}
    if not scan["usable"]:
        status["error"] = ("스캔 가능한 지역이 없습니다." if not scan["queryable_regions"]
                           else "원천 응답 부족으로 기존 찐매물 결과를 유지했습니다.")
        _record_radar_refresh(CERTIFIED_FILE, status)
        return {"ok": False, "count": 0, "regions": len(regions), "scan": status}
    listings = _preserve_unscanned(CERTIFIED_FILE, listings, scan)
    result = {"ready": True, "listings": listings, "regions": regions,
              "count": len(listings), "_scan_ver": _CERTIFIED_SCAN_VER}
    from realty_signal.storage import atomic_json
    atomic_json(CERTIFIED_FILE, jsonx.loads(jsonx.dumps(result)))
    _record_listing_price_history("baroezip", listings)
    _record_radar_refresh(CERTIFIED_FILE, status)
    return {"ok": True, "count": len(listings), "regions": len(regions), "scan": status}


def hanbang_refresh(data: dict = Body(default={})):
    """한방 매매 갱신. 원천 실패는 정상 0건으로 덮어쓰지 않는다."""
    import time
    last = _radar_refresh_status(HANBANG_FILE).get("attempted_at") or 0
    if time.time() - last < 600:
        return {"ok": False, "reason": "cooldown", "message": "10분 후 다시 수집할 수 있습니다."}
    requested = data.get("regions")
    known = set(_bundled_centroids())
    targets = None
    if requested is not None:
        if (not isinstance(requested, list) or len(requested) > 3
                or any(not isinstance(region, str) or region not in known for region in requested)):
            raise HTTPException(422, "수집 지역은 지원 시군구 최대 3곳만 지정할 수 있습니다.")
        regions = list(dict.fromkeys(requested))
    else:
        targets = _hanbang_complex_targets()
        regions = list(targets)
        if not regions:
            scan = {
                "requested_regions": 0, "queryable_regions": 0, "successful_requests": 0,
                "successful_regions": [], "complete_regions": [], "limited_regions": [],
                "named_lookup_regions": [], "failed_requests": 0, "required_successes": 0,
                "usable": True, "signal_context": "unused", "failures": [], "regions": [],
                "empty_reason": "no_favorite_complexes",
            }
            status = {"attempted_at": time.time(), "ok": True, **scan}
            from realty_signal.storage import atomic_json
            atomic_json(HANBANG_FILE, {"ready": True, "listings": [], "regions": [],
                                       "count": 0, "_scan_ver": _HANBANG_SCAN_VER})
            _record_radar_refresh(HANBANG_FILE, status)
            return {"ok": True, "count": 0, "regions": 0, "scan": status}
    listings, scan = _hanbang_scan_with_status(regions, names=targets)
    scan["regions"] = regions
    status = {"attempted_at": time.time(), "ok": bool(scan["usable"]), **scan}
    if not scan["usable"]:
        status["error"] = "원천 응답 부족으로 기존 일반 매물 결과를 유지했습니다."
        _record_radar_refresh(HANBANG_FILE, status)
        return {"ok": False, "count": 0, "regions": len(regions), "scan": status}
    listings = _hanbang_preserve_unscanned(listings, scan, names=targets)
    from realty_signal.storage import atomic_json
    atomic_json(HANBANG_FILE, {"ready": True, "listings": listings,
                               "regions": regions, "count": len(listings),
                               "_scan_ver": _HANBANG_SCAN_VER})
    _record_listing_price_history("hanbang", listings)
    _record_radar_refresh(HANBANG_FILE, status)
    return {"ok": True, "count": len(listings), "regions": len(regions), "scan": status}
