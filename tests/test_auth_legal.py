"""가입 ToS · 비밀번호 재설정 · admin 게이트 헬퍼."""

from __future__ import annotations

from realty_signal import auth, db
from realty_signal.routes import deps


def test_signup_requires_tos(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "app.db")
    db._migrated[0] = False
    tok, err = auth.signup("a@example.com", "secret1", accept_tos=False)
    assert tok is None and "동의" in (err or "")
    tok, err = auth.signup("a@example.com", "secret1", accept_tos=True)
    assert err is None and tok
    u = db.user_by_email("a@example.com")
    assert u
    p = db.profile_get(u["id"])
    assert p.get("tos_version") == "2026-07"


def test_password_reset_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "app.db")
    db._migrated[0] = False
    tok, _ = auth.signup("b@example.com", "oldpass1", accept_tos=True)
    assert tok
    reset_tok, msg = auth.request_password_reset("b@example.com")
    assert reset_tok and "재설정" in msg
    assert auth.reset_password(reset_tok, "newpass1") is None
    # 재사용 불가
    assert auth.reset_password(reset_tok, "another1") is not None
    login_tok, err = auth.login("b@example.com", "newpass1")
    assert err is None and login_tok
    _, err2 = auth.login("b@example.com", "oldpass1")
    assert err2


def test_reset_unknown_email_no_leak(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "app.db")
    db._migrated[0] = False
    tok, msg = auth.request_password_reset("nobody@example.com")
    assert tok is None
    assert "등록된 이메일" in msg


def test_require_admin_helper():
    class R:
        cookies = {}

    assert deps.require_admin(R()) is not None
    assert deps.require_admin(R()).status_code == 403
