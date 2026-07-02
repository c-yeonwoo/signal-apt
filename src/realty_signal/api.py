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
    try:
        cur = _signal_map()
    except Exception:
        return []
    prev = db.kv_get("signal_snapshot") or {}
    changes = []
    if prev:  # 최초 1회는 스냅샷만 저장(가짜 변동 방지)
        for region, sig in cur.items():
            old = prev.get(region)
            if old and old != sig:
                changes.append({"region": region, "from": old, "to": sig, "date": asof})
    if changes:
        log_ = db.kv_get("signal_changes") or []
        db.kv_set("signal_changes", (changes + log_)[:300])  # 최신순, 최대 300건
    db.kv_set("signal_snapshot", cur)
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


async def _startup_bg():
    """초기 수집·스냅샷을 백그라운드로 — 헬스체크(/)가 데이터 수집을 기다리지 않도록.

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
            "onboarded": bool(db.profile_get(u["id"]))}


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
    model = ai_report.OPUS if opus else ai_report.SONNET   # 화이트리스트=Opus, 그 외=Sonnet
    tier = "opus" if opus else "sonnet"
    profile = db.profile_get(uid)
    summary = data.get("summary") or {}
    fav = db.fav_list(uid)
    favorites = {
        "관심지역": [f["key"] for f in fav if f["kind"] == "region"],
        "관심단지": [(f.get("label") or f["key"]).split("|")[-1] for f in fav if f["kind"] == "complex"],
    }
    # 캐시: 같은 데이터주차·프로필·관심·티어면 재사용(뉴스는 키에서 제외 — 주 단위 재생성으로 충분)
    try:
        wk = _kb().last_date.strftime("%G-W%V")
    except Exception:  # noqa: BLE001
        wk = "na"
    sig = hashlib.md5(json.dumps([profile, summary, favorites, tier], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    ckey = f"aireport:{uid}:{wk}:{sig}"
    cached = db.kv_get(ckey, max_age=14 * 86400)
    if cached is not None:
        return {**cached, "cached": True}
    news = db.news_recent_for_ai(12)   # 최근 정책·시장 뉴스 맥락 주입
    report = ai_report.generate(profile, summary, news=news, favorites=favorites, model=model)
    if not report:
        return {"available": False}
    out = {"available": True, "report": report, "news_used": len(news), "tier": tier}
    db.kv_set(ckey, out)
    return out


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


# ---------- 알림 (즐겨찾기 지역 시그널 변동) ----------
@app.get("/api/alerts")
def alerts(request: Request):
    """내 즐겨찾기 지역의 시그널 변동 + 현재 상태 다이제스트. unread=마지막 확인 이후 변동 수."""
    uid = _uid(request)
    favs = {f["key"] for f in db.fav_list(uid) if f["kind"] == "region"}
    log_ = db.kv_get("signal_changes") or []
    mine = [c for c in log_ if c["region"] in favs][:50]
    seen = db.kv_get(f"alerts_seen:{uid}") or ""
    unread = sum(1 for c in mine if c["date"] > seen)
    cur = _signal_map()
    digest = [{"region": r, "signal": cur.get(r, "")} for r in sorted(favs)]
    return {"changes": mine, "unread": unread, "digest": digest}


@app.post("/api/alerts/seen")
def alerts_seen(request: Request):
    """알림 확인 처리 — 현재 데이터 기준일을 '마지막 확인'으로 기록."""
    try:
        last = str(_kb().last_date.date())
    except Exception:
        last = ""
    db.kv_set(f"alerts_seen:{_uid(request)}", last)
    return {"ok": True}


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
    code = _kb().codes.get(region, "")
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
    code = _kb().codes.get(region, "")
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
        code = _kb().codes.get(region, "")
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


def _region_centroid(region: str, code: str) -> tuple[float, float] | None:
    """시군구 중심좌표 (SQLite db.region_geo 캐시)."""
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


@app.get("/api/complex/{region}/{name}")
def complex_detail(region: str, name: str):
    """단지 deep-dive — 실거래 매매·전세 추이 + 평형별 + 전세가율·갭. DB 캐시(14일)."""
    from realty_signal import db
    grade = (_regime().get("regions", {}).get(region) or {}).get("급지")
    signal = _signal_map().get(region)

    def deco(d):   # 급지·시그널은 주간 변동 → 캐시에 굽지 않고 응답 시점에 부착
        return {**d, "급지": grade, "시그널": signal}

    code = _kb().codes.get(region, "")
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
        code = codes.get(region, "")
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
        c = _region_centroid(region, _kb().codes.get(region, ""))
        coords = list(c) if c else None
    if not coords:
        return {"available": True, "agents": [], "no_coord": True}
    lst = ag.search_agents(coords[0], coords[1], key)
    out = {"available": True, "agents": lst, "coord": coords}
    db.kv_set(ckey, out)
    return out


@app.get("/api/region-centroids")
def region_centroids(regions: str):
    """시군구 중심좌표 배치 — 콤마구분 지역명 → {지역:[lat,lng]}. DB 캐시 우선(즉시).

    핀 폴백용: 단지 지오코딩 실패 단지를 지역 중심에 표시해 항상 클릭/포커스 가능.
    """
    codes = _kb().codes
    out = {}
    for region in [r for r in regions.split(",") if r][:60]:
        c = _region_centroid(region, codes.get(region, ""))
        if c:
            out[region] = [c[0], c[1]]
    return {"centroids": out}


def _radar_scan(regions: list[str]) -> list[dict]:
    """급매 레이더 — 지역별 baroezip 공개 spatialmarket 조회 → 급매 매물 + 지역 시그널."""
    from realty_signal.ingest.baroezip import bbox_around, fetch_market

    codes = _kb().codes
    sig = _signal_map()
    seen, out = set(), []
    for region in regions:
        code = codes.get(region, "")
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
            m["지역"] = region
            m["시그널"] = sig.get(region, "")
            out.append(m)
    out.sort(key=lambda m: m["급매갭"] if m["급매갭"] is not None else 0)
    return out


_SIG_BONUS = {"STRONG_BUY": 25, "BUY": 15, "WATCH": 5, "NEUTRAL": 0, "SELL_RISK": -20}
_GRADE_BONUS = {"A": 8, "B": 4, "C": 0, "D": -4}   # 상급지=환금성·안정 가점


def _opportunity(kind: str, m: dict, signal: str | None, grade: str | None):
    """기회도 0~100 (v2) = 유형 고유 할인/기대(0~60) + 시그널 강도 + 급지(환금성). (score, 근거)."""
    why = []
    if kind == "급매":
        g = m.get("급매갭")
        base = 0 if g is None else min(60, round(-g * 2))                 # -30%→60
        why.append(f"급매갭 {g}%" if g is not None else "급매갭 –")
    elif kind == "경매":
        r = m.get("시세차익률")
        base = 0 if r is None else max(0, min(60, round(r * 1.8)))        # 33%→60
        why.append(f"시세차익 {r}%" if r is not None else "시세차익 –")
    elif kind == "청약":
        st, dd = m.get("상태"), m.get("Dday")
        base = 45 if st == "접수중" else 35 if st == "접수예정" else 20
        if dd is not None and 0 <= dd <= 14:
            base += (15 - dd)                                            # 임박(2주내) 가점
        why.append(f"청약 {st}" + (f" D-{dd}" if dd is not None and dd >= 0 else ""))
    elif kind == "재건축":
        p = m.get("잠재력")
        base = 0 if p is None else round(p * 0.55)                       # 잠재력 100→55
        why.append(f"재건축 잠재력 {p}" if p is not None else "재건축 잠재력 –")
    else:
        base = 0
    sb, gb = _SIG_BONUS.get(signal or "", 0), _GRADE_BONUS.get(grade or "", 0)
    if signal:
        why.append(f"{signal}({sb:+d})")
    if grade:
        why.append(f"{grade}급지({gb:+d})")
    return max(0, min(100, base + sb + gb)), " · ".join(why)


def _build_listings(want: set[str]) -> list[dict]:
    """통합 매물 정규화(공통 스키마 + 기회도 + 총액). 유형 필터(want)만 수집."""
    grade = {r: (v or {}).get("급지") for r, v in _regime().get("regions", {}).items()}
    out = []

    def add(kind, name, region, signal, mlabel, mval, munit, raw, lat, lng, ref, total=None):
        score, why = _opportunity(kind, raw, signal, grade.get(region))
        out.append({"유형": kind, "단지명": name, "지역": region, "시그널": signal or "",
                    "지역급지": grade.get(region), "지표라벨": mlabel, "지표값": mval, "지표단위": munit,
                    "기회도": score, "기회도근거": why, "총액": total, "lat": lat, "lng": lng, "ref": ref})

    if "경매" in want:
        for r in auction.enrich(auction.load(), _signal_map(), {}):
            add("경매", r.get("단지명"), r.get("region"), r.get("지역시그널"),
                "시세차익", r.get("시세차익률"), "%", r, r.get("lat"), r.get("lng"),
                {"id": r.get("id")}, total=r.get("권장입찰가"))
    if "급매" in want and QUICKSALE_FILE.exists():
        for m in json.loads(QUICKSALE_FILE.read_text(encoding="utf-8")).get("listings", []):
            add("급매", m.get("단지명"), m.get("지역"), m.get("시그널"),
                "급매갭", m.get("급매갭"), "%", m, m.get("lat"), m.get("lng"),
                {"평형": m.get("평형"), "호가": m.get("호가")}, total=m.get("호가"))
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


@app.get("/api/listings/all")
def listings_all(request: Request, types: str = "경매,급매,청약"):
    """통합 매물 — 경매·급매·청약을 공통 스키마로 정규화 + 기회도(시그널·급지 가중). 유형 교차 정렬."""
    out = _build_listings(set(t for t in types.split(",") if t))
    out.sort(key=lambda x: (x["기회도"] if x["기회도"] is not None else -1), reverse=True)
    return {"listings": out, "asof": str(_kb().last_date.date()),
            "counts": {k: sum(1 for x in out if x["유형"] == k) for k in ("경매", "급매", "청약", "재건축")}}


@app.get("/api/quicksale")
def quicksale():
    """급매 레이더 결과 (캐시). 개인용 — baroezip 공개 API 기반."""
    if QUICKSALE_FILE.exists():
        return json.loads(QUICKSALE_FILE.read_text(encoding="utf-8"))
    return {"ready": False, "listings": [], "regions": []}


@app.post("/api/quicksale/refresh")
def quicksale_refresh(data: dict = Body(default={})):
    """급매 레이더 갱신. body {regions:[...]} 없으면 BUY+ 시그널 지역 전체 스캔."""
    regions = data.get("regions")
    if not regions:
        df = _signals_df()
        regions = list(df[df["signal"].isin(["STRONG_BUY", "BUY"])]["region"])
    listings = _radar_scan(regions)
    result = {"ready": True, "listings": listings, "regions": regions,
              "count": len(listings)}
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
