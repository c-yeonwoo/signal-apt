"""인증 — pbkdf2 비밀번호 해시 + 세션 토큰. 외부 의존성 0 (표준 라이브러리)."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from realty_signal import db

_ITER = 200_000
COOKIE = "rsm_session"
_RESET_TTL = 3600  # 비밀번호 재설정 토큰 1시간
_RESET_KV = "pwreset:"


def hash_pw(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, _ITER)
    return salt.hex() + ":" + h.hex()


def verify_pw(pw: str, stored: str) -> bool:
    try:
        salt_hex, h_hex = stored.split(":")
        h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), _ITER)
        return hmac.compare_digest(h.hex(), h_hex)
    except Exception:
        return False


def signup(email: str, pw: str, *, accept_tos: bool = False) -> tuple[str | None, str | None]:
    """(token, error). 성공 시 token, 실패 시 error 메시지. 이용약관 동의 필수."""
    email = (email or "").strip().lower()
    if not accept_tos:
        return None, "이용약관 및 개인정보 처리방침에 동의해 주세요."
    if "@" not in email or len(pw or "") < 6:
        return None, "이메일 형식·비밀번호(6자+)를 확인하세요."
    uid = db.user_create(email, hash_pw(pw))
    if uid is None:
        return None, "이미 가입된 이메일입니다."
    db.profile_set(uid, {"tos_accepted_at": int(time.time()), "tos_version": "2026-07"})
    return _new_session(uid), None


def login(email: str, pw: str) -> tuple[str | None, str | None]:
    u = db.user_by_email(email or "")
    if not u or not verify_pw(pw or "", u["pwhash"]):
        return None, "이메일 또는 비밀번호가 올바르지 않습니다."
    return _new_session(u["id"]), None


def request_password_reset(email: str) -> tuple[str | None, str]:
    """재설정 토큰 발급. 이메일이 없어도 동일 메시지로 enumeration 방지.

    Returns: (token_or_none, public_message). token은 메일 발송용(테스트·드라이런).
    """
    email = (email or "").strip().lower()
    msg = "등록된 이메일이면 재설정 안내를 보냈습니다. 메일함을 확인해 주세요."
    if "@" not in email:
        return None, "이메일 형식을 확인하세요."
    u = db.user_by_email(email)
    if not u:
        return None, msg
    token = secrets.token_urlsafe(32)
    db.kv_set(_RESET_KV + token, {"uid": u["id"], "email": email, "exp": int(time.time()) + _RESET_TTL})
    return token, msg


def reset_password(token: str, new_pw: str) -> str | None:
    """토큰으로 비밀번호 변경. 실패 시 error 메시지, 성공 시 None."""
    token = (token or "").strip()
    if not token or len(new_pw or "") < 6:
        return "비밀번호는 6자 이상이어야 합니다."
    data = db.kv_get(_RESET_KV + token)
    if not data or not isinstance(data, dict):
        return "재설정 링크가 유효하지 않거나 만료되었습니다."
    if int(data.get("exp") or 0) < int(time.time()) or data.get("used"):
        return "재설정 링크가 만료되었습니다. 다시 요청해 주세요."
    uid = data.get("uid")
    if not uid or not db.user_set_pwhash(int(uid), hash_pw(new_pw)):
        return "비밀번호를 변경하지 못했습니다."
    db.kv_set(_RESET_KV + token, {"used": True, "exp": 0})
    return None


def _new_session(uid: int) -> str:
    token = secrets.token_urlsafe(32)
    db.session_create(token, uid)
    return token


def current_user(token: str | None):
    return db.session_user(token) if token else None


def logout(token: str | None) -> None:
    if token:
        db.session_delete(token)
