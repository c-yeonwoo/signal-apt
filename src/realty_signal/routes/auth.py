"""인증 · 프로필 · 즐겨찾기 · 사용량 · 이벤트."""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from realty_signal import auth, config, db
from realty_signal.routes import deps
from realty_signal.services import market_data as md

router = APIRouter(tags=["auth"])


@router.post("/api/auth/signup")
def auth_signup(data: dict = Body(...)):
    token, err = auth.signup(
        data.get("email", ""), data.get("pw", ""),
        accept_tos=bool(data.get("accept_tos")),
    )
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    r = JSONResponse({"ok": True})
    r.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return r


@router.post("/api/auth/login")
def auth_login(data: dict = Body(...)):
    token, err = auth.login(data.get("email", ""), data.get("pw", ""))
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=401)
    r = JSONResponse({"ok": True})
    r.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return r


@router.post("/api/auth/logout")
def auth_logout(request: Request):
    auth.logout(request.cookies.get(auth.COOKIE))
    r = JSONResponse({"ok": True})
    r.delete_cookie(auth.COOKIE)
    return r


@router.post("/api/auth/forgot-password")
def auth_forgot_password(data: dict = Body(...)):
    """비밀번호 재설정 메일 요청. SMTP 없으면 토큰을 응답에 포함(개발·드라이런)."""
    from realty_signal import digest
    config.load_env()
    token, msg = auth.request_password_reset(data.get("email", ""))
    out: dict = {"ok": True, "message": msg}
    if not token:
        return out
    base = config.app_base_url()
    link = f"{base}/?reset={token}"
    body = (
        "Signal APT 비밀번호 재설정\n\n"
        f"아래 링크를 1시간 이내에 열어 새 비밀번호를 설정하세요.\n{link}\n\n"
        "요청하지 않았다면 이 메일을 무시하세요.\n"
    )
    if digest.smtp_configured():
        try:
            digest.send_email(data.get("email", "").strip().lower(), "[Signal APT] 비밀번호 재설정", body)
        except Exception:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": "메일 발송에 실패했습니다."}, status_code=502)
    else:
        out["dev_reset_link"] = link  # SMTP 미설정 시에만(로컬/스테이징)
    return out


@router.post("/api/auth/reset-password")
def auth_reset_password(data: dict = Body(...)):
    err = auth.reset_password(data.get("token", ""), data.get("pw", ""))
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    return {"ok": True, "message": "비밀번호가 변경되었습니다. 로그인해 주세요."}


@router.get("/api/auth/me")
def auth_me(request: Request):
    u = auth.current_user(request.cookies.get(auth.COOKIE))
    if not u:
        return JSONResponse({"auth": False}, status_code=401)
    return {"auth": True, "email": u["email"], "profile": db.profile_get(u["id"]),
            "onboarded": bool(db.profile_get(u["id"])),
            "admin": (u["email"] or "").lower() in config.admin_whitelist()}


@router.get("/api/profile")
def profile_get(request: Request):
    return db.profile_get(deps.uid(request))


@router.put("/api/profile")
def profile_put(request: Request, data: dict = Body(...)):
    db.profile_set(deps.uid(request), data)
    return {"ok": True}


@router.post("/api/report/ai")
def report_ai(request: Request, data: dict = Body(...)):
    from realty_signal import ai_report
    config.load_env()
    if not ai_report.available():
        return {"available": False}
    uid = deps.uid(request)
    if not uid:
        return {"available": False}
    opus = deps.is_opus_user(request)
    unlimited = opus or deps.is_admin(request)
    ok, ust = deps.usage_allow(uid, "report", unlimited=unlimited)
    if not ok:
        return {"available": False, "reason": "limit", "usage": ust,
                "message": f"이번 주 AI 심층 리포트 한도({ust['limit']}회)에 도달했습니다. 규칙기반 리포트는 계속 이용할 수 있습니다."}
    model = ai_report.OPUS if opus else ai_report.SONNET
    tier = "opus" if opus else "sonnet"
    profile = db.profile_get(uid)
    summary = data.get("summary") or {}
    favorites = deps.fav_context(uid)
    try:
        wk = md.kb().last_date.strftime("%G-W%V")
    except Exception:  # noqa: BLE001
        wk = "na"
    sig = hashlib.md5(json.dumps([profile, summary, favorites, tier], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    ckey = f"aireport:{uid}:{wk}:{sig}"
    cached = db.kv_get(ckey, max_age=14 * 86400)
    if cached is not None:
        return {**cached, "cached": True, "usage": ust}
    news = db.news_recent_for_ai(12)
    report = ai_report.generate(profile, summary, news=news, favorites=favorites, model=model)
    if not report:
        return {"available": False}
    db.usage_inc(uid, "report")
    ust = deps.usage_status(uid, "report", unlimited=unlimited)
    out = {"available": True, "report": report, "news_used": len(news), "tier": tier, "usage": ust}
    db.kv_set(ckey, {k: v for k, v in out.items() if k != "usage"})
    return out


@router.get("/api/usage")
def usage_get(request: Request):
    uid = deps.uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    unlimited = deps.is_opus_user(request) or deps.is_admin(request)
    return {
        "ok": True,
        "nick": deps.usage_status(uid, "nick", unlimited=unlimited),
        "report": deps.usage_status(uid, "report", unlimited=unlimited),
    }


@router.get("/api/favorites")
def favorites_get(request: Request):
    return {"favorites": db.fav_list(deps.uid(request))}


@router.post("/api/favorites")
def favorites_add(request: Request, data: dict = Body(...)):
    db.fav_add(deps.uid(request), data.get("kind", "region"), data.get("key", ""), data.get("label", ""))
    return {"ok": True}


@router.delete("/api/favorites")
def favorites_del(request: Request, kind: str, key: str):
    db.fav_remove(deps.uid(request), kind, key)
    return {"ok": True}


@router.post("/api/events")
def track_event(request: Request, data: dict = Body(...)):
    uid = deps.uid(request)
    if not uid:
        return JSONResponse({"ok": False, "reason": "login_required"}, status_code=401)
    name = (data.get("name") or "").strip()
    props = data.get("props") if isinstance(data.get("props"), dict) else {}
    ok = db.event_log(uid, name, props)
    if not ok:
        return JSONResponse({"ok": False, "error": "invalid_event"}, status_code=400)
    return {"ok": True}


@router.get("/api/admin/events")
def admin_events(request: Request, days: int = 30):
    if not deps.is_admin(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    return {"ok": True, "days": days, "counts": db.event_counts(days)}
