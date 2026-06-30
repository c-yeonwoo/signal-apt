"""인증 — pbkdf2 비밀번호 해시 + 세션 토큰. 외부 의존성 0 (표준 라이브러리)."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from realty_signal import db

_ITER = 200_000
COOKIE = "rsm_session"


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


def signup(email: str, pw: str) -> tuple[str | None, str | None]:
    """(token, error). 성공 시 token, 실패 시 error 메시지."""
    email = (email or "").strip().lower()
    if "@" not in email or len(pw or "") < 6:
        return None, "이메일 형식·비밀번호(6자+)를 확인하세요."
    uid = db.user_create(email, hash_pw(pw))
    if uid is None:
        return None, "이미 가입된 이메일입니다."
    return _new_session(uid), None


def login(email: str, pw: str) -> tuple[str | None, str | None]:
    u = db.user_by_email(email or "")
    if not u or not verify_pw(pw or "", u["pwhash"]):
        return None, "이메일 또는 비밀번호가 올바르지 않습니다."
    return _new_session(u["id"]), None


def _new_session(uid: int) -> str:
    token = secrets.token_urlsafe(32)
    db.session_create(token, uid)
    return token


def current_user(token: str | None):
    return db.session_user(token) if token else None


def logout(token: str | None) -> None:
    if token:
        db.session_delete(token)
