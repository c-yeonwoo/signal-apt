"""라우트 공용 의존성 — auth / usage / favorites 헬퍼."""

from __future__ import annotations

from fastapi import Request

from realty_signal import auth, config, db


def uid(request: Request):
    u = auth.current_user(request.cookies.get(auth.COOKIE))
    return u["id"] if u else None


def is_opus_user(request: Request) -> bool:
    u = auth.current_user(request.cookies.get(auth.COOKIE))
    return bool(u) and (u.get("email") or "").lower() in config.opus_whitelist()


def is_admin(request: Request) -> bool:
    u = auth.current_user(request.cookies.get(auth.COOKIE))
    return bool(u) and (u.get("email") or "").lower() in config.admin_whitelist()


def fav_context(uid_: int) -> dict:
    fav = db.fav_list(uid_)
    return {
        "관심지역": [f["key"] for f in fav if f["kind"] == "region"],
        "관심단지": [(f.get("label") or f["key"]).split("|")[-1] for f in fav if f["kind"] == "complex"],
    }


def usage_status(uid_: int, kind: str, *, unlimited: bool) -> dict:
    used = db.usage_get(uid_, kind)
    if unlimited:
        return {"kind": kind, "used": used, "limit": None, "remaining": None, "unlimited": True}
    limit = config.nick_weekly_limit() if kind == "nick" else config.report_weekly_limit()
    return {
        "kind": kind, "used": used, "limit": limit,
        "remaining": max(0, limit - used), "unlimited": False,
    }


def usage_allow(uid_: int, kind: str, *, unlimited: bool) -> tuple[bool, dict]:
    st = usage_status(uid_, kind, unlimited=unlimited)
    if st["unlimited"]:
        return True, st
    return st["remaining"] > 0, st
